# Orchestrator (`server/`)

The add-on's backend, self-hosted: text -> Kimodo motion -> your rig in Blender
-> MiniMax H3 video -> PCK. It speaks the HTTP contract in
[docs/addon.md](../docs/addon.md#orchestrator-contract) and calls only local
processes and servers (no hosted model APIs). Stdlib Python 3.10+; the models
run in their own environments.

```
python -m server.orchestrator --config server/config.example.json      # real backends
python -m server.orchestrator --fake                                   # no models, no GPU, no Blender
python -m server.orchestrator --fake --backend retarget=blender --backend passes=blender \
    --blender /path/to/blender --set passes.still_engine='"CYCLES"'    # drives the add-on with fake models
python -m server.orchestrator --config my.json --print-config          # effective config
```

Point the add-on's preference *Orchestrator URL* at it (default `http://127.0.0.1:8765`).

## Layout

| File | What |
|---|---|
| `orchestrator.py` | HTTP server (ThreadingHTTPServer), job runner, health, CLI |
| `pipeline.py` | Stage logic: motion (+ ranking, retarget), passes, generate (+ eval), cache keys |
| `jobs.py` | Job store (JSON files), per-GPU locks, cancellable subprocesses |
| `state.py` | Projects, shots, per-stage status, cache index (JSON under the project root) |
| `config.py` | Defaults, config file (JSON / TOML), `--set` overrides |
| `kimodo.py` | Add-on constraints -> Kimodo (24 -> 30 fps), CLI command, output discovery |
| `takes.py`, `npyio.py` | Take ranking from Kimodo's `.npz` (stdlib .npz reader) |
| `h3.py` | H3 ComfyUI graphs (ported from `eval/p0/h3run.py`), prompt, reference images, 17k+5 snapping |
| `fakemotion.py` | Synthetic Kimodo-format takes (BVH + npz) for the fake backend and tests |
| `backends/` | One class per backend, each with a `fake` |
| `workers/retarget.py` | Headless Blender: BVH takes -> actions on the user's rig |
| `workers/pck_eval.py` | DWPose PCK@0.1 (same method as `eval/p0/pck.py`) |

## Configuration

Defaults are in `config.py`; a file overrides them key by key and CLI flags
override the file (`--port`, `--data`, `--blender`, `--fake`,
`--backend stage=impl`, `--set dotted.key=<json>`, `--no-cache`).
`config.example.json` is a 5090 + 4090 workstation: ComfyUI/H3 on `gpu0`
(5090, 32 GB), Kimodo on `gpu1` (4090, 24 GB).

* `gpus` names the cards. A backend's `device` names one: the job holds that
  card's lock while the stage runs (status `waiting_gpu` while another stage
  has it) and subprocesses get the card's `env`, e.g. `CUDA_VISIBLE_DEVICES`.
  With Kimodo and H3 on different cards they run in parallel; put both on one
  name to serialise them on a single GPU. ComfyUI is a separate daemon: start
  it pinned to the same card (`CUDA_VISIBLE_DEVICES=0 python main.py`); the
  orchestrator only serialises its own use of it.
  Use `CUDA_DEVICE_ORDER=PCI_BUS_ID` so CUDA numbers cards like `nvidia-smi`.
