"""R2 — cron scripts run on Hermes' managed Python (GROUNDING pack).

Every fixture here is synthetic. No test reads or writes live ``~/.hermes``
state, calls Google, or reaches an LLM: scripts load with ``HERMES_HOME`` in a
temp dir, Gmail is an in-memory fake, and the classifier is a stub.
"""

from __future__ import annotations

import ast
import base64
import contextlib
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import tokenize
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest import mock

import support

CHANGED_SCRIPTS = ("town-gmail-scan.py", "town-calendar-scan.py", "town-curate.py")
ACCOUNT = "alice@example.com"
PYDANTIC_ERROR = "No module named 'pydantic_core._pydantic_core'"
RECOVERY_MARKERS = (".update-incomplete", ".lazy-refresh-incomplete")
CLASSIFIER_FAILURE = (
    f"labeler: classifier failed: Town inbox classifier failed: {PYDANTIC_ERROR}"
)


def _load_sandboxed(filename: str, home: Path) -> ModuleType:
    with mock.patch.dict(os.environ, {"HERMES_HOME": str(home)}):
        return support.load_script(filename)


def _code_tokens(path: Path) -> str:
    """Source text with comments dropped (a comment naming .venv is fine)."""
    tokens = tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline)
    return " ".join(token.string for token in tokens if token.type != tokenize.COMMENT)


def _import_lines(tree: ast.AST, *roots: str) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names = [node.module]
        if any(name == root or name.startswith(f"{root}.") for name in names for root in roots):
            lines.append(node.lineno)
    return lines


def _fake_pm_layout(home: Path, agent_root: Path, version: tuple[int, int]) -> Path:
    """A committed PM environment record for ``agent_root`` under ``home``."""
    from pm.environments import install_key

    state = home / "installs" / install_key(agent_root)
    environment = state / "environments" / "generation-1" / "venv"
    (environment / "lib" / f"python{version[0]}.{version[1]}" / "site-packages").mkdir(parents=True)
    (environment / "bin").mkdir()
    (environment / "pyvenv.cfg").write_text(
        f"version_info = {version[0]}.{version[1]}.0\n", encoding="utf-8"
    )
    (state / "facts.json").write_text(
        json.dumps({"packages": {"venv": {"environment": str(environment)}}}),
        encoding="utf-8",
    )
    return environment.resolve()


class StaticRuntimeTest(unittest.TestCase):
    def test_no_script_references_a_venv_interpreter_or_execv(self) -> None:
        scripts = sorted(support.SCRIPTS.glob("*.py"))
        self.assertTrue(scripts)
        for path in scripts:
            with self.subTest(path.name):
                code = _code_tokens(path)
                self.assertNotIn(".venv", code)
                self.assertNotRegex(code, r"""['"/]venv['"/]""")
                self.assertNotIn("execv", code)

    def test_hermes_bootstrap_precedes_agent_and_dependency_imports(self) -> None:
        for path in sorted(support.SCRIPTS.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            dependent = _import_lines(tree, "agent", "google", "googleapiclient")
            if not dependent:
                continue
            with self.subTest(path.name):
                bootstrap = _import_lines(tree, "hermes_bootstrap")
                self.assertTrue(bootstrap, "imports agent/Google modules without hermes_bootstrap")
                path_inserts = [
                    node.lineno
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Call)
                    and ast.unparse(node.func) == "sys.path.insert"
                    and "HERMES_AGENT" in ast.unparse(node)
                ]
                self.assertTrue(path_inserts, "hermes-agent checkout never put on sys.path")
                self.assertLess(min(path_inserts), min(bootstrap))
                self.assertLess(min(bootstrap), min(dependent))


