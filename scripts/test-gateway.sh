#!/bin/sh
# Run the gateway unit tests on the Hermes-managed Python (the interpreter
# cron uses), so fastapi/pydantic/hermes-agent resolve exactly as in
# production. Override with HERMES_PYTHON=/path/to/python.
set -eu
cd "$(dirname "$0")/.."
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
if [ -z "${HERMES_PYTHON:-}" ]; then
  HERMES_PYTHON=$(python3 -c '
import glob, json, os, sys
for facts in sorted(glob.glob(os.path.join(sys.argv[1], "installs", "*", "facts.json"))):
    env = json.load(open(facts)).get("packages", {}).get("venv", {}).get("environment")
    if env:
        print(os.path.join(env, "bin", "python"))
        break
' "$HERMES_HOME")
fi
if [ -z "$HERMES_PYTHON" ] || [ ! -x "$HERMES_PYTHON" ]; then
  echo "test-gateway: no Hermes-managed Python found; set HERMES_PYTHON" >&2
  exit 1
fi
exec "$HERMES_PYTHON" -m unittest discover -s gateway/tests "$@"
