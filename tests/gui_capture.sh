#!/usr/bin/env bash
# Screenshots of the real UI. Usage: BLENDER=... tests/gui_capture.sh [out_dir]
# Needs: Xvfb, ImageMagick (import), optionally xdotool. Uses the mock orchestrator.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
blender="${BLENDER:-blender}"
out="${1:-/tmp/chimera_capture}"
tmp="$(mktemp -d)"
mkdir -p "$tmp/scripts/addons" "$tmp/config" "$out"
ln -s "$here/addon/chimera_director" "$tmp/scripts/addons/chimera_director"
export BLENDER_USER_SCRIPTS="$tmp/scripts" BLENDER_USER_CONFIG="$tmp/config" XDG_RUNTIME_DIR="$tmp"
# Skip the first-run splash.
"$blender" -b --factory-startup --python-expr \
  "import bpy; bpy.context.preferences.view.show_splash=False; bpy.ops.wm.save_userpref()" >/dev/null
port=$(( 20000 + RANDOM % 20000 ))
python3 "$here/tools/mock_orchestrator.py" --port "$port" --blender "$blender" --speed 0.3 \
    --data "$tmp/mock_data" >/dev/null 2>&1 &
mock=$!
disp=":$(( 90 + RANDOM % 50 ))"
Xvfb "$disp" -screen 0 "${SCREEN:-1920x1080}x24" >/dev/null 2>&1 &
xvfb=$!
trap 'kill $mock $xvfb 2>/dev/null; rm -rf "$tmp"' EXIT
sleep 1
DISPLAY="$disp" CAPTURE_DIR="$out" CHIMERA_E2E_URL="http://127.0.0.1:$port" \
  timeout 600 "$blender" --python "$here/tests/gui_capture.py" 2>&1 | grep -E -A12 "CAPTURED|Error|Traceback|\[chimera\]" || true
ls "$out"/*.png
