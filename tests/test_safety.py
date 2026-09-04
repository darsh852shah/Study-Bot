import importlib
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

# These modules read configuration at import time.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "123")
os.environ.setdefault("NOTION_API_KEY", "test-notion")
os.environ.setdefault("NOTION_DATABASE_ID", "test-database")
os.environ.setdefault("GROQ_API_KEY", "test-groq")
os.environ.setdefault("TELEGRAM_WEBHOOK_SECRET", "test-secret")

import notion_helper


class LogValidationTests(unittest.TestCase):
    def test_accepts_sensible_log_values(self):
        notion_helper.validate_log_fields("AFM:1.5, FR:2", 4, "3")

    def test_rejects_invalid_hours_and_scores(self):
        for breakdown, mood, energy in [
            ("AFM:-1", 3, 3),
            ("AFM:nan", 3, 3),
            ("AFM:25", 3, 3),
            ("AFM:12,FR:13", 3, 3),
            ("AFM:1", 0, 3),
            ("AFM:1", 3, 5.5),
        ]:
            with self.subTest(breakdown=breakdown, mood=mood, energy=energy):
                with self.assertRaises(ValueError):
                    notion_helper.validate_log_fields(breakdown, mood, energy)

    @patch("notion_helper.requests.post")
    def test_notion_writes_use_timeout(self, post):
        post.return_value = Mock(raise_for_status=Mock(), json=Mock(return_value={}))
        notion_helper.create_log_entry("AFM:1", 3, 4, "", "", "")
        self.assertEqual(post.call_args.kwargs["timeout"], notion_helper.REQUEST_TIMEOUT)


class WebhookSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory()
        os.environ["STUDY_BOT_STATE_FILE"] = os.path.join(cls.tempdir.name, "state.json")
        import app
        cls.app_module = importlib.reload(app)
        cls.client = cls.app_module.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.tempdir.cleanup()

    def post(self, body, secret="test-secret"):
        return self.client.post(
            "/telegram-webhook", json=body,
            headers={"X-Telegram-Bot-Api-Secret-Token": secret},
        )

    def test_rejects_bad_secret_and_malformed_update(self):
        self.assertEqual(self.post({}, secret="wrong").status_code, 403)
        self.assertEqual(self.post({"update_id": 10}).status_code, 200)
        self.assertEqual(self.post(["not", "an", "update"]).status_code, 200)

    @patch("app.threading.Thread")
    def test_duplicate_update_is_not_started_twice(self, thread):
        update = {"update_id": 77, "message": {"chat": {"id": 123}, "text": "hello"}}
        self.assertEqual(self.post(update).status_code, 200)
        self.assertEqual(self.post(update).status_code, 200)
        self.assertEqual(thread.call_count, 1)


if __name__ == "__main__":
    unittest.main()
