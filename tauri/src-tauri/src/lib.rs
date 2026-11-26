//! AutoSim Studio desktop app (Tauri).
//!
//! The window shows the AutoSim web UI; the CAN engine, adapters and Test API stay in the Python backend
//! (`AutoSimWeb`, the same program as web mode). At start the app
//!
//! 1. picks the port: 6789 (the usual Test API address for test systems) or, if it is taken, a free one;
//! 2. starts the backend on every network interface (0.0.0.0: test systems on other machines use the token,
//!    this PC needs none), without a console window, its output going to `backend.log`;
//! 3. shows a splash page until the backend answers, then opens `http://127.0.0.1:<port>/`;
//! 4. stops the backend when the window closes (and the backend stops by itself if this app dies: `--exit-with-pid`).
//!
//! The backend is looked up in this order: the `AUTOSIM_BACKEND` environment variable (a command line, e.g.
//! `uv run --extra web autosim-web` while developing), then `backend/AutoSimWeb[.exe]` next to the app
//! (bundled resources), then `../dist/AutoSimWeb/AutoSimWeb[.exe]` (a PyInstaller build in the repository).

use std::fs::{self, File};
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use tauri::{Manager, RunEvent, Url};

const PREFERRED_PORT: u16 = 6789;
const START_TIMEOUT: Duration = Duration::from_secs(40);

struct Backend {
    child: Mutex<Option<Child>>,
    port: u16,
}

/// A tiny HTTP/1.0 request to the local backend (no extra crate): the response text, or None.
fn local_http(port: u16, method: &str, path: &str) -> Option<String> {
    let addr: SocketAddr = ([127, 0, 0, 1], port).into();
    let mut s = TcpStream::connect_timeout(&addr, Duration::from_millis(300)).ok()?;
    s.set_read_timeout(Some(Duration::from_secs(2))).ok()?;
    write!(s, "{method} {path} HTTP/1.0\r\nHost: 127.0.0.1\r\nContent-Length: 0\r\n\r\n").ok()?;
    let mut out = String::new();
    s.read_to_string(&mut out).ok()?;
    Some(out)
}

/// The app is already running (its backend answers on the usual port as the desktop app): ask it to show its
/// window. This also works when the two copies run with different rights (e.g. the first one started by the
/// installer, elevated), where the single-instance plugin cannot see the first copy.
fn focus_running_copy() -> bool {
    match local_http(PREFERRED_PORT, "GET", "/api/instance") {
        Some(r) if r.contains("\"app\":\"ican\"") && r.contains("\"desktop\":true") => {
            let _ = local_http(PREFERRED_PORT, "POST", "/api/focus");
            true
        }
        _ => false,
    }
}

/// Another copy of the app already runs in this Windows session?
///
/// A named mutex, created by the first copy and kept for its whole life (Windows frees it when the process ends,
/// even after a crash). The single-instance plugin uses a mutex too, but it takes "access denied" for "no other
/// copy": that is what a normal copy gets when the first one runs as administrator (started by the installer, which
/// runs elevated). Here "access denied" counts as "already running" as well.
#[cfg(windows)]
fn another_copy_running() -> bool {
    use std::ffi::c_void;
    extern "system" {
        fn CreateMutexW(attrs: *const c_void, initial_owner: i32, name: *const u16) -> *mut c_void;
        fn GetLastError() -> u32;
    }
    const ERROR_ALREADY_EXISTS: u32 = 183;
    const ERROR_ACCESS_DENIED: u32 = 5;
    let name: Vec<u16> = "Local\\com.autosim.studio.instance".encode_utf16().chain(Some(0)).collect();
    let handle = unsafe { CreateMutexW(std::ptr::null(), 0, name.as_ptr()) };
    let err = unsafe { GetLastError() };
    if handle.is_null() {
        return err == ERROR_ACCESS_DENIED;
    }
    // the handle is kept (never closed) while this copy runs: it is the "running" mark
    err == ERROR_ALREADY_EXISTS
}

#[cfg(not(windows))]
fn another_copy_running() -> bool {
    false // the single-instance plugin covers Linux / macOS
}

/// A second copy: the first one may still be starting (its backend needs a few seconds before it answers). Wait
/// for it, bring its window to the front, and stop. Never a second window.
fn hand_over_to_running_copy() {
    let start = Instant::now();
    while start.elapsed() < START_TIMEOUT {
        if focus_running_copy() {
            return;
        }
        thread::sleep(Duration::from_millis(250));
    }
}

fn port_free(port: u16) -> bool {
    // one after the other: each listener is closed before the next bind
    let any = TcpListener::bind(("0.0.0.0", port)).is_ok();
    any && TcpListener::bind(("127.0.0.1", port)).is_ok()
}

fn pick_port() -> u16 {
    if port_free(PREFERRED_PORT) {
        return PREFERRED_PORT;
    }
    TcpListener::bind("0.0.0.0:0")
        .and_then(|l| l.local_addr())
        .map(|a| a.port())
        .unwrap_or(PREFERRED_PORT + 1)
}

fn exe_name() -> &'static str {
    if cfg!(windows) {
        "AutoSimWeb.exe"
    } else {
        "AutoSimWeb"
    }
}