* `motion`: `venv` (Kimodo's venv; `<venv>/bin` goes first on PATH),
  `command` (default `["kimodo_gen"]`), `extra_args`, `env`, `num_samples`
  (takes per round, default 4), `max_rounds` (extra rounds with new seeds when
  too few takes pass), `keep_rejected`, `ranking` (thresholds below).
* `retarget` / `passes`: `blender` (else the top-level `blender`); passes take
  `still_engine`, `playblast_engine`, `resolution_scale` and an optional `device`.
* `generate`: `url`, model file names, `ref2v` / `flf` `{unet, lora, steps}`,
  `sampler`, `scheduler`, `seed`, `max_frames` (362), `resolution_multiple` (16),
  `comfy_root` (copy files on disk instead of `/upload/image` + `/view`), `websocket`.
* `eval`: `python` (an interpreter with `rtmlib`, `opencv-python`,
  `onnxruntime[-gpu]`), `rtmlib_device` (`cpu` / `cuda`), optional `device`.
* `plan`: `heuristic` (sentence split) or `llm` (a local OpenAI-compatible
  server such as llama.cpp or vLLM at `url`, falling back to the heuristic).

## Stages

### motion -> retarget (one job)

1. The add-on's root2d constraints are resampled to Kimodo's 30 fps: dense runs
   are re-interpolated on every 30 fps frame (heading through the unwrapped
   yaw), sparse frames map to `round(f * 30 / 24)`, and each array keeps the
   length of `frame_indices`. The prompt is split into sentences, each with an
   equal share of the shot's duration (`--duration "2 2"`).
2. `kimodo_gen "<sentences>." --duration ... --constraints <json> --seed N
   --num_samples K --output <stem> --bvh`, with the motion GPU's env. Outputs
   are found as `<stem>.npz|bvh`, `<stem>_<k>.*` or `<stem>/*` and paired by name.
3. Each take is measured from its `.npz` (`posed_joints` (T,J,3), `root_positions`
   (T,3), Y up): rejected if the lowest joint is above `float_height` (0.15 m) on
   more than `max_float_frac` (25 %) of frames, or the root jumps more than
   `max_root_step` (0.5 m) between frames. Survivors are sorted by mean root
   distance to the root2d targets plus the floating and penetration fractions.
   If fewer than `motion.variants` survive, another round runs with a new seed
   (`max_rounds`); if none survive the job fails (or, with `keep_rejected`, returns
   the least bad take with a warning). Per-take metrics go to `takes.json`.
4. Retarget (`workers/retarget.py`, headless Blender) produces the add-on's
   artifact: `blend` (only the actions) + `actions: [{char, action, variant}]`.

**Retarget method.** The BVH is imported with `axis_forward=-Z, axis_up=Y,
global_scale=0.01, use_fps_scale` (cm, Y-up, 30 -> 24 fps), so the SOMA
skeleton faces -Y. It is placed at `START @ Rz(180) @ S(1/scale)`: Rz(180)
turns that facing onto START's +Y (the add-on's canonical frame is `(-x, z, y)`
of START-local) and `1/scale` undoes the add-on's `soma_hip / rig_hip`, so hip
translation lands at the rig's scale. The target armature is rebuilt from the
rest data the add-on now sends (`characters.<id>.rig`: bone names, parents,
rest matrices, rotation modes, rig world matrix, bone map); failing that it is
appended from the shot's `rig: "file.blend#Object"`. Per mapped bone, world space:

    R_tgt(f) = R_src(f) · R_src_rest⁻¹ · Q · G · R_tgt_rest

`G` is the yaw between the two skeletons' rest facings (from the hip joints),
`Q` the swing that points the target's rest limb (joint -> mapped child joint)
along the source's, so A-pose rigs take T-pose motion. The pelvis also takes
the source hips' world position. World rotations go through the rig's hierarchy
into each bone's basis and are keyed in its rotation mode (quaternion, Euler or
axis-angle). If every BVH bone exists on the rig (a SOMA rig), all bones map
1:1 with `Q = G = I`. Without any rig data the BVH action is returned
unretargeted (SOMA bone names) with a warning.

Checked in Blender 4.2.3 and 5.0.1 on the demo stick rig and on a SOMA rig
(tests below): hips follow the root2d path from START at hip height, feet reach
the ground, left/right stay on the right sides.

### passes

`blender -b <snapshot> --python blender_worker/passes.py -- <spec.json>`, spec =
the request body + `out_dir` + the configured engines. Artifact: `playblast`,
`first`, `last`, `kp2d`, `dir`.

### generate -> eval (one job)

Needs the shot's last passes. Frames = the kp2d frame count snapped to 17k+5
(<= 362), resolution = `render.resolution` rounded to multiples of 16, seed =
`render.seed` or the config's.

* **ref2v** (`MiniMaxH3ReferenceToVideo`, `minimax_h3_ref2va_pruned_int8_convrot`,
  8-step turbo LoRA, `res_multistep` / `simple`): the playblast is `<Video 1>`
  (`ref_videos.ref_video_0`); up to 9 reference images are `ref_images.ref_image_N`
  / `<Picture N+1>`, taken from `characters[].refs` (per character) then
  `look.style_refs`. Paths: absolute, project-root relative or `//`-relative.
* **flf** (`MiniMaxH3ImageToVideo`, `minimax_h3_fl2va_pruned_int8_convrot`, 20
  steps): `first.png` and `last.png`.

