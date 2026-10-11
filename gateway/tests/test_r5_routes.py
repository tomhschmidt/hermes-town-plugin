"""R5 — dashboard plumbing routes: /cron-runs response extraction, _public_item
source fields, and the background /curate trigger (synthetic data only)."""

import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

import support

PROMPT_FILLER = "Summarize the inbox for Alice. Keep it short and friendly.\n" * 800


def run_doc(response=None, *, prompt=PROMPT_FILLER, name="Morning briefing"):
    """A cron run document shaped like the scheduler's (header, prompt, response)."""
    doc = (
        f"# Cron Job: {name}\n\n"
        "**Job ID:** job0000briefing\n"
        "**Run Time:** 2026-10-10 08:15:00\n"
        "**Schedule:** 15 8 * * *\n\n"
        f"**Prompt Characters:** {len(prompt)}\n"
        f"## Prompt\n\n{prompt}\n\n"
    )
    if response is not None:
        doc += f"**Response Characters:** {len(response)}\n## Response\n\n{response}\n"
    return doc


def stub_modules(*, jobs, output_dir=None, execute=None):
    """sys.modules stand-ins for hermes-agent's cron.jobs and tools.cronjob_tools."""
    cron_pkg = types.ModuleType("cron")
    cron_jobs = types.ModuleType("cron.jobs")
    cron_jobs.list_jobs = lambda include_disabled=False: list(jobs)
    cron_jobs.get_cron_output_dir = lambda: output_dir
    tools_pkg = types.ModuleType("tools")
    cronjob_tools = types.ModuleType("tools.cronjob_tools")
    cronjob_tools._execute_job_now = execute or (lambda job: {"claimed": True, "success": True})
    return mock.patch.dict(
        sys.modules,
        {
            "cron": cron_pkg,
            "cron.jobs": cron_jobs,
            "tools": tools_pkg,
            "tools.cronjob_tools": cronjob_tools,
        },
    )


class RunDocExtractionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.api = support.load_plugin_api(self.tmp)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, text, name="2026-10-10_08-15-00.md", directory=None):
        directory = directory or self.tmp
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_long_prompt_yields_only_the_response(self):
        response = "🌅 **Morning briefing**\n\n• **9:00 AM** - Standup with Bob"
        path = self.write(run_doc(response))
        self.assertGreater(path.stat().st_size, 40_000)
        output = self.api._run_doc_response(path)
        self.assertEqual(output, response)
        self.assertNotIn("Cron Job", output)
        self.assertNotIn("Summarize the inbox", output)
        self.assertNotIn("Response Characters", output)

    def test_last_response_heading_wins(self):
        prompt = (
            "Format your reply like this example:\n\n"
            "## Response\n\nExample answer for Carol\n\n" + PROMPT_FILLER
        )
        path = self.write(run_doc("The real answer for Alice", prompt=prompt))
        self.assertEqual(self.api._run_doc_response(path), "The real answer for Alice")

    def test_heading_must_start_a_line(self):
        path = self.write(run_doc("Inline ## Response markers are prose, not headings"))
        self.assertEqual(
            self.api._run_doc_response(path),
            "Inline ## Response markers are prose, not headings",
        )

    def test_response_outside_the_first_tail_window_is_found(self):
        self.api._RUN_DOC_TAIL_BYTES = 64
        response = "Line for Bob\n" * 40
        path = self.write(run_doc(response))
        self.assertEqual(self.api._run_doc_response(path), response.strip())

    def test_doc_without_response_section_yields_empty(self):
        script_doc = (
            "# Cron Job: town-gmail-scan\n\n"
            "**Job ID:** job0000scan\n"
            "**Run Time:** 2026-10-10 08:15:00\n"
            "**Mode:** no_agent (script)\n\n---\n\n"
            '{"candidates": [{"headline": "Invoice from Example Co"}]}\n'
        )
        self.assertEqual(self.api._run_doc_response(self.write(script_doc)), "")
        self.assertEqual(self.api._run_doc_response(self.write("", name="empty.md")), "")
        self.assertEqual(self.api._run_doc_response(self.tmp / "missing.md"), "")

    def test_long_response_is_capped(self):
        response = "Alice needs the deck. " * 400
        output = self.api._run_doc_response(self.write(run_doc(response)))
        self.assertLessEqual(len(output), 2000)
        self.assertTrue(output.startswith("Alice needs the deck."))
        self.assertTrue(output.endswith("…"))

    def test_cron_runs_route_returns_response_and_delivery(self):
        job_dir = self.tmp / "output" / "job0000briefing"
        self.write(run_doc("Older answer"), "2026-10-09_08-15-00.md", job_dir)
        self.write(run_doc("Newest answer for Alice"), "2026-10-10_08-15-00.md", job_dir)
        job = {
            "id": "job0000briefing",
            "name": "Morning briefing",
            "last_status": "ok",
            "deliver": "telegram:-100500",
        }
        with stub_modules(jobs=[job], output_dir=self.tmp / "output"):
            result = self.api.get_cron_runs("job0000briefing")
        newest, older = result["runs"]
        self.assertEqual(newest["output"], "Newest answer for Alice")
        self.assertEqual(newest["status"], "ok")
        self.assertIs(newest["delivered"], True)
        self.assertEqual(older["output"], "Older answer")
        self.assertEqual(older["status"], "unknown")
        self.assertNotIn("delivered", older)

    def test_delivery_is_only_reported_when_derivable(self):
        self.assertIs(
            self.api._run_delivered(
                {"last_status": "ok", "deliver": "telegram", "last_delivery_error": "chat not found"}
            ),
            False,
        )
        self.assertIs(self.api._run_delivered({"last_status": "delivery_failed"}), False)
        self.assertIsNone(self.api._run_delivered({"last_status": "ok", "deliver": "local"}))
        self.assertIsNone(self.api._run_delivered({"last_status": "ok", "deliver": "origin"}))
        self.assertIsNone(self.api._run_delivered({"last_status": "error", "deliver": "telegram"}))


class PublicItemTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.api = support.load_plugin_api(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def base_item(self, **extra):
        item = {
            "id": "item-1",
            "headline": "Weekend plans",
            "summary": "Bob asked whether Saturday still works.",
            "source": "telegram:-100500",
            "score": 50.0,
            "created_at": "2026-10-10T08:00:00+00:00",
        }
        item.update(extra)
        return item

    def test_telegram_fields_pass_through(self):
        public = self.api._public_item(
            self.base_item(
                chat_name="Weekend Crew",
                last_sender="Bob Example",
                user_spoke_last=True,
                chat_type="group",
                thread_id=4242,
            )
        )
        self.assertEqual(public["chat_name"], "Weekend Crew")
        self.assertEqual(public["last_sender"], "Bob Example")
        self.assertIs(public["user_spoke_last"], True)
        self.assertEqual(public["chat_type"], "group")
        self.assertEqual(public["thread_id"], "4242")

    def test_gmail_fields_pass_through(self):
        public = self.api._public_item(
            self.base_item(
                source="gmail:alice@example.com:18c0ffee",
                account="alice@example.com",
                thread_id="18c0ffee",
                user_spoke_last=False,
            )
        )
        self.assertEqual(public["account"], "alice@example.com")
        self.assertEqual(public["thread_id"], "18c0ffee")
        self.assertIs(public["user_spoke_last"], False)
        self.assertNotIn("chat_name", public)

    def test_absent_or_blank_fields_are_omitted(self):
        public = self.api._public_item(self.base_item(chat_name="  ", user_spoke_last="yes"))
        for field in ("chat_name", "last_sender", "user_spoke_last", "chat_type", "account", "thread_id"):
            self.assertNotIn(field, public)
        self.assertEqual(public["headline"], "Weekend plans")


CURATE_JOB = {
    "id": "job0000curate",
    "name": "town-feed-curate",
    "script": "town-curate.py",
    "no_agent": True,
    "enabled": True,
}
SCANNER_JOB = {"id": "job0000scan", "name": "town-gmail-scan", "script": "town-gmail-scan.py"}


class CurateTriggerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.api = support.load_plugin_api(Path(self._tmp.name))
        self.heuristic_calls = []
        self.api.run_curator = lambda *args, **kwargs: self.heuristic_calls.append((args, kwargs))
        with self.api._process_lock():
            state = self.api._read_state_unlocked()
            state["last_curated_at"] = "2026-10-10T08:00:00+00:00"
            self.api._write_state_unlocked(state)
        self.release = threading.Event()
        self.started = threading.Event()
        self.executed = []

    def tearDown(self):
        self.release.set()
        thread = self.api._curate_trigger_thread
        if thread is not None:
            thread.join(timeout=5)
        self._tmp.cleanup()

    def blocking_execute(self, job):
        self.executed.append((job["id"], threading.current_thread()))
        self.started.set()
        self.release.wait(timeout=5)
        return {"claimed": True, "success": True}

    def test_curate_triggers_the_job_on_a_background_thread(self):
        with stub_modules(jobs=[SCANNER_JOB, CURATE_JOB], execute=self.blocking_execute):
            result = self.api.curate_feed()
            self.assertTrue(self.started.wait(timeout=5))
            # Returned while the executor is still blocked: not run inline.
            self.assertFalse(self.release.is_set())
            self.assertEqual(
                result,
                {
                    "ok": True,
                    "triggered": True,
                    "since": self.api._iso_timestamp(
                        self.api._parse_timestamp("2026-10-10T08:00:00+00:00")
                    ),
                },
            )
            job_id, worker = self.executed[0]
            self.assertEqual(job_id, "job0000curate")
            self.assertIsNot(worker, threading.current_thread())
            self.assertEqual(worker.name, "town-feed-curate-trigger")

            second = self.api.curate_feed()
            self.assertEqual(second["ok"], True)
            self.assertIs(second["triggered"], False)
            self.assertEqual(len(self.executed), 1)

            self.release.set()
            self.api._curate_trigger_thread.join(timeout=5)
            self.started.clear()
            self.release.clear()
            third = self.api.curate_feed()
            self.assertIs(third["triggered"], True)
            self.assertTrue(self.started.wait(timeout=5))
        self.assertEqual(self.heuristic_calls, [])

    def test_curate_matches_the_job_by_script_name(self):
        renamed = dict(CURATE_JOB, name="Feed curator", script="/opt/example/scripts/town-curate.py")
        with stub_modules(jobs=[renamed], execute=self.blocking_execute):
            result = self.api.curate_feed()
            self.assertTrue(self.started.wait(timeout=5))
        self.assertIs(result["triggered"], True)
        self.assertEqual(self.executed[0][0], "job0000curate")

    def test_curate_without_job_reports_error(self):
        with stub_modules(jobs=[SCANNER_JOB], execute=self.blocking_execute):
            result = self.api.curate_feed()
        self.assertIs(result["ok"], False)
        self.assertIn("town-feed-curate", result["error"])
        self.assertEqual(self.executed, [])
        self.assertEqual(self.heuristic_calls, [])

    def test_paused_curate_job_reports_error(self):
        paused = dict(CURATE_JOB, enabled=False)
        with stub_modules(jobs=[paused], execute=self.blocking_execute):
            result = self.api.curate_feed()
        self.assertIs(result["ok"], False)
        self.assertIn("paused", result["error"])
        self.assertEqual(self.executed, [])

    def test_since_is_null_before_first_curation(self):
        with self.api._process_lock():
            state = self.api._read_state_unlocked()
            state["last_curated_at"] = None
            self.api._write_state_unlocked(state)
        with stub_modules(jobs=[CURATE_JOB], execute=self.blocking_execute):
            result = self.api.curate_feed()
            self.assertTrue(self.started.wait(timeout=5))
        self.assertIsNone(result["since"])


if __name__ == "__main__":
    unittest.main()
