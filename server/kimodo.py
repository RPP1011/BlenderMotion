"""Add-on motion body -> Kimodo CLI inputs (pure functions, no I/O besides glob).

Kimodo (SOMA-RP v1.1): SOMA skeleton, 30 fps, Y-up, metres, heading 0 faces
+Z, left = +X, BVH in cm. The add-on authors at the project fps (24) in the
same canonical frame (docs/addon.md "Motion body"), so only time needs
converting:

* `root2d` entries pass through with their frame indices resampled to 30 fps.
  Dense runs (consecutive indices) are re-interpolated onto every 30 fps frame
  they span; sparse entries map each index to round(f * 30 / 24). The value
  arrays always stay the same length as `frame_indices`.
* `end_effector` entries are dropped with a warning. Kimodo's end-effector
  constraint takes a full-pose set of local joint rotations, which the add-on
  does not send (it sends one joint target). TODO: IK-solve a SOMA pose at
  the effector frame (from the root2d-implied root and a neutral pose) and
  emit Kimodo's full-pose end-effector entry.
"""

import glob
import math
import os
import re

KIMODO_FPS = 30
KIMODO_MAX_S = 10.0


def frames_at(n_src, src_fps=24, dst_fps=KIMODO_FPS):
    return max(1, int(round(n_src * dst_fps / src_fps)))


def _resample_index(f, src_fps, dst_fps):
    return int(round(f * dst_fps / src_fps))


def _unwrap(angles):
    out = []
    for a in angles:
        if out:
            prev = out[-1]
            while a - prev > math.pi:
                a -= 2 * math.pi
            while a - prev < -math.pi:
                a += 2 * math.pi
        out.append(a)
    return out


def _is_dense(idx):
    return len(idx) > 2 and all(b - a == 1 for a, b in zip(idx, idx[1:]))


def resample_root2d(entry, src_fps=24, dst_fps=KIMODO_FPS, n_dst=None):
    """One add-on root2d entry -> Kimodo root2d entry at dst_fps."""
    idx = [int(f) for f in entry.get("frame_indices", [])]
    pts = entry.get("smooth_root_2d") or []
    heads = entry.get("global_root_heading")
    if len(pts) != len(idx) or (heads is not None and len(heads) != len(idx)):
        raise ValueError("root2d arrays must match frame_indices in length")
    out = {"type": "root2d"}
    if not idx:
        out.update(frame_indices=[], smooth_root_2d=[])
        if heads is not None:
            out["global_root_heading"] = []
        return out
    hi = (n_dst - 1) if n_dst else None
    if _is_dense(idx):
        yaws = _unwrap([math.atan2(s, c) for c, s in heads]) if heads is not None else None
        t0, t1 = idx[0] / src_fps, idx[-1] / src_fps
        g0 = int(math.ceil(t0 * dst_fps - 1e-9))
        g1 = int(math.floor(t1 * dst_fps + 1e-9))
        if hi is not None:
            g1 = min(g1, hi)
        new_idx, new_pts, new_heads = [], [], []
        for g in range(g0, g1 + 1):
            u = g * src_fps / dst_fps - idx[0]   # position in source samples
            k = min(max(int(math.floor(u)), 0), len(idx) - 2)
            w = min(max(u - k, 0.0), 1.0)
            a, b = pts[k], pts[k + 1]
            new_idx.append(g)
            new_pts.append([a[0] + (b[0] - a[0]) * w, a[1] + (b[1] - a[1]) * w])
            if yaws is not None:
                y = yaws[k] + (yaws[k + 1] - yaws[k]) * w
                new_heads.append([round(math.cos(y), 6), round(math.sin(y), 6)])
        out.update(frame_indices=new_idx, smooth_root_2d=new_pts)
        if yaws is not None:
            out["global_root_heading"] = new_heads
        return out
    seen = {}
    for i, f in enumerate(idx):
        g = _resample_index(f, src_fps, dst_fps)
        if hi is not None:
            g = min(max(g, 0), hi)
        seen[g] = i  # a later sample wins if two land on the same frame
    order = sorted(seen)
    out["frame_indices"] = order
    out["smooth_root_2d"] = [list(pts[seen[g]]) for g in order]
    if heads is not None:
        out["global_root_heading"] = [list(heads[seen[g]]) for g in order]
    return out


def convert_constraints(char_body, src_fps=24, n_src=None):
    """Add-on per-character constraints -> (kimodo_constraints, warnings)."""
    n_dst = frames_at(n_src, src_fps) if n_src else None
    out, warnings = [], []
    for c in char_body.get("constraints") or []:
        kind = c.get("type")
        if kind == "root2d":
            r = resample_root2d(c, src_fps, KIMODO_FPS, n_dst)
            if r["frame_indices"]:
                out.append(r)
        elif kind == "end_effector":
            warnings.append(f"end_effector {c.get('joint')}@{c.get('frame')} dropped: Kimodo needs a full pose "
                            "(not implemented yet)")
        else:
            warnings.append(f"constraint type {kind!r} not supported; dropped")
    return out, warnings


def split_prompt(prompt):
    """Sentences for Kimodo's multi-segment prompt (one duration each)."""
    parts = [p.strip(" .") for p in re.split(r"[.!?\n]+", prompt or "")]
    return [p for p in parts if p] or ["a person stands still"]


def durations(n_sentences, total_s):
    """Even split of total_s over the sentences, 2 decimals, summing exactly."""
    if n_sentences <= 0:
        return []
    each = math.floor(total_s / n_sentences * 100) / 100
    ds = [each] * n_sentences
    ds[-1] = round(total_s - each * (n_sentences - 1), 2)
    return ds


def build_command(exe, prompt, total_s, seed, num_samples, out_stem, constraints_path=None, extra=()):
    sentences = split_prompt(prompt)
    ds = durations(len(sentences), total_s)
    cmd = list(exe) if isinstance(exe, (list, tuple)) else [exe]
    cmd += [". ".join(sentences) + ".", "--duration", " ".join(f"{d:g}" for d in ds)]
    if constraints_path:
        cmd += ["--constraints", constraints_path]
    cmd += ["--seed", str(int(seed)), "--num_samples", str(int(num_samples)), "--output", out_stem, "--bvh"]
    cmd += list(extra)
    return cmd


def _natural(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def discover_outputs(out_stem):
    """Pair Kimodo's output files: [(npz or None, bvh or None)], sample order.

    Kimodo's exact naming for multi-sample runs is not pinned down here, so
    accept `<stem>.npz`, `<stem>_<k>.npz`, `<stem>/<anything>.npz` and the
    same for `.bvh`, pairing by matching basename, then by sorted order.
    """
    def find(ext):
        pats = [out_stem + ext, out_stem + "_*" + ext, out_stem + "-*" + ext,
                os.path.join(out_stem, "*" + ext)]
        hits = []
        for p in pats:
            for h in glob.glob(p):
                if h not in hits:
                    hits.append(h)
        return sorted(hits, key=_natural)
    npzs, bvhs = find(".npz"), find(".bvh")
    by_stem = {os.path.splitext(b)[0]: b for b in bvhs}
    pairs, used = [], set()
    for n in npzs:
        b = by_stem.get(os.path.splitext(n)[0])
        if b:
            used.add(b)
        pairs.append([n, b])
    rest = [b for b in bvhs if b not in used]
    for p in pairs:
        if p[1] is None and rest:
            p[1] = rest.pop(0)
    pairs += [[None, b] for b in rest]
    return [tuple(p) for p in pairs]