The prompt is `action_prompt. look. <Picture> tags. <Video 1> motion clause.
Audio: render.audio_prompt.`; `render.prompt` replaces it. Inputs are uploaded
with `/upload/image`, the graph posted to `/prompt` (its JSON is saved next to
the output), step progress read from `/ws`, completion from `/history`, the mp4
fetched with `/view`. Cancel deletes the prompt from the queue and calls
`/interrupt`. Artifacts arrive in order: `video` (+ `audio`, the same mp4) at
stage `generate`, then `pck` at stage `eval` (`workers/pck_eval.py` in the eval
interpreter, PCK@0.1 averaged over the shot's characters).

## Jobs, cache, storage

* Jobs: `<data_dir>/jobs/<id>.json` (+ `.log`). States `queued -> waiting_gpu
  -> running -> done | failed | cancelled`. A job interrupted by a restart is
  marked failed. `GET /jobs` lists recent jobs; `GET /jobs/{id}` adds
  `warnings`, `cache_hit`, `input_hash` and `logs_tail` to the contract fields.
* Cancel (`POST /jobs/{id}/cancel`) kills the subprocess group (Kimodo,
  Blender, eval) or interrupts ComfyUI.
* Projects: `PUT /projects/{p}` with `root` places the project; without one it
  lives in `<data_dir>/projects/<p>`. Under the root: `project.json`,
  `shots/<id>.json` (with the server's `hashes`), `cas/<stage>/<key>/` outputs,
  `orchestrator/state.json` (per-shot stage status, last artifacts) and
  `orchestrator/cache_index.json`.
* Cache: each stage's key is a SHA-256 of its inputs from the shot JSON and
  request (motion: prompt, frames, seed, variants, constraints, START, scale,
  rig rest data, sampling settings; passes: the request incl. `scene_hash` and
  engines; generate: mode, prompt, frames, resolution, seed, model settings,
  reference image sizes/mtimes and the passes key). An unchanged key with its
  files still present returns the earlier artifact (`cache_hit: true`). A body
  with `"force": true`, `"cache": false` in the config or `--no-cache` skips it.
  Regenerating cached motion re-imports the same takes as new variant numbers;
  change the seed for new ones.

`GET /health` answers from a cache refreshed every 15 s: one line per backend
(Kimodo venv/executable, Blender version, ComfyUI version + GPU + H3 nodes,
eval interpreter imports), each GPU's holder, and job counts.

## Tests

```
pytest tests/test_server.py                       # 34 tests; Blender ones need BLENDER=...
BLENDER=/path/to/blender pytest tests/test_server.py
BLENDER=/path/to/blender tests/run_tests.sh --e2e-server   # add-on suite against this server
flake8 --max-line-length 120 server tests/test_server.py
```

`tests/fixtures/fake_kimodo_gen.py` and `tests/fixtures/fake_comfyui.py` stand
in for Kimodo and ComfyUI so the real backend code paths are exercised.

## Known gaps

* **end_effector constraints are dropped** (logged as a job warning). Kimodo
  needs a full-pose constraint (local rotations of all joints) at that frame;
  the add-on sends one joint target. TODO: IK-solve a SOMA pose (root from the
  root2d path, neutral pose, two-bone IK on the limb) and emit Kimodo's entry.
* **Kimodo's CLI details are assumed**, not verified here: output naming for
  multi-sample runs (discovery is tolerant) and the constraint file being a
  JSON list of `{type: "root2d", frame_indices, smooth_root_2d, global_root_heading}`.
* **Retarget** has no foot-contact cleanup or IK touch-up; proportions that
  differ from SOMA (beyond the hip-height scale) slide or float the feet a
  little. Twist around limb axes comes from the rest delta only. Rigs whose rest
  pose is not upright, and rigs with scaled bones, are not handled. Kimodo's
  77 joints are reduced to the add-on's 22 canonical joints (fingers stay at
  rest) unless the rig is SOMA.
* `continuity` (fullbody constraint from the previous shot) is ignored with a warning.
* Multi-character shots generate each character independently.
* Long shots are not segmented: > 10 s warns (Kimodo), > 362 frames fails (H3).
* The GPU lock covers only this orchestrator's jobs; anything else on the card
  (another ComfyUI client) is not seen.
