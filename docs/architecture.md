# Blender × Text-to-Motion × H3: add-on architecture

Status: draft for review · 2026-10-09
Companion to the "Blender × Text-to-Motion × H3" proposal deck.

> Implementation notes for the add-on, including where it deviates from this
> document and why, are in [addon.md](addon.md).
>
> **Decision, 2026-10-10: VACE is dropped. H3 is the only video model.**
> Everything below about Path A / VACE (§4.3 pose/depth/line passes, §4.4
> Path A, VACE segment extension in §4.4a, VACE residency in §7, the P0 VACE
> comparison) is superseded. The passes the pipeline still needs are
> `kp2d.json` (eval), `first.png` / `last.png` (H3 first/last frame) and
> `playblast.mp4` (H3 reference video). The shot JSON `render` block is now
> `{"model": "h3", "mode": "flf" | "ref2v", "audio_prompt", "resolution"}`.

---

## 1. Goals and non-goals

**Goals**
- Go from a text brief to a multi-shot animated video, with Blender as the single place where you edit motion, camera and timing.
- Edit one shot and re-render only that shot. Each stage is cached on a hash of its inputs.
- Make the models swappable: Kimodo ↔ HY-Motion, VACE ↔ H3 ↔ whatever comes next, without changing the add-on.
- Score generated video against ground truth from Blender (2D keypoints) on every run.

**Non-goals (v1)**
- Multi-character interaction, animals, facial or finger animation. Current text-to-motion models can't do these.
- Running inference inside Blender's Python.
- A web front end. Blender *is* the front end.
- Cloud or multi-user. One workstation with two GPUs.

---

## 2. Process topology

```
┌──────────────────────────── Workstation ────────────────────────────┐
│                                                                     │
│  Blender (interactive)                                              │
│   └─ chimera_director add-on  ── HTTP + WS ──┐                      │
│        (UI, scene I/O only)                  │                      │
│                                              ▼                      │
│                                  Orchestrator (FastAPI)             │
│                                   ├─ SQLite: projects/shots/jobs    │
│                                   ├─ Content-addressed store (CAS)  │
│                                   ├─ Scheduler (GPU-aware)          │
│                                   └─ Eval (PCK vs GT)               │
│                    ┌──────────────┼───────────────┬───────────┐     │
│                    ▼              ▼               ▼           ▼     │
│           Planner LLM      Motion server    Blender -b    ComfyUI   │
│           (OpenAI-compat)  (Kimodo / HY)    workers       (5090)    │
│           GPU1: 4090       GPU1: 4090       CPU/EEVEE     VACE, H3  │
└─────────────────────────────────────────────────────────────────────┘
```

| Process | Lifetime | Owns | Talks to |
|---|---|---|---|
| **Add-on** (inside Blender) | Blender session | UI, scene reads/writes, `.blend` snapshots | Orchestrator only |
| **Orchestrator** | Long-running daemon | Shot state, job DAG, cache, GPU scheduling, eval | Everything |
| **Planner LLM** | Daemon, unloaded when idle | Brief → shot list | Orchestrator |
| **Motion server** | Daemon | Text → motion clips (canonical skeleton) | Orchestrator |
| **Blender workers** | One per job (`blender -b`) | Retarget, control passes, keyframes, playblast | Orchestrator (stdin/out JSON) |
| **ComfyUI** | Daemon on the 5090 | Video generation | Orchestrator (`/prompt`, WS) |

**Why the add-on stays thin:** all three existing Kimodo add-ons already isolate inference in a separate venv and server. Torch inside Blender's Python means version conflicts with Blender's bundled Python, a frozen UI and crashes that lose scene state. The add-on does only `bpy` work and HTTP.

**Why the add-on talks only to the orchestrator:** if the add-on called ComfyUI and Kimodo directly, cache logic, retries and GPU contention would end up inside Blender. One API surface makes the add-on easy to test.

