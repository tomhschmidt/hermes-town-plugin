#!/usr/bin/env python3
"""Scan Gmail and apply Town labels through the installed Town Feed plugin.

Exit codes follow Hermes' classifier-script convention:
  0: completed successfully
  2: invalid config or unreadable/unwritable state
  3: required plugin or Hermes import unavailable
  4: auxiliary LLM classification failed
  5: Gmail scan, read, or label API failed
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterator


HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).expanduser()
HERMES_AGENT = HERMES_HOME / "hermes-agent"
HERMES_VENV_PYTHON = HERMES_AGENT / ".venv" / "bin" / "python"
if not HERMES_VENV_PYTHON.exists():
    HERMES_VENV_PYTHON = HERMES_AGENT / "venv" / "bin" / "python"

# Hermes and Google dependencies live in the Hermes venv. Re-exec once so a
# manual `python3 ~/.hermes/scripts/town-gmail-scan.py` matches cron execution.
if (
    HERMES_VENV_PYTHON.exists()
    and Path(sys.executable).resolve() != HERMES_VENV_PYTHON.resolve()
    and os.environ.get("HERMES_GMAIL_SCAN_BOOTSTRAPPED") != "1"
):
    os.environ["HERMES_GMAIL_SCAN_BOOTSTRAPPED"] = "1"
    os.execv(
        str(HERMES_VENV_PYTHON),
        [str(HERMES_VENV_PYTHON), __file__, *sys.argv[1:]],
    )

if HERMES_AGENT.exists():
    sys.path.insert(0, str(HERMES_AGENT))


PLUGIN_PATH = HERMES_HOME / "plugins" / "town-feed" / "dashboard" / "google_scan.py"
FEED_STATE_PATH = HERMES_HOME / "plugins" / "town-feed" / "feed-state.json"
STATE_PATH = HERMES_HOME / "town-feed" / "gmail-state.json"
LOCK_PATH = HERMES_HOME / "town-feed" / ".gmail-state.lock"
MAX_LABELING_LOG = 500
STATE_VERSION = 1
_PLUGIN_MODULE_NAME = "hermes_town_gmail_scan_impl"


def _eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)


def _bounded_text(value: Any, limit: int) -> str:
    return str(value or "")[:limit]


def _empty_payload() -> dict[str, Any]:
    return {
        "candidates": [],
        "known_contacts": [],
        "awaiting_reply": [],
        "open_loops": [],
        "labeling": {"applied": 0, "skipped": [], "errors": []},
        "watermarks_advanced": False,
    }


def _default_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "source_state": {"gmail": {}, "gmail_sent": {}},
        "known_correspondents": {},
        "awaiting_reply_threads": {},
        "lookback_seen": {},
        "open_loops": {},
        "labeling_log": [],
        "last_labeling_summary": None,
        "migrations": {},
    }


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"invalid state in {path}: expected a JSON object")
    return value


def _normalize_watermarks(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, int] = {}
    for raw_account, raw_watermark in value.items():
        account = str(raw_account or "").strip().casefold()
        try:
            watermark = max(0, int(raw_watermark or 0))
        except (TypeError, ValueError):
            continue
        if account and watermark:
            normalized[account] = max(normalized.get(account, 0), watermark)
    return normalized


def _normalize_state(raw: Any) -> dict[str, Any]:
    state = _default_state()
    if isinstance(raw, dict):
        state.update(raw)
    state["version"] = STATE_VERSION

    source_state = state.get("source_state")
    if not isinstance(source_state, dict):
        source_state = {}
    else:
        source_state = dict(source_state)
    source_state["gmail"] = _normalize_watermarks(source_state.get("gmail"))
    source_state["gmail_sent"] = _normalize_watermarks(
        source_state.get("gmail_sent")
    )
    state["source_state"] = source_state

    for key in (
        "known_correspondents",
        "awaiting_reply_threads",
        "lookback_seen",
        "open_loops",
        "migrations",
    ):
        if not isinstance(state.get(key), dict):
            state[key] = {}
    log = state.get("labeling_log")
    state["labeling_log"] = (
        [dict(entry) for entry in log if isinstance(entry, dict)][-MAX_LABELING_LOG:]
        if isinstance(log, list)
        else []
    )
    if not isinstance(state.get("last_labeling_summary"), dict):
        state["last_labeling_summary"] = None
    return state


def _audit_key(entry: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(entry.get(key) or "")
        for key in ("ts", "account", "message_id", "label", "confidence")
    )


def _migrate_feed_state_once(state: dict[str, Any]) -> None:
    migrations = state.setdefault("migrations", {})
    if isinstance(migrations.get("feed_state_gmail_v1"), dict):
        return

    source_exists = FEED_STATE_PATH.exists()
    migrated_fields: list[str] = []
    if source_exists:
        # Phase G6R-b owns feed-state.json itself. This migration is read-only:
        # it copies the Gmail-owned fields and never writes the plugin state.
        feed_state = _read_json_object(FEED_STATE_PATH)
        old_source = feed_state.get("source_state")
        old_source = old_source if isinstance(old_source, dict) else {}
        new_source = state["source_state"]
        for key in ("gmail", "gmail_sent"):
            destination = _normalize_watermarks(new_source.get(key))
            for account, watermark in _normalize_watermarks(old_source.get(key)).items():
                destination[account] = max(destination.get(account, 0), watermark)
            new_source[key] = destination
            if destination:
                migrated_fields.append(f"source_state.{key}")

        old_log = feed_state.get("labeling_log")
        old_log = old_log if isinstance(old_log, list) else []
        combined_log = [
            dict(entry)
            for entry in old_log + state.get("labeling_log", [])
            if isinstance(entry, dict)
        ]
        deduped_log: list[dict[str, Any]] = []
        seen_log_entries: set[tuple[str, ...]] = set()
        for entry in combined_log:
            key = _audit_key(entry)
            if key in seen_log_entries:
                continue
            seen_log_entries.add(key)
            deduped_log.append(entry)
        state["labeling_log"] = deduped_log[-MAX_LABELING_LOG:]
        if old_log:
            migrated_fields.append("labeling_log")

        for key in ("known_correspondents", "awaiting_reply_threads"):
            if not state.get(key) and isinstance(feed_state.get(key), dict):
                state[key] = dict(feed_state[key])
                if state[key]:
                    migrated_fields.append(key)

    migrations["feed_state_gmail_v1"] = {
        "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": str(FEED_STATE_PATH),
        "source_existed": source_exists,
        "migrated_fields": migrated_fields,
    }


def _load_state() -> dict[str, Any]:
    raw = _read_json_object(STATE_PATH) if STATE_PATH.exists() else {}
    state = _normalize_state(raw)
    _migrate_feed_state_once(state)
    return state


def _save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        _normalize_state(state), ensure_ascii=False, indent=2, sort_keys=True
    ) + "\n"
    fd, temp_name = tempfile.mkstemp(
        dir=str(STATE_PATH.parent), prefix=".gmail-state-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, STATE_PATH)
        os.chmod(STATE_PATH, 0o600)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


@contextmanager
def _state_lock() -> Iterator[None]:
    try:
        import fcntl
    except ImportError as exc:  # pragma: no cover - Hermes cron targets POSIX
        raise RuntimeError(f"state locking is unavailable: {exc}") from exc

    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _load_installed_google_scan() -> Any:
    if not PLUGIN_PATH.is_file():
        raise RuntimeError(f"installed Town Feed scanner not found at {PLUGIN_PATH}")
    spec = importlib.util.spec_from_file_location(_PLUGIN_MODULE_NAME, PLUGIN_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load installed Town Feed scanner at {PLUGIN_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_PLUGIN_MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(_PLUGIN_MODULE_NAME, None)
        raise
    return module


def _structured_input_text(value: Any) -> str:
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif isinstance(item, str):
                parts.append(item)
            else:
                parts.append(json.dumps(item, ensure_ascii=False))
        return "\n".join(parts)
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _parse_json_object(content: str) -> dict[str, Any]:
    text = (content or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("classifier returned no JSON object")
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"could not parse classifier JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("classifier JSON must be an object")
    return parsed


def _response_text(response: Any) -> str:
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError) as exc:
        raise ValueError("classifier response has no choices[0].message.content") from exc
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            text = item.get("text") if isinstance(item, dict) else getattr(item, "text", None)
            if text:
                parts.append(str(text))
        return "\n".join(parts)
    return str(content) if content is not None else ""


class _LLMAdapter:
    def __init__(self, call_llm: Callable[..., Any]) -> None:
        self._call_llm = call_llm
        self.last_error: str | None = None

    def complete_structured(
        self,
        *,
        instructions: str,
        input: Any,
        json_schema: dict[str, Any],
        **kwargs: Any,
    ) -> Any:
        prompt = (
            f"{instructions}\n\n"
            "Return only one JSON object matching this schema. Do not use markdown fences.\n"
            f"JSON schema:\n{json.dumps(json_schema, ensure_ascii=False, sort_keys=True)}\n\n"
            f"Input:\n{_structured_input_text(input)}"
        )
        try:
            response = self._call_llm(
                task="town_inbox",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=int(kwargs.get("max_tokens") or 2500),
                temperature=0,
            )
            parsed = _parse_json_object(_response_text(response))
        except Exception as exc:
            self.last_error = str(exc)
            raise RuntimeError(f"Town inbox classifier failed: {exc}") from exc
        return SimpleNamespace(parsed=parsed)


def _watermark_snapshot(state: dict[str, Any]) -> dict[str, dict[str, int]]:
    source_state = state.get("source_state")
    source_state = source_state if isinstance(source_state, dict) else {}
    return {
        "gmail": _normalize_watermarks(source_state.get("gmail")),
        "gmail_sent": _normalize_watermarks(source_state.get("gmail_sent")),
    }


def _watermarks_advanced(
    before: dict[str, dict[str, int]], after: dict[str, dict[str, int]]
) -> bool:
    for key in ("gmail", "gmail_sent"):
        for account, watermark in after.get(key, {}).items():
            if watermark > before.get(key, {}).get(account, 0):
                return True
    return False


def _run(
    load_call_llm: Callable[[], Callable[..., Any]],
) -> tuple[int, dict[str, Any]]:
    payload = _empty_payload()
    try:
        with _state_lock():
            state = _load_state()
            before_watermarks = _watermark_snapshot(state)

            try:
                scanner = _load_installed_google_scan()
            except Exception as exc:
                _eprint(f"town-gmail-scan: cannot import installed scanner: {exc}")
                return 3, payload

            try:
                labeling_enabled, _confidence_min, _taxonomy = (
                    scanner.get_town_inbox_settings()
                )
            except (ImportError, ModuleNotFoundError) as exc:
                _eprint(f"town-gmail-scan: cannot import Hermes config loader: {exc}")
                return 3, payload
            except Exception as exc:
                _eprint(f"town-gmail-scan: invalid Town inbox config: {exc}")
                return 2, payload

            gmail_services: dict[str, Any] = {}
            labeling_candidates: list[dict[str, Any]] = []
            try:
                candidates, scan_errors = scanner.scan_gmail(
                    time.time(),
                    state,
                    gmail_services,
                    labeling_candidates,
                )
            except Exception as exc:
                candidates, scan_errors = [], [f"gmail: scanner failed: {exc}"]

            adapter: _LLMAdapter | None = None
            if labeling_enabled and labeling_candidates:
                # Keep the Hermes import lazy so module loading and config/state
                # diagnostics work even when auxiliary dependencies are absent.
                try:
                    call_llm = load_call_llm()
                except Exception as exc:
                    _eprint(f"town-gmail-scan: cannot import auxiliary client: {exc}")
                    return 3, payload
                adapter = _LLMAdapter(call_llm)

            try:
                labeling_result = scanner.label_gmail_messages(
                    labeling_candidates,
                    gmail_services,
                    state,
                    adapter,
                )
            except Exception as exc:
                _eprint(f"town-gmail-scan: labeling pass failed: {exc}")
                scan_errors = list(scan_errors) + [f"labeler: {exc}"]
                labeling_result = {
                    "summary": {
                        "applied": 0,
                        "skipped": [],
                        "errors": [f"labeler: {exc}"],
                    }
                }

            summary = labeling_result.get("summary")
            summary = summary if isinstance(summary, dict) else {}
            labeling_errors = [
                str(error)
                for error in summary.get("errors", [])
                if str(error).strip()
            ]
            skipped = [
                dict(item)
                for item in summary.get("skipped", [])
                if isinstance(item, dict)
            ]
            classifications = {
                (
                    str(candidate.get("account") or "").strip().casefold(),
                    str(candidate.get("message_id") or "").strip(),
                ): (
                    candidate.get("town_label"),
                    candidate.get("town_label_confidence"),
                )
                for candidate in labeling_candidates
                if isinstance(candidate, dict)
            }
            enriched_candidates: list[dict[str, Any]] = []
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                enriched = dict(candidate)
                enriched.pop("_awaiting_reply_to", None)
                enriched["body"] = _bounded_text(candidate.get("body"), 1500)
                key = (
                    str(candidate.get("account") or "").strip().casefold(),
                    str(candidate.get("message_id") or "").strip(),
                )
                town_label, town_label_confidence = classifications.get(
                    key, (None, None)
                )
                enriched["town_label"] = town_label
                enriched["town_label_confidence"] = town_label_confidence
                enriched_candidates.append(enriched)
            known_contacts: list[str] = []
            raw_known = state.get("known_correspondents")
            if isinstance(raw_known, dict):
                seen_known: set[str] = set()
                for raw_account_cache in raw_known.values():
                    if not isinstance(raw_account_cache, dict):
                        continue
                    for raw_address, raw_entry in raw_account_cache.items():
                        address = str(raw_address or "").strip().casefold()
                        if (
                            address
                            and address not in seen_known
                            and isinstance(raw_entry, dict)
                            and raw_entry.get("known") is True
                        ):
                            seen_known.add(address)
                            known_contacts.append(address)

            payload = {
                "candidates": enriched_candidates,
                # Addresses the user has previously written to — the grounded
                # basis for any "no prior contact" claim downstream.
                "known_contacts": sorted(known_contacts)[:1000],
                "awaiting_reply": [
                    dict(item)
                    for item in labeling_result.get("awaiting_reply", [])
                    if isinstance(item, dict)
                ][:10],
                "open_loops": [
                    dict(item)
                    for item in labeling_result.get("open_loops", [])
                    if isinstance(item, dict)
                ][:15],
                "labeling": {
                    "applied": int(summary.get("applied") or 0),
                    "skipped": skipped,
                    "errors": labeling_errors,
                },
                "watermarks_advanced": _watermarks_advanced(
                    before_watermarks, _watermark_snapshot(state)
                ),
            }

            try:
                _save_state(state)
            except Exception as exc:
                _eprint(f"town-gmail-scan: could not save {STATE_PATH}: {exc}")
                return 2, payload

            for error in scan_errors:
                _eprint(f"town-gmail-scan: {error}")
            for error in labeling_errors:
                _eprint(f"town-gmail-scan: {error}")

            classifier_failed = bool(adapter and adapter.last_error) or any(
                "classifier" in error.casefold() for error in labeling_errors
            )
            if classifier_failed:
                return 4, payload
            if scan_errors or labeling_errors:
                return 5, payload
            return 0, payload
    except Exception as exc:
        _eprint(f"town-gmail-scan: state/config failure: {exc}")
        return 2, payload


def main() -> int:
    def load_call_llm() -> Callable[..., Any]:
        from agent.auxiliary_client import call_llm

        return call_llm

    code, payload = _run(load_call_llm)
    # No wakeAgent gate: it would replace the saved output with a silent-run
    # stub (scheduler.py _parse_wake_gate path), starving the curator of the
    # candidates JSON. Jobs run with deliver:local; failure visibility comes
    # from the curator's town-cron-health surfacing (G6R-b).
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
