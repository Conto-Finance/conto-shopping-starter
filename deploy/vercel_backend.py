"""Single shopping API entrypoint; Vercel routes /api/* here unchanged."""
from pathlib import Path
import sys
import os
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(REPO_ROOT / part) for part in (
    "examples", "commerce-common", "shopping-agent/core",
    "shopping-agent/runtime-messages-api", "merchant-agent/core",
    "merchant-agent/runtime-messages-api",
)]
# Upstream allows loopback hosts by default. Add only this configured public host.
origin = urlparse(os.getenv("SHOPPING_AGENT_ORIGIN", ""))
if not origin.hostname or origin.scheme not in {"http", "https"}:
    raise RuntimeError("Set SHOPPING_AGENT_ORIGIN to this shopping deployment's public origin")
hosts = [host.strip() for host in os.getenv("DEMO_ALLOWED_HOSTS", "").split(",") if host.strip()]
if origin.hostname not in hosts:
    hosts.append(origin.hostname)
os.environ["DEMO_ALLOWED_HOSTS"] = ",".join(hosts)
from retail.api.main import app
