import json
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from parser import SUPPORTED_EXTENSIONS

logger = logging.getLogger(__name__)

_WATCH_STATE_FILE = Path(__file__).parent.parent / "storage" / "watched_folders.json"
_DEBOUNCE_SECONDS = 0.5


class _DebouncedIndexHandler(FileSystemEventHandler):
    """Watchdog handler with per-file debouncing."""

    def __init__(self, index_callback: Callable[[str], None]):
        super().__init__()
        self._callback = index_callback
        self._pending: dict[str, float] = {}
        self._lock = threading.Lock()
        self._timer: Optional[threading.Timer] = None

    def _should_process(self, path: str) -> bool:
        p = Path(path)
        return (
            p.is_file()
            and p.suffix.lower() in SUPPORTED_EXTENSIONS
            and not p.name.startswith(".")
            and not p.name.startswith("~")
            and ".DS_Store" not in p.parts
        )

    def _schedule(self, path: str) -> None:
        with self._lock:
            self._pending[path] = time.monotonic()
            if self._timer is None or not self._timer.is_alive():
                self._timer = threading.Timer(_DEBOUNCE_SECONDS, self._flush)
                self._timer.daemon = True
                self._timer.start()

    def _flush(self) -> None:
        now = time.monotonic()
        with self._lock:
            ready = [
                path for path, ts in list(self._pending.items())
                if now - ts >= _DEBOUNCE_SECONDS
            ]
            for path in ready:
                del self._pending[path]

        for path in ready:
            try:
                self._callback(path)
            except Exception as e:
                logger.error(f"Index callback failed for {path}: {e}")

        with self._lock:
            if self._pending:
                self._timer = threading.Timer(_DEBOUNCE_SECONDS, self._flush)
                self._timer.daemon = True
                self._timer.start()

    def on_created(self, event):
        if not event.is_directory and self._should_process(event.src_path):
            logger.debug(f"File created: {event.src_path}")
            self._schedule(event.src_path)

    def on_modified(self, event):
        if not event.is_directory and self._should_process(event.src_path):
            logger.debug(f"File modified: {event.src_path}")
            self._schedule(event.src_path)

    def on_moved(self, event):
        if not event.is_directory and self._should_process(event.dest_path):
            logger.debug(f"File moved to: {event.dest_path}")
            self._schedule(event.dest_path)


class FolderWatcher:
    """
    Manages watchdog observers for multiple folders.
    Persists watched folder list to storage/watched_folders.json.
    """

    def __init__(self, index_callback: Callable[[str], None]):
        self._callback = index_callback
        self._observer = Observer()
        self._watched: dict[str, object] = {}
        self._lock = threading.Lock()
        self._started = False

    def start(self) -> None:
        if not self._started:
            self._observer.start()
            self._started = True
            logger.info("File watcher started")
            self._restore_watched_folders()

    def stop(self) -> None:
        if self._started:
            self._observer.stop()
            self._observer.join()
            self._started = False
            logger.info("File watcher stopped")

    def watch_folder(self, folder_path: str) -> bool:
        path = Path(folder_path).resolve()
        if not path.exists() or not path.is_dir():
            logger.error(f"Invalid folder path: {folder_path}")
            return False

        str_path = str(path)
        with self._lock:
            if str_path in self._watched:
                logger.info(f"Already watching: {str_path}")
                return False
            self._add_watch_locked(str_path)

        self._persist()
        logger.info(f"Now watching: {str_path}")
        return True

    def unwatch_folder(self, folder_path: str) -> bool:
        str_path = str(Path(folder_path).resolve())
        with self._lock:
            if str_path not in self._watched:
                return False
            watch = self._watched.pop(str_path)
            self._observer.unschedule(watch)

        self._persist()
        logger.info(f"Stopped watching: {str_path}")
        return True

    def watched_folders(self) -> list[str]:
        with self._lock:
            return sorted(self._watched.keys())

    def _add_watch_locked(self, str_path: str) -> None:
        handler = _DebouncedIndexHandler(self._callback)
        watch = self._observer.schedule(handler, str_path, recursive=True)
        self._watched[str_path] = watch

    def _persist(self) -> None:
        try:
            _WATCH_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(_WATCH_STATE_FILE, "w") as f:
                json.dump({"watched": self.watched_folders()}, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not persist watch state: {e}")

    def _restore_watched_folders(self) -> None:
        if not _WATCH_STATE_FILE.exists():
            return
        try:
            with open(_WATCH_STATE_FILE) as f:
                data = json.load(f)
            folders = data.get("watched", [])
        except Exception as e:
            logger.warning(f"Could not read watch state: {e}")
            return

        restored, skipped = 0, 0
        for folder in folders:
            p = Path(folder)
            if p.exists() and p.is_dir():
                with self._lock:
                    if folder not in self._watched:
                        self._add_watch_locked(folder)
                restored += 1
            else:
                logger.warning(f"Watched folder missing, skipping: {folder}")
                skipped += 1

        if restored:
            logger.info(f"Restored {restored} watched folder(s)")
        if skipped:
            logger.warning(f"Skipped {skipped} missing folder(s)")