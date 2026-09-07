#!/usr/bin/env bash
set -euo pipefail

DEMO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_ROOT="$DEMO_ROOT/.runtime/commerce-agents"

# Generate one shopping storefront and one API service.

if [[ ! -d "$RUNTIME_ROOT/.git" ]]; then
  echo "Run ./scripts/bootstrap.sh first" >&2
  exit 1
fi

while IFS= read -r -d '' overlay_file; do
  relative_path="${overlay_file#"$DEMO_ROOT/overlay/"}"
  destination="$RUNTIME_ROOT/$relative_path"
  mkdir -p "$(dirname "$destination")"
  cp "$overlay_file" "$destination"
done < <(find "$DEMO_ROOT/overlay" -type f -print0)

cp "$DEMO_ROOT/deploy/vercel.json" "$RUNTIME_ROOT/vercel.json"
cp "$DEMO_ROOT/deploy/vercel_backend.py" "$RUNTIME_ROOT/vercel_backend.py"
cp "$DEMO_ROOT/deploy/requirements.txt" "$RUNTIME_ROOT/requirements.txt"

echo "$RUNTIME_ROOT"
