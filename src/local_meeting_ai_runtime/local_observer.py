"""Local observation helpers that arm the local AI body with screen and audio capture."""

from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any
from uuid import uuid4

import numpy as np


class LocalObserverError(RuntimeError):
    """Raised when local observation cannot proceed."""


class LocalObserver:
    def __init__(self) -> None:
        self._ocr_lang = os.getenv("DELEGATE_LOCAL_OCR_LANGUAGE", "kor+eng").strip() or "kor+eng"
        self._ocr_psm = os.getenv("DELEGATE_LOCAL_OCR_PSM", "6").strip() or "6"
        self._audio_sample_rate = int(os.getenv("DELEGATE_LOCAL_AUDIO_SAMPLE_RATE", "16000"))
        self._audio_channels = int(os.getenv("DELEGATE_LOCAL_AUDIO_CHANNELS", "1"))
        self._audio_blocksize_ms = max(int(os.getenv("DELEGATE_LOCAL_AUDIO_BLOCKSIZE_MS", "300")), 100)
        self._audio_rms_threshold = float(os.getenv("DELEGATE_LOCAL_AUDIO_RMS_THRESHOLD", "0.003"))
        self._microphone_device_name = os.getenv("DELEGATE_LOCAL_MICROPHONE_DEVICE", "").strip()
        self._system_audio_device_name = os.getenv("DELEGATE_LOCAL_SYSTEM_AUDIO_DEVICE", "").strip()
        self._meeting_output_device_name = (
            os.getenv("DELEGATE_LOCAL_MEETING_OUTPUT_DEVICE", "").strip()
            or self._system_audio_device_name
            or "스피커(USB Audio Device)"
        )
        self._strict_audio_device_selection = self._env_bool("DELEGATE_LOCAL_AUDIO_STRICT_DEVICE", False)
        self._artifact_dir = Path(os.getenv("DELEGATE_LOCAL_OBSERVER_DIR", ".tmp/local-observer"))
        self._artifact_dir.mkdir(parents=True, exist_ok=True)
        self._windows_audio_helper_enabled = self._env_bool("DELEGATE_WINDOWS_AUDIO_HELPER_ENABLED", True)
        self._windows_audio_helper_project_dir = (
            Path(os.getenv("DELEGATE_WINDOWS_AUDIO_HELPER_PROJECT", "")).expanduser()
            if os.getenv("DELEGATE_WINDOWS_AUDIO_HELPER_PROJECT")
            else Path(__file__).resolve().parents[2] / "tools" / "windows-audio-recorder"
        )
        self._windows_audio_helper_build_config = (
            os.getenv("DELEGATE_WINDOWS_AUDIO_HELPER_BUILD_CONFIG", "Release").strip() or "Release"
        )
        self._windows_audio_helper_target_framework = (
            os.getenv("DELEGATE_WINDOWS_AUDIO_HELPER_TARGET_FRAMEWORK", "net8.0-windows").strip() or "net8.0-windows"
        )

    @property
    def capabilities(self) -> dict[str, bool]:
        system_audio_capture_ready = (
            self.windows_audio_capture_available()
            if sys.platform == "win32"
            else (importlib.util.find_spec("soundcard") is not None and importlib.util.find_spec("soundfile") is not None)
        )
        return {
            "window_capture": importlib.util.find_spec("mss") is not None and importlib.util.find_spec("pygetwindow") is not None,
            "ocr": importlib.util.find_spec("pytesseract") is not None,
            "system_audio_capture": system_audio_capture_ready,
            "window_automation": importlib.util.find_spec("pywinauto") is not None or importlib.util.find_spec("pyautogui") is not None,
        }

    @property
    def audio_devices(self) -> dict[str, Any]:
        if sys.platform == "win32":
            configured = {
                "microphone_device_name": self._microphone_device_name or None,
                "system_audio_device_name": self._system_audio_device_name or None,
                "meeting_output_device_name": self._meeting_output_device_name or None,
                "strict_device_selection": self._strict_audio_device_selection,
                "capture_backend": "windows_native_helper",
            }
            if not self.windows_audio_capture_available():
                return {
                    "microphones": [],
                    "speakers": [],
                    "configured": {**configured, "enumeration_error": "Windows native audio helper is not available."},
                }
            try:
                devices = self._windows_audio_helper_devices()
            except Exception as exc:
                return {
                    "microphones": [],
                    "speakers": [],
                    "configured": {**configured, "enumeration_error": str(exc)},
                }
            return {
                "microphones": list(devices.get("microphones") or []),
                "speakers": list(devices.get("speakers") or []),
                "configured": configured,
            }
        if importlib.util.find_spec("soundcard") is None:
            return {"microphones": [], "speakers": [], "configured": {}}
        configured = {
            "microphone_device_name": self._microphone_device_name or None,
            "system_audio_device_name": self._system_audio_device_name or None,
            "meeting_output_device_name": self._meeting_output_device_name or None,
            "strict_device_selection": self._strict_audio_device_selection,
        }
        try:
            soundcard = self._import_module("soundcard")
            microphones = self._device_descriptors(getattr(soundcard, "all_microphones", lambda: [])())
            speakers = self._device_descriptors(getattr(soundcard, "all_speakers", lambda: [])())
        except Exception as exc:
            return {
                "microphones": [],
                "speakers": [],
                "configured": {**configured, "enumeration_error": str(exc)},
            }
        return {
            "microphones": microphones,
            "speakers": speakers,
            "configured": configured,
        }

    @property
    def meeting_output_device_name(self) -> str:
        return self._meeting_output_device_name

    def windows_audio_capture_available(self) -> bool:
        if sys.platform != "win32" or not self._windows_audio_helper_enabled:
            return False
        helper_command = self._find_existing_windows_audio_helper_command()
        if helper_command:
            return True
        return bool(
            shutil.which("dotnet") is not None
            and self._windows_audio_helper_project_dir.exists()
            and any(self._windows_audio_helper_project_dir.glob("*.csproj"))
        )

    def ensure_windows_audio_helper_command(self) -> list[str]:
        if not self.windows_audio_capture_available():
            raise LocalObserverError("Windows native audio helper is not available on this system.")
        existing_command = self._find_existing_windows_audio_helper_command()
        if existing_command:
            return existing_command
        project_dir = self._windows_audio_helper_project_dir
        csproj_candidates = sorted(project_dir.glob("*.csproj"))
        if not csproj_candidates:
            raise LocalObserverError(f"Windows audio helper project was not found: {project_dir}")
        csproj_path = csproj_candidates[0]
        exe_path, dll_path = self._resolve_windows_audio_helper_artifacts(project_dir, csproj_path.stem)
        project_mtime = max(
            path.stat().st_mtime
            for path in [csproj_path, *project_dir.rglob("*.cs")]
            if path.exists()
        )
        preferred_artifact = exe_path if exe_path.exists() else dll_path
        if (not preferred_artifact.exists()) or preferred_artifact.stat().st_mtime < project_mtime:
            completed = subprocess.run(
                [
                    shutil.which("dotnet") or "dotnet",
                    "build",
                    str(csproj_path),
                    "-c",
                    self._windows_audio_helper_build_config,
                    "--nologo",
                ],
                capture_output=True,
                text=True,
                cwd=str(project_dir),
            )
            if completed.returncode != 0 or not dll_path.exists():
                details = (completed.stderr or completed.stdout or "").strip()
                raise LocalObserverError(
                    "Windows audio helper build failed."
                    + (f" {details}" if details else "")
                )
            exe_path, dll_path = self._resolve_windows_audio_helper_artifacts(project_dir, csproj_path.stem)
            if not exe_path.exists() and not dll_path.exists():
                details = (completed.stderr or completed.stdout or "").strip()
                raise LocalObserverError(
                    "Windows audio helper build succeeded but output artifact was not found."
                    + (f" {details}" if details else "")
                )
        if exe_path.exists():
            return [str(exe_path)]
        return [shutil.which("dotnet") or "dotnet", str(dll_path)]

    def _find_existing_windows_audio_helper_command(self) -> list[str]:
        project_dir = self._windows_audio_helper_project_dir
        if not project_dir.exists():
            return []
        assembly_name = "WindowsAudioRecorder"
        exe_path, dll_path = self._resolve_windows_audio_helper_artifacts(project_dir, assembly_name)
        if exe_path.exists():
            return [str(exe_path)]
        if dll_path.exists() and shutil.which("dotnet") is not None:
            return [shutil.which("dotnet") or "dotnet", str(dll_path)]
        return []

    def _resolve_windows_audio_helper_artifacts(self, project_dir: Path, assembly_name: str) -> tuple[Path, Path]:
        release_dir = project_dir / "bin" / self._windows_audio_helper_build_config
        framework = self._windows_audio_helper_target_framework
        direct_exe_candidates = [
            release_dir / framework / f"{assembly_name}.exe",
            release_dir / "net8.0-windows" / f"{assembly_name}.exe",
            release_dir / "net8.0" / f"{assembly_name}.exe",
        ]
        direct_dll_candidates = [
            release_dir / framework / f"{assembly_name}.dll",
            release_dir / "net8.0-windows" / f"{assembly_name}.dll",
            release_dir / "net8.0" / f"{assembly_name}.dll",
        ]
        for candidate in direct_exe_candidates:
            if candidate.exists():
                return candidate, candidate.with_suffix(".dll")
        for candidate in direct_dll_candidates:
            if candidate.exists():
                return candidate.with_suffix(".exe"), candidate
        recursive_exe_candidates = sorted(
            release_dir.glob(f"**/{assembly_name}.exe"),
            key=lambda path: path.stat().st_mtime if path.exists() else 0.0,
            reverse=True,
        )
        if recursive_exe_candidates:
            exe_path = recursive_exe_candidates[0]
            return exe_path, exe_path.with_suffix(".dll")
        recursive_dll_candidates = sorted(
            release_dir.glob(f"**/{assembly_name}.dll"),
            key=lambda path: path.stat().st_mtime if path.exists() else 0.0,
            reverse=True,
        )
        if recursive_dll_candidates:
            dll_path = recursive_dll_candidates[0]
            return dll_path.with_suffix(".exe"), dll_path
        return direct_exe_candidates[0], direct_dll_candidates[0]

    def _windows_audio_helper_devices(self) -> dict[str, Any]:
        completed = subprocess.run(
            [*self.ensure_windows_audio_helper_command(), "list-devices"],
            capture_output=True,
            text=True,
            cwd=str(self._windows_audio_helper_project_dir),
        )
        if completed.returncode != 0:
            details = (completed.stderr or completed.stdout or "").strip()
            raise LocalObserverError(
                "Windows audio helper device enumeration failed."
                + (f" {details}" if details else "")
            )
        try:
            payload = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise LocalObserverError("Windows audio helper returned malformed device metadata.") from exc
        return dict(payload or {})

    def tail_text_file(self, path: str | Path | None, *, lines: int = 20) -> str:
        candidate = Path(str(path or "").strip())
        if not str(candidate) or not candidate.exists():
            return ""
        try:
            content = candidate.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            return ""
        return "\n".join(content[-max(int(lines), 1):]).strip()

    def audio_quality_readiness(self) -> dict[str, Any]:
        quality_notes: list[str] = []
        if sys.platform == "win32":
            if not self.windows_audio_capture_available():
                return {
                    "microphone_device_ready": False,
                    "meeting_output_device_ready": False,
                    "configured_meeting_output_device": self._meeting_output_device_name or None,
                    "blocking_reasons": ["Windows native audio helper is not available."],
                    "quality_notes": quality_notes,
                }
            try:
                devices = self._windows_audio_helper_devices()
            except Exception as exc:
                return {
                    "microphone_device_ready": False,
                    "meeting_output_device_ready": False,
                    "configured_meeting_output_device": self._meeting_output_device_name or None,
                    "blocking_reasons": [str(exc)],
                    "quality_notes": quality_notes,
                }
            microphones = list(devices.get("microphones") or [])
            speakers = list(devices.get("speakers") or [])
            microphone_device_ready = bool(
                self._match_named_descriptor(microphones, self._microphone_device_name)
                if self._microphone_device_name
                else microphones
            )
            meeting_output_device_ready = bool(self._match_named_descriptor(speakers, self._meeting_output_device_name))
            blocking_reasons: list[str] = []
            if not microphone_device_ready:
                if self._microphone_device_name:
                    blocking_reasons.append(
                        f"Configured microphone device was not found: {self._microphone_device_name}"
                    )
                else:
                    blocking_reasons.append("No default microphone device is available.")
            if not meeting_output_device_ready:
                blocking_reasons.append(
                    f"Configured meeting output device was not found: {self._meeting_output_device_name}"
                )
            return {
                "microphone_device_ready": microphone_device_ready,
                "meeting_output_device_ready": meeting_output_device_ready,
                "configured_meeting_output_device": self._meeting_output_device_name or None,
                "blocking_reasons": blocking_reasons,
                "quality_notes": quality_notes,
            }
        if importlib.util.find_spec("soundcard") is None:
            return {
                "microphone_device_ready": False,
                "meeting_output_device_ready": False,
                "configured_meeting_output_device": self._meeting_output_device_name or None,
                "blocking_reasons": ["soundcard dependency is not installed."],
                "quality_notes": quality_notes,
            }
        try:
            soundcard = self._import_module("soundcard")
            microphones = list(getattr(soundcard, "all_microphones", lambda: [])())
            speakers = list(getattr(soundcard, "all_speakers", lambda: [])())
        except Exception as exc:
            return {
                "microphone_device_ready": False,
                "meeting_output_device_ready": False,
                "configured_meeting_output_device": self._meeting_output_device_name or None,
                "blocking_reasons": [str(exc)],
                "quality_notes": quality_notes,
            }
        microphone_device_ready = bool(
            self._match_named_device(microphones, self._microphone_device_name)
            if self._microphone_device_name
            else getattr(soundcard, "default_microphone", lambda: None)() is not None
        )
        meeting_output_device_ready = bool(self._match_named_device(speakers, self._meeting_output_device_name))
        blocking_reasons: list[str] = []
        if not microphone_device_ready:
            if self._microphone_device_name:
                blocking_reasons.append(
                    f"Configured microphone device was not found: {self._microphone_device_name}"
                )
            else:
                blocking_reasons.append("No default microphone device is available.")
        if not meeting_output_device_ready:
            blocking_reasons.append(
                f"Configured meeting output device was not found: {self._meeting_output_device_name}"
            )
        if sys.platform == "darwin":
            known_loopback_markers = ("blackhole", "loopback", "soundflower", "aggregate", "multi-output")
            normalized_device = str(self._meeting_output_device_name or "").strip().lower()
            if normalized_device and not any(marker in normalized_device for marker in known_loopback_markers):
                quality_notes.append(
                    "macOS meeting output device does not look like a known loopback device. "
                    "BlackHole, Loopback, Soundflower, or an aggregate/multi-output device is recommended."
                )
            quality_notes.append(
                "macOS requires Microphone and Screen Recording permissions for stable local observation."
            )
        return {
            "microphone_device_ready": microphone_device_ready,
            "meeting_output_device_ready": meeting_output_device_ready,
            "configured_meeting_output_device": self._meeting_output_device_name or None,
            "blocking_reasons": blocking_reasons,
            "quality_notes": quality_notes,
        }

    def microphone_device_available(self, device_name: str | None = None) -> bool:
        if sys.platform == "win32":
            if not self.windows_audio_capture_available():
                return False
            try:
                microphones = list((self._windows_audio_helper_devices().get("microphones") or []))
                hint = str(device_name or self._microphone_device_name or "").strip()
                if hint:
                    return self._match_named_descriptor(microphones, hint) is not None
                return bool(microphones)
            except Exception:
                return False
        if importlib.util.find_spec("soundcard") is None:
            return False
        try:
            soundcard = self._import_module("soundcard")
            microphones = list(getattr(soundcard, "all_microphones", lambda: [])())
            hint = str(device_name or self._microphone_device_name or "").strip()
            if hint:
                return self._match_named_device(microphones, hint) is not None
            return getattr(soundcard, "default_microphone", lambda: None)() is not None
        except Exception:
            return False

    def speaker_device_available(self, device_name: str | None = None) -> bool:
        if sys.platform == "win32":
            if not self.windows_audio_capture_available():
                return False
            try:
                speakers = list((self._windows_audio_helper_devices().get("speakers") or []))
                hint = str(device_name or self._meeting_output_device_name or "").strip()
                if hint:
                    return self._match_named_descriptor(speakers, hint) is not None
                return bool(speakers)
            except Exception:
                return False
        if importlib.util.find_spec("soundcard") is None:
            return False
        try:
            soundcard = self._import_module("soundcard")
            speakers = list(getattr(soundcard, "all_speakers", lambda: [])())
            hint = str(device_name or self._meeting_output_device_name or "").strip()
            if hint:
                return self._match_named_device(speakers, hint) is not None
            return getattr(soundcard, "default_speaker", lambda: None)() is not None
        except Exception:
            return False

    def capture_window_text(
        self,
        *,
        window_title: str,
        crop: dict[str, int] | None = None,
        save_image: bool = True,
    ) -> dict[str, Any]:
        mss = self._import_module("mss")
        gw = self._import_module("pygetwindow")
        pytesseract = self._import_module("pytesseract")
        pil_image_module = self._import_module("PIL.Image")

        window = self._find_window(gw, window_title)
        bounds = {
            "left": int(window.left),
            "top": int(window.top),
            "width": int(window.width),
            "height": int(window.height),
        }
        if crop:
            bounds = self._apply_crop(bounds, crop)

        with mss.mss() as sct:
            raw = sct.grab(bounds)
        image = pil_image_module.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        prepared = self._prepare_ocr_image(image)
        config = f"--psm {self._ocr_psm}"
        text = str(pytesseract.image_to_string(prepared, lang=self._ocr_lang, config=config) or "").strip()

        artifact_path = None
        if save_image:
            artifact_path = self._artifact_dir / f"window-capture-{self._safe_name(window_title)}.png"
            image.save(artifact_path)

        return {
            "window_title": window.title,
            "bounds": bounds,
            "text": text,
            "image_path": str(artifact_path) if artifact_path else None,
        }

    def capture_audio(
        self,
        *,
        seconds: float,
        sample_rate: int | None = None,
        source: str = "system",
        device_name: str | None = None,
        strict_device: bool | None = None,
    ) -> dict[str, Any]:
        normalized_source = str(source or "system").strip().lower() or "system"
        if sys.platform == "win32":
            if not self.windows_audio_capture_available():
                raise LocalObserverError("Windows native audio helper is not available for local audio capture.")
            return self._capture_audio_windows_helper(
                seconds=seconds,
                sample_rate=sample_rate,
                source=normalized_source,
                device_name=device_name,
                strict_device=strict_device,
            )

        soundcard = self._import_module("soundcard")
        soundfile = self._import_module("soundfile")

        rate = int(sample_rate or self._audio_sample_rate)
        duration = max(float(seconds), 0.5)
        frames = max(int(rate * duration), 1)
        requested_device_name = str(device_name or "").strip()
        device_hint = requested_device_name or (
            self._microphone_device_name if normalized_source == "microphone" else self._system_audio_device_name
        )
        strict = self._strict_audio_device_selection if strict_device is None else bool(strict_device)
        blocksize = max(int(rate * (self._audio_blocksize_ms / 1000.0)), frames)
        if normalized_source == "microphone":
            recorder_device = self._select_microphone(soundcard, device_hint, strict=strict)
            filename = "microphone-capture.wav"
            capture_mode = "local_microphone"
        else:
            speaker = self._select_speaker(soundcard, device_hint, strict=strict)
            recorder_device = soundcard.get_microphone(str(speaker.name), include_loopback=True)
            filename = "loopback-capture.wav"
            capture_mode = "local_system_audio"

        with recorder_device.recorder(
            samplerate=rate,
            blocksize=blocksize,
        ) as recorder:
            audio = recorder.record(numframes=frames)

        if audio is None or len(audio) == 0:
            raise LocalObserverError("Local audio capture returned no samples.")

        if getattr(audio, "ndim", 1) > 1:
            audio = audio.astype(np.float32, copy=False).mean(axis=1)

        audio_rms = float(np.sqrt(np.mean(np.square(audio.astype(np.float32))))) if len(audio) else 0.0
        audio_peak = float(np.max(np.abs(audio.astype(np.float32)))) if len(audio) else 0.0

        buffer = io.BytesIO()
        soundfile.write(buffer, audio, rate, format="WAV")
        wav_bytes = buffer.getvalue()
        artifact_path = self._artifact_dir / filename
        artifact_path.write_bytes(wav_bytes)

        return {
            "audio_bytes": wav_bytes,
            "filename": filename,
            "sample_rate": rate,
            "seconds": duration,
            "artifact_path": str(artifact_path),
            "capture_mode": capture_mode,
            "audio_source": normalized_source,
            "audio_channels": int(self._audio_channels),
            "channel_layout": "stereo" if int(self._audio_channels) >= 2 else "mono",
            "device_name": str(getattr(recorder_device, "name", "") or getattr(speaker if normalized_source != "microphone" else recorder_device, "name", "") or device_hint or ""),
            "audio_rms": audio_rms,
            "audio_peak": audio_peak,
            "below_rms_threshold": audio_rms < self._audio_rms_threshold,
        }

    def _capture_audio_windows_helper(
        self,
        *,
        seconds: float,
        sample_rate: int | None,
        source: str,
        device_name: str | None,
        strict_device: bool | None,
    ) -> dict[str, Any]:
        del sample_rate  # Windows helper records with the native device format.
        normalized_source = str(source or "system").strip().lower() or "system"
        duration = max(float(seconds), 0.5)
        requested_device_name = str(device_name or "").strip()
        strict = self._strict_audio_device_selection if strict_device is None else bool(strict_device)
        helper_dir = self._artifact_dir / f"windows-one-shot-{normalized_source}-{uuid4().hex}"
        helper_dir.mkdir(parents=True, exist_ok=True)
        microphone_output_path = helper_dir / "microphone.wav"
        system_output_path = helper_dir / "system.wav"
        manifest_path = helper_dir / "manifest.json"
        log_path = helper_dir / "capture.log"

        helper_args = [
            *self.ensure_windows_audio_helper_command(),
            "record",
            "--microphone-output",
            str(microphone_output_path),
            "--system-output",
            str(system_output_path),
            "--manifest-path",
            str(manifest_path),
            "--log-path",
            str(log_path),
        ]
        microphone_device_name = str(
            (requested_device_name if normalized_source == "microphone" else "")
            or self._microphone_device_name
            or ""
        ).strip()
        speaker_device_name = str(
            (requested_device_name if normalized_source == "system" else "")
            or self._meeting_output_device_name
            or self._system_audio_device_name
            or ""
        ).strip()
        if microphone_device_name:
            helper_args.extend(["--microphone-device", microphone_device_name])
        if speaker_device_name:
            helper_args.extend(["--speaker-device", speaker_device_name])

        process = subprocess.Popen(
            helper_args,
            cwd=str(self._windows_audio_helper_project_dir),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            started_at = time.monotonic()
            while time.monotonic() - started_at < duration:
                if process.poll() is not None:
                    break
                remaining = duration - (time.monotonic() - started_at)
                time.sleep(max(min(remaining, 0.1), 0.01))
            if process.stdin is not None and not process.stdin.closed:
                try:
                    process.stdin.write(b"stop\n")
                    process.stdin.flush()
                except OSError:
                    pass
                try:
                    process.stdin.close()
                except OSError:
                    pass
            try:
                process.wait(timeout=10.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5.0)
        finally:
            if process.stdin is not None and not process.stdin.closed:
                try:
                    process.stdin.close()
                except OSError:
                    pass

        stderr_text = ""
        if process.stderr is not None:
            try:
                stderr_text = process.stderr.read().decode("utf-8", errors="replace").strip()
            except Exception:
                stderr_text = ""

        if not manifest_path.exists():
            details = stderr_text or self.tail_text_file(log_path) or "capture manifest was not produced."
            raise LocalObserverError(f"Windows native audio helper failed. {details}".strip())

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise LocalObserverError("Windows native audio helper returned malformed capture metadata.") from exc

        fatal_error = str(manifest.get("fatal_error") or "").strip()
        if process.returncode not in (0, None) or fatal_error:
            details = fatal_error or stderr_text or self.tail_text_file(log_path)
            raise LocalObserverError(f"Windows native audio helper failed. {details}".strip())

        track = dict(
            manifest.get("microphone" if normalized_source == "microphone" else "system") or {}
        )
        track_path = Path(str(track.get("path") or "")).expanduser()
        if not track_path.exists():
            raise LocalObserverError(f"Captured audio file is missing: {track_path}")

        wav_bytes = track_path.read_bytes()
        filename = "microphone-capture.wav" if normalized_source == "microphone" else "loopback-capture.wav"
        artifact_path = self._artifact_dir / filename
        artifact_path.write_bytes(wav_bytes)
        metrics = self._measure_wav_bytes(wav_bytes)
        audio_rms = metrics["rms"]
        audio_peak = metrics["peak"]
        channel_count = int(track.get("channels") or metrics["channels"] or 1)
        actual_device_name = str(
            track.get("device_name")
            or (
                microphone_device_name
                if normalized_source == "microphone"
                else speaker_device_name
            )
            or ""
        )

        if strict:
            if normalized_source == "microphone" and microphone_device_name and actual_device_name:
                if microphone_device_name.lower() not in actual_device_name.lower():
                    raise LocalObserverError(f"Configured microphone device was not found: {microphone_device_name}")
            if normalized_source == "system" and speaker_device_name and actual_device_name:
                if speaker_device_name.lower() not in actual_device_name.lower():
                    raise LocalObserverError(f"Configured system audio device was not found: {speaker_device_name}")

        return {
            "audio_bytes": wav_bytes,
            "filename": filename,
            "sample_rate": int(track.get("sample_rate") or self._audio_sample_rate),
            "seconds": max(float(track.get("seconds") or duration), 0.0),
            "artifact_path": str(artifact_path),
            "capture_mode": "windows_native_microphone" if normalized_source == "microphone" else "windows_native_system_audio",
            "audio_source": normalized_source,
            "audio_channels": channel_count,
            "channel_layout": "stereo" if channel_count >= 2 else "mono",
            "device_name": actual_device_name,
            "audio_rms": audio_rms,
            "audio_peak": audio_peak,
            "below_rms_threshold": audio_rms < self._audio_rms_threshold,
        }

    def _measure_wav_bytes(self, wav_bytes: bytes) -> dict[str, Any]:
        soundfile = self._import_module("soundfile")
        audio, _ = soundfile.read(io.BytesIO(wav_bytes), dtype="float32", always_2d=False)
        normalized = np.asarray(audio, dtype="float32")
        if normalized.ndim > 1:
            channel_count = int(normalized.shape[1])
            metrics_audio = normalized.mean(axis=1)
        else:
            channel_count = 1
            metrics_audio = normalized
        if len(metrics_audio) == 0:
            return {"rms": 0.0, "peak": 0.0, "channels": channel_count}
        return {
            "rms": float(np.sqrt(np.mean(np.square(metrics_audio)))),
            "peak": float(np.max(np.abs(metrics_audio))),
            "channels": channel_count,
        }

    def _import_module(self, name: str) -> Any:
        if importlib.util.find_spec(name) is None:
            raise LocalObserverError(f"Required local observation dependency is missing: {name}")
        module = __import__(name, fromlist=["*"])
        if name == "soundcard":
            self._patch_soundcard_numpy_compat(module)
        return module

    def _patch_soundcard_numpy_compat(self, soundcard: Any) -> None:
        try:
            mediafoundation = getattr(soundcard, "mediafoundation", None)
            if mediafoundation is None:
                mediafoundation = __import__("soundcard.mediafoundation", fromlist=["*"])
            if getattr(mediafoundation, "_delegate_numpy_binary_patch", False):
                return
            original_fromstring = np.fromstring

            def _compat_fromstring(
                data: Any,
                dtype: Any = float,
                count: int = -1,
                sep: str = "",
                *,
                like: Any = None,
            ) -> Any:
                if sep not in ("", b""):
                    kwargs: dict[str, Any] = {"dtype": dtype, "count": count, "sep": sep}
                    if like is not None:
                        kwargs["like"] = like
                    return original_fromstring(data, **kwargs)
                kwargs = {"dtype": dtype}
                if count != -1:
                    kwargs["count"] = count
                if like is not None:
                    kwargs["like"] = like
                return np.frombuffer(data, **kwargs).copy()

            mediafoundation.numpy.fromstring = _compat_fromstring
            mediafoundation._delegate_numpy_binary_patch = True
        except Exception:
            return

    def _find_window(self, gw: Any, title_fragment: str) -> Any:
        fragment = str(title_fragment or "").strip()
        if not fragment:
            raise LocalObserverError("A window title fragment is required for window observation.")
        matches = [window for window in gw.getWindowsWithTitle(fragment) if getattr(window, "width", 0) > 0 and getattr(window, "height", 0) > 0]
        if not matches:
            raise LocalObserverError(f"No visible window matched title fragment: {fragment}")
        matches.sort(key=lambda item: (item.width * item.height), reverse=True)
        return matches[0]

    def _apply_crop(self, bounds: dict[str, int], crop: dict[str, int]) -> dict[str, int]:
        left = bounds["left"] + int(crop.get("left", 0))
        top = bounds["top"] + int(crop.get("top", 0))
        width = int(crop.get("width", bounds["width"]))
        height = int(crop.get("height", bounds["height"]))
        return {
            "left": max(left, 0),
            "top": max(top, 0),
            "width": max(width, 1),
            "height": max(height, 1),
        }

    def _prepare_ocr_image(self, image: Any) -> Any:
        cv2 = self._import_module("cv2")
        pil_image_module = self._import_module("PIL.Image")
        pil_image = pil_image_module.Image if hasattr(pil_image_module, "Image") else None
        if pil_image is not None and isinstance(image, pil_image):
            np_image = np.array(image)
        else:
            np_image = np.array(image)
        gray = cv2.cvtColor(np_image, cv2.COLOR_RGB2GRAY)
        thresholded = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
        return thresholded

    def _safe_name(self, value: str) -> str:
        return "".join(char if char.isalnum() else "-" for char in value)[:80].strip("-") or "window"

    def _env_bool(self, name: str, default: bool) -> bool:
        raw = os.getenv(name)
        if raw is None:
            return default
        return raw.strip().lower() not in {"0", "false", "no", "off"}

    def _select_microphone(self, soundcard: Any, device_name: str, *, strict: bool) -> Any:
        candidates = list(getattr(soundcard, "all_microphones", lambda: [])())
        selected = self._match_named_device(candidates, device_name)
        if selected is not None:
            return selected
        if device_name and strict:
            raise LocalObserverError(f"Configured microphone device was not found: {device_name}")
        recorder_device = soundcard.default_microphone()
        if recorder_device is None:
            raise LocalObserverError("No default microphone is available for local microphone capture.")
        return recorder_device

    def _select_speaker(self, soundcard: Any, device_name: str, *, strict: bool) -> Any:
        candidates = list(getattr(soundcard, "all_speakers", lambda: [])())
        selected = self._match_named_device(candidates, device_name)
        if selected is not None:
            return selected
        if device_name and strict:
            raise LocalObserverError(f"Configured system audio device was not found: {device_name}")
        speaker = soundcard.default_speaker()
        if speaker is None:
            raise LocalObserverError("No default system speaker is available for loopback capture.")
        return speaker

    def _match_named_device(self, candidates: list[Any], device_name: str) -> Any | None:
        hint = str(device_name or "").strip().lower()
        if not hint:
            return None
        exact = [device for device in candidates if str(getattr(device, "name", "")).strip().lower() == hint]
        if exact:
            return exact[0]
        partial = [device for device in candidates if hint in str(getattr(device, "name", "")).strip().lower()]
        if partial:
            return partial[0]
        return None

    def _match_named_descriptor(self, candidates: list[dict[str, Any]], device_name: str) -> dict[str, Any] | None:
        hint = str(device_name or "").strip().lower()
        if not hint:
            return None
        exact = [device for device in candidates if str(device.get("name") or "").strip().lower() == hint]
        if exact:
            return exact[0]
        partial = [device for device in candidates if hint in str(device.get("name") or "").strip().lower()]
        if partial:
            return partial[0]
        return None

    def _device_descriptors(self, devices: list[Any]) -> list[dict[str, Any]]:
        descriptors: list[dict[str, Any]] = []
        for device in devices:
            descriptors.append(
                {
                    "name": str(getattr(device, "name", "") or ""),
                    "id": str(getattr(device, "id", "") or ""),
                }
            )
        return descriptors
