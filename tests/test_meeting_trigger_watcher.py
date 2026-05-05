import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zoom_meeting_bot_cli.meeting_trigger_watcher import (
    _infer_zoom_join_details,
    normalize_zoom_join_input,
)


class MeetingTriggerWatcherTest(unittest.TestCase):
    def test_normalize_zoom_join_input_from_join_url(self) -> None:
        payload = normalize_zoom_join_input("https://us06web.zoom.us/j/12345678901?pwd=abc123")

        self.assertEqual(payload["kind"], "join_url")
        self.assertEqual(payload["join_url"], "https://us06web.zoom.us/j/12345678901?pwd=abc123")
        self.assertEqual(payload["meeting_number"], "12345678901")
        self.assertEqual(payload["passcode"], "abc123")

    def test_normalize_zoom_join_input_from_meeting_number(self) -> None:
        payload = normalize_zoom_join_input("123 4567 8901")

        self.assertEqual(payload["kind"], "meeting_number")
        self.assertEqual(payload["join_url"], "https://zoom.us/j/12345678901")
        self.assertEqual(payload["meeting_number"], "12345678901")
        self.assertEqual(payload["passcode"], "")

    def test_infer_zoom_join_details_from_zoommtg_protocol(self) -> None:
        payload = _infer_zoom_join_details(
            'Zoom.exe --url="zoommtg://zoom.us/join?action=join&confno=98765432109&pwd=qwerty"'
        )

        self.assertEqual(payload["join_url"], "https://zoom.us/j/98765432109?pwd=qwerty")
        self.assertEqual(payload["meeting_number"], "98765432109")
        self.assertEqual(payload["passcode"], "qwerty")

    def test_infer_zoom_join_details_from_https_command_line(self) -> None:
        payload = _infer_zoom_join_details(
            'C:\\\\Program Files\\\\Zoom\\\\bin\\\\Zoom.exe https://us06web.zoom.us/j/24681012141?pwd=hello1'
        )

        self.assertEqual(payload["join_url"], "https://us06web.zoom.us/j/24681012141?pwd=hello1")
        self.assertEqual(payload["meeting_number"], "24681012141")
        self.assertEqual(payload["passcode"], "hello1")


if __name__ == "__main__":
    unittest.main()
