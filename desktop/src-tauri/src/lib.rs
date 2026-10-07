pub mod bridge;

use bridge::{ApplicationResponse, BackendProcess, BridgeError};
use serde_json::Value;
use std::sync::Arc;
use tauri::Manager;

#[tauri::command]
pub async fn application_request(
    backend: tauri::State<'_, Arc<BackendProcess>>,
    method: String,
    params: Value,
) -> Result<ApplicationResponse, BridgeError> {
    backend.request(&method, params).await
}

pub fn run() {
    let app = tauri::Builder::default()
        .setup(|app| {
            let backend = tauri::async_runtime::block_on(async { BackendProcess::development() });
            match backend {
                Ok(backend) => {
                    app.manage(Arc::new(backend));
                }
                Err(error) => return Err(format!("{error:?}").into()),
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![application_request])
        .build(tauri::generate_context!())
        .expect("desktop host setup failed");
    app.run(|app, event| {
        if matches!(event, tauri::RunEvent::Exit) {
            if let Some(backend) = app.try_state::<Arc<BackendProcess>>() {
                tauri::async_runtime::block_on(backend.shutdown());
            }
        }
    });
}
