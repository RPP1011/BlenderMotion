# Orchestrator (`server/`)

The add-on's backend. It turns text into Kimodo motion, retargets it onto your rig in
Blender, renders passes, generates MiniMax H3 video, and scores it with PCK. Everything runs
locally, with no hosted model APIs. The server itself is stdlib Python 3.10+, and each model
runs in its own environment. It implements the HTTP contract in
[docs/addon.md](../docs/addon.md#orchestrator-contract).

```
python -m server.orchestrator --config server/config.example.json    # real backends
python -m server.orchestrator --fake                                 # no models, GPU or Blender
python -m server.orchestrator --fake --backend retarget=blender --backend passes=blender \
    --blender /path/to/blender                                       # fake models, real Blender workers
python -m server.orchestrator --config my.json --print-config        # show the effective config
```

Point the add-on's *Orchestrator URL* at it (default `http://127.0.0.1:8765`).

## Configuration

Settings are read in this order, each overriding the last: `config.py` defaults, then a
JSON or TOML file, then CLI flags (`--set dotted.key=<json>`, `--backend stage=impl`,
`--no-cache`).

`config.example.json` is set up for a two-GPU workstation: ComfyUI/H3 on `gpu0` (the RTX
5090) and Kimodo on `gpu1` (the RTX 4090). Each stage holds its GPU's lock only while it
runs, so on two cards the two stages run side by side. Put them on one GPU name to run them
one after the other.

Start ComfyUI pinned to the same card yourself. Set `CUDA_DEVICE_ORDER=PCI_BUS_ID` so
the card numbers match `nvidia-smi`.

## What each stage does

| Stage | Runs | Notes |
|---|---|---|
| motion | `kimodo_gen` in Kimodo's venv | Root2d targets are resampled from 24 to 30 fps. A take is rejected if it floats (lowest joint > 0.15 m on > 25 % of frames) or teleports (root step > 0.5 m). Another seed is tried if too few takes pass. |
| retarget | `workers/retarget.py` (headless Blender) | Retargets onto the rig from the rest pose the add-on sends. A SOMA rig maps 1:1. |
| passes | `blender_worker/passes.py` | Renders the playblast, first and last stills, and kp2d on H3's 17k+5 frame grid. |
| generate | ComfyUI, H3 | Ref2V (playblast as `<Video 1>`, up to 9 reference images) with the 8-step turbo LoRA, or first/last frame (FLF). |
| eval | `workers/pck_eval.py` | DWPose PCK@0.1 of the video against `kp2d.json`. |

Each stage has a `fake` backend; `--fake` selects it for all of them. Every stage's output
is cached under a hash of its inputs. Send `"force": true` to re-run. `GET /health` shows
each backend's state and what each GPU is doing.

## Tests

```
BLENDER=/path/to/blender pytest tests/test_server.py
BLENDER=/path/to/blender tests/run_tests.sh --e2e-server     # the add-on suite against this server
```

## Known gaps

- **Hand and foot (end-effector) targets are dropped**, with a warning. Kimodo needs a
  full-body pose at that frame, and the add-on sends a single joint.
- **Characters in one shot are generated separately**, so there is no interaction between
  them. Two-person takes like the `eval/p1` handshake have to be built by hand for now.
- **Retargeting does no foot-contact cleanup.** On rigs whose proportions differ from SOMA,
  the feet slide a little. Non-SOMA rigs keep their fingers at rest.
- `continuity` (carrying the pose over from the previous shot) is ignored. Shots over
  362 frames fail.
- The GPU lock only covers this server's own jobs.