---

## 3. Data model

### 3.1 Shot JSON: the contract

```jsonc
{
  "id": "sh_010",
  "project": "demo",
  "order": 10,
  "duration_s": 6.0,                 // any length; > segment cap → split into segments (§4.4a)
  "fps": 24,                         // fixed project-wide
  "characters": [
    {
      "char_id": "hero",
      "rig": "assets/hero.blend#HeroRig",
      "refs": ["refs/hero_front.png", "refs/hero_side.png"],
      "lora": null
    }
  ],
  "action_prompt": "a man vaults over a low wall and lands in a crouch",
  "motion": {
    "backend": "kimodo",
    "seed": 1234,
    "variants": 4,
    "selected_variant": 2,
    "source": "generated"            // "generated" | "manual" (user edited in Blender)
  },
  "camera": {
    "mode": "authored",              // "authored" | "template"
    "template": "dolly_in_35mm",
    "object": "CAM_sh_010"
  },
  "look": {
    "prompt": "overcast concrete rooftop, cinematic, 35mm film grain",
    "style_refs": []
  },
  "render": {
    "path": "A",                     // "A" VACE | "B" H3 (alternatives, never chained)
    "a": { "controls": ["pose", "depth"], "steps": 30 },
    "b": { "mode": "flf", "audio_prompt": "wind, distant traffic" },
    "resolution": [1280, 720]
  },
  "hashes": {}                       // filled by the orchestrator per stage
}
```

The shot JSON is the **single source of truth**. The `.blend` file holds the editable result of stages 2–3. When you edit a shot in Blender, the add-on updates `motion.source = "manual"` and the scene hash, and the orchestrator invalidates the downstream stages.

### 3.2 Scene conventions (enforced by the add-on)

| Thing | Convention |
|---|---|
| Shot container | Collection `SHOT_<id>` |
| Shot timing | Scene markers `<id>_in` / `<id>_out` on a shared timeline; one scene |
| Camera | `CAM_<id>`, bound to the `<id>_in` marker |
| Motion | Action `MO_<id>_<char>_v<n>`; the selected variant is assigned to the rig, the others are kept with a fake user |
| Rig | Library override of the character `.blend` |
| Output | VSE strip `VID_<id>` on channel 2; the playblast on channel 1, muted |

### 3.3 Storage

```
project_root/
  project.json                 # shot order, global look, fps, resolution
  shots/<id>.json              # the contracts
  scene.blend                  # interactive scene
  cas/<sha256>/…               # every stage's outputs, content-addressed
  cache_index.sqlite           # (stage, input_hash) → cas key
  eval/<run_id>.parquet        # PCK, identity scores
```

---

## 4. Pipeline stages

Each stage is a pure function `(inputs) → outputs`. Its cache key is `sha256(stage_version ‖ canonical_json(inputs) ‖ input_artifact_hashes)`.

| # | Stage | Runs in | Inputs | Outputs | Invalidated by |
|---|---|---|---|---|---|
| 1 | **plan** | Planner LLM | brief, character list | `shots/*.json` drafts | brief edit (manual only; never auto-replans) |
| 2 | **motion** | Motion server | action_prompt, duration, seed, backend | N clips on the canonical skeleton (`.npz`) | prompt, duration, seed, backend |
| 3 | **retarget** | Blender worker | clip, rig | Action on the rig (in a `.blend` snapshot) | motion, rig version |
| 4 | **passes** | Blender worker | scene snapshot, shot id | `kp2d.json`, `pose/*.png`, `depth/*.exr`, `line/*.png`, `first.png`, `last.png`, `playblast.mp4` | any change to the scene hash for that shot |
| 5 | **generate** | ComfyUI | passes, look, refs, render settings | `out.mp4` (+ `audio.wav` for H3) | passes, look, render settings |
| 6 | **eval** | Orchestrator | `kp2d.json`, `out.mp4` | PCK per frame and joint | generate |
| 7 | **assemble** | Add-on (live) | all `out.mp4` | VSE strips | any shot's generate output |

