// SPDX-License-Identifier: MIT
// Copyright (c) 2026 orfixe and the Fling port contributors
//! fling-sign — the maintainer's release signing tool (minisign format, Ed25519).
//!
//!   fling-sign keygen                      create the key pair (asks for a password)
//!   fling-sign sign <file>... [--comment <trusted comment>]
//!
//! The secret key lives in %USERPROFILE%\.fling-signing\fling-release.key (or
//! $FLING_SIGNING_KEY), encrypted with your password. It must never be copied into the
//! repository, CI secrets or a cloud drive. Back it up offline (USB stick in a drawer).
//! Anyone can check a signature with `minisign -Vm <file> -P <public key>` or
//! `fling-guard verify <file>`.

use minisign::{KeyPair, PublicKeyBox, SecretKeyBox};
use std::fs;
use std::io::Cursor;
use std::path::PathBuf;
use std::process::ExitCode;

fn key_dir() -> PathBuf {
    let home = std::env::var_os("USERPROFILE").or_else(|| std::env::var_os("HOME")).unwrap_or_default();
    PathBuf::from(home).join(".fling-signing")
}

fn secret_path() -> PathBuf {
    std::env::var_os("FLING_SIGNING_KEY").map(PathBuf::from).unwrap_or_else(|| key_dir().join("fling-release.key"))
}

/// The key password: typed in the console, or $FLING_SIGN_PASSWORD for automated tests
/// with a throw-away key (never set it for the release key).
fn password() -> Option<String> {
    std::env::var("FLING_SIGN_PASSWORD").ok().filter(|p| !p.is_empty())
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let r = match args.first().map(String::as_str) {
        Some("keygen") => keygen(),
        Some("sign") => sign(&args[1..]),
        _ => {
            eprintln!("fling-sign keygen\nfling-sign sign <file>... [--comment <trusted comment>]");
            return ExitCode::from(64);
        }
    };
    match r {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("error: {e}");
            ExitCode::FAILURE
        }
    }
}

fn keygen() -> Result<(), String> {
    let sk_path = secret_path();
    let pk_path = sk_path.with_extension("pub");
    if sk_path.exists() {
        return Err(format!("{} already exists — refusing to overwrite a release key", sk_path.display()));
    }
    fs::create_dir_all(sk_path.parent().unwrap()).map_err(|e| e.to_string())?;
    if password().is_none() {
        eprintln!("Choose a password for the Fling release key (you will type it for every release).");
    }
    let kp = KeyPair::generate_encrypted_keypair(password()).map_err(|e| e.to_string())?;
    let pk_box = kp.pk.to_box().map_err(|e| e.to_string())?;
    let sk_box = kp.sk.to_box(None).map_err(|e| e.to_string())?;
    fs::write(&sk_path, sk_box.to_string()).map_err(|e| e.to_string())?;
    fs::write(&pk_path, pk_box.to_string()).map_err(|e| e.to_string())?;
    println!("secret key: {}  (keep it private, back it up offline)", sk_path.display());
    println!("public key: {}\n\n{}", pk_path.display(), pk_box.to_string());
    Ok(())
}

fn sign(args: &[String]) -> Result<(), String> {
    let mut files = Vec::new();
    let mut comment: Option<String> = None;
    let mut it = args.iter();
    while let Some(a) = it.next() {
        if a == "--comment" {
            comment = Some(it.next().ok_or("--comment needs a value")?.clone());
        } else {
            files.push(a.clone());
        }
    }
    if files.is_empty() {
        return Err("no files to sign".into());
    }
    let sk_path = secret_path();
    let sk_box = SecretKeyBox::from_string(
        &fs::read_to_string(&sk_path).map_err(|e| format!("{}: {e}", sk_path.display()))?,
    )
    .map_err(|e| e.to_string())?;
    let sk = sk_box.into_secret_key(password()).map_err(|e| e.to_string())?;
    let pk = fs::read_to_string(sk_path.with_extension("pub"))
        .ok()
        .and_then(|s| PublicKeyBox::from_string(&s).ok())
        .and_then(|b| b.into_public_key().ok());
    for f in files {
        let data = fs::read(&f).map_err(|e| format!("{f}: {e}"))?;
        let name = std::path::Path::new(&f).file_name().unwrap().to_string_lossy().into_owned();
        let tc = comment.clone().unwrap_or_else(|| format!("fling-release file:{name}"));
        let sig = minisign::sign(pk.as_ref(), &sk, Cursor::new(&data), Some(&tc), Some("Fling release signature"))
            .map_err(|e| e.to_string())?;
        let out = format!("{f}.minisig");
        fs::write(&out, sig.to_string()).map_err(|e| e.to_string())?;
        println!("{out}");
    }
    Ok(())
}
