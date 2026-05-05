from __future__ import annotations

import ctypes
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import webbrowser
from ctypes import wintypes
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

import httpx

from .config import build_default_config, load_config, merge_config
from .launcher_manager import start_launcher as start_full_launcher
from .paths import resolve_workspace_path, workspace_root
from .runtime_env import build_runtime_env, package_root, runtime_host, runtime_port
from .runtime_manager import create_runtime_session, start_runtime


WATCHER_STATE_PATH = ".tmp/zoom-meeting-bot/meeting-trigger-watcher-state.json"
WATCHER_LOG_PATH = ".tmp/zoom-meeting-bot/logs/meeting-trigger-watcher.log"
WATCHER_POLL_INTERVAL_SECONDS = 2.0
WATCHER_FOREGROUND_HOLD_SECONDS = 4.0
WATCHER_REPROMPT_COOLDOWN_SECONDS = 15 * 60.0
WATCHER_AUTO_OPEN_TARGET = "browser_auto"
ATOMIC_REPLACE_RETRY_DELAYS_SECONDS = (0.0, 0.05, 0.1, 0.2, 0.35, 0.5, 0.75, 1.0)

_WINDOWS_CREATE_FLAGS = (
    getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
)
_GENERIC_ZOOM_TITLES = {
    "zoom",
    "zoom meetings",
    "zoom workplace",
    "zoom workplace - zoom workplace",
}
_ACTIVE_SESSION_STATUSES = {
    "planned",
    "joining",
    "active",
    "suspected_ended",
    "queued",
    "leased",
    "processing",
}


def watcher_state_path() -> Path:
    return resolve_workspace_path(WATCHER_STATE_PATH)


def watcher_log_path() -> Path:
    return resolve_workspace_path(WATCHER_LOG_PATH)


def start_watcher(config: dict[str, Any], *, config_path: Path) -> dict[str, Any]:
    current = read_watcher_status(config, config_path=config_path)
    if bool(current.get("alive")):
        return current

    state_path = watcher_state_path()
    log_path = watcher_log_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    env = build_runtime_env(config)
    command = [
        sys.executable,
        "-m",
        "zoom_meeting_bot_cli",
        "_watcher-loop",
        "--config",
        str(config_path.resolve()),
    ]
    kwargs: dict[str, Any] = {
        "cwd": str(package_root()),
        "env": env,
        "stdin": subprocess.DEVNULL,
    }
    with log_path.open("ab") as handle:
        kwargs["stdout"] = handle
        kwargs["stderr"] = handle
        if os.name == "nt":
            kwargs["creationflags"] = _WINDOWS_CREATE_FLAGS
            process = subprocess.Popen(command, **kwargs)
        else:  # pragma: no cover
            kwargs["start_new_session"] = True
            process = subprocess.Popen(command, **kwargs)

    state = {
        "status": "starting",
        "pid": process.pid,
        "alive": True,
        "config_path": str(config_path.resolve()),
        "workspace_dir": str(workspace_root()),
        "package_dir": str(package_root()),
        "state_path": str(state_path),
        "log_path": str(log_path),
        "started_at": _utcnow_iso(),
        "provider": "zoom",
    }
    _write_json_atomic(state_path, state)

    deadline = time.time() + 5.0
    while time.time() < deadline:
        live = read_watcher_status(config, config_path=config_path)
        if bool(live.get("alive")):
            return live
        time.sleep(0.2)
    return read_watcher_status(config, config_path=config_path)


def stop_watcher(config: dict[str, Any], *, config_path: Path) -> dict[str, Any]:
    state = _read_json(watcher_state_path())
    pid = int((state or {}).get("pid") or 0)
    if pid > 0:
        _terminate_pid(pid, force=False)
        deadline = time.time() + 8.0
        while time.time() < deadline:
            if not _pid_alive(pid):
                break
            time.sleep(0.2)
        if _pid_alive(pid):
            _terminate_pid(pid, force=True)

    stopped = {
        "status": "stopped",
        "pid": pid or None,
        "alive": False,
        "config_path": str(config_path.resolve()),
        "workspace_dir": str(workspace_root()),
        "package_dir": str(package_root()),
        "state_path": str(watcher_state_path()),
        "log_path": str(watcher_log_path()),
        "stopped_at": _utcnow_iso(),
        "provider": "zoom",
    }
    _write_json_atomic(watcher_state_path(), stopped)
    return stopped