/// The backend command: program + its first arguments.
fn backend_command(app: &tauri::AppHandle) -> Result<(PathBuf, Vec<String>), String> {
    if let Ok(cmd) = std::env::var("AUTOSIM_BACKEND") {
        let mut parts = cmd.split_whitespace().map(String::from);
        if let Some(prog) = parts.next() {
            return Ok((PathBuf::from(prog), parts.collect()));
        }
    }
    let mut tried = Vec::new();
    if let Ok(res) = app.path().resource_dir() {
        tried.push(res.join("backend").join(exe_name()));
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            tried.push(dir.join("backend").join(exe_name()));
            tried.push(dir.join("../../../../dist/AutoSimWeb").join(exe_name())); // cargo build: tauri/src-tauri/target/<profile>
        }
    }
    for p in &tried {
        if p.is_file() {
            return Ok((p.clone(), Vec::new()));
        }
    }
    Err(format!(
        "The AutoSim backend was not found. Looked for:\n{}",
        tried.iter().map(|p| p.display().to_string()).collect::<Vec<_>>().join("\n")
    ))
}

fn start_backend(app: &tauri::AppHandle, port: u16, data_dir: &Path) -> Result<Child, String> {
    let (prog, mut args) = backend_command(app)?;
    fs::create_dir_all(data_dir).map_err(|e| format!("{}: {e}", data_dir.display()))?;
    let log_path = data_dir.join("backend.log");
    let mut log = File::create(&log_path).map_err(|e| format!("{}: {e}", log_path.display()))?;
    let _ = writeln!(log, "starting {} on 0.0.0.0:{port}", prog.display());
    let err_log = log.try_clone().map_err(|e| e.to_string())?;
    args.extend([
        "--host".into(),
        "0.0.0.0".into(),                   // reachable from the network: test systems on other PCs
        "--port".into(),
        port.to_string(),
        "--data-dir".into(),
        data_dir.display().to_string(),
        "--no-browser".into(),
        "--trust-local".into(),             // this window needs no token; other machines use the stored one
        "--start-stopped".into(),             // like CANoe: Start (F9) sends and traces; Settings can change it
        "--exit-with-pid".into(),           // the backend stops by itself if this app crashes or is killed
        std::process::id().to_string(),
    ]);
    let mut cmd = Command::new(&prog);
    cmd.args(&args)
        .stdin(Stdio::null())
        .stdout(Stdio::from(log))
        .stderr(Stdio::from(err_log));
    if let Some(dir) = prog.parent() {
        if dir.as_os_str().len() > 0 {
            cmd.current_dir(dir);
        }
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    cmd.spawn().map_err(|e| format!("Could not start {}: {e}", prog.display()))
}

fn wait_until_up(port: u16, child: &Mutex<Option<Child>>) -> Result<(), String> {
    let addr: SocketAddr = ([127, 0, 0, 1], port).into();
    let start = Instant::now();
    while start.elapsed() < START_TIMEOUT {
        if TcpStream::connect_timeout(&addr, Duration::from_millis(200)).is_ok() {
            return Ok(());
        }
        if let Some(c) = child.lock().unwrap().as_mut() {
            if let Ok(Some(status)) = c.try_wait() {
                return Err(format!("The AutoSim backend stopped at start ({status}). See backend.log."));
            }
        }
        thread::sleep(Duration::from_millis(150));
    }
    Err("The AutoSim backend did not answer in time. See backend.log.".into())
}

fn stop_backend(app: &tauri::AppHandle) {
    if let Some(state) = app.try_state::<Backend>() {
        if let Some(mut c) = state.child.lock().unwrap().take() {
            let _ = c.kill();
            let _ = c.wait();
        }
    }
}

fn show_error(window: &tauri::WebviewWindow, text: &str) {
    let msg = serde_json::to_string(text).unwrap_or_else(|_| "\"error\"".into());
    let _ = window.eval(&format!("window.showStartError && window.showStartError({msg})"));
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    if another_copy_running() {
        hand_over_to_running_copy();
        return; // one window only: the running copy comes to the front
    }
    if focus_running_copy() {
        return; // the app runs in another way (e.g. another Windows session's backend on the usual port)
    }
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.unminimize();
                let _ = w.set_focus();
            }
        }))
        // Size, position and maximized state are remembered, never the title bar: up to 1.2.0 the window had the
        // Windows title bar and the plugin saved "decorated": true; restoring it put a second (Windows) title bar
        // above the app's own one.
        .plugin(
            tauri_plugin_window_state::Builder::default()
                .with_state_flags(
                    tauri_plugin_window_state::StateFlags::all() - tauri_plugin_window_state::StateFlags::DECORATIONS,
                )
                .build(),
        )
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .setup(|app| {
            let handle = app.handle().clone();
            let port = pick_port();
            let data_dir = app
                .path()
                .app_data_dir()
                .unwrap_or_else(|_| std::env::temp_dir().join("autosim-studio"));
            let window = app.get_webview_window("main").expect("main window");
            let _ = window.set_decorations(false); // the app draws its own title bar (index.html #titlebar)
            match start_backend(&handle, port, &data_dir) {
                Ok(child) => {
                    app.manage(Backend { child: Mutex::new(Some(child)), port });
                    let h = handle.clone();
                    thread::spawn(move || {
                        let state = h.state::<Backend>();
                        match wait_until_up(state.port, &state.child) {
                            Ok(()) => {
                                let url = Url::parse(&format!("http://127.0.0.1:{}/", state.port)).unwrap();
                                if let Some(w) = h.get_webview_window("main") {
                                    let _ = w.navigate(url);
                                }
                            }
                            Err(e) => {
                                if let Some(w) = h.get_webview_window("main") {
                                    show_error(&w, &e);
                                }
                            }
                        }
                    });
                }
                Err(e) => show_error(&window, &e),
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building the AutoSim Studio app")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                stop_backend(app);
            }
        });
}
