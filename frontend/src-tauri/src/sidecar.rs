// sidecar.rs — Launches the Python FastAPI backend as a Tauri sidecar.
//
// In dev:   runs `python brain.py` from the backend directory
// In prod:  runs the compiled `brain` binary from src-tauri/bin/

use tauri::{AppHandle, Manager};

pub fn start_brain_sidecar(app: &AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    use tauri_plugin_shell::ShellExt;

    let shell = app.shell();

    // Try sidecar binary first (production), fall back to python (dev)
    match shell.sidecar("brain") {
        Ok(cmd) => {
            let (_, _child) = cmd.spawn()?;
            println!("[sidecar] brain binary started");
        }
        Err(_) => {
            // Dev mode: run python directly
            println!("[sidecar] brain binary not found, dev mode — start python brain.py manually");
        }
    }

    Ok(())
}