class ManagedInterpreterTest(unittest.TestCase):
    def test_import_under_managed_interpreter_never_reexecs(self) -> None:
        # Default HERMES_HOME: the guard checks this interpreter against the
        # PM environment that cron uses (read-only); hermes_bootstrap is the
        # harness stand-in, so nothing is activated.
        for filename in CHANGED_SCRIPTS:
            with self.subTest(filename), mock.patch(
                "os.execv", side_effect=AssertionError("execv")
            ) as execv, mock.patch("os.execve", side_effect=AssertionError("execve")) as execve:
                module = support.load_script(filename)
                execv.assert_not_called()
                execve.assert_not_called()
                self.assertIsNone(module._RUNTIME_ERROR)
                self.assertIs(module.hermes_bootstrap, sys.modules["hermes_bootstrap"])

    @unittest.skipUnless(support.HERMES_AGENT.is_dir(), "needs the hermes-agent checkout")
    def test_real_bootstrap_runs_in_process_under_cron_environment(self) -> None:
        agent = support.HERMES_AGENT.resolve()
        if any((agent / marker).exists() for marker in RECOVERY_MARKERS):
            self.skipTest("hermes-agent has an unfinished update; bootstrap would repair it")
        snippet = (
            "import importlib.util, json, os, sys\n"
            "calls = []\n"
            "def refuse(*args, **kwargs):\n"
            "    calls.append(str(args[:1]))\n"
            "    raise AssertionError('re-exec attempted')\n"
            "os.execv = os.execve = os.execvp = os.execvpe = refuse\n"
            "errors = {}\n"
            "for path in sys.argv[1:]:\n"
            "    name = 'r2_' + os.path.basename(path)[:-3].replace('-', '_')\n"
            "    spec = importlib.util.spec_from_file_location(name, path)\n"
            "    module = importlib.util.module_from_spec(spec)\n"
            "    spec.loader.exec_module(module)\n"
            "    errors[os.path.basename(path)] = module._RUNTIME_ERROR\n"
            "bootstrap = sys.modules.get('hermes_bootstrap')\n"
            "print(json.dumps({'execs': calls, 'errors': errors,\n"
            "                  'bootstrap': getattr(bootstrap, '__file__', None)}))\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "hermes-agent").symlink_to(agent, target_is_directory=True)
            env = {
                **os.environ,
                "HERMES_HOME": str(home),
                # Cron's launch contract for scripts (cron/scheduler_script.py).
                "HERMES_DISABLE_LAZY_INSTALLS": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            proc = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    snippet,
                    *(str(support.SCRIPTS / name) for name in CHANGED_SCRIPTS),
                ],
                env=env,
                cwd=tmp,
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            result = json.loads(proc.stdout.strip().splitlines()[-1])
            # The real module, reached through the sandbox's hermes-agent link.
            bootstrap = Path(result["bootstrap"]).resolve()
        self.assertEqual(result["execs"], [])
        self.assertEqual(result["errors"], {name: None for name in CHANGED_SCRIPTS})
        self.assertEqual(bootstrap, agent / "hermes_bootstrap.py")


class ForeignInterpreterTest(unittest.TestCase):
    def test_guard_names_the_managed_interpreter_on_version_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            module = _load_sandboxed("town-gmail-scan.py", home)
            environment = _fake_pm_layout(home, module.HERMES_AGENT, (3, 99))
            with mock.patch.dict(os.environ, {"HERMES_HOME": str(home)}):
                error = module._foreign_python_error()
        self.assertIsNotNone(error)
        self.assertNotIn("\n", error)
        self.assertIn("(Python 3.99)", error)
        self.assertIn(
            f"run: {environment / 'bin' / 'python'} {Path(module.__file__).resolve()}", error
        )

    def test_guard_accepts_the_matching_interpreter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            module = _load_sandboxed("town-curate.py", home)
            _fake_pm_layout(home, module.HERMES_AGENT, tuple(sys.version_info[:2]))
            with mock.patch.dict(os.environ, {"HERMES_HOME": str(home)}):
                self.assertIsNone(module._foreign_python_error())

    def test_guard_without_pm_api_still_names_a_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = _load_sandboxed("town-calendar-scan.py", Path(tmp))
            with mock.patch.dict(sys.modules, {"pm.environments": None}):
                error = module._foreign_python_error((3, 9))
        self.assertIsNotNone(error)
        self.assertNotIn("\n", error)
        self.assertTrue(error.startswith("Python 3.9 cannot load Hermes"))
        self.assertTrue(error.endswith("run it the way cron does: hermes cron run <job_id>"))

    @unittest.skipUnless(support.HERMES_AGENT.is_dir(), "needs the hermes-agent checkout")
    def test_manual_run_on_foreign_interpreter_fails_fast_with_one_line(self) -> None:
        expected_stdout = {
            "town-gmail-scan.py": lambda out: json.loads(out)["watermarks_advanced"] is False
            and json.loads(out)["candidates"] == [],
            "town-curate.py": lambda out: json.loads(out)["ok"] is False,
            "town-calendar-scan.py": lambda out: out == "",
        }
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            agent_link = home / "hermes-agent"
            agent_link.symlink_to(support.HERMES_AGENT.resolve(), target_is_directory=True)
            # This interpreter is "foreign" to a PM environment built for 3.99.
            environment = _fake_pm_layout(home, agent_link, (3, 99))
            env = {
                **os.environ,
                "HERMES_HOME": str(home),
                "HERMES_DISABLE_LAZY_INSTALLS": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            for filename in CHANGED_SCRIPTS:
                with self.subTest(filename):
                    script = support.SCRIPTS / filename
                    proc = subprocess.run(
                        [sys.executable, str(script)],
                        env=env,
                        cwd=tmp,
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                    self.assertEqual(proc.returncode, 3)
                    stderr = proc.stderr.strip().splitlines()
                    self.assertEqual(len(stderr), 1, proc.stderr)
                    self.assertTrue(stderr[0].startswith(f"{filename[:-3]}: Python "))
                    self.assertTrue(
                        stderr[0].endswith(
                            f"run: {environment / 'bin' / 'python'} {script.resolve()}"
                        ),
                        stderr[0],
                    )
                    self.assertTrue(expected_stdout[filename](proc.stdout.strip()), proc.stdout)


# --- Gmail watermark hold -------------------------------------------------


def _message(
    message_id: str, internal_date: int, subject: str, *, starred: bool = False
) -> dict[str, Any]:
    body = base64.urlsafe_b64encode(
        f"Example News weekly digest: {subject}. Read more on the website.".encode()
    ).decode()
    return {
        "id": message_id,
        "threadId": f"thread-{message_id}",
        "internalDate": str(internal_date),
        "labelIds": ["INBOX", "UNREAD", *(["STARRED"] if starred else [])],
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "Example News <news@example.com>"},
                {"name": "To", "value": ACCOUNT},
                {"name": "Subject", "value": subject},
                {"name": "List-Unsubscribe", "value": "<mailto:leave@example.com>"},
            ],
            "body": {"data": body},
        },
    }


