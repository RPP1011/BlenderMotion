#!/usr/bin/env bash
# Usage: BLENDER=/path/to/blender tests/run_tests.sh [--e2e | --e2e-server]
#   --e2e         end to end against tools/mock_orchestrator.py
#   --e2e-server  end to end against server/orchestrator.py with fake models, but the
#                 real Blender retarget and passes workers (Cycles at 25 %, no GPU needed)
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
blender="${BLENDER:-blender}"
scripts="$(mktemp -d)"
mkdir -p "$scripts/addons"
ln -s "$here/addon/chimera_director" "$scripts/addons/chimera_director"
cleanup() { [[ -n "${mock_pid:-}" ]] && kill "$mock_pid" 2>/dev/null; rm -rf "$scripts"; }
trap cleanup EXIT
port=$(( 20000 + RANDOM % 20000 ))
case "${1:-}" in
  --e2e)
    python3 "$here/tools/mock_orchestrator.py" --port "$port" --blender "$blender" --speed 0.2 \
        --data "$scripts/mock_data" &
    mock_pid=$! ;;
  --e2e-server)
    python3 "$here/server/orchestrator.py" --port "$port" --data "$scripts/server_data" --fake \
        --backend retarget=blender --backend passes=blender --blender "$blender" \
        --set passes.still_engine='"CYCLES"' --set passes.playblast_engine='"CYCLES"' \
        --set passes.resolution_scale=25 &
    mock_pid=$!
    export CHIMERA_E2E_SERVER=1 ;;
esac
if [[ -n "${mock_pid:-}" ]]; then
  for _ in $(seq 50); do curl -sf "http://127.0.0.1:$port/health" >/dev/null 2>&1 && break; sleep 0.2; done
  export CHIMERA_E2E_URL="http://127.0.0.1:$port"
fi
BLENDER_USER_SCRIPTS="$scripts" "$blender" -b --factory-startup --python "$here/tests/test_addon.py"
