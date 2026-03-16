#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::{
    AppHandle, Manager, WebviewWindow,
};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

mod sidecar;

fn toggle_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        if window.is_visible().unwrap_or(false) {
            let _ = window.hide();
        } else {
            let _ = window.show();
            let _ = window.set_focus();
            let _ = window.center();
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .setup(|app| {
            // ── Start Python sidecar ─────────────────────────────
            sidecar::start_brain_sidecar(app.handle())?;

            // ── Register global shortcut ─────────────────────────
            // Mac: Cmd+Space  |  Windows/Linux: Alt+Space
            #[cfg(target_os = "macos")]
            let shortcut = Shortcut::new(Some(Modifiers::SUPER), Code::Space);
            #[cfg(not(target_os = "macos"))]
            let shortcut = Shortcut::new(Some(Modifiers::ALT), Code::Space);

            let app_handle = app.handle().clone();
            app.global_shortcut().on_shortcut(shortcut, move |_app, _shortcut, event| {
                if event.state == ShortcutState::Pressed {
                    toggle_window(&app_handle);
                }
            })?;

            // ── Hide window on startup (show via shortcut) ───────
            // Comment out for dev — useful to see the window immediately
            // if let Some(window) = app.get_webview_window("main") {
            //     let _ = window.hide();
            // }

            Ok(())
        })
        .on_window_event(|window, event| {
            // Hide instead of close when user clicks the X
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running OmniSearch");
}