def read_watcher_status(config: dict[str, Any], *, config_path: Path) -> dict[str, Any]:
    state_path = watcher_state_path()
    state = _read_json(state_path)
    if not state:
        return {
            "status": "stopped",
            "pid": None,
            "alive": False,
            "config_path": str(config_path.resolve()),
            "workspace_dir": str(workspace_root()),
            "package_dir": str(package_root()),
            "state_path": str(state_path),
            "log_path": str(watcher_log_path()),
            "provider": "zoom",
        }

    pid = int(state.get("pid") or 0)
    alive = bool(pid > 0 and _pid_alive(pid))
    status = "running" if alive else "stopped"
    return {
        **state,
        "status": status if str(state.get("status") or "").strip().lower() != "error" else "error",
        "alive": alive,
        "config_path": str(config_path.resolve()),
        "workspace_dir": str(workspace_root()),
        "package_dir": str(package_root()),
        "state_path": str(state_path),
        "log_path": str(watcher_log_path()),
        "provider": "zoom",
    }


def run_watcher_loop(*, config_path: Path) -> int:
    if sys.platform != "win32":
        _write_loop_state(config_path=config_path, status="error", last_error="meeting trigger watcher currently supports Windows only")
        return 1

    prompted_until: dict[str, float] = {}
    active_key = ""
    active_seen_at = 0.0
    prompt_count = 0
    created_sessions = 0
    last_session_id = ""
    last_action = "idle"

    while True:
        config = _load_effective_config(config_path)
        candidate = detect_zoom_meeting_candidate()
        now = time.time()

        if candidate is None:
            active_key = ""
            active_seen_at = 0.0
            _write_loop_state(
                config_path=config_path,
                status="running",
                prompt_count=prompt_count,
                created_sessions=created_sessions,
                last_session_id=last_session_id,
                last_action=last_action,
                current_candidate=None,
            )
            time.sleep(WATCHER_POLL_INTERVAL_SECONDS)
            continue

        candidate_key = str(candidate["candidate_key"])
        if candidate_key != active_key:
            active_key = candidate_key
            active_seen_at = now

        if now < prompted_until.get(candidate_key, 0.0):
            _write_loop_state(
                config_path=config_path,
                status="running",
                prompt_count=prompt_count,
                created_sessions=created_sessions,
                last_session_id=last_session_id,
                last_action=last_action,
                current_candidate=candidate,
            )
            time.sleep(WATCHER_POLL_INTERVAL_SECONDS)
            continue

        if now - active_seen_at < WATCHER_FOREGROUND_HOLD_SECONDS:
            _write_loop_state(
                config_path=config_path,
                status="running",
                prompt_count=prompt_count,
                created_sessions=created_sessions,
                last_session_id=last_session_id,
                last_action=last_action,
                current_candidate=candidate,
            )
            time.sleep(WATCHER_POLL_INTERVAL_SECONDS)
            continue

        if _has_active_delegate_session(config):
            last_action = "skipped_active_session"
            prompted_until[candidate_key] = now + WATCHER_REPROMPT_COOLDOWN_SECONDS
            _write_loop_state(
                config_path=config_path,
                status="running",
                prompt_count=prompt_count,
                created_sessions=created_sessions,
                last_session_id=last_session_id,
                last_action=last_action,
                current_candidate=candidate,
            )
            time.sleep(WATCHER_POLL_INTERVAL_SECONDS)
            continue

        prompt_count += 1
        result = _prompt_and_maybe_create_session(config=config, config_path=config_path, candidate=candidate)
        prompted_until[candidate_key] = time.time() + WATCHER_REPROMPT_COOLDOWN_SECONDS
        last_action = str(result.get("action") or "idle")
        if str(result.get("session_id") or "").strip():
            created_sessions += 1
            last_session_id = str(result["session_id"]).strip()
        _write_loop_state(
            config_path=config_path,
            status="running",
            prompt_count=prompt_count,
            created_sessions=created_sessions,
            last_session_id=last_session_id,
            last_action=last_action,
            last_error=str(result.get("error") or "").strip(),
            current_candidate=candidate,
        )
        active_key = ""
        active_seen_at = 0.0
        time.sleep(WATCHER_POLL_INTERVAL_SECONDS)


