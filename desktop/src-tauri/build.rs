fn main() {
    // Plain cargo checks/tests and tauri dev never need a prebuilt sidecar.
    // Release configuration is untouched and must supply the real externalBin.
    if std::env::var("PROFILE").as_deref() != Ok("release") {
        let mut config: serde_json::Value = std::env::var("TAURI_CONFIG")
            .ok()
            .map(|value| serde_json::from_str(&value).expect("invalid TAURI_CONFIG"))
            .unwrap_or_else(|| serde_json::json!({}));
        if config.get("bundle").is_none() {
            config["bundle"] = serde_json::json!({});
        }
        config["bundle"]["externalBin"] = serde_json::json!([]);
        config["bundle"]["active"] = serde_json::json!(false);
        let config = config.to_string();
        std::env::set_var("TAURI_CONFIG", &config);
        println!("cargo:rustc-env=TAURI_CONFIG={config}");
    }
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
