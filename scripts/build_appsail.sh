#!/usr/bin/env bash
# Build the AppSail bundle for the backend (Phase 4 stage 2).
#
#   scripts/build_appsail.sh            # -> build/appsail/
#   (cd catalyst && catalyst deploy)    # deploys build/appsail as "ticketapp-api"
#
# AppSail's managed Python runtime does NOT install dependencies: the build
# folder is uploaded as-is, so Linux x86-64 / CPython 3.13 wheels are
# vendored next to the code. Only runtime files go in: backend/ (no local DB,
# uploads or caches), the KB JSON, requirements-runtime.txt's packages and
# app-config.json. No .env, no secrets — set environment variables in the
# AppSail console. Only APP_ENV=production is baked in, so a deploy without
# the console secrets fails closed (startup refuses) instead of starting
# with the dev admin on a public URL.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/build/appsail"
PY="${PYTHON:-$ROOT/.ven/bin/python}"

rm -rf "$OUT"
mkdir -p "$OUT/data/kb"

rsync -a --exclude '__pycache__' --exclude '*.pyc' --exclude '*.db' --exclude '*.db-*' \
  --exclude 'uploads/' "$ROOT/backend/" "$OUT/backend/"
cp "$ROOT/data/kb/kb_entries.json" "$OUT/data/kb/"
cp "$ROOT/requirements-runtime.txt" "$OUT/"

"$PY" -m pip install -r "$ROOT/requirements-runtime.txt" -t "$OUT" \
  --platform manylinux2014_x86_64 --python-version 3.13 --implementation cp \
  --only-binary=:all: --no-compile -q
find "$OUT" -name '__pycache__' -type d -prune -exec rm -rf {} +
rm -rf "$OUT/bin"  # console scripts written for this machine's Python

cat > "$OUT/app-config.json" <<'JSON'
{
  "command": "sh -c 'python3 -m uvicorn backend.main:app --host 0.0.0.0 --port ${X_ZOHO_CATALYST_LISTEN_PORT:-9000}'",
  "build_path": ".",
  "stack": "python_3_13",
  "env_variables": {"APP_ENV": "production"},
  "memory": 512,
  "scripts": {}
}
JSON

# Fail loudly if anything secret-looking slipped in.
if find "$OUT" -name '.env*' -o -name '*.db' -o -name '.catalystrc' | grep -q .; then
  echo "refusing: secret or local-state file in build/appsail" >&2
  exit 1
fi

echo "build/appsail: $(du -sh "$OUT" | cut -f1) total, $(find "$OUT" -type f | wc -l | tr -d ' ') files"
du -sk "$OUT"/* | sort -rn | head -8 | awk '{printf "  %6.1f MB  %s\n", $1/1024, $2}' | sed "s#$OUT/##"
