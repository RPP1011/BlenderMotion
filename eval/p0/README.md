# P0 spike: Kimodo -> Blender -> H3 (RunPod RTX 5090, 2026-10-10)

Scripts used on the pod. `run_p0.sh` is the driver; it expects Kimodo `.bvh` clips,
Blender 5.0.1 and ComfyUI (>= 0.30, MiniMax H3 models) on localhost:8188.

- `build_shot.py`: Kimodo BVH -> grey capsule mannequin, rooftop set, tracked camera `CAM_p0`.
  BVH is in cm: import at `BVH_SCALE=0.01`.
- `blender_worker/passes.py` (repo) renders kp2d + stills + playblast from that .blend.
- `h3run.py`: builds the Ref2V (`MiniMaxH3ReferenceToVideo`, ref video = playblast) or
  first/last-frame graph, posts it, copies the mp4. `wf/` holds the stock ComfyUI templates.
- `pck.py`: DWPose (rtmlib Wholebody) PCK@0.1 of the GT bbox diagonal vs kp2d.json.

## Results (124 frames, 960x544, 24 fps, 20 steps, seed 42)

| run | PCK@0.1 | H3 wall time |
|---|---|---|
| walk playblast itself (metric ceiling) | 0.911 | - |
| walk Ref2V, grey Workbench ref | **0.978** | 46 min (cold) |
| vault playblast itself (metric ceiling) | 0.844 | - |
| vault Ref2V, grey Workbench ref | **0.809** | 24 min |
| vault Ref2V, EEVEE-shaded ref | 0.751 | 40 min |
| vault first/last-frame (FLF) | 0.317 | 11 min |

- Ref2V follows the reference's pose, timing, framing and camera move. The real person can
  score above the mannequin because DWPose reads people better than capsules.
- Grey Workbench is the better reference: cheaper and scores higher than shaded EEVEE.
- FLF ignores the motion: it invents its own camera and set, then snaps to the mannequin's
  last frame. Not a usable path for motion transfer.
- Speed: Ref2V sampled at ~49 s/step vs 4.8 s/step for FLF with `ref_image_size: match`
  already set. The reference video's tokens ride every step and push the 5090 (32 GB) into
  offloading. The Qwen3-VL text/ref encode took ~15 min cold.
- DWPose ran on CPU (no CUDA provider in onnxruntime): ~22 min per clip.

Kimodo conventions verified: 30 fps, Y-up, metres, heading 0 faces +Z, left = +X, hips ~1.0 m.

## Speed follow-up (RTX PRO 6000 96 GB, 2026-10-10)

Same walk job (124 frames, 960x544, seed 42), ComfyUI unless noted. `H3_LORA` / `H3_STEPS` env vars on
`h3run.py` add a LoRA and set the step count; `sgreq.py` sends the job to an SGLang server.

| run | steps | s/step | PCK@0.1 | audio mean |
|---|---|---|---|---|
| ComfyUI base | 20 | 9.9 | 0.953 | -39 dB |
| ComfyUI + LightX2V `ref2v_turbo_8step_v1.0_768p` | 8 | 9.7 | **0.982** | -39 dB |
| ComfyUI + LightX2V `ref2v_turbo_4step_v0.1` | 4 | 9.7 | 0.818 | -14 dB (noise) |
| SGLang 0.5.21, same int8 DiT, TE layerwise | 20 | 22 | 0.675 | -17 dB |

- The 46 min on the 32 GB 5090 pod was ComfyUI offloading, not compute: 4 min on 96 GB.
- 8-step Ref2V turbo keeps (slightly beats) base motion adherence and audio level at ~40% of the time.
  4-step loses motion and audio.
- SGLang on one card with the pruned int8 file was slower and worse here; not tuned further
  (short edge 544 is outside its verified 768 recipe). Its single-5090 desktop recipe is in the SGLang
  MiniMax-H3 cookbook.
- Target hardware is a local RTX 5090 + RTX 4090 (no hosted APIs).

## Multi-cut scene at 2 MP (RTX PRO 6000 96 GB, 2026-10-10)

Four shots, three environments, two characters, 1920x1088, 8-step Ref2V turbo LoRA. `build_seq.py` stages
each shot (rooftop, corridor, street meet, over-the-shoulder talk) from Kimodo BVHs; `h3run.py ref2v`
takes up to 9 reference images. Identity comes from H3 turntable character sheets: a 360-degree orbit
around a standing clip, cropped to front/three-quarter/side/back with `crop.py`, passed as Picture 1-8.

- Identity held across all cuts; motion follows the mannequins. Signage text is garbled.
- Time per shot: 124 frames ~10-11 min (~72 s/step), 192 frames ~24 min (~166 s/step).
- `fx.py` adds EEVEE haze, neon lights, DoF and motion blur to a shot for the FX carry-over test.
