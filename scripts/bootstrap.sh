#!/usr/bin/env bash
set -euo pipefail

DEMO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_ROOT="$DEMO_ROOT/.runtime/commerce-agents"
UPSTREAM_REVISION="fd4d59224ab96b43c6dc6888207c67b3bd5a24cf"

if [[ -n "${PYTHON_BIN:-}" ]]; then
  PYTHON="$PYTHON_BIN"
elif command -v python3.11 >/dev/null 2>&1; then
  PYTHON="$(command -v python3.11)"
else
  PYTHON="$(command -v python3)"
fi

node -e 'if (Number(process.versions.node.split(".")[0]) < 22) { console.error("Node 22+ is required"); process.exit(1); }'

"$PYTHON" -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ is required"'

if [[ ! -d "$RUNTIME_ROOT/.git" ]]; then
  mkdir -p "$(dirname "$RUNTIME_ROOT")"
  git clone https://github.com/anthropics/commerce-agents.git "$RUNTIME_ROOT"
fi

current_origin="$(git -C "$RUNTIME_ROOT" remote get-url origin)"
if [[ "$current_origin" != "https://github.com/anthropics/commerce-agents.git" ]]; then
  echo "Refusing to modify an unexpected runtime checkout: $current_origin" >&2
  exit 1
fi

git -C "$RUNTIME_ROOT" fetch --quiet origin "$UPSTREAM_REVISION"
git -C "$RUNTIME_ROOT" checkout --quiet --detach "$UPSTREAM_REVISION"
git -C "$RUNTIME_ROOT" show "$UPSTREAM_REVISION:requirements.txt" > "$RUNTIME_ROOT/requirements.txt"

while IFS= read -r -d '' overlay_file; do
  relative_path="${overlay_file#"$DEMO_ROOT/overlay/"}"
  destination="$RUNTIME_ROOT/$relative_path"
  mkdir -p "$(dirname "$destination")"
  cp "$overlay_file" "$destination"
done < <(find "$DEMO_ROOT/overlay" -type f -print0)

if [[ ! -d "$RUNTIME_ROOT/.venv" ]]; then
  "$PYTHON" -m venv "$RUNTIME_ROOT/.venv"
fi

(
  cd "$RUNTIME_ROOT"
  .venv/bin/pip install -q -r requirements.txt
  .venv/bin/pip install -q -r "$DEMO_ROOT/deploy/requirements.txt"
  cd examples
  npm ci --silent
)

echo "Conto Shopping Starter is ready at $RUNTIME_ROOT"
