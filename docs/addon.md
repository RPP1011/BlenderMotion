# chimera_director add-on: implementation notes

This covers what the add-on in `addon/chimera_director/` does, the contract it
expects from the orchestrator, and where it departs from
[architecture.md](architecture.md) and why.

Tested on Blender **4.2.23 LTS** and **5.0.1** (`tests/run_tests.sh --e2e`).

## Install

* **Extension (4.2+):** `blender --command extension build --source-dir addon/chimera_director`,
  then *Preferences → Get Extensions → Install from Disk*.
* **Development:** symlink `addon/chimera_director` into
  `<user scripts>/addons/` and enable "Chimera Director".

The UI is in the 3D Viewport sidebar, under the **Director** tab. Set the
orchestrator URL in the add-on preferences (default `http://127.0.0.1:8765`).

## Working without the real backend

```
python tools/mock_orchestrator.py --blender /path/to/blender
```

The mock implements the API below with no models. Motion jobs run a headless
Blender that generates object-level root motion following your START, PATH and
waypoint targets, so you can check the constraint round trip by eye. Passes
jobs render a low-res playblast. Generate jobs copy the playblast as the
"video" and report a random PCK. It is there to exercise the add-on, not to
stand in for the real worker. It produces no keypoints, depth or line art.

## Workflow in the panel

1. **Project:** set the name and root, create a brief text block, and **Plan Shots
   from Brief**, or add shots by hand with **+**.
2. **Characters:** set `Library` to `assets/hero.blend#HeroRig` and click the
   link icon (this creates a library override), or point `Rig` at any armature.
3. **Targets** (optional): place **Start** first (at the 3D cursor; +Y is the
   facing direction), then **Path**, **Waypoint** (uses the current frame) or
   **Effector**.
4. **Generate Motion:** variants arrive as `MO_<shot>_<char>_v<n>`. Click a
   variant number to swap it in.
5. **Render Control Passes**, then **Generate Video**. The strips appear in the
   `<scene>_EDIT` scene.
6. Edit the camera, motion or markers. The affected stages turn *stale*.
   Nothing re-runs until you click.

## Orchestrator contract

All bodies are JSON. Rows marked **added** are not in architecture §6, but the
add-on needs them.

| Call | Body | Response |
|---|---|---|
| `PUT /projects/{p}` **added** | `project.json` fields + `root` | `{}` |
| `PUT /projects/{p}/shots/{s}` **added** | shot JSON (§3.1) | `{}` |
| `GET /projects/{p}/shots/{s}` | | `{shot, stages: {stage: {status, job_id}}}` |
| `POST /projects/{p}/plan` | `{brief, characters: [{char_id, rig}]}` | `{shots: [shot JSON]}` |
| `POST …/shots/{s}/motion` | see below | `{job_id, status}` |
| `POST …/shots/{s}/passes` | see below | `{job_id, status}` |
| `POST …/shots/{s}/generate` | `{path: "A"\|"B"}` | `{job_id, status}` |
| `GET /jobs/{id}` | | `{shot, stage, status, progress, artifact, error}` |
| `POST /jobs/{id}/cancel` | | |
| `GET /health` | | flat `{name: status}` dictionary, shown in the panel |

The add-on PUTs the shot JSON before every job. The orchestrator persists it
(`shots/<id>.json`) and derives its cache keys from it. Fields the add-on does
not model, such as `hashes` and `look.style_refs`, round-trip unchanged.

**Motion body.** Spatial targets go with the request, because they are scene
data the orchestrator cannot see:

```jsonc
{
  "frames": 96, "continuity": false, "scene": "Scene", "variant_offset": 4,
  "characters": {
    "hero": {
      "start_matrix_world": [[...4x4...]],  // null if no START placed
      "scale": 0.5,                         // soma_hip / rig_hip; worker divides on the way back
      "constraints": [
        {"type": "root2d", "mode": "dense",  "frames": [0..n-1], "xz": [[x,z]...], "heading": [rad...]},
        {"type": "root2d", "mode": "sparse", "frames": [24], "xz": [[x,z]], "heading": [null]},
        {"type": "end_effector", "joint": "right_hand", "frame": 40, "position": [x,y,z]}
      ]
    }
  }
}
```