### 4.1 Stage 2: motion server

- **Fork, don't depend.** Vendor the server half of `atticus-lv/kimodo_motion` (GPL-3, fine for a Blender add-on). Wrap it in a stable interface:
  - `POST /generate {prompt, duration_s, seed, n}` → `[{clip_id, fps, joints: SOMA77, root_traj, rotations}]`
- **Canonical skeleton:** SOMA-77 (Kimodo native). If you add HY-Motion, convert SMPL-X → SOMA at the motion server, so retargeting only ever sees one source skeleton.
- **fps:** resample to the project fps (24) at the motion server. *Unverified: Kimodo's native output fps. Check it.*
- **Residency:** about 16 GB on the 4090, plus the Llama-3-8B text encoder. Unload the planner LLM while it runs.

### 4.1a Spatial targeting (constraints authored in Blender → Kimodo)

Without this, every clip starts at the origin facing +Z with no knowledge of the scene. Kimodo takes sparse or dense constraints (`root2d`, `fullbody`, `end-effector`/hand/foot), so the generator learns where things are. The network follows them only approximately, and Kimodo's post-process optimization makes the motion hit them exactly.

**Authoring primitives** (per shot, in `SHOT_<id>`; the add-on supplies operators and gizmos):

| Blender object | Kimodo constraint | Notes |
|---|---|---|
| `START_<char>` empty | defines the canonical frame | Location gives the root start and its +Y axis the facing. The clip is placed back into the world by this matrix. |
| `PATH_<char>` curve | `root2d` dense (`smooth_root_2d`, `global_root_heading`) | Sampled per frame; pacing comes from arc length / duration or a `t` F-curve on the curve |
| `WP_<char>_<frame>` empty | `root2d` sparse | Waypoint with a frame property; optional heading from the empty's rotation |
| Rig posed on frame *f* (pose marker) | `fullbody` at *f* | For "lands in a crouch". Exported as joint rotations plus root position |
| `HAND_R_<frame>` / `FOOT_L_<frame>` empty | `end-effector` | Needs a full pose: the worker IK-solves the rig to the target at that frame and exports the FK result; only the named joints are constrained |
| Previous shot's last frame | `fullbody` at frame 0 | Continuity across cuts (opt-in per shot) |

**Transform:** world (Z-up) → `START` local → Kimodo canonical (Y-up, metres, root XZ = (0,0) at frame 0, `first_heading_angle = 0`). Inverse on import.

**Proportion mismatch:** constraints are solved on Kimodo's skeleton (somaskel77 → somaskel30 internally), not your rig. Scale root positions and targets by `rig_hip_height / soma_hip_height` before export. After retargeting, run an IK touch-up pass on the contact frames (hand on wall, foot plants) so the *rig*, not the SOMA proxy, hits the target.

**Not handled:** scene geometry. Kimodo doesn't see the wall. The worker runs a penetration and float check (raycasting feet and hands against tagged collision geometry) and flags the shot; it doesn't fix it.