class _Call:
    def __init__(self, result: Any) -> None:
        self._result = result

    def execute(self) -> Any:
        return copy.deepcopy(self._result)


class _Messages:
    def __init__(self, gmail: "FakeGmail") -> None:
        self.gmail = gmail

    def list(
        self, userId: str, q: str = "", maxResults: int = 100, labelIds: Any = None, **_: Any
    ) -> _Call:
        if labelIds or not q.startswith("in:inbox"):
            return _Call({"messages": []})
        newest_first = sorted(
            self.gmail.store.values(),
            key=lambda message: int(message["internalDate"]),
            reverse=True,
        )
        return _Call({"messages": [{"id": message["id"]} for message in newest_first][:maxResults]})

    def get(self, userId: str, id: str, format: str = "full") -> _Call:
        return _Call(self.gmail.store[id])

    def modify(self, userId: str, id: str, body: dict[str, Any]) -> _Call:
        self.gmail.modified.append((id, copy.deepcopy(body)))
        labels = self.gmail.store[id]["labelIds"]
        labels.extend(label for label in body.get("addLabelIds", []) if label not in labels)
        for label in body.get("removeLabelIds", []):
            if label in labels:
                labels.remove(label)
        return _Call({"id": id})


class _Labels:
    def __init__(self, gmail: "FakeGmail") -> None:
        self.gmail = gmail

    def list(self, userId: str) -> _Call:
        return _Call({"labels": self.gmail.label_list})

    def get(self, userId: str, id: str) -> _Call:
        return _Call(next(label for label in self.gmail.label_list if label["id"] == id))

    def create(self, userId: str, body: dict[str, Any]) -> _Call:
        label = {**body, "id": f"Label_new_{len(self.gmail.label_list)}"}
        self.gmail.label_list.append(label)
        return _Call(label)

    def patch(self, userId: str, id: str, body: dict[str, Any]) -> _Call:
        return _Call({"id": id})


class _Threads:
    def __init__(self, gmail: "FakeGmail") -> None:
        self.gmail = gmail

    def get(self, userId: str, id: str, format: str = "full") -> _Call:
        return _Call({"messages": [m for m in self.gmail.store.values() if m["threadId"] == id]})


