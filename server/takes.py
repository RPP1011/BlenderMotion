"""Rank and reject Kimodo takes from their `.npz` output.

Finding from the P0 experiments: with dense root2d constraints Kimodo often
returns "floating" takes (3 of 4 samples had the lowest joint 0.7-1.1 m above
the ground for most of the clip), and occasionally a root that teleports. Each
take is measured on Kimodo's own output (Y up, metres, 30 fps):

* float_frac  fraction of frames whose lowest joint is above `float_height`
* pen_frac    fraction of frames whose lowest joint is below -`penetration_depth`
* max_step    largest frame-to-frame root displacement (m)
* root_err    mean XZ distance of the root to the root2d targets (m), if any

A take is rejected when float_frac > max_float_frac or max_step > max_root_step.
Survivors are sorted by score = root_err + float_frac + pen_frac (lower is better).
"""

import math

from .npyio import load_npz

DEFAULTS = {
    "float_height": 0.15,       # m: lowest joint above this counts as floating
    "max_float_frac": 0.25,     # reject when more frames than this float (jumps are short)
    "penetration_depth": 0.10,  # m below ground
    "max_root_step": 0.5,       # m per 30 fps frame (= 15 m/s): a teleport
}


def take_metrics(posed_joints, root_positions, constraints=(), opts=None):
    """posed_joints / root_positions: NDArray (T,J,3) / (T,3), Y up."""
    o = {**DEFAULTS, **(opts or {})}
    T = posed_joints.shape[0]
    J = posed_joints.shape[1] if len(posed_joints.shape) > 2 else 1
    lows = []
    for t in range(T):
        fr = posed_joints.frame(t)
        lows.append(min(fr[j * 3 + 1] for j in range(J)))
    float_frac = sum(1 for y in lows if y > o["float_height"]) / max(T, 1)
    pen_frac = sum(1 for y in lows if y < -o["penetration_depth"]) / max(T, 1)
    roots = [root_positions.frame(t) for t in range(root_positions.shape[0])]
    max_step = 0.0
    for a, b in zip(roots, roots[1:]):
        max_step = max(max_step, math.dist(a[:3], b[:3]))
    errs = []
    for c in constraints or ():
        if c.get("type") != "root2d":
            continue
        for g, (x, z) in zip(c.get("frame_indices", []), c.get("smooth_root_2d", [])):
            if 0 <= g < len(roots):
                errs.append(math.hypot(roots[g][0] - x, roots[g][2] - z))
    root_err = sum(errs) / len(errs) if errs else 0.0
    reasons = []
    if float_frac > o["max_float_frac"]:
        reasons.append(f"floating {float_frac:.0%} of frames (lowest joint > {o['float_height']} m)")
    if max_step > o["max_root_step"]:
        reasons.append(f"root teleport {max_step:.2f} m in one frame")
    return {
        "frames": T, "float_frac": round(float_frac, 4), "pen_frac": round(pen_frac, 4),
        "max_step": round(max_step, 4), "root_err": round(root_err, 4),
        "min_low": round(min(lows), 4) if lows else 0.0, "median_low": round(sorted(lows)[T // 2], 4) if lows else 0.0,
        "score": round(root_err + float_frac + pen_frac, 4),
        "rejected": bool(reasons), "reasons": reasons,
    }


def load_takes(npz_path):
    """[(posed_joints, root_positions)] from one .npz (batched or single)."""
    d = load_npz(npz_path)
    pj, rp = d.get("posed_joints"), d.get("root_positions")
    if pj is None or rp is None:
        raise ValueError(f"{npz_path}: missing posed_joints/root_positions (have {sorted(d)})")
    if len(pj.shape) == 4:  # (K, T, J, 3)
        return list(zip(pj.split0(), rp.split0()))
    return [(pj, rp)]


def rank(takes, constraints=(), opts=None):
    """takes: [{id, posed_joints, root_positions, ...}] -> (survivors, rejected), each sorted best first.

    Every take dict gets a `metrics` key."""
    for t in takes:
        t["metrics"] = take_metrics(t["posed_joints"], t["root_positions"], constraints, opts)
    key = lambda t: t["metrics"]["score"]  # noqa: E731
    good = sorted((t for t in takes if not t["metrics"]["rejected"]), key=key)
    bad = sorted((t for t in takes if t["metrics"]["rejected"]), key=key)
    return good, bad