def detect_zoom_meeting_candidate() -> dict[str, Any] | None:
    title, pid = _foreground_window_title_and_pid()
    if not title or pid <= 0:
        return None
    process_info = _windows_process_info(pid)
    process_name = str(process_info.get("name") or "").strip()
    executable_path = str(process_info.get("executable_path") or "").strip()
    command_line = str(process_info.get("command_line") or "").strip()
    search_text = " ".join([title, process_name, executable_path, command_line]).casefold()
    if "zoom" not in search_text:
        return None
    normalized_title = _normalize_title(title)
    if normalized_title in _GENERIC_ZOOM_TITLES:
        return None
    inferred = _infer_zoom_join_details(command_line)
    return {
        "pid": pid,
        "window_title": title,
        "process_name": process_name or Path(executable_path).stem,
        "command_line": command_line,
        "join_url": inferred.get("join_url") or "",
        "meeting_number": inferred.get("meeting_number") or "",
        "passcode": inferred.get("passcode") or "",
        "candidate_key": f"{pid}:{normalized_title}",
    }


def normalize_zoom_join_input(raw: str) -> dict[str, str]:
    text = str(raw or "").strip()
    if not text:
        return {"kind": "", "join_url": "", "meeting_number": "", "passcode": ""}
    if _looks_like_zoom_join_url(text):
        parsed = urlsplit(text)
        meeting_number = _extract_zoom_meeting_number(text)
        passcode = str(parse_qs(parsed.query).get("pwd", [""])[0] or "").strip()
        return {
            "kind": "join_url",
            "join_url": text,
            "meeting_number": meeting_number,
            "passcode": passcode,
        }
    digits = re.sub(r"\D+", "", text)
    if digits:
        return {
            "kind": "meeting_number",
            "join_url": f"https://zoom.us/j/{digits}",
            "meeting_number": digits,
            "passcode": "",
        }
    return {"kind": "", "join_url": "", "meeting_number": "", "passcode": ""}


def _prompt_and_maybe_create_session(
    *,
    config: dict[str, Any],
    config_path: Path,
    candidate: dict[str, Any],
) -> dict[str, str]:
    try:
        consent = _show_yes_no_dialog(
            title="ZOOM_MEETING_BOT",
            message=(
                "회의가 감지되었습니다.\n\n"
                f"창 제목: {candidate.get('window_title') or 'Zoom'}\n\n"
                "bot을 참가시킬까요?"
            ),
        )
        if not consent:
            return {"action": "declined"}

        if not str(dict(config.get("zoom") or {}).get("client_id") or "").strip() or not str(
            dict(config.get("zoom") or {}).get("client_secret") or ""
        ).strip():
            _show_info_dialog(
                title="ZOOM_MEETING_BOT",
                message="Zoom Meeting SDK 설정이 비어 있어 watcher가 bot을 참가시킬 수 없습니다. 먼저 초기 설정을 완료해주세요.",
                error=True,
            )
            return {"action": "blocked", "error": "missing_zoom_sdk_credentials"}

        request = _resolve_session_request(candidate)
        if not request:
            return {"action": "cancelled"}

        if not _ensure_backend_ready(config=config, config_path=config_path):
            _show_info_dialog(
                title="ZOOM_MEETING_BOT",
                message="로컬 런타임을 시작하지 못했습니다. 설정을 확인한 뒤 다시 시도해주세요.",
                error=True,
            )
            return {"action": "backend_error", "error": "runtime_start_failed"}

        payload = create_runtime_session(
            config,
            join_url=request["join_url"],
            passcode=request["passcode"],
            meeting_number=request["meeting_number"],
            meeting_topic=request["meeting_topic"],
            requested_by="meeting_trigger_watcher",
            instructions="Triggered from the local Zoom meeting watcher after user approval.",
            delegate_mode="answer_on_ask",
        )
        session = dict(payload.get("session") or {})
        launch_url = _resolve_launch_url(session)
        if launch_url:
            webbrowser.open(launch_url)
        _show_info_dialog(
            title="ZOOM_MEETING_BOT",
            message="bot 참가를 시작했습니다.",
            error=False,
        )
        return {"action": "created", "session_id": str(session.get("session_id") or "").strip()}
    except Exception as exc:
        _show_info_dialog(
            title="ZOOM_MEETING_BOT",
            message=f"watcher가 회의 bot 참가를 시작하지 못했습니다.\n\n{exc}",
            error=True,
        )
        return {"action": "error", "error": str(exc)}


