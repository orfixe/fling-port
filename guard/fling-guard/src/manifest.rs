// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
//! The signed file list of an install: `fling.manifest` + `fling.manifest.minisig`.
//!
//! ```text
//! fling-manifest 1
//! version 0.3.4
//! platform windows-x64
//! <sha256 hex>  <path/relative/to/the/install, forward slashes>
//! ```
//! The trusted (signed) comment of the signature must read
//! `fling-manifest version=<version> platform=<platform>`, so the header is signed too.

use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::fs;
use std::io::Read;
use std::path::Path;

pub const MANIFEST: &str = "fling.manifest";
pub const SIGNATURE: &str = "fling.manifest.minisig";

/// Files at the top of the install that are not in the list: the list itself and what
/// Inno Setup writes after the install (its uninstaller). Fling never starts them, and
/// the guard blocks them like any other unlisted program if something does.
const NOT_LISTED: &[&str] = &[MANIFEST, SIGNATURE, "unins000.exe", "unins000.dat", "unins000.msg"];

pub struct Manifest {
    pub version: String,
    pub platform: String,
    /// lower-case relative path → sha256 hex
    pub files: HashMap<String, String>,
}

impl Manifest {
    pub fn trusted_comment(&self) -> String {
        trusted_comment(&self.version, &self.platform)
    }
}

pub fn trusted_comment(version: &str, platform: &str) -> String {
    format!("fling-manifest version={version} platform={platform}")
}

pub fn is_not_listed(rel: &str) -> bool {
    NOT_LISTED.iter().any(|n| n.eq_ignore_ascii_case(rel))
}

pub fn sha256_file(p: &Path) -> std::io::Result<String> {
    let mut f = fs::File::open(p)?;
    let mut h = Sha256::new();
    let mut buf = vec![0u8; 1 << 20];
    loop {
        let n = f.read(&mut buf)?;
        if n == 0 {
            break;
        }
        h.update(&buf[..n]);
    }
    Ok(h.finalize().iter().map(|b| format!("{b:02x}")).collect())
}

/// Every file under `root` as a relative path with forward slashes. Links and junctions
/// are returned as entries too (never followed), so they show up as unlisted files.
pub fn walk(root: &Path) -> std::io::Result<Vec<String>> {
    fn go(root: &Path, dir: &Path, out: &mut Vec<String>) -> std::io::Result<()> {
        for e in fs::read_dir(dir)? {
            let e = e?;
            let p = e.path();
            let md = fs::symlink_metadata(&p)?;
            let rel = p
                .strip_prefix(root)
                .map_err(|_| std::io::Error::other("path outside the install"))?
                .components()
                .map(|c| c.as_os_str().to_string_lossy().into_owned())
                .collect::<Vec<_>>()
                .join("/");
            if md.is_dir() && !md.file_type().is_symlink() && !is_reparse_point(&md) {
                go(root, &p, out)?;
            } else {
                out.push(rel);
            }
        }
        Ok(())
    }
    let mut out = Vec::new();
    go(root, root, &mut out)?;
    out.sort_by_key(|s| s.to_lowercase());
    Ok(out)
}

#[cfg(windows)]
fn is_reparse_point(md: &fs::Metadata) -> bool {
    use std::os::windows::fs::MetadataExt;
    md.file_attributes() & 0x400 != 0 // FILE_ATTRIBUTE_REPARSE_POINT: a junction
}
#[cfg(not(windows))]
fn is_reparse_point(_: &fs::Metadata) -> bool {
    false
}

/// Write the list for the files under `root` (the build output).
pub fn make(root: &Path, version: &str, platform: &str) -> std::io::Result<String> {
    for v in [version, platform] {
        if v.is_empty() || !v.chars().all(|c| c.is_ascii_alphanumeric() || "._-+".contains(c)) {
            return Err(std::io::Error::other(format!("bad version/platform: {v:?}")));
        }
    }
    let mut out = format!("fling-manifest 1\nversion {version}\nplatform {platform}\n");
    for rel in walk(root)? {
        if is_not_listed(&rel) {
            continue;
        }
        if rel.contains("  ") || rel.contains('\n') {
            return Err(std::io::Error::other(format!("file name not allowed: {rel:?}")));
        }
        out.push_str(&format!("{}  {}\n", sha256_file(&root.join(&rel))?, rel));
    }
    Ok(out)
}

pub fn parse(text: &str) -> Result<Manifest, String> {
    let mut lines = text.lines();
    if lines.next() != Some("fling-manifest 1") {
        return Err("not a fling manifest".into());
    }
    let field = |l: Option<&str>, key: &str| -> Result<String, String> {
        l.and_then(|l| l.strip_prefix(key))
            .and_then(|v| v.strip_prefix(' '))
            .map(str::to_string)
            .ok_or_else(|| format!("manifest: no {key}"))
    };
    let version = field(lines.next(), "version")?;
    let platform = field(lines.next(), "platform")?;
    let mut files = HashMap::new();
    for l in lines {
        if l.is_empty() {
            continue;
        }
        let (hash, rel) = l.split_once("  ").ok_or("manifest: bad line")?;
        let ok_hash = hash.len() == 64 && hash.bytes().all(|b| b.is_ascii_hexdigit());
        let ok_rel = !rel.is_empty()
            && !rel.starts_with('/')
            && !rel.contains('\\')
            && !rel.contains(':')
            && !rel.split('/').any(|c| c.is_empty() || c == "." || c == "..");
        if !ok_hash || !ok_rel {
            return Err(format!("manifest: bad entry {rel:?}"));
        }
        files.insert(rel.to_lowercase(), hash.to_lowercase());
    }
    Ok(Manifest { version, platform, files })
}
