#!/usr/bin/env bash
# Tutorial clips of the real UI. Usage: BLENDER=... tests/gui_tour.sh [out_dir]
# Needs: Xvfb, ffmpeg (x11grab), ImageMagick (import), xdotool. Runs server/ with fake models
# and the real Blender retarget and passes workers. Then: tests/gui_tour_post.sh out_dir docs/media
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
blender="${BLENDER:-blender}"
out="${1:-/tmp/chimera_tour}"
tmp="$(mktemp -d)"
mkdir -p "$tmp/scripts/addons" "$tmp/config" "$out"
ln -s "$here/addon/chimera_director" "$tmp/scripts/addons/chimera_director"
export BLENDER_USER_SCRIPTS="$tmp/scripts" BLENDER_USER_CONFIG="$tmp/config" XDG_RUNTIME_DIR="$tmp"
"$blender" -b --factory-startup --python-expr \
  "import bpy; bpy.context.preferences.view.show_splash=False; bpy.ops.wm.save_userpref()" >/dev/null
port=$(( 20000 + RANDOM % 20000 ))
(cd "$here" && python3 -m server.orchestrator --port "$port" --data "$tmp/server_data" --fake \
    --backend retarget=blender --backend passes=blender --blender "$blender" \
    --set passes.resolution_scale=50 >"$out/server.log" 2>&1) &
srv=$!
disp=":$(( 90 + RANDOM % 50 ))"
Xvfb "$disp" -screen 0 "${SCREEN:-1920x1080}x24" >/dev/null 2>&1 &
xvfb=$!
trap 'kill $srv $xvfb 2>/dev/null; rm -rf "$tmp"' EXIT
for _ in $(seq 50); do curl -sf "http://127.0.0.1:$port/health" >/dev/null 2>&1 && break; sleep 0.2; done
DISPLAY="$disp" CAPTURE_DIR="$out" CHIMERA_E2E_URL="http://127.0.0.1:$port" \
  timeout 900 "$blender" --python "$here/tests/gui_tour.py" 2>&1 | grep -E -A12 "^REC|Error|Traceback|\[chimera\]" || true
ls "$out"/*.mp4
