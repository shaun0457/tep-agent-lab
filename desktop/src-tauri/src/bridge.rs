//! Process/framing adapter only. Industrial reads and validation belong to Python.
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use std::{
    path::Path,
    process::Stdio,
    sync::atomic::{AtomicBool, Ordering},
    time::Duration,
};
use tauri_plugin_shell::ShellExt;
use tokio::{
    io::{AsyncBufRead, AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader},
    process::{Child, ChildStdin, ChildStdout, Command},
    sync::{Mutex, Notify},
    task::JoinHandle,
    time::timeout,
};

pub const PROTOCOL: &str = "tep-agent-lab.application-transport/v0";
pub const RUN_ID: &str = "e0-observatory";
pub const MAX_REQUEST_BYTES: usize = 64 * 1024;
/// Includes the LF terminator, so a frame never allocates beyond 1 MiB.
pub const MAX_RESPONSE_BYTES: usize = 1024 * 1024;
const REQUEST_TIMEOUT: Duration = Duration::from_secs(30);
const EXIT_GRACE: Duration = Duration::from_secs(2);

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum BridgeError {
    BackendNotRunning,
    BackendIoError,
    BackendProtocolError,
    BackendResponseTooLarge,
    BackendExited,
    BackendTimeout,
    InvalidRequest,
    UnsupportedMethod,
}

