// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
//! Windows: put Fling into a Job Object and watch every process it starts.
//!
//! * A program started by Fling (or by anything Fling starts) must be either a file of
//!   the signed install list with the same SHA-256, or live in a folder only an
//!   administrator can write (System32, Program Files), or carry a valid Authenticode
//!   signature. Anything else — a dropped miner, a script host copied to %TEMP% — is
//!   stopped the moment Windows reports it.
//! * A child that keeps a large share of the CPU for minutes is stopped as well.
//! * In the background, at low priority, the whole install is compared with its list.
//!
//! Event driven (an I/O completion port gets the job's messages), so the guard sleeps
//! between events and costs ~1-2 MB.

use crate::check::{self, Load, OFFICIAL};
use crate::manifest::{self, Manifest};
use std::collections::{HashMap, HashSet};
use std::ffi::c_void;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::ptr::{null, null_mut};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use windows_sys::core::GUID;
use windows_sys::Win32::Foundation::*;
use windows_sys::Win32::Globalization::GetUserDefaultUILanguage;
use windows_sys::Win32::Security::WinTrust::*;
use windows_sys::Win32::Storage::FileSystem::SYNCHRONIZE;
use windows_sys::Win32::System::Com::CoTaskMemFree;
use windows_sys::Win32::System::IO::*;
use windows_sys::Win32::System::JobObjects::*;
use windows_sys::Win32::System::Threading::*;
use windows_sys::Win32::UI::Shell::*;
use windows_sys::Win32::UI::WindowsAndMessaging::*;

const MSG_NEW_PROCESS: u32 = 6;
const MSG_EXIT_PROCESS: u32 = 7;
const MSG_ABNORMAL_EXIT_PROCESS: u32 = 8;

/// share of the whole machine (all cores) a child may keep…
const CPU_SHARE: f64 = 0.30;
/// …for this long before it is stopped
const CPU_HOT_FOR: Duration = Duration::from_secs(90);
const TICK_MS: u32 = 5000;

fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(Some(0)).collect()
}

fn ru() -> bool {
    unsafe { GetUserDefaultUILanguage() & 0x3ff == 0x19 }
}

// ------------------------------------------------------------------ log + messages

fn log_path() -> PathBuf {
    let base = std::env::var_os("LOCALAPPDATA").map(PathBuf::from).unwrap_or_else(std::env::temp_dir);
    base.join("Fling").join("logs").join("guard.log")
}

pub fn log(msg: &str) {
    let p = log_path();
    let _ = std::fs::create_dir_all(p.parent().unwrap());
    if std::fs::metadata(&p).map(|m| m.len() > 1 << 20).unwrap_or(false) {
        let _ = std::fs::rename(&p, p.with_extension("log.1"));
    }
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&p) {
        let _ = writeln!(f, "{secs} {msg}");
    }
}

/// A message box on its own thread: the guard keeps watching while it is open.
fn alert(text: String) {
    if quiet() {
        return;
    }
    std::thread::spawn(move || {
        let title = wide(if ru() { "Fling — защита" } else { "Fling — protection" });
        let body = wide(&text);
        unsafe {
            MessageBoxW(null_mut(), body.as_ptr(), title.as_ptr(),
                        MB_OK | MB_ICONWARNING | MB_TOPMOST | MB_SETFOREGROUND);
        }
    });
}

// ------------------------------------------------------------------ trust rules

fn known_folder(id: &GUID) -> Option<String> {
    unsafe {
        let mut p: windows_sys::core::PWSTR = null_mut();
        if SHGetKnownFolderPath(id, 0, null_mut(), &mut p) != 0 || p.is_null() {
            return None;
        }
        let mut n = 0;
        while *p.add(n) != 0 {
            n += 1;
        }
        let s = String::from_utf16_lossy(std::slice::from_raw_parts(p, n));
        CoTaskMemFree(p as *const c_void);
        Some(s)
    }
}

