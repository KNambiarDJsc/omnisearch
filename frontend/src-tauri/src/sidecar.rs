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
    let backend_dir = manifest_dir
        .parent()
        .expect("project root")
        .join("backend");

    let brain_py = backend_dir.join("brain.py");

    if !brain_py.exists() {
        eprintln!(
            "[sidecar] brain.py not found at {}. \
             Start the backend manually: cd backend && python brain.py",
            brain_py.display()
        );
        return Ok(());
    }

    let venv_python = backend_dir.join(".venv").join("bin").join("python");
    let python_exe = if venv_python.exists() {
        venv_python.to_str().unwrap().to_string()
    } else {
        let win_venv = backend_dir
            .join(".venv")
            .join("Scripts")
            .join("python.exe");
        if win_venv.exists() {
            win_venv.to_str().unwrap().to_string()
        } else {
            "python".to_string()
        }
    };

    println!("[sidecar] DEV — spawning: {} {}", python_exe, brain_py.display());

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
                            println!(
                                "[brain] {}",
                                String::from_utf8_lossy(&line).trim()
                            );
                        }
                        CommandEvent::Stderr(line) => {
                            eprintln!(
                                "[brain:err] {}",
                                String::from_utf8_lossy(&line).trim()
                            );
                        }
                        CommandEvent::Error(e) => {
                            eprintln!("[brain:error] {}", e);
                        }
                        CommandEvent::Terminated(status) => {
                            println!("[brain] process exited: {:?}", status);
                            break;
                        }
                        _ => {}
                    }
                }
            });
            println!("[sidecar] DEV backend spawned successfully");
        }
        Err(e) => {
            eprintln!(
                "[sidecar] failed to spawn Python backend: {}. \
                 Start it manually: cd backend && python brain.py",
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
        Ok(cmd) => {
            match cmd.spawn() {
                Ok((mut rx, _child)) => {
                    tauri::async_runtime::spawn(async move {
                        use tauri_plugin_shell::process::CommandEvent;
                        while let Some(event) = rx.recv().await {
                            match event {
                                CommandEvent::Stdout(line) => {
                                    println!(
                                        "[brain] {}",
                                        String::from_utf8_lossy(&line).trim()
                                    );
                                }
                                CommandEvent::Stderr(line) => {
                                    eprintln!(
                                        "[brain:err] {}",
                                        String::from_utf8_lossy(&line).trim()
                                    );
                                }
                                CommandEvent::Terminated(status) => {
                                    println!("[brain] process exited: {:?}", status);
                                    break;
                                }
                                _ => {}
                            }
                        }
                    });
                    println!("[sidecar] PROD brain binary started");
                }
                Err(e) => {
                    eprintln!("[sidecar] failed to spawn brain binary: {}", e);
                }
            }
        }
        Err(e) => {
            eprintln!(
                "[sidecar] brain binary not found in bundle: {}. \
                 Run: pyinstaller --onefile brain.py && cp dist/brain src-tauri/bin/",
                e
            );
        }
    }

    Ok(())
}