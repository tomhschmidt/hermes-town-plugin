#!/usr/bin/env python3
"""Run the installed Town Feed curator through Hermes' auxiliary LLM.

Exit codes:
  0: completed successfully
  2: curator state could not be read or written
  3: required plugin or Hermes import unavailable
  4: auxiliary LLM failed and the curator fell back to heuristics
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable


HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).expanduser()
HERMES_AGENT = HERMES_HOME / "hermes-agent"
HERMES_VENV_PYTHON = HERMES_AGENT / ".venv" / "bin" / "python"
if not HERMES_VENV_PYTHON.exists():
    HERMES_VENV_PYTHON = HERMES_AGENT / "venv" / "bin" / "python"

# Hermes dependencies live in the Hermes venv. Re-exec once so a manual
# `python3 ~/.hermes/scripts/town-curate.py` matches cron execution.
if (
    HERMES_VENV_PYTHON.exists()
    and Path(sys.executable).resolve() != HERMES_VENV_PYTHON.resolve()
    and os.environ.get("HERMES_TOWN_CURATE_BOOTSTRAPPED") != "1"
):
    os.environ["HERMES_TOWN_CURATE_BOOTSTRAPPED"] = "1"
    os.execv(
        str(HERMES_VENV_PYTHON),
        [str(HERMES_VENV_PYTHON), __file__, *sys.argv[1:]],
    )

if HERMES_AGENT.exists():
    sys.path.insert(0, str(HERMES_AGENT))


PLUGIN_PATH = HERMES_HOME / "plugins" / "town-feed" / "dashboard" / "plugin_api.py"
_PLUGIN_MODULE_NAME = "hermes_town_feed_curate_impl"


def _eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)


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
            raise ValueError("curator returned no JSON object")
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"could not parse curator JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("curator JSON must be an object")
    return parsed


def _response_text(response: Any) -> str:
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError) as exc:
        raise ValueError("curator response has no choices[0].message.content") from exc
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
                task="town_curator",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=int(kwargs.get("max_tokens") or 3000),
                temperature=0,
            )
            parsed = _parse_json_object(_response_text(response))
        except Exception as exc:
            self.last_error = str(exc)
            raise RuntimeError(f"Town Feed curator failed: {exc}") from exc
        return SimpleNamespace(parsed=parsed)


def _load_installed_plugin() -> Any:
    if not PLUGIN_PATH.is_file():
        raise RuntimeError(f"installed Town Feed plugin not found at {PLUGIN_PATH}")
    spec = importlib.util.spec_from_file_location(_PLUGIN_MODULE_NAME, PLUGIN_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load installed Town Feed plugin at {PLUGIN_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_PLUGIN_MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(_PLUGIN_MODULE_NAME, None)
        raise
    return module


def _run(
    load_call_llm: Callable[[], Callable[..., Any]],
) -> tuple[int, dict[str, Any]]:
    try:
        plugin = _load_installed_plugin()
    except Exception as exc:
        _eprint(f"town-curate: cannot import installed plugin: {exc}")
        return 3, {"ok": False, "error": str(exc)}

    try:
        adapter = _LLMAdapter(load_call_llm())
    except Exception as exc:
        _eprint(f"town-curate: cannot import auxiliary client: {exc}")
        return 3, {"ok": False, "error": str(exc)}

    try:
        result = plugin.run_curator(llm=adapter)
    except Exception as exc:
        _eprint(f"town-curate: curator state failure: {exc}")
        return 2, {"ok": False, "error": str(exc)}

    if not isinstance(result, dict):
        error = "installed Town Feed curator returned a non-object result"
        _eprint(f"town-curate: {error}")
        return 2, {"ok": False, "error": error}
    if str(result.get("ranking_mode") or "") == "heuristic (LLM fallback)":
        return 4, result
    return 0, result


def main() -> int:
    def load_call_llm() -> Callable[..., Any]:
        from agent.auxiliary_client import call_llm

        return call_llm

    code, result = _run(load_call_llm)
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