/// Folders only an administrator can write to.
fn admin_folders() -> Vec<String> {
    [&FOLDERID_System, &FOLDERID_SystemX86, &FOLDERID_ProgramFiles, &FOLDERID_ProgramFilesX86]
        .iter()
        .filter_map(|id| known_folder(id))
        .map(|s| norm(&s) + "\\")
        .collect()
}

fn norm(p: &str) -> String {
    let p = p.strip_prefix(r"\\?\").unwrap_or(p);
    p.trim_end_matches('\\').to_lowercase()
}

/// A valid Authenticode signature embedded in the file (catalog-signed system files are
/// covered by the admin-folder rule instead).
fn authenticode_ok(path: &str) -> bool {
    let w = wide(path);
    unsafe {
        let mut file = WINTRUST_FILE_INFO {
            cbStruct: std::mem::size_of::<WINTRUST_FILE_INFO>() as u32,
            pcwszFilePath: w.as_ptr(),
            hFile: null_mut(),
            pgKnownSubject: null_mut(),
        };
        let mut data: WINTRUST_DATA = std::mem::zeroed();
        data.cbStruct = std::mem::size_of::<WINTRUST_DATA>() as u32;
        data.dwUIChoice = WTD_UI_NONE;
        data.fdwRevocationChecks = WTD_REVOKE_NONE;
        data.dwUnionChoice = WTD_CHOICE_FILE;
        data.Anonymous.pFile = &mut file;
        data.dwStateAction = WTD_STATEACTION_VERIFY;
        data.dwProvFlags = WTD_CACHE_ONLY_URL_RETRIEVAL;
        let mut action = WINTRUST_ACTION_GENERIC_VERIFY_V2;
        let r = WinVerifyTrust(null_mut(), &mut action, &mut data as *mut _ as *mut c_void);
        data.dwStateAction = WTD_STATEACTION_CLOSE;
        WinVerifyTrust(null_mut(), &mut action, &mut data as *mut _ as *mut c_void);
        r == 0
    }
}

fn image_path(h: HANDLE) -> Option<String> {
    let mut buf = vec![0u16; 32768];
    let mut n = buf.len() as u32;
    let ok = unsafe { QueryFullProcessImageNameW(h, PROCESS_NAME_WIN32, buf.as_mut_ptr(), &mut n) };
    (ok != 0).then(|| String::from_utf16_lossy(&buf[..n as usize]))
}

/// Tools Fling uses from outside its install (adb, scrcpy picked or installed by the Tools
/// window): `%LOCALAPPDATA%\Fling\guard-trusted.txt`, "sha256  full path" per line.
fn trusted_path() -> PathBuf {
    let base = std::env::var_os("LOCALAPPDATA").map(PathBuf::from).unwrap_or_else(std::env::temp_dir);
    base.join("Fling").join("guard-trusted.txt")
}

/// Where Fling's updater keeps a downloaded setup next to the release's signed checksum list
/// (fling_update.DIR): `%LOCALAPPDATA%\Fling\updates`.
fn updates_dir() -> String {
    let base = std::env::var_os("LOCALAPPDATA").map(PathBuf::from).unwrap_or_else(std::env::temp_dir);
    norm(&base.join("Fling").join("updates").to_string_lossy()) + "\\"
}

/// (size, modified) of a file: a cached hash is reused only while this is unchanged.
fn file_key(path: &str) -> Option<(u64, std::time::SystemTime)> {
    let md = std::fs::metadata(path).ok()?;
    Some((md.len(), md.modified().ok()?))
}

struct Rules {
    install: String,          // normalised install folder + "\"
    list: Option<Manifest>,   // the signed list (None: a build from source)
    admin: Vec<String>,
    enforce: bool,            // stop what breaks the rules (official signed build) or only log
    /// image path → (size, modified, sha256) already computed
    hashes: HashMap<String, (u64, std::time::SystemTime, String)>,
    /// normalised path → sha256 from guard-trusted.txt, and that file's modified time
    trusted: HashMap<String, String>,
    trusted_at: Option<std::time::SystemTime>,
}

impl Rules {
    fn hash(&mut self, path: &str) -> Option<String> {
        let n = norm(path);
        let (len, at) = file_key(path)?;
        if let Some((l, a, h)) = self.hashes.get(&n) {
            if *l == len && *a == at {
                return Some(h.clone());
            }
        }
        let h = manifest::sha256_file(Path::new(path)).ok()?;
        self.hashes.insert(n, (len, at, h.clone()));
        Some(h)
    }

    fn reload_trusted(&mut self) {
        let p = trusted_path();
        let at = std::fs::metadata(&p).and_then(|m| m.modified()).ok();
        if at == self.trusted_at {
            return;
        }
        self.trusted_at = at;
        self.trusted.clear();
        if let Ok(text) = std::fs::read_to_string(&p) {
            for l in text.lines() {
                if let Some((h, path)) = l.split_once("  ") {
                    if h.len() == 64 && h.bytes().all(|b| b.is_ascii_hexdigit()) && !path.starts_with(r"\\") {
                        self.trusted.insert(norm(path), h.to_lowercase());
                    }
                }
            }
        }
    }

    /// The update setup Fling's updater runs: `Fling-Setup-<v>.exe` in the updates folder whose
    /// SHA-256 is in the SHA256SUMS.txt beside it, that list signed with the release key as a
    /// release (trusted comment "fling-release version=<v>"). Returns <v>. Nobody can make such a
    /// pair without the release key — the same check fling_update did before it downloaded.
    fn verified_update(&mut self, path: &str) -> Option<String> {
        let n = norm(path);
        let name = n.strip_prefix(&updates_dir())?.to_string();
        if name.contains('\\') || !name.starts_with("fling-setup-") || !name.ends_with(".exe") {
            return None;
        }
        let dir = Path::new(path).parent()?;
        let sums = std::fs::read(dir.join("SHA256SUMS.txt")).ok()?;
        let sig = std::fs::read_to_string(dir.join("SHA256SUMS.txt.minisig")).ok()?;
        let tc = check::verify(&sums, &sig).ok()?;
        let v = tc.strip_prefix("fling-release version=")?.trim().to_string();
        if name != format!("fling-setup-{}.exe", v.to_lowercase()) {
            return None;                        // the list is for another version
        }
        let want = String::from_utf8_lossy(&sums).lines().find_map(|l| {
            let (h, f) = l.split_once("  ")?;
            f.trim().trim_start_matches('*').eq_ignore_ascii_case(&name).then(|| h.trim().to_lowercase())
        })?;
        (self.hash(path)? == want).then_some(v)
    }

    /// Why this program may run, or None.
    fn allowed(&mut self, path: &str) -> Option<&'static str> {
        let n = norm(path);
        if let Some(rel) = n.strip_prefix(&self.install) {
            let rel = rel.replace('\\', "/");
            let want = self.list.as_ref()?.files.get(&rel)?.clone();
            return (self.hash(path)? == want).then_some("listed");
        }
        if self.admin.iter().any(|d| n.starts_with(d.as_str())) {
            return Some("admin folder");
        }
        self.reload_trusted();
        if let Some(want) = self.trusted.get(&n).cloned() {
            if self.hash(path).as_deref() == Some(want.as_str()) {
                return Some("trusted tool");
            }
        }
        if authenticode_ok(path) {
            return Some("signed");
        }
        None
    }
}

