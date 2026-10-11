"""Shared helpers for the Town gateway unit tests (stdlib unittest only).

Run with ``npm run test:gateway``: it executes on the Hermes-managed Python
(the interpreter cron uses), so fastapi/pydantic/hermes-agent imports resolve
exactly as they do in production.

Tests must never read or write the live ``~/.hermes`` state. Load modules
through these helpers and point every state path at a temporary directory.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "gateway" / "scripts"
DASHBOARD = REPO / "gateway" / "town-feed" / "dashboard"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
HERMES_AGENT = (
    Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).expanduser()
    / "hermes-agent"
)

if HERMES_AGENT.exists() and str(HERMES_AGENT) not in sys.path:
    sys.path.insert(0, str(HERMES_AGENT))
if str(DASHBOARD) not in sys.path:
    sys.path.insert(0, str(DASHBOARD))

# Cron scripts import hermes_bootstrap at module level, as in production. The
# real module activates PM state under ~/.hermes and, without cron's
# HERMES_DISABLE_LAZY_INSTALLS=1, relaunches the process onto PM's store
# Python, so in-process loads get an inert stand-in. Exercise the real
# bootstrap only in a subprocess with HERMES_HOME pointed at a temp dir.
if "hermes_bootstrap" not in sys.modules:
    sys.modules["hermes_bootstrap"] = ModuleType(
        "hermes_bootstrap", "Inert test stand-in (gateway/tests/support.py)."
    )


def load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_script(filename: str) -> ModuleType:
    """Import a hyphenated cron script, e.g. ``load_script("town-telegram-scan.py")``."""
    stem = filename.removesuffix(".py").replace("-", "_")
    return load_module(SCRIPTS / filename, f"town_test_{stem}")


def load_plugin_api(state_dir: Path) -> ModuleType:
    """Import plugin_api with feed-state redirected into ``state_dir``."""
    module = load_module(DASHBOARD / "plugin_api.py", "town_test_plugin_api")
    module._state_path = lambda: state_dir / "feed-state.json"
    module._lock_path = lambda: state_dir / ".feed-state.lock"
    return module