def _resolve_session_request(candidate: dict[str, Any]) -> dict[str, str] | None:
    clipboard_text = _read_clipboard_text()
    clipboard_request = normalize_zoom_join_input(clipboard_text)
    candidate_join = normalize_zoom_join_input(str(candidate.get("join_url") or ""))

    selected = candidate_join if candidate_join["join_url"] else clipboard_request
    if selected["join_url"] and _show_yes_no_dialog(
        title="ZOOM_MEETING_BOT",
        message=(
            "회의 참가 정보를 찾았습니다.\n\n"
            f"{selected['join_url']}\n\n"
            "이 값으로 bot을 참가시킬까요?"
        ),
    ):
        pass
    else:
        raw_target = _ask_string_dialog(
            title="ZOOM_MEETING_BOT",
            prompt="회의 링크 또는 회의 번호를 입력해주세요.",
            initial_value=selected["join_url"] or selected["meeting_number"],
        )
        normalized = normalize_zoom_join_input(raw_target)
        if not normalized["join_url"]:
            return None
        selected = normalized

    passcode = str(selected.get("passcode") or candidate.get("passcode") or "").strip()
    if not passcode:
        passcode = _ask_string_dialog(
            title="ZOOM_MEETING_BOT",
            prompt="회의 암호를 입력해주세요.",
            initial_value="",
            mask=True,
        )
        if not passcode:
            return None

    return {
        "join_url": str(selected["join_url"]).strip(),
        "meeting_number": str(selected["meeting_number"]).strip() or _extract_zoom_meeting_number(selected["join_url"]),
        "passcode": passcode.strip(),
        "meeting_topic": str(candidate.get("window_title") or "").strip(),
    }


def _ensure_backend_ready(*, config: dict[str, Any], config_path: Path, wait_seconds: float = 20.0) -> bool:
    if _runtime_api_ready(config):
        return True
    if _execution_mode(config) == "launcher":
        start_full_launcher(config, config_path=config_path)
    else:
        start_runtime(config, config_path=config_path)
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if _runtime_api_ready(config):
            return True
        time.sleep(0.5)
    return False


def _has_active_delegate_session(config: dict[str, Any]) -> bool:
    if not _runtime_api_ready(config):
        return False
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(f"http://{runtime_host(config)}:{runtime_port(config)}/delegate/sessions")
            response.raise_for_status()
            payload = response.json()
    except Exception:
        return False
    sessions = list(dict(payload or {}).get("sessions") or [])
    for raw in sessions:
        status = str(dict(raw or {}).get("status") or "").strip().lower()
        if status in _ACTIVE_SESSION_STATUSES:
            return True
    return False


def _runtime_api_ready(config: dict[str, Any]) -> bool:
    try:
        with httpx.Client(timeout=3.0) as client:
            response = client.get(f"http://{runtime_host(config)}:{runtime_port(config)}/health")
            response.raise_for_status()
            payload = response.json()
            return bool(isinstance(payload, dict) and payload.get("ok"))
    except Exception:
        return False


def _resolve_launch_url(session: dict[str, Any]) -> str:
    join_ticket = dict(session.get("join_ticket") or {})
    return (
        str(join_ticket.get("browser_auto_join_url") or "").strip()
        or str(join_ticket.get("browser_join_url") or "").strip()
        or str(join_ticket.get("join_url") or "").strip()
    )


