#!/usr/bin/env bash
# Usage: BLENDER=/path/to/blender tests/run_tests.sh [--e2e]
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
blender="${BLENDER:-blender}"
scripts="$(mktemp -d)"
mkdir -p "$scripts/addons"
ln -s "$here/addon/chimera_director" "$scripts/addons/chimera_director"
cleanup() { [[ -n "${mock_pid:-}" ]] && kill "$mock_pid" 2>/dev/null; rm -rf "$scripts"; }
trap cleanup EXIT
if [[ "${1:-}" == "--e2e" ]]; then
  port=$(( 20000 + RANDOM % 20000 ))
  python3 "$here/tools/mock_orchestrator.py" --port "$port" --blender "$blender" --speed 0.2 \
      --data "$scripts/mock_data" &
  mock_pid=$!
  sleep 1
  export CHIMERA_E2E_URL="http://127.0.0.1:$port"
fi
BLENDER_USER_SCRIPTS="$scripts" "$blender" -b --factory-startup --python "$here/tests/test_addon.py"
