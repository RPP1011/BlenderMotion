# Final at 2 MP: layered set mesh without shards, mannequins, set picture. layered set mesh + textured rigged characters (Hunyuan3D + MIA on the Kimodo skeleton), with the set picture.
cd /root/p4
B=/root/blender-5.0.1-linux-x64/blender; W=1920; H=1088
log(){ echo "$(date +%T) $*" >> final.log; }
$B -b --python build_cov.py -- plate cov_final.blend $W $H hsA.bvh hsB.bvh set11d.obj set11d.json > b_final.log 2>&1
spec(){ echo "{\"scene\":\"Scene\",\"camera\":\"$2\",\"frame_in\":$3,\"frame_out\":$(( $3 + $4 )),\"frames\":$4,\"frames_gen\":$4,\"characters\":{\"man\":\"A\",\"woman\":\"B\"},\"out_dir\":\"/root/p4/pass_$1_$2\",\"still_engine\":\"WORKBENCH\",\"playblast_engine\":\"EEVEE\",\"resolution\":[$W,$H]}" > spec_$1_$2.json; }
spec final CAM_wide 1 107; spec final CAM_two 96 73; spec final CAM_ots 132 73; spec final CAM_close 144 73
for c in CAM_wide CAM_two CAM_ots CAM_close; do
  $B -b cov_final.blend --python blender_worker/passes.py -- spec_final_$c.json > pass_final_$c.log 2>&1 && log "passes $c ok" || log "passes $c FAILED"
done
export H3_LORA=minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors H3_STEPS=8
R=/root/p2/refs; CH="$R/man_1.png $R/man_2.png $R/man_3.png $R/man_4.png $R/woman_1.png $R/woman_2.png $R/woman_3.png $R/woman_4.png"
MAN="a man in his thirties with short dark hair and light stubble, wearing a charcoal wool overcoat over a dark grey sweater, black trousers and dark leather boots"
WOMAN="a woman in her late twenties with shoulder-length black hair, wearing a red hooded raincoat over a cream sweater, dark jeans and white sneakers"
ID="<Picture 1>, <Picture 2>, <Picture 3> and <Picture 4> show the man from the front, three-quarter, side and back: he is $MAN. <Picture 5>, <Picture 6>, <Picture 7> and <Picture 8> show the woman from the front, three-quarter, side and back: she is $WOMAN."
SETP="<Picture 9> is the location: this exact quiet cobblestone square at night in front of these old shopfronts."
SETT="A quiet wet cobblestone square at night in front of old European shopfronts with warm windows, iron street lamps and magenta and cyan neon accents."
TAIL="The background, set and camera move of <Video 1> are the real location: keep them, but render them photoreal. The lighter mannequin in <Video 1> is the man, the darker one is the woman. Shop signs show no readable text. Cinematic 35mm film, natural motion, rich detail. <Video 1> is the motion reference: the body movement, timing, position in frame and the camera move must follow <Video 1> exactly."
declare -A P N
P[CAM_wide]="Wide establishing shot. The man walks across the square toward the woman, who waits in front of the shops, and stops in front of her."
P[CAM_two]="Medium two-shot. The man and the woman shake hands warmly and smile at each other."
P[CAM_ots]="Over-the-shoulder shot from behind the man, looking at the woman. He talks and gestures with his hands; she listens, nods, then laughs."
P[CAM_close]="Medium close-up of the woman, the man's shoulder soft at the edge of frame. She listens, nods, then laughs."
N[CAM_wide]=107; N[CAM_two]=73; N[CAM_ots]=73; N[CAM_close]=73
for v in withset; do
  for c in CAM_wide CAM_two CAM_ots CAM_close; do
    if [ $v = withset ]; then S="$SETP"; I="$CH /root/p4/plate_11.png"; else S="$SETT"; I="$CH"; fi
    log "h3 $v $c start"
    python3 h3run.py ref2v pass_final_$c/playblast.mp4 "$ID $S ${P[$c]} $TAIL" outF_${v}_$c.mp4 ${N[$c]} $W $H 42 $I >> h3.log 2>&1 && log "h3 $v $c ok" || log "h3 $v $c FAILED"
  done
done
log FINAL_DONE