// ------------------------------------------------------------------ watch

struct Proc {
    h: HANDLE,
    path: String,
    cpu: u64,       // 100 ns units, kernel + user
    hot_since: Option<Instant>,
}

fn cpu_time(h: HANDLE) -> u64 {
    let z = FILETIME { dwLowDateTime: 0, dwHighDateTime: 0 };
    let (mut c, mut e, mut k, mut u) = (z, z, z, z);
    unsafe { GetProcessTimes(h, &mut c, &mut e, &mut k, &mut u) };
    let f = |t: FILETIME| ((t.dwHighDateTime as u64) << 32) | t.dwLowDateTime as u64;
    f(k) + f(u)
}

pub fn watch(pid: u32) -> i32 {
    let dir = std::env::current_exe().ok().and_then(|p| p.parent().map(Path::to_path_buf));
    let Some(dir) = dir else { return 1 };

    let main = unsafe {
        OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION
                    | SYNCHRONIZE, 0, pid)
    };
    if main.is_null() {
        log(&format!("watch: cannot open process {pid}"));
        return 1;
    }
    // The guard watches the Fling that lives next to it, nothing else.
    let main_path = image_path(main).unwrap_or_default();
    if !norm(&main_path).starts_with(&(norm(&dir.to_string_lossy()) + "\\")) && OFFICIAL {
        log(&format!("watch: {main_path} is not in {}", dir.display()));
        return 1;
    }

    // 1. the signed list — checked before anything else, it is quick
    let (list, enforce) = match check::load(&dir) {
        Ok(Load::Signed(m)) => {
            log(&format!("watch: Fling {} (signed list, {} files)", m.version, m.files.len()));
            (Some(m), true)
        }
        Ok(Load::Unsigned(m, why)) => {
            log(&format!("watch: list not signed ({why})"));
            if OFFICIAL {
                return tampered(main, None, &[format!("{}: {why}", manifest::SIGNATURE)]);
            }
            (Some(m), false)
        }
        Ok(Load::Unlisted) => {
            if OFFICIAL {
                return tampered(main, None, &[format!("{} missing", manifest::MANIFEST)]);
            }
            log("watch: build from source (no list), report only");
            (None, false)
        }
        Err(e) => {
            log(&format!("watch: {e}"));
            if OFFICIAL {
                return tampered(main, None, &[e]);
            }
            (None, false)
        }
    };
    let rules = Arc::new(Mutex::new(Rules {
        install: norm(&dir.to_string_lossy()) + "\\",
        list,
        admin: admin_folders(),
        enforce,
        hashes: HashMap::new(),
        trusted: HashMap::new(),
        trusted_at: None,
    }));

    // 2. job + completion port: Windows tells us about every new process at once
    let (job, port) = unsafe {
        let job = CreateJobObjectW(null(), null());
        let port = CreateIoCompletionPort(INVALID_HANDLE_VALUE, null_mut(), 0, 1);
        (job, port)
    };
    if job.is_null() || port.is_null() {
        log("watch: no job object");
        return 1;
    }
    unsafe {
        let assoc = JOBOBJECT_ASSOCIATE_COMPLETION_PORT { CompletionKey: job, CompletionPort: port };
        SetInformationJobObject(job, JobObjectAssociateCompletionPortInformation,
                                &assoc as *const _ as *const c_void,
                                std::mem::size_of_val(&assoc) as u32);
        if AssignProcessToJobObject(job, main) == 0 {
            log(&format!("watch: cannot put Fling into a job (error {})", GetLastError()));
            return 1;
        }
    }

    // 3. the full comparison, in the background at low priority
    {
        let rules = rules.clone();
        let dir = dir.clone();
        let main_raw = main as usize;
        let job_raw = job as usize;
        std::thread::spawn(move || unsafe {
            SetThreadPriority(GetCurrentThread(), THREAD_MODE_BACKGROUND_BEGIN);
            let t = Instant::now();
            let report = {
                let r = rules.lock().unwrap();
                let Some(m) = r.list.as_ref() else { return };
                // hashing ~300 MB takes a moment: compare a copy without holding the lock
                Manifest { version: m.version.clone(), platform: m.platform.clone(), files: m.files.clone() }
            };
            match check::compare(&dir, &report) {
                Ok(rep) => {
                    log(&format!("check: {} changed, {} unlisted, {} missing ({} ms)",
                                 rep.changed.len(), rep.foreign.len(), rep.missing.len(),
                                 t.elapsed().as_millis()));
                    if rep.tampered() {
                        let mut lines: Vec<String> = rep.changed.iter().map(|f| format!("~ {f}")).collect();
                        lines.extend(rep.foreign.iter().map(|f| format!("+ {f}")));
                        if rules.lock().unwrap().enforce {
                            tampered(main_raw as HANDLE, Some(job_raw as HANDLE), &lines);
                        } else {
                            for l in &lines {
                                log(&format!("check (report only): {l}"));
                            }
                        }
                    }
                }
                Err(e) => log(&format!("check: {e}")),
            }
        });
    }

    // 4. events
    let ncpu = unsafe { GetActiveProcessorCount(0xffff) }.max(1) as f64;
    let mut procs: HashMap<u32, Proc> = HashMap::new();
    let mut warned: HashSet<String> = HashSet::new();
    let mut last_tick = Instant::now();
    loop {
        let mut msg = 0u32;
        let mut key = 0usize;
        let mut ov: *mut OVERLAPPED = null_mut();
        let got = unsafe { GetQueuedCompletionStatus(port, &mut msg, &mut key, &mut ov, TICK_MS) };
        if got != 0 && key == job as usize {
            let id = ov as usize as u32;
            match msg {
                MSG_NEW_PROCESS => {
                    if on_new(id, pid, &rules, &mut procs, &mut warned) {
                        break;                  // an update is installing: hand over
                    }
                }
                MSG_EXIT_PROCESS | MSG_ABNORMAL_EXIT_PROCESS => {
                    if let Some(p) = procs.remove(&id) {
                        unsafe { CloseHandle(p.h) };
                    }
                    if id == pid {
                        break;
                    }
                }
                _ => {}
            }
        }
        if unsafe { WaitForSingleObject(main, 0) } == WAIT_OBJECT_0 {
            break;
        }
        if last_tick.elapsed() >= Duration::from_millis(TICK_MS as u64) {
            let secs = last_tick.elapsed().as_secs_f64();
            last_tick = Instant::now();
            cpu_tick(pid, secs, ncpu, &rules, &mut procs, &mut warned);
        }
    }
    log("watch: Fling exited");
    0
}

