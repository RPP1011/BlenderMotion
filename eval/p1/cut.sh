cd /root/p4
for set in plate grey; do
  # editorial trims (local frame ranges at 24 fps) cut from overlapping coverage
  ffmpeg -v error -y -i out_${set}_CAM_wide.mp4 -i out_${set}_CAM_two.mp4 -i out_${set}_CAM_ots.mp4 -i out_${set}_CAM_close.mp4 -filter_complex \
  "[0:v]trim=start_frame=0:end_frame=100,setpts=PTS-STARTPTS[v0];[0:a]atrim=0:4.1667,asetpts=PTS-STARTPTS[a0];\
[1:v]trim=start_frame=4:end_frame=54,setpts=PTS-STARTPTS[v1];[1:a]atrim=0.1667:2.2500,asetpts=PTS-STARTPTS[a1];\
[2:v]trim=start_frame=18:end_frame=48,setpts=PTS-STARTPTS[v2];[2:a]atrim=0.7500:2.0000,asetpts=PTS-STARTPTS[a2];\
[3:v]trim=start_frame=36:end_frame=73,setpts=PTS-STARTPTS[v3];[3:a]atrim=1.5000:3.0417,asetpts=PTS-STARTPTS[a3];\
[v0][a0][v1][a1][v2][a2][v3][a3]concat=n=4:v=1:a=1[v][a]" -map "[v]" -map "[a]" -c:v libx264 -crf 18 -c:a aac cut_$set.mp4
done
for set in plate grey; do for c in CAM_wide CAM_two CAM_ots CAM_close; do
  ffmpeg -v error -y -i pass_${set}_$c/playblast.mp4 -i out_${set}_$c.mp4 -filter_complex "[0]select=eq(n\,40),scale=480:-2[a];[1]select=eq(n\,40),scale=480:-2[b];[a][b]vstack=inputs=2" -frames:v 1 -vsync 0 cmp_${set}_$c.jpg
done; ffmpeg -v error -y -i cmp_${set}_CAM_wide.jpg -i cmp_${set}_CAM_two.jpg -i cmp_${set}_CAM_ots.jpg -i cmp_${set}_CAM_close.jpg -filter_complex hstack=inputs=4 sheet_$set.jpg; done
ls -la cut_*.mp4 sheet_*.jpg
