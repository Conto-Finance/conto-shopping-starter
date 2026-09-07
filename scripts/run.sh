#!/usr/bin/env bash
set -euo pipefail
DEMO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_ROOT="$DEMO_ROOT/.runtime/commerce-agents"
if [[ ! -x "$RUNTIME_ROOT/.venv/bin/python" ]]; then
  echo "Run ./dev setup first" >&2; exit 1
fi
cd "$RUNTIME_ROOT"
export PATH="$RUNTIME_ROOT/.venv/bin:$PATH"
exec .venv/bin/python - "$DEMO_ROOT" <<'PYTHON'
import os
from pathlib import Path
import runpy
import socket
import sys
sys.path.insert(0, str(Path(sys.argv[1]) / "scripts"))
from config import load_config, config_errors
load_config()
errors = config_errors(os.environ)
if errors:
    raise SystemExit("Configuration needs attention. Run ./dev doctor.")
# Upstream can choose new ports automatically, but checkout return URLs need a stable origin.
for port in (8000, 3000):
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            raise SystemExit(f"Port {port} is in use. Free ports 8000 and 3000 before starting.")
os.environ.setdefault("SHOPPING_AGENT_ORIGIN", "http://localhost:8000")
sys.argv = ["scripts/run_demo.py", "retail"]
runpy.run_path("scripts/run_demo.py", run_name="__main__")
PYTHON
