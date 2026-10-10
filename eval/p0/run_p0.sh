#!/bin/bash
# P0 driver: H3 runs serialised on the GPU; Blender passes for vault run alongside.
cd /workspace/p0
B=/root/blender-5.0.1-linux-x64/blender
P="A man in a dark grey hoodie, jeans and sneakers on an overcast concrete rooftop, cinematic 35mm film look. <Video 1> is the motion reference: the man's body movement, timing, position in frame and the camera must follow <Video 1> exactly."
log(){ echo "$(date +%T) $*" >> p0.log; }
log start
( $B -b --python build_shot.py -- vault.bvh vault.blend 124 > vault_build.log 2>&1
  sed "s/walk_grey/vault_grey/" walk_grey.json > vault_grey.json
  sed "s/walk_eevee/vault_eevee/" walk_eevee.json > vault_eevee.json
  $B -b vault.blend --python blender_worker/passes.py -- vault_grey.json > vault_grey.log 2>&1
  $B -b vault.blend --python blender_worker/passes.py -- vault_eevee.json > vault_eevee.log 2>&1
  touch vault_passes.done; log vault passes done ) &
python3 h3run.py ref2v walk_grey/playblast.mp4 "$P" out/walk_ref2v_grey.mp4 124 960 544 42 >> p0.log 2>&1
while [ ! -f vault_passes.done ]; do sleep 10; done
python3 h3run.py ref2v vault_grey/playblast.mp4 "$P" out/vault_ref2v_grey.mp4 124 960 544 42 >> p0.log 2>&1
python3 h3run.py ref2v vault_eevee/playblast.mp4 "$P" out/vault_ref2v_eevee.mp4 124 960 544 42 >> p0.log 2>&1
python3 h3run.py flf vault_grey/first.png vault_grey/last.png "A man in a dark grey hoodie, jeans and sneakers on an overcast concrete rooftop runs toward a low wall, vaults over it with one hand and lands in a crouch. Cinematic 35mm film look." out/vault_flf.mp4 124 960 544 42 >> p0.log 2>&1
log h3 done
