# P1: one performance, one set, several cameras (RTX PRO 6000 pod, 2026-10-10/11)

Goal: fix the first multi-cut sequence (set changed between cuts, flat staging, no contact, face drift).

## Pipeline

1. **Set image**: `h3run.py t2v` (Ref2V with no reference video, 22 frames) renders an empty location; take the last frame (`plate.sh`).
2. **Set mesh**: `moge_mesh.py` (MoGe-2) turns it into a metric, levelled, two-layer mesh: foreground cut at depth edges,
   background with thin near objects removed (depth max-filter) and their pixels inpainted. Imported unlit, view transform Standard.
3. **Performance**: one two-person Kimodo take with `root2d` constraints (`mkcons.py`): the man walks in along +X and stops 0.9 m from the woman.
   Generate several samples and keep a grounded one (3 of 4 dense-path samples floated 0.7-1.1 m; the orchestrator now rejects these automatically).
4. **Coverage**: `build_cov.py` rotates the pair so every camera looks into the set image's field of view and adds CAM_wide, CAM_two,
   CAM_ots and CAM_close over overlapping frame ranges (editorial handles). `RIG_A` / `RIG_B` optionally swap mannequins for textured rigs.
5. **H3**: Ref2V, 8-step turbo LoRA, playblast as Video 1, 4+4 character-sheet views + the set image as Picture 9 (`run_coverage.sh`); `cut.sh` trims and concatenates.

## Final recipe (v5)

Textured set mesh for the buildings, **grey lit ground**, grey mannequins, set image as Picture 9. Same doors, windows and
shop signs from every angle; H3 renders the wet cobblestones itself, so the ground no longer smears.
The earlier variants below had the set ground ~2 m below the characters (the camera-space plane fit in `moge_mesh.py`
only sees the nearest rows); `build_cov.py` now re-levels the set in world space (`settle_ground`) before anything else.

## Findings (1 MP unless noted, ~3.5-5.5 min per shot)

| Variant | Set continuity | Camera/framing adherence | Look |
|---|---|---|---|
| Grey blockout + set image as Picture 9 | Same photo pasted behind every angle (wrong perspective) | OTS lost | Backdrop, people out of scale |
| Mesh set, no set picture | Close shots invent a different square | OTS lost | Photoreal, inconsistent |
| **Mesh set + set picture** | Same doors/windows from every angle | Followed, incl. OTS | Copies mesh flaws, so the mesh must be clean |
| Mesh set + textured rigged characters | Same | Followed | CG characters copied nearly verbatim: worse than mannequins |

- H3 treats the reference video as near-final pixels for anything textured, and as motion/layout only for grey mannequins.
  Rule: **textured set, grey characters**.
- FX (haze, neon, DoF, motion blur) in the render do not carry over; only described looks do (see eval/p0 results).
- The handshake lands when the mannequins' hands come within ~0.4 m; H3 closes the gap.
- Small floating fragments of the foreground layer are dropped (`drop_islands`); ground faces get a grey material (`grey_ground`).
- One set image cannot cover a reverse angle; a second set image generated with the first as a reference came back as a near copy.
- Bugs fixed on the way: stacked COPY_LOCATION influences are a running blend (use w_i / sum(w_0..w_i)); rtlib's retarget
  scale must be 1.0 when the clip is far from the origin; passes `characters` must name the skinned armature when rigs are swapped in.
