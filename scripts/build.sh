#!/usr/bin/env bash
set -euo pipefail
DEMO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_ROOT="$DEMO_ROOT/.runtime/commerce-agents"
if [[ ! -d "$RUNTIME_ROOT/examples/node_modules" ]]; then
  echo "Run ./scripts/bootstrap.sh first" >&2
  exit 1
fi
(cd "$RUNTIME_ROOT/examples/retail/storefront-web" && npm run build)