class FakeGmail:
    """The slice of the Gmail v1 client that scan_gmail/label_gmail_messages use."""

    def __init__(self, messages: list[dict[str, Any]], labels: dict[str, dict[str, str]]) -> None:
        self.store = {message["id"]: message for message in messages}
        self.label_list = [
            {"id": f"Label_{index}", "name": name, "type": "user", "color": dict(color)}
            for index, (name, color) in enumerate(labels.items())
        ]
        self.modified: list[tuple[str, dict[str, Any]]] = []

    def users(self) -> "FakeGmail":
        return self

    def getProfile(self, userId: str) -> _Call:
        return _Call({"emailAddress": ACCOUNT})

    def messages(self) -> _Messages:
        return _Messages(self)

    def labels(self) -> _Labels:
        return _Labels(self)

    def threads(self) -> _Threads:
        return _Threads(self)

    def label_name(self, label_id: str) -> str:
        return next(label["name"] for label in self.label_list if label["id"] == label_id)

    def content_labels(self) -> dict[str, list[str]]:
        applied: dict[str, list[str]] = {}
        for message_id, body in self.modified:
            for label_id in body.get("addLabelIds", []):
                applied.setdefault(message_id, []).append(self.label_name(label_id))
        return applied


class _Credentials:
    valid = True
    expired = False
    refresh_token = None
    granted_scopes = ["https://www.googleapis.com/auth/gmail.readonly"]

    @classmethod
    def from_authorized_user_file(cls, path: str, scopes: Any = None) -> "_Credentials":
        return cls()


def _failing_call_llm(**_: Any) -> Any:
    raise ModuleNotFoundError(PYDANTIC_ERROR)


