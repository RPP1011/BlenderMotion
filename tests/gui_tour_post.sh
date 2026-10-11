#!/usr/bin/env bash
# Caption the clips from tests/gui_tour.sh and write web versions.
# Usage: tests/gui_tour_post.sh <tour_dir> <out_dir>   ->  <out_dir>/<name>.mp4, .gif, .png
# Clips longer than MAX_S seconds (default 20) are sped up to fit; the caption says so.
set -euo pipefail
src="$1" out="$2"
max="${MAX_S:-20}"
font="${FONT:-/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf}"
mkdir -p "$out"
python3 -c 'import json,sys; [print(s["name"] + "\t" + s["caption"]) for s in json.load(open(sys.argv[1]))]' \
    "$src/steps.json" | while IFS=$'\t' read -r name caption; do
  dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$src/$name.mp4")
  speed=$(python3 -c "print(max(1.0, $dur / $max))")
  if python3 -c "import sys; sys.exit(0 if $speed > 1.05 else 1)"; then
    caption="$caption  ($(python3 -c "print(round($speed))")x speed)"
  fi
  printf '%s' "$caption" > "$out/.caption.txt"
  vf="setpts=PTS/$speed,scale=1280:-2,pad=iw:ih+44:0:0:0x1d1d1d,drawtext=fontfile=$font:textfile=$out/.caption.txt:fontcolor=white:fontsize=22:x=16:y=h-33"
  ffmpeg -nostdin -v error -y -i "$src/$name.mp4" -vf "$vf" -r 15 -c:v libx264 -crf 26 -preset slow -pix_fmt yuv420p \
      -movflags +faststart "$out/$name.mp4"
  ffmpeg -nostdin -v error -y -i "$out/$name.mp4" -vf "fps=8,scale=960:-2:flags=lanczos,split[a][b];[a]palettegen=max_colors=96[p];[b][p]paletteuse=dither=none" \
      "$out/$name.gif"
  convert "$src/$name.png" -resize 1280x "$out/$name.png"
  echo "$name $(du -h "$out/$name.mp4" | cut -f1) mp4, $(du -h "$out/$name.gif" | cut -f1) gif"
done
rm -f "$out/.caption.txt"
