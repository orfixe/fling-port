// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
//! Signature check and the full comparison of an install with its signed list.

use crate::manifest::{self, Manifest};
use minisign_verify::{PublicKey, Signature};
use std::fs;
use std::path::Path;

/// The release public key (minisign). Only files signed with the matching secret key,
/// which never leaves the maintainer's machine, pass.
pub const PUBLIC_KEY: &str = include_str!("../../../security/fling-release.pub");

/// Built by the official release script (`FLING_OFFICIAL=1 cargo build`): such a guard
/// treats a missing or broken signature as tampering. A guard built by anyone else only
/// reports.
pub const OFFICIAL: bool = option_env!("FLING_OFFICIAL").is_some();

fn public_key() -> Result<PublicKey, String> {
    let b64 = PUBLIC_KEY
        .lines()
        .map(str::trim)
        .find(|l| !l.is_empty() && !l.starts_with("untrusted comment:"))
        .ok_or("no public key")?;
    PublicKey::from_base64(b64).map_err(|e| format!("public key: {e}"))
}

/// Verify a minisign signature with the release key; returns the trusted comment.
pub fn verify(data: &[u8], sig_text: &str) -> Result<String, String> {
    let pk = public_key()?;
    let sig = Signature::decode(sig_text).map_err(|e| format!("signature: {e}"))?;
    pk.verify(data, &sig, false).map_err(|_| "the signature does not match".to_string())?;
    Ok(sig.trusted_comment().to_string())
}

pub enum Load {
    /// no fling.manifest: a build from source
    Unlisted,
    /// a list without a valid release signature
    Unsigned(Manifest, String),
    Signed(Manifest),
}

/// Read the list of `dir` and check its signature (fast: no file is hashed here).
pub fn load(dir: &Path) -> Result<Load, String> {
    let text = match fs::read(dir.join(manifest::MANIFEST)) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Load::Unlisted),
        Err(e) => return Err(format!("{}: {e}", manifest::MANIFEST)),
    };
    // the signature first: nothing of an unsigned list is trusted, not even its syntax
    let verdict = match fs::read_to_string(dir.join(manifest::SIGNATURE)) {
        Ok(sig) => verify(&text, &sig),
        Err(_) => Err("no signature".into()),
    };
    let m = manifest::parse(std::str::from_utf8(&text).map_err(|_| "manifest is not UTF-8")?)
        .map_err(|e| match &verdict {
            Err(v) => format!("{e} ({v})"),
            Ok(_) => e,
        })?;
    match verdict {
        Ok(tc) if tc == m.trusted_comment() => Ok(Load::Signed(m)),
        Ok(tc) => Ok(Load::Unsigned(m, format!("signed for something else: {tc:?}"))),
        Err(e) => Ok(Load::Unsigned(m, e)),
    }
}

#[derive(Default)]
pub struct Report {
    /// listed files whose contents differ
    pub changed: Vec<String>,
    /// files that are not in the list
    pub foreign: Vec<String>,
    /// listed files that are gone (a broken install, not by itself a danger)
    pub missing: Vec<String>,
}

impl Report {
    pub fn tampered(&self) -> bool {
        !self.changed.is_empty() || !self.foreign.is_empty()
    }
}

/// Hash every file of the install and compare with the list.
pub fn compare(dir: &Path, m: &Manifest) -> Result<Report, String> {
    let mut r = Report::default();
    let present = manifest::walk(dir).map_err(|e| format!("reading {}: {e}", dir.display()))?;
    let mut seen = std::collections::HashSet::new();
    for rel in &present {
        let key = rel.to_lowercase();
        if manifest::is_not_listed(&key) {
            continue;
        }
        match m.files.get(&key) {
            None => r.foreign.push(rel.clone()),
            Some(want) => {
                seen.insert(key);
                match manifest::sha256_file(&dir.join(rel)) {
                    Ok(got) if &got == want => {}
                    _ => r.changed.push(rel.clone()),
                }
            }
        }
    }
    for k in m.files.keys() {
        if !seen.contains(k) {
            r.missing.push(k.clone());
        }
    }
    r.missing.sort();
    Ok(r)
}
