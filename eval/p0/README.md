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
