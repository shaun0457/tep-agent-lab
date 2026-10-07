//! Headless Tauri IPC + real Python: this does not substitute a fake bridge.
use serde_json::{json, Value};
use std::sync::Arc;
use tauri::{
    ipc::{CallbackFn, InvokeBody},
    test::{get_ipc_response, mock_builder, INVOKE_KEY},
    webview::InvokeRequest,
};
use tep_desktop::bridge::{BackendProcess, PROTOCOL, RUN_ID};

#[test]
fn registered_tauri_command_reads_one_real_python_session() {
    let backend = Arc::new(tauri::async_runtime::block_on(async {
        BackendProcess::development().unwrap()
    }));
    let pid = tauri::async_runtime::block_on(backend.process_id()).unwrap();
    let app = mock_builder()
        .manage(backend.clone())
        .invoke_handler(tauri::generate_handler![
            tep_desktop::commands::application_request
        ])
        .build(tauri::generate_context!())
        .unwrap();
    let webview = tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
        .build()
        .unwrap();
    let invoke = |command: &str, method: &str, params: Value| {
        get_ipc_response(
            &webview,
            InvokeRequest {
                cmd: command.into(),
                callback: CallbackFn(0),
                error: CallbackFn(1),
                url: if cfg!(windows) {
                    "http://tauri.localhost"
                } else {
                    "tauri://localhost"
                }
                .parse()
                .unwrap(),
                body: InvokeBody::Json(json!({"method":method,"params":params})),
                headers: Default::default(),
                invoke_key: INVOKE_KEY.into(),
            },
        )
    };
    for (index, (method, params)) in [
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
        let response = invoke("application_request", method, params)
            .unwrap()
            .deserialize::<Value>()
            .unwrap();
        assert_eq!(response["protocol_version"], PROTOCOL);
        assert_eq!(response["request_id"], format!("desktop-{}", index + 1));
        assert_eq!(response["ok"], true, "{response}");
        if method == "get_run" {
            assert_eq!(response["result"]["run_id"], RUN_ID);
        }
        if method == "get_signal_history" {
            let points = response["result"]["points"].as_array().unwrap();
            assert!(!points.is_empty() && points.len() <= 30);
        }
        assert_eq!(
            tauri::async_runtime::block_on(backend.process_id()),
            Some(pid)
        );
    }
    assert_eq!(
        invoke("application_request", "run_shell", json!({})).unwrap_err(),
        json!("UNSUPPORTED_METHOD")
    );
    assert!(invoke("run_shell", "get_run", json!({})).is_err());
    let failure = invoke(
        "application_request",
        "get_entity",
        json!({"entity_id":"missing"}),
    )
    .unwrap()
    .deserialize::<Value>()
    .unwrap();
    assert_eq!(
        failure["error"],
        json!({"code":"UNKNOWN_ENTITY","message":"unknown entity"})
    );
    tauri::async_runtime::block_on(backend.shutdown());
    assert_eq!(tauri::async_runtime::block_on(backend.process_id()), None);
}