Canonical frame: `(-x, z, y) · scale` of the START-local position, so Y is up,
the character faces +Z, and heading 0 means facing +Z. **This convention has not
been checked against Kimodo.** It lives in one function,
`constraints.to_canonical`.

**Passes body:** `{snapshot_path, scene_hash, scene, frame_in, frame_out
(exclusive), frames, frames_wan, camera, characters: {char_id: rig object
name}}`. The worker must hide characters that are not in the shot.

**Artifacts.** Job artifacts are applied by key, idempotently, and may be
partial while the job is still running:

| Key | Effect |
|---|---|
| `blend` + `actions: [{char, action, variant}]` | Appends the actions, renames them to `MO_<shot>_<char>_v<n>` after any existing variants, and puts the first one on the NLA |
| `playblast` | Movie strip `PB_<shot>`, channel 1, muted |
| `video` | Movie strip `VID_<shot>`, channel 2 |
| `audio` | Sound strip `AUD_<shot>`, channel 3 |
| `pck` | PCK badge; marks eval done |

A job's `stage` field may advance within one job (motion → retarget,
generate → eval). The add-on tracks status per stage.

## Deviations from architecture.md

| Doc | Implementation | Why |
|---|---|---|
| §5.2 WS `/events` listener | Polls `GET /jobs/{id}` from a background thread (2 Hz by default) | A WS client in Blender's Python needs a third-party package or a hand-written RFC 6455 client. Polling a few jobs on localhost costs nothing, and the event shape is the same, so only `events.py` would change. |
| §3.2 "selected variant is assigned to the rig" | Each shot's variant is an **NLA strip** on track `SHOT_<id>` at the in-marker | One rig on one shared timeline can't have a different active action per shot. With the NLA it can, and the strips move when shots are re-laid out. You edit motion in NLA tweak mode. |
| §3.2 VSE in the same scene | VSE lives in `<scene>_EDIT` | Strips in the animation scene turn on sequencer post-processing, so every render, including worker passes, would output the edit instead of the 3D scene. |
| §4.1a names `START_<char>`, `WP_<char>_<f>` | `START_<shot>_<char>` etc.; identity comes from custom properties (`chimera_role/shot/char/frame/joint`) | Object names are global in a .blend, so `START_hero` in two shots would turn into `START_hero.001`. |
| §4.1a scale `rig_hip / soma_hip` | `soma_hip / rig_hip` | The doc's ratio is inverted. A rig twice as tall covers twice the ground, so its targets have to shrink going into SOMA space. |
| §5.3 camera matrix *per frame* | Static matrix + camera action keys + constraint targets | Per-frame evaluation needs `frame_set` over the whole shot inside a depsgraph handler, which is slow and re-entrant. Camera motion that comes only from drivers or an animated parent is **not** detected. Click refresh after such edits. |
| §3.1 shot JSON as single source of truth | Markers are authoritative for timing once they exist; `duration_s` follows them | Otherwise dragging a marker and the JSON fight each other. |
| §6 API | Adds `PUT project`, `PUT shot`; motion/passes bodies carry scene data | Without them the orchestrator never sees edits made in Blender. |

## Known gaps

* **Bone map joint names** are a semantic 22-joint core, not SOMA-77 (those names
  weren't available). The retarget worker has to map SOMA onto them, or the
  list gets replaced.
* **`fullbody` constraints** (posed rig on frame f, continuity from the previous
  shot) are not exported. The add-on sends `continuity: true` and leaves it to
  the worker, which has to evaluate the rig anyway.
* **Hashing cost.** It serializes every keyframe of every shot about once a second while
  you edit. That's fine for tens of shots with 77-bone clips; past that it
  needs incremental hashing.
* No segmenting UI (§4.4a). Long shots are sent as one request.
* `SOMA hip height` (preference, default 0.95 m) is a guess.
