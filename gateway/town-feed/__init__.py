"""Hermes gateway entry point for the Town Need-to-Know feed."""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


_API_MODULE_NAME = "hermes_town_feed_tool_api"
_LOGGER = logging.getLogger(__name__)

_CRON_SUGGESTIONS = (
    {
        "title": "Schedule the Town Feed Gmail scanner",
        "description": "Scan Gmail for Town Feed candidates and apply configured Town/ labels every 10 minutes.",
        "job_spec": {
            "name": "town-gmail-scan",
            "schedule": "every 10m",
            "script": "town-gmail-scan.py",
            "no_agent": True,
            "deliver": "local",
        },
    },
    {
        "title": "Schedule the Town Feed Telegram scanner",
        "description": "Scan recent Telegram threads for Town Feed candidates every 10 minutes.",
        "job_spec": {
            "name": "town-telegram-scan",
            "schedule": "every 10m",
            "script": "town-telegram-scan.py",
            "no_agent": True,
            "deliver": "local",
        },
    },
    {
        "title": "Schedule the Town Feed calendar scanner",
        "description": "Scan calendar-scoped Google accounts for upcoming Town Feed candidates every 30 minutes.",
        "job_spec": {
            "name": "town-calendar-scan",
            "schedule": "every 30m",
            "script": "town-calendar-scan.py",
            "no_agent": True,
            "deliver": "local",
        },
    },
    {
        "title": "Schedule the Town Feed curator",
        "description": "Rank and persist recent Town Feed candidates every 30 minutes.",
        "job_spec": {
            "name": "town-feed-curate",
            "schedule": "every 30m",
            "script": "town-curate.py",
            "no_agent": True,
            "deliver": "local",
        },
    },
)


def _load_api_module() -> ModuleType:
    """Load the dashboard API module so the cron-facing tool shares its curator."""
    mounted = sys.modules.get("hermes_dashboard_plugin_town-feed")
    if mounted is not None:
        return mounted
    cached = sys.modules.get(_API_MODULE_NAME)
    if cached is not None:
        return cached

    api_path = Path(__file__).resolve().parent / "dashboard" / "plugin_api.py"
    spec = importlib.util.spec_from_file_location(_API_MODULE_NAME, api_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load Town Feed API from {api_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_API_MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(_API_MODULE_NAME, None)
        raise
    return module


def register(ctx: Any) -> None:
    """Register the cron-callable curator with host-owned LLM access."""

    try:
        ctx.register_auxiliary_task(
            "town_inbox",
            display_name="Town inbox labeler",
            description="Classify new Gmail messages into the configured Town inbox taxonomy.",
            defaults={},
        )
    except Exception:
        # Older hosts lack register_auxiliary_task; the aux client still
        # honors config.yaml auxiliary.town_inbox without registration.
        _LOGGER.info("register_auxiliary_task unavailable; town_inbox uses config-only routing")

    try:
        ctx.register_auxiliary_task(
            "town_curator",
            display_name="Town Feed curator",
            description="Rank recent Town Feed candidates into the Need-to-Know feed.",
            defaults={},
        )
    except Exception:
        # Older hosts lack register_auxiliary_task; the aux client still
        # honors config.yaml auxiliary.town_curator without registration.
        _LOGGER.info("register_auxiliary_task unavailable; town_curator uses config-only routing")

    def curate_handler(*_args: Any, **_kwargs: Any) -> str:
        # The tool dispatcher passes schema params plus injected extras
        # (e.g. task_id) as keyword arguments — accept and ignore them all.
        result = _load_api_module().run_curator(llm=ctx.llm)
        return json.dumps(result, ensure_ascii=True, sort_keys=True)

    ctx.register_tool(
        name="town_feed_curate",
        toolset="town_feed",
        schema={
            "name": "town_feed_curate",
            "description": (
                "Scan recent Hermes sessions, cron outcomes, and memory changes; "
                "rank and persist the Town Need-to-Know feed. Call exactly once."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        handler=curate_handler,
        description="Curate and persist the Town Need-to-Know feed.",
        emoji="newspaper",
    )

    try:
        from cron.jobs import load_jobs
        from cron.suggestions import add_suggestion

        installed_names = {
            str(job.get("name") or "").strip()
            for job in load_jobs()
            if isinstance(job, dict)
        }
        for suggestion in _CRON_SUGGESTIONS:
            job_spec = suggestion["job_spec"]
            job_name = job_spec["name"]
            if job_name in installed_names:
                continue
            add_suggestion(
                title=suggestion["title"],
                description=suggestion["description"],
                source="integration",
                job_spec=dict(job_spec),
                dedup_key=f"town-feed:{job_name}",
            )
    except Exception:
        # Suggestions are an install convenience, not a plugin requirement.
        # Older Hermes hosts may not expose this module yet.
        _LOGGER.debug("Town Feed cron suggestions unavailable", exc_info=True)
