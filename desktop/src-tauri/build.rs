fn main() {
    tauri_build::try_build(
        tauri_build::Attributes::new()
            .app_manifest(tauri_build::AppManifest::new().commands(&["application_request"])),
    )
    .expect("Tauri build configuration failed");

    // tauri-build embeds Common Controls v6 in the app binary, but not Cargo's
    // integration-test executables. MockRuntime still links those Windows APIs.
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows")
        && std::env::var("CARGO_CFG_TARGET_ENV").as_deref() == Ok("msvc")
    {
        let manifest =
            std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/windows.manifest");
        println!("cargo:rerun-if-changed=tests/windows.manifest");
        println!("cargo:rustc-link-arg-tests=/MANIFEST:EMBED");
        println!(
            "cargo:rustc-link-arg-tests=/MANIFESTINPUT:{}",
            manifest.display()
        );
    }
}