def _labeling_call_llm(*, messages: list[dict[str, str]], **_: Any) -> Any:
    rows = json.loads(messages[0]["content"].split("Input:\n", 1)[1])
    content = json.dumps(
        {
            "items": [
                {"message_ref": row["message_ref"], "label": "updates", "confidence": 0.9}
                for row in rows
            ]
        }
    )
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class GmailWatermarkHoldTest(unittest.TestCase):
    WATERMARK = 1_000_000

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.script = _load_sandboxed("town-gmail-scan.py", self.home)
        self.assertEqual(self.script.STATE_PATH.parent, self.home / "town-feed")
        token = self.home / "google_token.json"
        token.write_text("{}", encoding="utf-8")
        self.script.STATE_PATH.parent.mkdir(parents=True)
        self.script.STATE_PATH.write_text(
            json.dumps(
                {
                    "version": 1,
                    "source_state": {"gmail": {ACCOUNT: self.WATERMARK}, "gmail_sent": {}},
                    "lookback_seen": {ACCOUNT: []},
                    "migrations": {"feed_state_gmail_v1": {"completed_at": "2026-01-01T00:00:00Z"}},
                }
            ),
            encoding="utf-8",
        )
        load_scanner = self.script._load_installed_google_scan

        def load_repo_scanner() -> Any:
            scanner = load_scanner()
            scanner._gmail_token_paths = lambda: [token]
            scanner.get_town_inbox_settings = lambda: (
                True,
                scanner.MIN_LABEL_CONFIDENCE,
                dict(scanner.DEFAULT_INBOX_TAXONOMY),
            )
            return scanner

        patches = [
            mock.patch.object(self.script, "PLUGIN_PATH", support.DASHBOARD / "google_scan.py"),
            mock.patch.object(self.script, "_load_installed_google_scan", load_repo_scanner),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.gmail: FakeGmail | None = None

    def _gmail(self, messages: list[dict[str, Any]]) -> FakeGmail:
        scanner = support.load_module(
            support.DASHBOARD / "google_scan.py", "town_test_r2_google_scan"
        )
        self.gmail = FakeGmail(messages, scanner.LABEL_COLORS)
        return self.gmail

    def _run(self, call_llm: Any) -> tuple[int, dict[str, Any], str]:
        assert self.gmail is not None
        google_modules = {
            "google.auth.transport.requests": SimpleNamespace(Request=object),
            "google.oauth2.credentials": SimpleNamespace(Credentials=_Credentials),
            "googleapiclient.discovery": SimpleNamespace(build=lambda *a, **k: self.gmail),
        }
        stderr = io.StringIO()
        # HERMES_HOME keeps the scanner's operator-profile read in the sandbox.
        with (
            mock.patch.dict(sys.modules, google_modules),
            mock.patch.dict(os.environ, {"HERMES_HOME": str(self.home)}),
            contextlib.redirect_stderr(stderr),
        ):
            code, payload = self.script._run(lambda: call_llm)
        # main() prints exactly this object as the job's stdout.
        stdout = json.loads(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        return code, stdout, stderr.getvalue()

    def _state(self) -> dict[str, Any]:
        return json.loads(self.script.STATE_PATH.read_text(encoding="utf-8"))

    def test_classifier_failure_holds_watermarks_for_unclassified_messages(self) -> None:
        self._gmail(
            [
                _message("msg-1", 1_100_000, "Spring catalog"),
                _message("msg-2", 1_200_000, "Weekly roundup"),
                _message("msg-3", 1_300_000, "Pinned note", starred=True),
            ]
        )
        before = self._state()["source_state"]

        code, stdout, stderr = self._run(_failing_call_llm)

        self.assertEqual(code, 4)
        self.assertIs(stdout["watermarks_advanced"], False)
        self.assertEqual(self._state()["source_state"]["gmail"], before["gmail"])
        self.assertEqual(self._state()["source_state"]["gmail_sent"], before["gmail_sent"])
        unclassified = {
            item["message_id"]
            for item in stdout["labeling"]["skipped"]
            if item["reason"] == "classifier_error"
        }
        self.assertEqual(unclassified, {"msg-1", "msg-2"})
        self.assertTrue(any(PYDANTIC_ERROR in error for error in stdout["labeling"]["errors"]))
        self.assertEqual(self.gmail.content_labels(), {})
        self.assertIn(f"town-gmail-scan: {CLASSIFIER_FAILURE}", stderr)

    def test_classifier_success_advances_watermarks(self) -> None:
        self._gmail(
            [
                _message("msg-1", 1_100_000, "Spring catalog"),
                _message("msg-2", 1_200_000, "Weekly roundup"),
                _message("msg-3", 1_300_000, "Pinned note", starred=True),
            ]
        )

        code, stdout, _ = self._run(_labeling_call_llm)

        self.assertEqual(code, 0)
        self.assertIs(stdout["watermarks_advanced"], True)
        self.assertEqual(self._state()["source_state"]["gmail"], {ACCOUNT: 1_300_000})
        self.assertEqual(self.gmail.content_labels(), {"msg-1": ["updates"], "msg-2": ["updates"]})
        self.assertEqual(stdout["labeling"]["applied"], 2)

    def test_hold_is_precise_and_next_run_retries_only_what_failed(self) -> None:
        self._gmail(
            [
                _message("msg-0", 1_050_000, "Pinned reminder", starred=True),
                _message("msg-1", 1_100_000, "Spring catalog"),
                _message("msg-2", 1_200_000, "Weekly roundup"),
                _message("msg-3", 1_300_000, "Pinned note", starred=True),
                # Older than the watermark: only the 7-day lookback pass sees it.
                _message("msg-old", 900_000, "Last week's digest"),
            ]
        )

        code, stdout, _ = self._run(_failing_call_llm)

        self.assertEqual(code, 4)
        state = self._state()
        # Advanced over the handled message older than every unclassified one,
        # never past an unclassified message and never below the old watermark.
        self.assertEqual(state["source_state"]["gmail"], {ACCOUNT: 1_050_000})
        self.assertIs(stdout["watermarks_advanced"], True)
        self.assertEqual(set(state["lookback_seen"][ACCOUNT]), {"msg-0", "msg-3"})

        code, stdout, _ = self._run(_labeling_call_llm)

        self.assertEqual(code, 0)
        self.assertEqual(
            self.gmail.content_labels(),
            {"msg-1": ["updates"], "msg-2": ["updates"], "msg-old": ["updates"]},
        )
        state = self._state()
        self.assertEqual(state["source_state"]["gmail"], {ACCOUNT: 1_300_000})
        self.assertIs(stdout["watermarks_advanced"], True)
        self.assertEqual(
            set(state["lookback_seen"][ACCOUNT]), {"msg-0", "msg-1", "msg-2", "msg-3", "msg-old"}
        )


# --- Health detail ----------------------------------------------------------

LOADER_NOISE = (
    "Plugin example-parcel-tracker: unknown kind 'user' (valid: backend, exclusive, "
    "model-provider, platform, standalone); treating as 'standalone'\n"
    "Plugin example-bookmarks: unknown kind 'user' (valid: backend, exclusive, "
    "model-provider, platform, standalone); treating as 'standalone'\n"
    "providers.example-ai: unknown config keys ignored: supports_vision_tool_messages\n"
)
STDOUT_BLOB = (
    '{"candidates":[{"headline":"Example News: Weekly roundup","summary":"Read more"}],'
    '"labeling":{"applied":0,"skipped":[],"errors":[]},"watermarks_advanced":false}'
)
LIVE_SHAPED_ERROR = (
    "Script exited with code 4\nstderr:\n"
    + LOADER_NOISE
    + f"town-gmail-scan: {CLASSIFIER_FAILURE}\n"
    + "stdout:\n"
    + STDOUT_BLOB
)


class HealthDetailTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.api = support.load_plugin_api(Path(self._tmp.name))

    def test_town_line_wins_over_loader_noise_and_stdout(self) -> None:
        detail = self.api._cron_failure_detail(LIVE_SHAPED_ERROR, "error")
        self.assertEqual(detail, f"labeler: classifier failed: … {PYDANTIC_ERROR}")
        self.assertLessEqual(len(detail), 80)

    def test_last_town_line_wins_even_before_later_noise(self) -> None:
        error = (
            "Script exited with code 5\nstderr:\n"
            "town-gmail-scan: gmail: first problem\n"
            "town-gmail-scan: labeler: alice@example.com: Gmail label write failed: quota\n"
            + LOADER_NOISE
        )
        self.assertEqual(
            self.api._cron_failure_detail(error, "error"),
            "labeler: alice@example.com: Gmail label write failed: quota",
        )

    def test_without_town_line_the_last_non_noise_line_wins(self) -> None:
        error = (
            "Script exited with code 1\nstderr:\n"
            + LOADER_NOISE
            + "Traceback (most recent call last):\n"
            + '  File "/example/script.py", line 3, in <module>\n'
            + "ModuleNotFoundError: No module named 'example_dependency'\n"
            + "stdout:\n"
            + STDOUT_BLOB
        )
        self.assertEqual(
            self.api._cron_failure_detail(error, "error"),
            "ModuleNotFoundError: No module named 'example_dependency'",
        )

    def test_pure_noise_falls_back_to_status(self) -> None:
        error = "Script exited with code 3\nstderr:\n" + LOADER_NOISE + "stdout:\n" + STDOUT_BLOB
        self.assertEqual(self.api._cron_failure_detail(error, "error"), "error")
        self.assertEqual(self.api._cron_failure_detail(None, "failed"), "failed")

    def test_long_detail_is_capped_at_80(self) -> None:
        detail = self.api._cron_failure_detail(
            "Script timed out after 120s: " + "/example/" * 20 + "town-gmail-scan.py", "error"
        )
        self.assertEqual(len(detail), 80)
        self.assertTrue(detail.endswith("..."))

    def test_scan_cron_health_item_shows_the_specific_error(self) -> None:
        output_root = Path(self._tmp.name) / "cron-output"
        output_root.mkdir()
        job = {
            "id": "job-gmail",
            "name": "town-gmail-scan",
            "last_status": "error",
            "last_error": LIVE_SHAPED_ERROR,
            "last_run_at": "2026-10-11T10:00:00+00:00",
        }
        cron_package = ModuleType("cron")
        cron_package.__path__ = []  # type: ignore[attr-defined]
        cron_jobs = ModuleType("cron.jobs")
        cron_jobs.get_cron_output_dir = lambda: output_root  # type: ignore[attr-defined]
        cron_jobs.list_jobs = lambda include_disabled=False: [job]  # type: ignore[attr-defined]
        now = self.api._parse_timestamp("2026-10-11T10:05:00+00:00")
        with mock.patch.dict(sys.modules, {"cron": cron_package, "cron.jobs": cron_jobs}):
            candidates, errors, *_ = self.api._scan_cron(now)
        self.assertEqual(errors, [])
        health = [candidate for candidate in candidates if candidate["source"] == "cron:job-gmail"]
        self.assertEqual(len(health), 1)
        self.assertEqual(
            health[0]["headline"],
            f"town-gmail-scan failing — labeler: classifier failed: … {PYDANTIC_ERROR}",
        )


if __name__ == "__main__":
    unittest.main()
