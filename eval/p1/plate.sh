cd /root/p4
export H3_LORA=minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors H3_STEPS=8
P="Locked-off static wide shot at eye level, camera standing in the middle of an empty narrow cobblestone street at night after rain, looking straight across at a row of old European shopfronts on the far sidewalk about eight metres away: a cafe with warm window light, a bakery, a dark wooden door, a small bookshop, iron street lamps, a bare tree and a wide empty sidewalk in front of the shops. Magenta and cyan neon accents, wet reflective ground. Completely empty: no people, no cars. Shop signs are simple shapes and symbols with no lettering. Cinematic 35mm photograph, sharp detail."
for s in 11 12 13; do python3 h3run.py t2v - "$P" plate_$s.mp4 22 1920 1088 $s >> plate.log 2>&1; done
echo PLATE_DONE >> plate.log