/// Returns true when a verified update setup started: the guard hands over and stops watching.
fn on_new(id: u32, main_pid: u32, rules: &Arc<Mutex<Rules>>, procs: &mut HashMap<u32, Proc>,
          warned: &mut HashSet<String>) -> bool {
    let h = unsafe {
        OpenProcess(PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION, 0, id)
    };
    if h.is_null() {
        return false; // already gone
    }
    let path = image_path(h).unwrap_or_default();
    if id != main_pid {
        let mut r = rules.lock().unwrap();
        let allowed = r.allowed(&path);
        // The setup replaces this install (the guard too) and closes Fling; its own helper
        // (Inno's setup.tmp in %TEMP%) can't be listed anywhere. Step aside for it: watching on
        // would block that helper, or count the files it replaces as tampering and stop it.
        if allowed.is_none() {
            if let Some(v) = r.verified_update(&path) {
                log(&format!("update {id} {path} (signed release {v}): the setup takes over, guard stops watching"));
                unsafe { CloseHandle(h) };
                return true;
            }
        }
        match allowed {
            Some(why) => log(&format!("start {id} {path} ({why})")),
            None if r.enforce => {
                unsafe { TerminateProcess(h, 0xF1) };
                unsafe { CloseHandle(h) };
                log(&format!("BLOCKED {id} {path}"));
                if warned.insert(path.clone()) {
                    alert(if ru() {
                        format!("Fling остановил программу, которой нет в его подписанном списке \
                                 и у которой нет подписи издателя:\n\n{path}\n\n\
                                 Если это не вы её запустили через Fling — проверьте компьютер \
                                 антивирусом.\nПодробности: {}", log_path().display())
                    } else {
                        format!("Fling stopped a program that is not in its signed file list and \
                                 has no publisher signature:\n\n{path}\n\n\
                                 If you did not start it through Fling, scan the computer for \
                                 malware.\nDetails: {}", log_path().display())
                    });
                }
                return false;
            }
            None => log(&format!("would block {id} {path} (report only)")),
        }
    }
    procs.insert(id, Proc { h, path, cpu: cpu_time(h), hot_since: None });
    false
}

