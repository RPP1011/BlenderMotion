# BlenderMotion

Blender front end for a text → motion → video pipeline: a brief becomes shots,
shots get generated motion (Kimodo / HY-Motion), which you edit in Blender,
and then video (H3) is rendered from Blender passes.

![Director workspace](docs/img/director_workspace.png)

| Path | What |
|---|---|
| `addon/chimera_director/` | The Blender add-on / extension (4.2 LTS – 5.0) |
| `server/` | Self-hosted orchestrator: Kimodo, headless Blender (retarget, passes), ComfyUI H3, DWPose eval; see `server/README.md` |
| `blender_worker/passes.py` | Headless passes worker (kp2d, first/last stills, playblast) |
| `tools/mock_orchestrator.py` | Stdlib stand-in for the orchestrator API; runs headless Blender workers |
| `tools/mock_worker.py` | Mock motion and playblast worker |
| `tools/demo_scene.py` | Builds a demo project (rig, set, 3 shots, targets) |
| `tests/` | Add-on: `BLENDER=/path/to/blender tests/run_tests.sh [--e2e \| --e2e-server]`; server: `pytest tests/test_server.py`; UI screenshots: `tests/gui_capture.sh` |
| `docs/architecture.md` | System design |
| `docs/addon.md` | Add-on usage, orchestrator contract, deviations from the design |

Run the real orchestrator with `python -m server.orchestrator --config server/config.example.json`
(edit the paths first). With no GPU, `--fake --backend retarget=blender --backend passes=blender
--blender /path/to/blender` drives the add-on with fake models and the real Blender workers.
The mock in `tools/` still works for add-on development.
