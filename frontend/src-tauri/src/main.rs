#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod sidecar;

use tauri::{AppHandle, Manager};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

fn toggle_window(app: &AppHandle) {
    let Some(window) = app.get_webview_window("main") else {
        eprintln!("[omnisearch] main window not found");
        return;
    };

    let visible = window.is_visible().unwrap_or(false);

    if visible {
        let _ = window.hide();
    } else {
        let _ = window.show();
        let _ = window.set_focus();
        let _ = window.center();
    }
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .setup(|app| {
            // Start Python backend sidecar
            sidecar::start_brain(app.handle())?;

            #[cfg(target_os = "macos")]
            let shortcut = Shortcut::new(Some(Modifiers::SUPER), Code::Space);

            #[cfg(not(target_os = "macos"))]
            let shortcut = Shortcut::new(Some(Modifiers::ALT), Code::Space);

            let handle = app.handle().clone();

            app.global_shortcut()
                .on_shortcut(shortcut, move |_app, _shortcut, event| {
                    if event.state == ShortcutState::Pressed {
                        toggle_window(&handle);
                    }
                })?;

            println!("[omnisearch] setup complete");
            Ok(())
        })
        .on_window_event(|window, event| {
            // Hide instead of quit when user clicks X
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .run(tauri::generate_context!())
        .expect("[omnisearch] fatal: failed to run application");
}