fn cpu_tick(main_pid: u32, secs: f64, ncpu: f64, rules: &Arc<Mutex<Rules>>,
            procs: &mut HashMap<u32, Proc>, warned: &mut HashSet<String>) {
    let enforce = rules.lock().unwrap().enforce;
    let mut stop = Vec::new();
    for (&id, p) in procs.iter_mut() {
        let now = cpu_time(p.h);
        let share = (now.saturating_sub(p.cpu)) as f64 / 1e7 / secs / ncpu;
        p.cpu = now;
        if share < CPU_SHARE {
            p.hot_since = None;
            continue;
        }
        let since = *p.hot_since.get_or_insert_with(Instant::now);
        if since.elapsed() < CPU_HOT_FOR {
            continue;
        }
        p.hot_since = None;
        if id == main_pid {
            // Fling itself: never stopped by the guard, only reported once
            log(&format!("cpu: Fling itself at {:.0}% of the machine", share * 100.0));
            if warned.insert("cpu:main".into()) {
                alert(if ru() { "Fling долго сильно грузит процессор. Если это не трансляция и не \
                                 экран ТВ — перезапустите Fling и пришлите guard.log и app.log." }
                      else { "Fling has been using a lot of CPU for a while. If it is not casting \
                              or showing the TV screen, restart it and send guard.log and app.log." }
                      .to_string());
            }
        } else if enforce {
            stop.push(id);
        } else {
            log(&format!("cpu: {} {} at {:.0}% (report only)", id, p.path, share * 100.0));
        }
    }
    for id in stop {
        if let Some(p) = procs.remove(&id) {
            unsafe { TerminateProcess(p.h, 0xF2) };
            unsafe { CloseHandle(p.h) };
            log(&format!("STOPPED (cpu) {id} {}", p.path));
            if warned.insert(format!("cpu:{}", p.path)) {
                alert(if ru() {
                    format!("Fling остановил процесс, который больше {} с занимал больше {}% \
                             процессора:\n\n{}", CPU_HOT_FOR.as_secs(), (CPU_SHARE * 100.0) as u32, p.path)
                } else {
                    format!("Fling stopped a process that used more than {}% of the CPU for over \
                             {} s:\n\n{}", (CPU_SHARE * 100.0) as u32, CPU_HOT_FOR.as_secs(), p.path)
                });
            }
        }
    }
}

