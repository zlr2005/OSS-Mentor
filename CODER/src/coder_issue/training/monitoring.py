"""Persistent, low-overhead observability for long-running training jobs."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any
import uuid


def process_identity(pid: int | None = None) -> dict[str, Any]:
    """Return PID plus creation time/command line to guard against PID reuse."""

    pid = os.getpid() if pid is None else int(pid)
    try:
        import psutil
        process = psutil.Process(pid)
        return {"pid": pid, "process_create_time": process.create_time(),
                "process_command": process.cmdline()}
    except Exception:
        if os.name == "nt":
            try:
                import ctypes
                from ctypes import wintypes
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                handle = ctypes.windll.kernel32.OpenProcess(
                    PROCESS_QUERY_LIMITED_INFORMATION, False, pid
                )
                if not handle:
                    raise OSError("process does not exist or cannot be queried")
                creation = wintypes.FILETIME(); exit_time = wintypes.FILETIME()
                kernel = wintypes.FILETIME(); user = wintypes.FILETIME()
                if not ctypes.windll.kernel32.GetProcessTimes(
                    handle, ctypes.byref(creation), ctypes.byref(exit_time),
                    ctypes.byref(kernel), ctypes.byref(user)
                ):
                    raise OSError("GetProcessTimes failed")
                ctypes.windll.kernel32.CloseHandle(handle)
                ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
                return {"pid": pid,
                        "process_create_time": ticks / 10_000_000 - 11_644_473_600,
                        "process_command": None}
            except Exception:
                pass
        return {"pid": pid, "process_create_time": None, "process_command": None}


def running_state_is_active(state: dict[str, Any]) -> bool:
    """Require multiple process attributes; a reused PID alone is never sufficient."""

    try:
        current = process_identity(int(state["pid"]))
    except (KeyError, TypeError, ValueError):
        return False
    saved_time = state.get("process_create_time")
    saved_command = state.get("process_command")
    if saved_time is None or current.get("process_create_time") is None:
        return False
    if abs(float(saved_time) - float(current["process_create_time"])) > 1.0:
        return False
    return saved_command == current.get("process_command")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        for attempt in range(5):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.01 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)


def format_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:d}h{minutes:02d}m{seconds:02d}s"
    if minutes:
        return f"{minutes:d}m{seconds:02d}s"
    return f"{seconds:d}s"


class RunMonitor:
    """Mirror progress to the console, a log file, and an atomic status file."""

    def __init__(
        self,
        run_dir: Path,
        *,
        run_id: str,
        variant: str,
        interval_seconds: float,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("progress interval must be positive")
        self.run_dir = run_dir
        self.run_id = run_id
        self.variant = variant
        self.interval_seconds = float(interval_seconds)
        self.started_at = _utc_now()
        self.started_monotonic = time.monotonic()
        self.last_console_update = -float("inf")
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._heartbeat_logger = logging.getLogger("coder_issue.training.heartbeat")
        identity = process_identity()
        self.state: dict[str, Any] = {
            "schema_version": 1,
            "run_id": run_id,
            "variant": variant,
            **identity,
            "started_at": self.started_at,
            "updated_at": self.started_at,
            "stage": "initializing",
        }
        self.running_path = run_dir / "_RUNNING"
        self._handler = logging.FileHandler(run_dir / "training.log", encoding="utf-8")
        self._handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        logging.getLogger().addHandler(self._handler)
        _atomic_json(self.running_path, self.state)
        self._heartbeat = threading.Thread(
            target=self._heartbeat_loop,
            name=f"training-heartbeat-{run_id}",
            daemon=True,
        )
        self._heartbeat.start()

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_monotonic

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            now = time.monotonic()
            with self._lock:
                if now - self.last_console_update < self.interval_seconds:
                    continue
                self.state.update(
                    updated_at=_utc_now(),
                    elapsed_seconds=round(self.elapsed_seconds, 3),
                )
                _atomic_json(self.running_path, self.state)
                self._heartbeat_logger.info(
                    "[heartbeat | elapsed=%s] still running: stage=%s; %s",
                    format_duration(self.elapsed_seconds),
                    self.state.get("stage", "unknown"),
                    self.state.get("message", "waiting for first progress event"),
                )
                self.last_console_update = now

    def update(
        self,
        logger: logging.Logger,
        stage: str,
        message: str,
        *,
        force: bool = True,
        **progress: Any,
    ) -> None:
        now = time.monotonic()
        with self._lock:
            self.state.update(progress)
            self.state.update(
                stage=stage,
                message=message,
                updated_at=_utc_now(),
                elapsed_seconds=round(self.elapsed_seconds, 3),
            )
            should_publish = force or now - self.last_console_update >= self.interval_seconds
            if should_publish:
                _atomic_json(self.running_path, self.state)
                logger.info(
                    "[%s | elapsed=%s] %s",
                    stage,
                    format_duration(self.elapsed_seconds),
                    message,
                )
                self.last_console_update = now

    def finish(
        self,
        status: str,
        *,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> None:
        if status not in {"success", "failed", "cancelled"}:
            raise ValueError(f"unsupported terminal run status: {status}")
        self._stop.set()
        with self._lock:
            payload = dict(self.state)
            payload.update(
                status=status,
                finished_at=_utc_now(),
                updated_at=_utc_now(),
                elapsed_seconds=round(self.elapsed_seconds, 3),
            )
            if error_type is not None:
                payload["error_type"] = error_type
            if error_message is not None:
                payload["error_message"] = error_message
            terminal = self.run_dir / f"_{status.upper()}"
            _atomic_json(terminal, payload)
            self.running_path.unlink(missing_ok=True)

    def close(self) -> None:
        self._stop.set()
        self._heartbeat.join(timeout=max(1.0, self.interval_seconds + 0.5))
        logging.getLogger().removeHandler(self._handler)
        self._handler.close()

