//! Run explicitly after freezing: no development Python is used by this test.
#![cfg(windows)]
use serde_json::{json, Value};
use std::sync::Arc;
use tauri::{
    ipc::{CallbackFn, InvokeBody},
    test::{get_ipc_response, mock_builder, INVOKE_KEY},
    webview::InvokeRequest,
};
use tep_desktop::bridge::{BackendProcess, BridgeError, PROTOCOL};

#[test]
#[ignore = "requires a built target-specific sidecar; Windows packaging CI runs this explicitly"]
fn frozen_sidecar_via_shell_ext_and_application_ipc() {
    let binary = std::env::var_os("TEP_FROZEN_SIDECAR").expect("built sidecar path");
    let exe = std::env::current_exe().unwrap();
    // Same basename/layout used by Tauri's final bundle. ShellExt handles test /deps.
    let beside_app = exe
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .join("tep-agent-backend.exe");
    std::fs::copy(binary, &beside_app).unwrap();
    let app = mock_builder()
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![
            tep_desktop::commands::application_request
        ])
        .build(tauri::generate_context!())
        .unwrap();
    let backend = Arc::new(tauri::async_runtime::block_on(async {
        BackendProcess::packaged(app.handle()).unwrap()
    }));
    use tauri::Manager;
    app.manage(backend.clone());
    let webview = tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
        .build()
        .unwrap();
    let invoke = |command: &str, body: Value| {
        get_ipc_response(
            &webview,
            InvokeRequest {
                cmd: command.into(),
                callback: CallbackFn(0),
                error: CallbackFn(1),
                url: "http://tauri.localhost".parse().unwrap(),
                body: InvokeBody::Json(body),
                headers: Default::default(),
                invoke_key: INVOKE_KEY.into(),
            },
        )
    };
    let pid = tauri::async_runtime::block_on(backend.process_id()).unwrap();
    for (i, (method, params)) in [
        ("get_run", json!({})),
        ("get_entity", json!({"entity_id":"reactor"})),
        ("get_signal", json!({"signal_id":"XMEAS(9)"})),
        (
            "get_signal_history",
            json!({"signal_id":"XMEAS(7)","max_points":30}),
        ),
    ]
    .into_iter()
    .enumerate()
    {
        let reply = invoke(
            "application_request",
            json!({"method":method,"params":params}),
        )
        .unwrap()
        .deserialize::<Value>()
        .unwrap();
        assert_eq!(reply["ok"], true, "{reply}");
        assert_eq!(reply["protocol_version"], PROTOCOL);
        assert_eq!(reply["request_id"], format!("desktop-{}", i + 1));
        assert_eq!(
            tauri::async_runtime::block_on(backend.process_id()),
            Some(pid)
        );
        if method == "get_signal_history" {
            let points = reply["result"]["points"].as_array().unwrap();
            assert!(!points.is_empty() && points.len() <= 30);
        }
        let text = reply.to_string().to_lowercase();
        for hidden in ["idv", "evaluator", "developer_setup", "active_disturbances"] {
            assert!(!text.contains(hidden));
        }
    }
    // Plugin is registered for Rust only. IPC cannot spawn, write or kill any process.
    for command in ["execute", "spawn", "stdin_write", "kill", "open"] {
        let denied = invoke(&format!("plugin:shell|{command}"), json!({})).unwrap_err();
        assert!(denied.to_string().contains("not allowed"), "{denied}");
    }
    tauri::async_runtime::block_on(backend.shutdown());
    assert_eq!(tauri::async_runtime::block_on(backend.process_id()), None);

    // Killing a one-file bootloader must not leave its Python worker behind.
    let backend =
        tauri::async_runtime::block_on(async { BackendProcess::packaged(app.handle()).unwrap() });
    assert!(
        tauri::async_runtime::block_on(backend.request("get_run", json!({})))
            .unwrap()
            .ok
    );
    let pid = tauri::async_runtime::block_on(backend.process_id()).unwrap();
    assert_bootloader_death_reaps_worker(&backend, pid);
}

fn assert_bootloader_death_reaps_worker(backend: &BackendProcess, pid: u32) {
    use std::{
        mem::size_of,
        os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle},
    };
    use windows_sys::Win32::{
        Foundation::{INVALID_HANDLE_VALUE, WAIT_OBJECT_0},
        System::{
            Diagnostics::ToolHelp::{
                CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W,
                TH32CS_SNAPPROCESS,
            },
            Threading::{
                OpenProcess, TerminateProcess, WaitForSingleObject, PROCESS_SYNCHRONIZE,
                PROCESS_TERMINATE,
            },
        },
    };
    // SAFETY: owned valid handles are closed once; only the test's backend is killed.
    unsafe {
        let raw = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
        assert_ne!(raw, INVALID_HANDLE_VALUE);
        let snapshot = OwnedHandle::from_raw_handle(raw);
        let mut entry: PROCESSENTRY32W = std::mem::zeroed();
        entry.dwSize = size_of::<PROCESSENTRY32W>() as u32;
        let mut workers = Vec::new();
        let mut valid = Process32FirstW(snapshot.as_raw_handle(), &mut entry);
        while valid != 0 {
            if entry.th32ParentProcessID == pid {
                let handle = OpenProcess(PROCESS_SYNCHRONIZE, 0, entry.th32ProcessID);
                assert!(!handle.is_null());
                workers.push(OwnedHandle::from_raw_handle(handle));
            }
            valid = Process32NextW(snapshot.as_raw_handle(), &mut entry);
        }
        assert!(!workers.is_empty(), "expected PyInstaller one-file worker");
        let raw = OpenProcess(PROCESS_TERMINATE | PROCESS_SYNCHRONIZE, 0, pid);
        assert!(!raw.is_null());
        let parent = OwnedHandle::from_raw_handle(raw);
        assert_ne!(TerminateProcess(parent.as_raw_handle(), 1), 0);
        assert_eq!(
            WaitForSingleObject(parent.as_raw_handle(), 5000),
            WAIT_OBJECT_0
        );
        assert_eq!(
            tauri::async_runtime::block_on(backend.request("get_run", json!({}))).unwrap_err(),
            BridgeError::BackendExited
        );
        tauri::async_runtime::block_on(backend.shutdown());
        for worker in workers {
            assert_eq!(
                WaitForSingleObject(worker.as_raw_handle(), 5000),
                WAIT_OBJECT_0,
                "orphaned frozen worker"
            );
        }
    }
}