**Verify before relying on it:** whether the Python API you vendor accepts constraint JSON end to end (the docs say foot contacts aren't exposed in the API), and Kimodo's native output fps.

### 4.2 Stage 3: retarget

- Reuse the `mathutils` rest-delta retarget from `kimodo_motion` (no FBX SDK; Y-up → Z-up).
- Bone maps are per rig, stored as `rigs/<rig>.bonemap.json`, created once by a mapping operator in the add-on (with an auto-guess plus manual fixes).
- Post-pass: foot-contact cleanup (lock feet below a velocity threshold), root height offset for the rig's scale. Keep this simple in v1; foot sliding is the known artifact.

### 4.3 Stage 4: control passes (Blender worker)

Run headless on a **snapshot**: the add-on calls `bpy.ops.wm.save_as_mainfile(filepath=snap, copy=True)` so the worker never touches the user's live file and the UI never blocks.

| Pass | How |
|---|---|
| `kp2d.json` | For each frame: evaluate armature pose bones → world positions → `bpy_extras.object_utils.world_to_camera_view` → pixel coordinates. Visibility comes from `scene.ray_cast` from the camera to each joint (occluded joints marked `v=0`). Map SOMA → COCO-18 / OpenPose with a static table. |
| `pose/*.png` | Drawn **in the orchestrator** from `kp2d.json` using the standard OpenPose colours (cv2), not in Blender. That keeps one renderer for both control and eval. |
| `depth/*.exr` | Z pass → normalized per shot (near/far from the camera clip range), inverted to match the depth convention the VACE workflow expects. *Check the convention against the Depth Anything output the RunComfy workflow was tuned on.* |
| `line/*.png` | Grease Pencil Line Art modifier, EEVEE, white on black. |
| `first.png` / `last.png` | EEVEE beauty render of the in/out frames (for H3 first/last frame). An optional image-model restyle happens in ComfyUI, not here. |
| `playblast.mp4` | Viewport render, 24 fps, for review and as the H3 reference video. |

Frame count: snap to **4n+1** for Wan by extending the out-point (hold the last pose) instead of trimming motion.

### 4.4 Stage 5: generate (ComfyUI)

- **Workflow templates:** API-format JSON graphs in `workflows/`, one per render path. Each has a small manifest that maps contract fields → `node_id.input`:
  ```yaml
  # workflows/h3_flf.manifest.yaml
  inputs:
    prompt:      "12.text"
    first_frame: "7.image"
    last_frame:  "8.image"
    frames:      "4.length"
  ```
  The orchestrator loads the template, injects values, uploads images through `/upload/image`, posts `/prompt` and tracks progress over WS.
- **Known gotcha:** H3 reference inputs are dot-namespaced (`ref_images.ref_image_0`). A bare name is **silently ignored**. Every manifest gets a validation step that diffs the injected keys against `/object_info`.
- **Path A (VACE):** pose + depth by default; line art optional. Character reference image plus an optional Wan LoRA per character.
- **Path B (H3, local):** `MiniMaxH3ImageToVideo` (first/last frame) is the dependable mode. `MiniMaxH3ReferenceToVideo` takes character photos plus the playblast as a video reference (confirmed working locally). P0 measures how closely it follows the playblast.
- **No hybrid path.** If H3 doesn't follow a reference video, a VACE render as the reference won't fix that. The only real question is the domain gap (grey mannequin vs. shaded character), and P0 tests that directly with an EEVEE-shaded reference.
- **VACE is conditional.** If P0 shows H3 Ref2V following the reference closely (PCK near VACE), drop Path A and its workflow entirely.
- **GPU residency on the 5090:** VACE 14B FP8 and H3 NVFP4 (about 15.5 GB) don't sit together comfortably. The scheduler batches jobs **by model** (all pending VACE jobs, then all H3 jobs) to minimize swaps.

### 4.4a Continuous shots longer than one generation

Caps: about 10 s per Kimodo call and 15 s per H3 generation. A longer continuous shot is split into **segments** that overlap.

**Choosing boundaries:** Blender already has the motion, so put each handoff at a frame where joint and root velocity are lowest, not at a fixed interval. Seams are least visible where little is moving.

**Motion side (stage 2):** segment k+1 gets a `fullbody` interval constraint over its first *m* frames, taken from the last *m* frames of segment k. The overlap is crossfaded (rotation slerp) during retargeting.

**Video side, H3: a custom node, `H3LatentContinue`**
- Inputs: the previous segment's final AV latent, the overlap in latent frames, and the new segment's conditioning.
- Method: temporal inpainting. Seed the new segment's first *k* latent frames with the previous segment's tail. At every sampler step, re-inject them, noised to the current sigma, so only the new frames are denoised. Drop the overlap at decode.
- **The audio latents must be stitched the same way**, or the sound has a seam even when the picture doesn't.
- The overlap has to line up with the VAE's temporal compression. Causal video VAEs usually encode the first frame differently from the rest; check this for H3's VAE before choosing *k*.

**Video side, VACE:** VACE has frame-masked extension built in. Feed the previous segment's tail frames as known frames, and the rest is driven by the control passes as usual. No custom node needed.

**Risks**
- H3 is guidance-distilled. Re-injecting known latents usually works on distilled models, but that's unverified for H3. This is the first thing to prototype.
- Drift compounds from segment to segment (identity, colour, lighting). Re-supply the character references on every segment, and gate shots of three or more segments behind an eval check.
- Fallback if latent continuation fails: last frame → first frame chaining through FLF. It's cheap but loses momentum and audio continuity, so it only works at low-velocity boundaries.

**Eval:** PCK inside the seam window, plus a seam score: the frame-difference spike at the boundary against the segment's own baseline.

### 4.5 Stage 6: eval

- Run DWPose on `out.mp4` and compare with `kp2d.json` (visible joints only). Report PCK@0.1 of the bbox diagonal, per frame and per joint.
- Identity: the cosine similarity of face or body embeddings to the character refs is a v2 item. In v1, a 1–5 score you enter in the panel.
- Results feed the add-on panel (a score badge per shot) and `eval/*.parquet` for the P0 analysis.

---

## 5. Add-on internals

### 5.1 Module layout

```
chimera_director/
  __init__.py            # bl_info / blender_manifest.toml (Blender 5.0 extension)
  props.py               # PropertyGroups: ProjectProps, ShotProps (mirror of shot JSON)
  client.py              # orchestrator HTTP client (urllib; no third-party deps)
  events.py              # WS listener thread → queue.Queue
  sync.py                # bpy.app.timers tick: drain queue, apply results on main thread
  scene_io.py            # conventions §3.2: create/find shot collections, cameras, actions
  snapshot.py            # save_as_mainfile(copy=True), scene hashing per shot
  ops/
    plan.py              # OT_plan_from_brief
    motion.py            # OT_generate_motion, OT_pick_variant
    passes.py            # OT_render_passes
    generate.py          # OT_generate_video (path A/B/AB)
    assemble.py          # OT_assemble_vse
    rig_map.py           # OT_build_bonemap
  ui/
    panel_project.py     # brief, global look, fps/res, server status
    panel_shot.py        # active shot: prompt, variants, camera, path, status, PCK
    list_shots.py        # UIList of shots with per-stage status icons
```

### 5.2 Threading rule

`bpy` is main-thread only. The pattern:

1. Operators post a job to the orchestrator and return `{'FINISHED'}` immediately.
2. `events.py` runs a background thread on the orchestrator WS and pushes `{job_id, stage, status, artifact}` onto a `queue.Queue`.
3. `sync.py` registers a `bpy.app.timers` callback (about 0.25 s) that drains the queue and applies results: imports the Action, loads the VSE strip, updates status icons.

No modal operators for long work, and no `time.sleep` on the main thread.

### 5.3 Scene hashing

Per-shot hash = `sha256` over: the shot's Action F-curve keyframes (rounded), camera matrix world per frame, lens, rig override properties, and the shot's in/out markers. Computed on demand (before stage 4) and on `depsgraph_update_post`, debounced. A hash change marks stages 4 to 7 stale in the UI. Nothing re-renders until you click.

---

## 6. Orchestrator API (v1)

```
POST /projects/{p}/plan                  {brief}               → shot drafts
POST /projects/{p}/shots/{s}/motion      {}                    → job_id
POST /projects/{p}/shots/{s}/passes      {snapshot_path}       → job_id
POST /projects/{p}/shots/{s}/generate    {path}                → job_id
GET  /projects/{p}/shots/{s}             → shot JSON + per-stage {status, hash, artifact}
GET  /jobs/{id}                          → status, progress, logs tail
POST /jobs/{id}/cancel
GET  /health                             → planner/motion/comfy/gpu residency
WS   /events                             → {job_id, shot, stage, status, progress, artifact}
```

Job states: `queued → waiting_gpu → running → done | failed | cancelled`. Failures keep logs in the CAS and can be retried.

---

## 7. GPU scheduling

| GPU | Resident by default | Swapped in on demand |
|---|---|---|
| 4090 (24 GB) | Motion server (Kimodo) | Planner LLM (unload Kimodo first) |
| 5090 (32 GB) | ComfyUI | VACE ⇄ H3 (batch by model) · HY-Motion fallback (26 GB, exclusive) |

The scheduler is a single priority queue per GPU with "model affinity": it picks the next job that needs the model already loaded, unless a job has waited more than T seconds. Interactive jobs (motion variants) take priority over batch work (full-sequence renders).

---

## 8. Repo layout

```
director/
  addon/chimera_director/        # §5
  orchestrator/                  # FastAPI, scheduler, CAS, eval
  motion_server/                 # vendored Kimodo server + adapters
  blender_worker/                # retarget.py, passes.py (run via blender -b --python)
  workflows/                     # ComfyUI API graphs + manifests
  rigs/                          # bonemaps
  eval/                          # P0 notebooks, PCK code
```

---

## 9. Milestone mapping

| Phase | Ships | Components |
|---|---|---|
| **P0** spike | PCK for VACE / H3-FLF / H3-Ref2V (grey playblast vs. EEVEE-shaded reference); decides whether VACE survives | `blender_worker/passes.py`, `eval/`, two workflows. No add-on UI, just scripts. |
| **P1** single shot | Prompt → video for one shot from the panel | add-on §5, orchestrator stages 2–5, motion server, Path A only |
| **P2** multi-shot | Planner, cache/invalidation, VSE assembly, per-character LoRA | stages 1, 7; §5.3 hashing; scheduler |
| **P3** H3 layer | Path B, audio strips in VSE, long continuous shots | H3 manifests, audio ingest, `H3LatentContinue` node, segmenting (§4.4a) |

---

## 10. Open questions to settle

1. **Shot-to-scene mapping.** One scene with markers (proposed) or one Blender scene per shot? Markers keep a single timeline for assembly; separate scenes isolate shots more cleanly and make snapshots smaller.
2. **Who owns camera authoring?** Templates from the planner, or always hand-authored? This decides whether stage 1 writes cameras at all.
3. **Manual motion edits vs regeneration.** If a shot is `motion.source = "manual"`, should "regenerate motion" be blocked, stored as a new variant, or overwrite? (Proposed: always a new variant; never destroy edits.)
4. **Canonical skeleton.** SOMA-77 (Kimodo native) or SMPL-X (wider ecosystem, HY-Motion native)? This affects the retarget code and the eval mapping.
5. **Planner model choice.** Which local model on the 4090 (the swap against Kimodo is accepted; see Decisions).
6. **Who places the targets?** v1: you place them in Blender. Later: the planner emits semantic targets (`"to": "DOOR_A"`) resolved against named scene empties. It shouldn't emit raw coordinates.

### Decisions

- **Planner runs locally on the 4090**, swapped with Kimodo; the swap cost is accepted. The scheduler unloads one before loading the other (§7).
- **Standalone tool.** No coupling to the Chimera quest engine's formats; the shot JSON is designed only for this pipeline.
- **Local H3 Ref2VA takes reference video.** Path B runs locally.
- **No hybrid (VACE → H3) path.** VACE and H3 are alternatives, and P0 decides whether VACE stays at all. Continuous shots longer than one generation need a latent-stitching custom node (§4.4a).
