//! Installer build script: embed the app payload + Windows
//! resources (icon, requireAdministrator manifest).
//!
//! The payload is the release `puspabot.exe` built by the
//! desktop crate: `PUSPA_APP_EXE` wins, else
//! `../desktop/target/release/pusbabot.exe` when it exists. When
//! no payload is available (Linux dev builds, `cargo test`), an
//! empty payload is embedded and the installer says so honestly
//! instead of pretending to install.

fn main() {
    let out_dir = std::env::var("OUT_DIR").expect("OUT_DIR is set by cargo");
    let payload_src = std::env::var("PUSPA_APP_EXE")
        .ok()
        .filter(|value| !value.is_empty())
        .map(std::path::PathBuf::from)
        .or_else(|| {
            let candidate = std::path::Path::new("../desktop/target/release/puspabot.exe");
            if candidate.is_file() {
                Some(candidate.to_path_buf())
            } else {
                None
            }
        });
    let mut present = false;
    if let Some(src) = payload_src {
        if src.is_file() {
            std::fs::copy(&src, format!("{out_dir}/payload.exe"))
                .expect("copy the app payload into OUT_DIR");
            present = true;
        }
    }
    if !present {
        std::fs::write(format!("{out_dir}/payload.exe"), b"")
            .expect("write an empty payload placeholder");
    }
    println!(
        "cargo:rustc-env=PAYLOAD_PRESENT={}",
        if present { "1" } else { "0" }
    );
    println!("cargo:rerun-if-env-changed=PUSPA_APP_EXE");

    // Windows resources: the official icon + the admin manifest
    // (Program Files needs elevation; Windows shows UAC once).
    #[cfg(windows)]
    {
        let mut res = winres::WindowsResource::new();
        res.set_icon("../desktop/installer/puspa.ico");
        res.set_manifest_file("app.manifest");
        res.compile().expect("compile the Windows resources");
    }
}
