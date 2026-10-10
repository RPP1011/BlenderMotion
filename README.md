# BlenderMotion

Blender front end for a text → motion → video pipeline: a brief becomes shots,
shots get generated motion (Kimodo / HY-Motion), which you edit in Blender,
and then video (VACE / H3) is rendered from Blender control passes.

| Path | What |
|---|---|
| `addon/chimera_director/` | The Blender add-on / extension (4.2 LTS – 5.0) |
| `tools/mock_orchestrator.py` | Stdlib stand-in for the orchestrator API; runs headless Blender workers |
| `tools/mock_worker.py` | Mock motion and playblast worker |
| `tests/` | Headless tests: `BLENDER=/path/to/blender tests/run_tests.sh [--e2e]` |
| `docs/architecture.md` | System design |
| `docs/addon.md` | Add-on usage, orchestrator contract, deviations from the design |

The orchestrator, motion server, real Blender workers and ComfyUI workflows are
not in this repo yet. Until they are, use the mock to drive the add-on.
