// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
//! fling-guard — checks a Fling install against its signed file list and watches the
//! processes Fling starts.
//!
//!   fling-guard verify <file> [<file>.minisig]   check a release file's signature
//!   fling-guard check [<install dir>]            compare an install with its signed list
//!   fling-guard manifest <dir> <version> <platform>   write <dir>/fling.manifest (build)
//!   fling-guard watch <pid>                      (Windows) started by Fling itself
#![cfg_attr(windows, windows_subsystem = "windows")]

mod check;
mod manifest;
#[cfg(windows)]
mod win;

use std::path::{Path, PathBuf};
use std::process::ExitCode;

fn usage() -> ExitCode {
    eprintln!(
        "fling-guard {} ({})\n\
         \n  fling-guard verify <file> [<signature>]\
         \n  fling-guard check [<install dir>]\
         \n  fling-guard manifest <dir> <version> <platform>\
         \n  fling-guard watch <pid>",
        env!("CARGO_PKG_VERSION"),
        if check::OFFICIAL { "official" } else { "unofficial build" }
    );
    ExitCode::from(64)
}

/// A console for the commands run by hand (the exe is a GUI-subsystem program so that
/// `watch` never flashes a window).
#[cfg(windows)]
fn attach_console() {
    extern "system" {
        fn AttachConsole(pid: u32) -> i32;
    }
    unsafe { AttachConsole(u32::MAX) };
}
#[cfg(not(windows))]
fn attach_console() {}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let a: Vec<&str> = args.iter().map(String::as_str).collect();
    if a.first() != Some(&"watch") {
        attach_console();
    }
    match a.as_slice() {
        ["verify", file] => verify(Path::new(file), &PathBuf::from(format!("{file}.minisig"))),
        ["verify", file, sig] => verify(Path::new(file), Path::new(sig)),
        ["check"] => {
            let exe = std::env::current_exe().ok();
            match exe.as_deref().and_then(Path::parent) {
                Some(d) => check_dir(d),
                None => usage(),
            }
        }
        ["check", dir] => check_dir(Path::new(dir)),
        ["manifest", dir, version, platform] => {
            match manifest::make(Path::new(dir), version, platform) {
                Ok(text) => {
                    let out = Path::new(dir).join(manifest::MANIFEST);
                    if let Err(e) = std::fs::write(&out, text.as_bytes()) {
                        eprintln!("{}: {e}", out.display());
                        return ExitCode::FAILURE;
                    }
                    println!("{} ({} files)\ntrusted comment: {}", out.display(),
                             text.lines().count() - 3, manifest::trusted_comment(version, platform));
                    ExitCode::SUCCESS
                }
                Err(e) => {
                    eprintln!("{e}");
                    ExitCode::FAILURE
                }
            }
        }
        #[cfg(windows)]
        ["watch", pid] => match pid.parse() {
            Ok(pid) => ExitCode::from(win::watch(pid) as u8),
            Err(_) => usage(),
        },
        ["--version"] | ["-V"] => {
            println!("fling-guard {}{}", env!("CARGO_PKG_VERSION"),
                     if check::OFFICIAL { "" } else { " (unofficial build)" });
            ExitCode::SUCCESS
        }
        _ => usage(),
    }
}

fn verify(file: &Path, sig: &Path) -> ExitCode {
    let data = match std::fs::read(file) {
        Ok(d) => d,
        Err(e) => {
            eprintln!("{}: {e}", file.display());
            return ExitCode::from(2);
        }
    };
    let sig_text = match std::fs::read_to_string(sig) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("{}: {e}", sig.display());
            return ExitCode::from(2);
        }
    };
    match check::verify(&data, &sig_text) {
        Ok(tc) => {
            println!("OK: signed by the Fling release key\ntrusted comment: {tc}");
            ExitCode::SUCCESS
        }
        Err(e) => {
            eprintln!("NOT VALID: {e}");
            ExitCode::from(1)
        }
    }
}

fn check_dir(dir: &Path) -> ExitCode {
    let m = match check::load(dir) {
        Ok(check::Load::Signed(m)) => {
            println!("signed list: Fling {} {} ({} files)", m.version, m.platform, m.files.len());
            m
        }
        Ok(check::Load::Unsigned(m, why)) => {
            println!("UNSIGNED list ({why}): Fling {} {}", m.version, m.platform);
            m
        }
        Ok(check::Load::Unlisted) => {
            eprintln!("no {} in {}", manifest::MANIFEST, dir.display());
            return ExitCode::from(3);
        }
        Err(e) => {
            eprintln!("{e}");
            return ExitCode::from(2);
        }
    };
    match check::compare(dir, &m) {
        Ok(r) => {
            for f in &r.changed {
                println!("CHANGED  {f}");
            }
            for f in &r.foreign {
                println!("UNLISTED {f}");
            }
            for f in &r.missing {
                println!("missing  {f}");
            }
            if r.tampered() {
                ExitCode::from(1)
            } else {
                println!("OK: every file matches the list");
                ExitCode::SUCCESS
            }
        }
        Err(e) => {
            eprintln!("{e}");
            ExitCode::from(2)
        }
    }
}
