import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from local_meeting_ai_runtime import storage as runtime_storage
from lush_local_ai_launcher import launcher as launcher_module


def _winerror_5() -> PermissionError:
    error = PermissionError("access denied")
    error.winerror = 5
    return error


class AtomicJsonRetryTests(unittest.TestCase):
    def test_runtime_storage_retries_winerror_5_replace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.json"
            payload = {"session_id": "abc123"}
            real_replace = runtime_storage.os.replace
            attempts = {"count": 0}

            def flaky_replace(src: str, dst: Path) -> None:
                attempts["count"] += 1
                if attempts["count"] < 3:
                    raise _winerror_5()
                real_replace(src, dst)

            with (
                mock.patch.object(runtime_storage.os, "replace", side_effect=flaky_replace),
                mock.patch.object(runtime_storage.time, "sleep") as sleep_mock,
            ):
                runtime_storage._write_json_atomic(path, payload)

            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), payload)
            self.assertEqual(attempts["count"], 3)
            self.assertEqual(sleep_mock.call_count, 2)

    def test_launcher_state_writer_retries_winerror_5_replace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "launcher-state.json"
            payload = {"status": "running"}
            real_replace = launcher_module.os.replace
            attempts = {"count": 0}

            def flaky_replace(src: str, dst: Path) -> None:
                attempts["count"] += 1
                if attempts["count"] < 4:
                    raise _winerror_5()
                real_replace(src, dst)

            with (
                mock.patch.object(launcher_module.os, "replace", side_effect=flaky_replace),
                mock.patch.object(launcher_module.time, "sleep") as sleep_mock,
            ):
                launcher_module._write_json_atomic(path, payload)

            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), payload)
            self.assertEqual(attempts["count"], 4)
            self.assertEqual(sleep_mock.call_count, 3)


if __name__ == "__main__":
    unittest.main()