def _load_effective_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        raise FileNotFoundError(f"Config file does not exist: {config_path}")
    return merge_config(build_default_config(), load_config(config_path))


def _foreground_window_title_and_pid() -> tuple[str, int]:
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return "", 0
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return "", 0
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return str(buffer.value or "").strip(), int(pid.value or 0)


def _windows_process_info(pid: int) -> dict[str, str]:
    if pid <= 0:
        return {}
    script = (
        f"$proc = Get-CimInstance Win32_Process -Filter \"ProcessId = {pid}\"; "
        "if ($null -eq $proc) { exit 1 }; "
        "[pscustomobject]@{ExecutablePath=$proc.ExecutablePath; CommandLine=$proc.CommandLine; Name=$proc.Name} "
        "| ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        **_windows_hidden_process_kwargs(),
    )
    if result.returncode != 0:
        return {}
    payload = result.stdout.decode(errors="ignore").strip()
    if not payload:
        return {}
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        "executable_path": str(parsed.get("ExecutablePath") or "").strip(),
        "command_line": str(parsed.get("CommandLine") or "").strip(),
        "name": str(parsed.get("Name") or "").strip(),
    }


def _infer_zoom_join_details(command_line: str) -> dict[str, str]:
    text = str(command_line or "").strip()
    if not text:
        return {"join_url": "", "meeting_number": "", "passcode": ""}
    https_match = re.search(r"https://[^\s\"']*zoom\.us/[^\s\"']+", text, flags=re.IGNORECASE)
    if https_match:
        return normalize_zoom_join_input(https_match.group(0))
    protocol_match = re.search(r"zoommtg://[^\s\"']+", text, flags=re.IGNORECASE)
    if protocol_match:
        protocol_url = protocol_match.group(0)
        parsed = urlsplit(protocol_url)
        query = parse_qs(parsed.query)
        meeting_number = str(query.get("confno", [""])[0] or "").strip()
        passcode = str(query.get("pwd", [""])[0] or "").strip()
        join_url = f"https://zoom.us/j/{meeting_number}" if meeting_number else ""
        if join_url and passcode:
            join_url = f"{join_url}?pwd={quote(passcode)}"
        return {
            "join_url": join_url,
            "meeting_number": meeting_number,
            "passcode": passcode,
        }
    confno_match = re.search(r"(?:confno|meetingNo|meeting_number)=([0-9]{9,13})", text, flags=re.IGNORECASE)
    pwd_match = re.search(r"(?:pwd|passcode)=([A-Za-z0-9]+)", text, flags=re.IGNORECASE)
    meeting_number = str(confno_match.group(1) if confno_match else "").strip()
    passcode = str(pwd_match.group(1) if pwd_match else "").strip()
    join_url = f"https://zoom.us/j/{meeting_number}" if meeting_number else ""
    if join_url and passcode:
        join_url = f"{join_url}?pwd={quote(passcode)}"
    return {
        "join_url": join_url,
        "meeting_number": meeting_number,
        "passcode": passcode,
    }


def _looks_like_zoom_join_url(value: str) -> bool:
    if not value:
        return False
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and "zoom.us" in parsed.netloc and "/j/" in parsed.path


def _extract_zoom_meeting_number(join_url: str) -> str:
    if not join_url:
        return ""
    parsed = urlsplit(join_url)
    match = re.search(r"/j/(\d+)", parsed.path)
    return str(match.group(1) if match else "").strip()


def _normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", str(title or "").strip()).casefold()


def _read_clipboard_text() -> str:
    try:
        import tkinter as tk
    except Exception:
        return ""
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        return str(root.clipboard_get() or "").strip()
    except Exception:
        return ""
    finally:
        root.destroy()


def _show_yes_no_dialog(*, title: str, message: str) -> bool:
    try:
        import tkinter as tk
        from tkinter import messagebox
    except Exception:
        return False
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    root.update()
    try:
        return bool(messagebox.askyesno(title, message, parent=root))
    finally:
        root.destroy()