/// The install does not match its signed list: stop Fling and everything it started.
fn tampered(main: HANDLE, job: Option<HANDLE>, lines: &[String]) -> i32 {
    unsafe {
        match job {
            Some(j) => { TerminateJobObject(j, 0xF0); }
            None => { TerminateProcess(main, 0xF0); }
        }
    }
    for l in lines {
        log(&format!("TAMPERED {l}"));
    }
    let shown: Vec<&str> = lines.iter().take(8).map(String::as_str).collect();
    let more = if lines.len() > 8 { format!("\n… +{}", lines.len() - 8) } else { String::new() };
    let text = if ru() {
        format!("Файлы Fling изменены после установки, поэтому Fling закрыт:\n\n{}{}\n\n\
                 Установите Fling заново со страницы релизов на GitHub (orfixe/fling-port) \
                 и проверьте компьютер антивирусом.", shown.join("\n"), more)
    } else {
        format!("Fling's files were changed after the install, so Fling was closed:\n\n{}{}\n\n\
                 Install Fling again from the GitHub releases page (orfixe/fling-port) and scan \
                 the computer for malware.", shown.join("\n"), more)
    };
    if quiet() {
        return 2;
    }
    let title = wide(if ru() { "Fling — защита" } else { "Fling — protection" });
    let body = wide(&text);
    unsafe {
        MessageBoxW(null_mut(), body.as_ptr(), title.as_ptr(), MB_OK | MB_ICONERROR | MB_TOPMOST | MB_SETFOREGROUND);
    }
    2
}

/// FLING_GUARD_QUIET=1: log only, no message boxes (automated tests).
fn quiet() -> bool {
    std::env::var_os("FLING_GUARD_QUIET").is_some()
}