#[derive(Serialize)]
struct ApplicationRequest<'a> {
    protocol_version: &'static str,
    request_id: String,
    method: &'a str,
    params: Map<String, Value>,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct TransportError {
    pub code: String,
    pub message: String,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ApplicationResponse {
    pub protocol_version: String,
    pub request_id: String,
    pub ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<Map<String, Value>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<TransportError>,
}

fn decode_response(bytes: &[u8], request_id: &str) -> Result<ApplicationResponse, BridgeError> {
    // Check exact field presence as Option alone cannot distinguish missing from null.
    let value: Value =
        serde_json::from_slice(bytes).map_err(|_| BridgeError::BackendProtocolError)?;
    let object = value.as_object().ok_or(BridgeError::BackendProtocolError)?;
    if object.len() != 4
        || !object.contains_key(if object.get("ok") == Some(&Value::Bool(true)) {
            "result"
        } else {
            "error"
        })
    {
        return Err(BridgeError::BackendProtocolError);
    }
    let response: ApplicationResponse =
        serde_json::from_value(value).map_err(|_| BridgeError::BackendProtocolError)?;
    if response.protocol_version != PROTOCOL
        || response.request_id != request_id
        || (response.ok && (response.result.is_none() || response.error.is_some()))
        || (!response.ok && (response.error.is_none() || response.result.is_some()))
    {
        return Err(BridgeError::BackendProtocolError);
    }
    // Only fixed E0.2B public failures may cross the native boundary.
    if let Some(error) = &response.error {
        let message = match error.code.as_str() {
            "UNKNOWN_ENTITY" => "unknown entity",
            "UNKNOWN_SIGNAL" => "unknown signal",
            "AMBIGUOUS_SIGNAL" => "ambiguous signal",
            "VIEW_UNAVAILABLE" => "view unavailable",
            "VISIBILITY_VIOLATION" => "visibility violation",
            "INVALID_REQUEST" => "invalid request",
            "UNSUPPORTED_METHOD" => "unsupported method",
            "UNSUPPORTED_PROTOCOL_VERSION" => "unsupported protocol version",
            "INTERNAL_ERROR" => "internal error",
            _ => return Err(BridgeError::BackendProtocolError),
        };
        if error.message != message {
            return Err(BridgeError::BackendProtocolError);
        }
    }
    Ok(response)
}

async fn read_frame(reader: &mut (impl AsyncBufRead + Unpin)) -> Result<Vec<u8>, BridgeError> {
    let mut frame = Vec::new();
    loop {
        let buffer = reader
            .fill_buf()
            .await
            .map_err(|_| BridgeError::BackendIoError)?;
        if buffer.is_empty() {
            return Err(if frame.is_empty() {
                BridgeError::BackendExited
            } else {
                BridgeError::BackendProtocolError
            });
        }
        let take = buffer
            .iter()
            .position(|b| *b == b'\n')
            .map_or(buffer.len(), |i| i + 1);
        if take > MAX_RESPONSE_BYTES - frame.len() {
            return Err(BridgeError::BackendResponseTooLarge);
        }
        let complete = buffer[take - 1] == b'\n';
        frame.extend_from_slice(&buffer[..take]);
        reader.consume(take);
        if complete {
            return Ok(frame);
        }
    }
}

struct Session {
    child: Child,
    stdin: Option<ChildStdin>,
    stdout: BufReader<ChildStdout>,
    stderr_task: JoinHandle<()>,
    next_id: u64,
    failure: Option<BridgeError>,
}

pub struct BackendProcess {
    session: Mutex<Session>,
    stopping: AtomicBool,
    stop: Notify,
}

impl BackendProcess {
    /// Host-resolved externalBin; official Command -> std Command adapter preserves
    /// ordinary OS pipes, EOF and Tokio kill/reap instead of plugin event framing.
    pub fn packaged<R: tauri::Runtime>(app: &tauri::AppHandle<R>) -> Result<Self, BridgeError> {
        use tauri::Manager;
        let sessions = app
            .path()
            .app_local_data_dir()
            .map_err(|_| BridgeError::BackendNotRunning)?
            .join("sessions");
        std::fs::create_dir_all(&sessions).map_err(|_| BridgeError::BackendNotRunning)?;
        let output = tempfile::Builder::new()
            .prefix("session-")
            .tempdir_in(sessions)
            .map_err(|_| BridgeError::BackendNotRunning)?
            .keep();
        let sidecar = app
            .shell()
            .sidecar("tep-agent-backend") // Installed externalBin basename, beside app.
            .map_err(|_| BridgeError::BackendNotRunning)?
            .args(["--run-id", RUN_ID, "--output-root"])
            .arg(output);
        let command: std::process::Command = sidecar.into();
        Self::spawn(Command::from(command))
    }

    /// Host-only development configuration: executable has no frontend input.
    pub fn development() -> Result<Self, BridgeError> {
        let executable = std::env::var_os("TEP_AGENT_PYTHON").unwrap_or_else(|| "python".into());
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let output = tempfile::Builder::new()
            .prefix("tep-desktop-")
            .tempdir()
            .map_err(|_| BridgeError::BackendNotRunning)?
            .keep();
        let mut paths = vec![root.join("src")];
        if let Some(existing) = std::env::var_os("PYTHONPATH") {
            paths.extend(std::env::split_paths(&existing));
        }
        let mut command = Command::new(executable);
        command
            .current_dir(&root)
            .env(
                "PYTHONPATH",
                std::env::join_paths(paths).map_err(|_| BridgeError::BackendNotRunning)?,
            )
            .env("PYTHONUNBUFFERED", "1")
            .args([
                "-m",
                "tep_agent_lab.desktop_backend",
                "--run-id",
                RUN_ID,
                "--output-root",
            ])
            .arg(output);
        Self::spawn(command)
    }

    /// No shell is involved. Also used by protocol fixtures in integration tests.
    pub fn spawn(mut command: Command) -> Result<Self, BridgeError> {
        command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        #[cfg(windows)]
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW: background child, no console popup.
        let mut child = command.spawn().map_err(|error| {
            eprintln!("backend spawn failed: {error}");
            BridgeError::BackendNotRunning
        })?;
        let stdin = child.stdin.take().ok_or(BridgeError::BackendNotRunning)?;
        let stdout = BufReader::new(child.stdout.take().ok_or(BridgeError::BackendNotRunning)?);
        let mut stderr = child.stderr.take().ok_or(BridgeError::BackendNotRunning)?;
        let stderr_task = tokio::spawn(async move {
            let mut buffer = [0; 4096];
            loop {
                match stderr.read(&mut buffer).await {
                    Ok(0) | Err(_) => break,
                    Ok(n) => eprint!("[python] {}", String::from_utf8_lossy(&buffer[..n])),
                }
            }
        });
        Ok(Self {
            session: Mutex::new(Session {
                child,
                stdin: Some(stdin),
                stdout,
                stderr_task,
                next_id: 1,
                failure: None,
            }),
            stopping: AtomicBool::new(false),
            stop: Notify::new(),
        })
    }

    pub async fn request(
        &self,
        method: &str,
        params: Value,
    ) -> Result<ApplicationResponse, BridgeError> {
        if !matches!(
            method,
            "get_run" | "get_entity" | "get_signal" | "get_signal_history"
        ) {
            return Err(BridgeError::UnsupportedMethod);
        }
        let mut params = params
            .as_object()
            .cloned()
            .ok_or(BridgeError::InvalidRequest)?;
        // Run identity is host-owned. The frontend cannot override it.
        if params.contains_key("run_id") {
            return Err(BridgeError::InvalidRequest);
        }
        let allowed: &[&str] = match method {
            "get_run" => &[],
            "get_entity" => &["entity_id"],
            "get_signal" => &["signal_id"],
            _ => &["signal_id", "start_hours", "end_hours", "max_points"],
        };
        if params.keys().any(|key| !allowed.contains(&key.as_str())) {
            return Err(BridgeError::InvalidRequest);
        }
        params.insert("run_id".into(), RUN_ID.into());
        let mut session = self.session.lock().await;
        if self.stopping.load(Ordering::Acquire) {
            return Err(BridgeError::BackendNotRunning);
        }
        if let Some(failure) = session.failure {
            return Err(failure);
        }
        if session
            .child
            .try_wait()
            .map_err(|_| BridgeError::BackendIoError)?
            .is_some()
        {
            session.failure = Some(BridgeError::BackendExited);
            return Err(BridgeError::BackendExited);
        }
        let request_id = format!("desktop-{}", session.next_id);
        let request = ApplicationRequest {
            protocol_version: PROTOCOL,
            request_id: request_id.clone(),
            method,
            params,
        };
        // serde_json's default Map is sorted: compact canonical object JSON, no concatenation.
        let canonical = serde_json::to_value(&request).map_err(|_| BridgeError::InvalidRequest)?;
        let mut bytes = serde_json::to_vec(&canonical).map_err(|_| BridgeError::InvalidRequest)?;
        if bytes.len() > MAX_REQUEST_BYTES {
            return Err(BridgeError::InvalidRequest);
        }
        session.next_id = session
            .next_id
            .checked_add(1)
            .ok_or(BridgeError::BackendProtocolError)?;
        bytes.push(b'\n');
        let exchange = async {
            let stdin = session
                .stdin
                .as_mut()
                .ok_or(BridgeError::BackendNotRunning)?;
            stdin
                .write_all(&bytes)
                .await
                .map_err(|_| BridgeError::BackendIoError)?;
            stdin
                .flush()
                .await
                .map_err(|_| BridgeError::BackendIoError)?;
            let frame = read_frame(&mut session.stdout).await?;
            decode_response(&frame, &request_id)
        };
        let result = tokio::select! {
            biased;
            _ = self.stop.notified() => Err(BridgeError::BackendNotRunning),
            result = timeout(REQUEST_TIMEOUT, exchange) => result.unwrap_or(Err(BridgeError::BackendTimeout)),
        };
        if let Err(error) = result {
            session.failure = Some(error);
            // Framing may be lost. Stop the same child; never restart implicitly.
            session.stdin.take();
            if !self.stopping.load(Ordering::Acquire) {
                let _ = session.child.start_kill();
            }
        }
        result
    }

    pub async fn shutdown(&self) {
        self.stopping.store(true, Ordering::Release);
        self.stop.notify_one(); // Also cancels a hung in-flight exchange.
        let mut session = self.session.lock().await;
        session.stdin.take(); // EOF is the normal backend shutdown path.
        let graceful = timeout(EXIT_GRACE, session.child.wait()).await;
        if !matches!(graceful, Ok(Ok(_))) {
            let _ = session.child.start_kill();
            let _ = timeout(EXIT_GRACE, session.child.wait()).await;
        }
        if timeout(EXIT_GRACE, &mut session.stderr_task).await.is_err() {
            session.stderr_task.abort();
        }
    }

    pub async fn process_id(&self) -> Option<u32> {
        self.session.lock().await.child.id()
    }
}

impl Drop for BackendProcess {
    fn drop(&mut self) {
        // Last-resort cleanup on failed Tauri setup/panic; normal exit awaits shutdown.
        self.session.get_mut().stderr_task.abort();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn bounded_framing_accepts_limit_and_rejects_overflow_and_partial_eof() {
        let mut exact = vec![b' '; MAX_RESPONSE_BYTES - 1];
        exact.push(b'\n');
        assert_eq!(
            read_frame(&mut BufReader::new(exact.as_slice()))
                .await
                .unwrap()
                .len(),
            MAX_RESPONSE_BYTES
        );
        let over = vec![b'x'; MAX_RESPONSE_BYTES + 1];
        assert_eq!(
            read_frame(&mut BufReader::new(over.as_slice()))
                .await
                .unwrap_err(),
            BridgeError::BackendResponseTooLarge
        );
        assert_eq!(
            read_frame(&mut BufReader::new(&b"{}"[..]))
                .await
                .unwrap_err(),
            BridgeError::BackendProtocolError
        );
    }

    #[test]
    fn envelopes_fail_closed() {
        for wire in [
            r#"{}"#,
            r#"{"protocol_version":"bad","request_id":"desktop-1","ok":true,"result":{}}"#,
            r#"{"protocol_version":"tep-agent-lab.application-transport/v0","request_id":"desktop-2","ok":true,"result":{}}"#,
            r#"{"protocol_version":"tep-agent-lab.application-transport/v0","request_id":"desktop-1","ok":true,"result":{},"error":null}"#,
        ] {
            assert_eq!(
                decode_response(wire.as_bytes(), "desktop-1").unwrap_err(),
                BridgeError::BackendProtocolError
            );
        }
    }
}
