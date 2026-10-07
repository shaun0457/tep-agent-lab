use serde_json::{json, Value};
use std::{path::Path, sync::Arc, time::Duration};
use tep_desktop::bridge::{BackendProcess, BridgeError, PROTOCOL, RUN_ID};
use tokio::process::Command;

fn python() -> std::ffi::OsString {
    std::env::var_os("TEP_AGENT_PYTHON").unwrap_or_else(|| "python".into())
}

fn fixture(mode: &str) -> BackendProcess {
    let mut command = Command::new(python());
    command
        .arg(Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/child.py"))
        .arg(mode);
    BackendProcess::spawn(command).unwrap()
}

#[tokio::test]
async fn one_child_monotonic_ids_canonical_newline_and_allowlist() {
    let backend = fixture("normal");
    let pid = backend.process_id().await.unwrap();
    for (index, method) in ["get_run", "get_entity", "get_signal", "get_signal_history"]
        .iter()
        .enumerate()
    {
        let response = backend.request(method, json!({})).await.unwrap();
        assert_eq!(response.request_id, format!("desktop-{}", index + 1));
        let result = response.result.unwrap();
        assert_eq!(result["pid"], pid);
        assert_eq!(result["count"], index + 1);
        assert_eq!(result["params"]["run_id"], RUN_ID);
    }
    for method in ["start", "get_evaluator", "run_shell", "send_raw_stdin"] {
        assert_eq!(
            backend.request(method, json!({})).await.unwrap_err(),
            BridgeError::UnsupportedMethod
        );
    }
    for params in [
        json!({"run_id":"other"}),
        json!({"scope":"EVALUATOR"}),
        json!({"executable":"bad"}),
        json!([]),
        json!({"signal_id":"x".repeat(65536)}),
    ] {
        assert_eq!(
            backend.request("get_signal", params).await.unwrap_err(),
            BridgeError::InvalidRequest
        );
    }
    assert_eq!(
        backend
            .request("get_run", json!({}))
            .await
            .unwrap()
            .request_id,
        "desktop-5"
    );
    backend.shutdown().await;
    assert_eq!(backend.process_id().await, None);
}

#[tokio::test]
async fn bad_frames_poison_session_and_child_death_never_restarts() {
    for (mode, expected) in [
        ("mismatch", BridgeError::BackendProtocolError),
        ("malformed", BridgeError::BackendProtocolError),
        ("oversized", BridgeError::BackendResponseTooLarge),
        ("exit", BridgeError::BackendExited),
    ] {
        let backend = fixture(mode);
        assert_eq!(
            backend.request("get_run", json!({})).await.unwrap_err(),
            expected
        );
        assert_eq!(
            backend.request("get_run", json!({})).await.unwrap_err(),
            expected
        );
        backend.shutdown().await;
        assert_eq!(backend.process_id().await, None);
    }
}

#[tokio::test]
async fn stderr_is_drained_separately_from_application_data() {
    let backend = fixture("stderr");
    let response = backend.request("get_run", json!({})).await.unwrap();
    assert!(!serde_json::to_string(&response)
        .unwrap()
        .contains("private"));
    backend.shutdown().await;
}

#[tokio::test]
async fn concurrent_callers_have_only_one_in_flight() {
    let backend = Arc::new(fixture("serialized"));
    let mut handles = Vec::new();
    for _ in 0..8 {
        let backend = backend.clone();
        handles.push(tokio::spawn(async move {
            backend.request("get_run", json!({})).await.unwrap()
        }));
    }
    let mut ids = Vec::new();
    for handle in handles {
        let response = handle.await.unwrap();
        ids.push(response.result.unwrap()["count"].as_u64().unwrap());
    }
    ids.sort();
    assert_eq!(ids, (1..=8).collect::<Vec<_>>());
    backend.shutdown().await;
}

#[tokio::test]
async fn shutdown_cancels_hung_request_and_reaps_child() {
    let backend = Arc::new(fixture("hang"));
    let caller = backend.clone();
    let request = tokio::spawn(async move { caller.request("get_run", json!({})).await });
    tokio::time::sleep(Duration::from_millis(100)).await;
    tokio::time::timeout(Duration::from_secs(5), backend.shutdown())
        .await
        .unwrap();
    assert_eq!(
        request.await.unwrap().unwrap_err(),
        BridgeError::BackendNotRunning
    );
    assert_eq!(backend.process_id().await, None);
}

// Deliberately not ignored: CI must prove the real process boundary, without a GUI.
#[tokio::test]
async fn real_python_four_reads_same_process_session() {
    let backend = BackendProcess::development().unwrap();
    let pid = backend.process_id().await.unwrap();
    let reads = [
        ("get_run", json!({})),
        ("get_entity", json!({"entity_id":"reactor"})),
        ("get_signal", json!({"signal_id":"XMEAS(9)"})),
        (
            "get_signal_history",
            json!({"signal_id":"XMEAS(7)","max_points":30}),
        ),
    ];
    for (index, (method, params)) in reads.into_iter().enumerate() {
        let response = backend.request(method, params).await.unwrap();
        assert!(response.ok, "{response:?}");
        assert_eq!(response.protocol_version, PROTOCOL);
        assert_eq!(response.request_id, format!("desktop-{}", index + 1));
        assert_eq!(backend.process_id().await, Some(pid));
        let result = response.result.unwrap();
        match method {
            "get_run" => {
                assert_eq!(result["run_id"], RUN_ID);
                assert_eq!(result["run_status"], "COMPLETED");
            }
            "get_entity" => assert_eq!(result["entity_id"], "reactor"),
            "get_signal" => {
                assert_eq!(result["signal_id"], "XMEAS(9)");
                assert_eq!(result["telemetry_available"], true);
            }
            _ => {
                assert_eq!(result["run_id"], RUN_ID);
                assert_eq!(result["signal_id"], "XMEAS(7)");
                let points = result["points"].as_array().unwrap();
                assert!(!points.is_empty() && points.len() <= 30);
            }
        }
        let wire = serde_json::to_string(&Value::Object(result))
            .unwrap()
            .to_lowercase();
        for hidden in [
            "active_disturbances",
            "evaluator",
            "known_injected_cause",
            "developer_setup",
        ] {
            assert!(!wire.contains(hidden));
        }
    }
    backend.shutdown().await;
    assert_eq!(backend.process_id().await, None);
}