def _show_info_dialog(*, title: str, message: str, error: bool) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox
    except Exception:
        return
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    root.update()
    try:
        if error:
            messagebox.showerror(title, message, parent=root)
        else:
            messagebox.showinfo(title, message, parent=root)
    finally:
        root.destroy()


def _ask_string_dialog(*, title: str, prompt: str, initial_value: str = "", mask: bool = False) -> str:
    try:
        import tkinter as tk
        from tkinter import simpledialog
    except Exception:
        return ""
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    root.update()
    try:
        value = simpledialog.askstring(
            title=title,
            prompt=prompt,
            initialvalue=initial_value,
            parent=root,
            show="*" if mask else None,
        )
        return str(value or "").strip()
    finally:
        root.destroy()


def _execution_mode(config: dict[str, Any]) -> str:
    runtime = dict(config.get("runtime") or {})
    mode = str(runtime.get("execution_mode") or "runtime_only").strip()
    return mode or "runtime_only"


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        _replace_path_with_retry(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def _replace_path_with_retry(temp_path: str, path: Path) -> None:
    last_error: OSError | None = None
    for attempt, delay in enumerate(ATOMIC_REPLACE_RETRY_DELAYS_SECONDS):
        try:
            os.replace(temp_path, path)
            return
        except OSError as exc:
            last_error = exc
            if not _is_retryable_windows_replace_error(exc) or attempt == len(ATOMIC_REPLACE_RETRY_DELAYS_SECONDS) - 1:
                raise
            time.sleep(delay)
    if last_error is not None:
        raise last_error


def _is_retryable_windows_replace_error(exc: OSError) -> bool:
    if os.name != "nt":
        return False
    if isinstance(exc, PermissionError):
        return getattr(exc, "winerror", None) in {5, 32}
    return getattr(exc, "winerror", None) in {5, 32}


def _write_loop_state(
    *,
    config_path: Path,
    status: str,
    prompt_count: int = 0,
    created_sessions: int = 0,
    last_session_id: str = "",
    last_action: str = "",
    last_error: str = "",
    current_candidate: dict[str, Any] | None = None,
) -> None:
    existing = _read_json(watcher_state_path()) or {}
    payload = {
        **existing,
        "status": status,
        "pid": os.getpid(),
        "alive": True,
        "config_path": str(config_path.resolve()),
        "workspace_dir": str(workspace_root()),
        "package_dir": str(package_root()),
        "state_path": str(watcher_state_path()),
        "log_path": str(watcher_log_path()),
        "provider": "zoom",
        "updated_at": _utcnow_iso(),
        "prompt_count": int(prompt_count),
        "created_sessions": int(created_sessions),
        "last_session_id": str(last_session_id or "").strip(),
        "last_action": str(last_action or "").strip(),
        "last_error": str(last_error or "").strip(),
    }
    if "started_at" not in payload:
        payload["started_at"] = _utcnow_iso()
    if current_candidate:
        payload["current_candidate"] = {
            "window_title": str(current_candidate.get("window_title") or "").strip(),
            "process_name": str(current_candidate.get("process_name") or "").strip(),
            "pid": int(current_candidate.get("pid") or 0),
        }
    else:
        payload["current_candidate"] = None
    _write_json_atomic(watcher_state_path(), payload)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
                **_windows_hidden_process_kwargs(),
            )
            output = result.stdout.decode(errors="ignore").strip()
            if not output or output.lower().startswith("info:"):
                return False
            return f'"{pid}"' in output
        os.kill(pid, 0)
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    return True


def _terminate_pid(pid: int, *, force: bool) -> None:
    if pid <= 0:
        return
    if os.name == "nt":
        args = ["taskkill", "/PID", str(pid), "/T"]
        if force:
            args.append("/F")
        subprocess.run(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            **_windows_hidden_process_kwargs(),
        )
        return
    try:  # pragma: no cover
        os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
    except OSError:
        return


def _windows_hidden_process_kwargs() -> dict[str, Any]:
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {
        "startupinfo": startupinfo,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
    }


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()
