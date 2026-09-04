import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

# Support both ``python -m unittest discover`` and direct execution from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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

    def test_load_state_discards_wrong_field_types(self):
        state_path = os.environ["STUDY_BOT_STATE_FILE"]
        with open(state_path, "w", encoding="utf-8") as state_file:
            state_file.write('{"pending": null, "chat_history": "bad", "processed_updates": "bad"}')
        state = self.app_module.load_state()
        self.assertEqual(state["chat_history"], [])
        self.assertEqual(state["processed_updates"], [])

    @patch("app.poll_log.process_update", return_value={"breakdown": "AFM:2"})
    def test_clear_log_reaches_log_flow_when_classifier_service_is_unavailable(self, process_update):
        self.app_module.STATE["pending"] = None
        update = {"update_id": 78, "message": {"chat": {"id": 123}, "text": "Studied AFM for 2 hours."}}

        self.app_module.route_update(update)

        process_update.assert_called_once_with(update, None, incoming_text="Studied AFM for 2 hours.")

class MemorySafetyTests(unittest.TestCase):
    def test_only_long_term_messages_trigger_memory_extraction(self):
        from query_handler import _is_memory_worthy

        self.assertTrue(_is_memory_worthy("I prefer studying at night because it is quieter."))
        self.assertTrue(_is_memory_worthy("My goal is to finish FR by November."))
        self.assertFalse(_is_memory_worthy("Thanks"))
        self.assertFalse(_is_memory_worthy("I studied AFM for two hours today."))

    @patch("query_handler.save_memory")
    @patch("query_handler.generate_text")
    def test_memory_extraction_deduplicates_and_preserves_source(self, generate_text, save_memory):
        from query_handler import _extract_and_save_memories
        from llm_helper import MEMORY_MODEL

        generate_text.return_value = (
            '[{"memory":"Prefers studying at night.","category":"preference",'
            '"source":"user-stated"},'
            '{"memory":"Prefers studying at night.","category":"preference",'
            '"source":"user-stated"}]'
        )
        _extract_and_save_memories("I prefer studying at night.", "Noted.", [])

        save_memory.assert_called_once_with(
            "Prefers studying at night.", category="preference", source="user-stated"
        )
        self.assertEqual(generate_text.call_args.kwargs["model"], MEMORY_MODEL)
        self.assertEqual(generate_text.call_args.kwargs["max_tokens"], 300)


class IntentClassificationTests(unittest.TestCase):
    def test_clear_free_text_log_skips_remote_classifier(self):
        from llm_helper import classify_intent

        with patch("llm_helper.generate_text") as generate_text:
            self.assertEqual(classify_intent("Studied AFM for 2 hours, mood 4 and energy 3."), "log")
        generate_text.assert_not_called()

    def test_study_question_remains_a_query(self):
        from llm_helper import classify_intent

        with patch("llm_helper.generate_text") as generate_text:
            self.assertEqual(classify_intent("I studied AFM for 2 hours today, is that enough?"), "query")
        generate_text.assert_not_called()


if __name__ == "__main__":
    unittest.main()
