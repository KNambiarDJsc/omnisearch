use tauri::AppHandle;
use tauri_plugin_shell::ShellExt;

pub fn start_brain(app: &AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    #[cfg(debug_assertions)]
    {
        start_dev(app)
    }

    #[cfg(not(debug_assertions))]
    {
        start_prod(app)
    }
}

#[cfg(debug_assertions)]
fn start_dev(app: &AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    use std::path::PathBuf;

    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));

    // FIX: Go up two levels → project root
    let project_root = manifest_dir
        .parent() // src-tauri → frontend
        .and_then(|p| p.parent()) // frontend → root
        .expect("project root");

    let backend_dir = project_root.join("backend");
    let brain_py = backend_dir.join("brain.py");

    if !brain_py.exists() {
        eprintln!(
            "[sidecar] brain.py not found at {}. \
             Run manually: cd backend && python brain.py",
            brain_py.display()
        );
        return Ok(());
    }

    // Detect virtualenv
    let venv_unix = backend_dir.join(".venv").join("bin").join("python");
    let venv_win = backend_dir.join(".venv").join("Scripts").join("python.exe");

    let python_exe = if venv_unix.exists() {
        venv_unix.to_string_lossy().to_string()
    } else if venv_win.exists() {
        venv_win.to_string_lossy().to_string()
    } else {
        "python".to_string()
    };

    println!(
        "[sidecar] DEV → running: {} {}",
        python_exe,
        brain_py.display()
    );

    let shell = app.shell();

    match shell
        .command(&python_exe)
        .args([brain_py.to_str().unwrap()])
        .current_dir(backend_dir.to_str().unwrap())
        .spawn()
    {
        Ok((mut rx, _child)) => {
            tauri::async_runtime::spawn(async move {
                use tauri_plugin_shell::process::CommandEvent;

                while let Some(event) = rx.recv().await {
                    match event {
                        CommandEvent::Stdout(line) => {
                            println!("[brain] {}", String::from_utf8_lossy(&line).trim());
                        }
                        CommandEvent::Stderr(line) => {
                            eprintln!("[brain:err] {}", String::from_utf8_lossy(&line).trim());
                        }
                        CommandEvent::Error(e) => {
                            eprintln!("[brain:error] {}", e);
                        }
                        CommandEvent::Terminated(status) => {
                            println!("[brain] exited: {:?}", status);
                            break;
                        }
                        _ => {}
                    }
                }
            });

            println!("[sidecar] DEV backend started ✅");
        }
        Err(e) => {
            eprintln!(
                "[sidecar] failed to start backend: {}. \
                 Run manually: cd backend && python brain.py",
                e
            );
        }
    }

    Ok(())
}

#[cfg(not(debug_assertions))]
fn start_prod(app: &AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    let shell = app.shell();

    match shell.sidecar("brain") {
        Ok(cmd) => match cmd.spawn() {
            Ok((mut rx, _child)) => {
                tauri::async_runtime::spawn(async move {
                    use tauri_plugin_shell::process::CommandEvent;

                    while let Some(event) = rx.recv().await {
                        match event {
                            CommandEvent::Stdout(line) => {
                                println!("[brain] {}", String::from_utf8_lossy(&line).trim());
                            }
                            CommandEvent::Stderr(line) => {
                                eprintln!("[brain:err] {}", String::from_utf8_lossy(&line).trim());
                            }
                            CommandEvent::Terminated(status) => {
                                println!("[brain] exited: {:?}", status);
                                break;
                            }
                            _ => {}
                        }
                    }
                });

                println!("[sidecar] PROD backend started ✅");
            }
            Err(e) => {
                eprintln!("[sidecar] failed to spawn brain binary: {}", e);
            }
        },
        Err(e) => {
            eprintln!(
                "[sidecar] brain binary not found: {}. \
                 Build it using PyInstaller.",
                e
            );
        }
    }

    Ok(())
}