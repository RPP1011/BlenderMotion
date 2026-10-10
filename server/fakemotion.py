"""Synthetic Kimodo-like takes for the fake motion backend (no GPU, no Kimodo).

Writes what kimodo_gen writes, in Kimodo's conventions: a BVH (SOMA-style bone
names, cm, Y-up, facing +Z, left = +X, 30 fps) and an .npz with
`posed_joints` (T, J, 3) and `root_positions` (T, 3) in metres. The root
follows the dense root2d constraint if there is one, else the sparse targets,
else walks along +Z; the legs and arms swing. `floating=True` lifts the whole
take 0.9 m off the ground, like the bad takes seen with real Kimodo, so the
ranking path can be tested.
"""

import math
import random

from .npyio import save_npz

# name, parent, offset (cm). Mirrors the SOMA names Kimodo's BVH uses for the body core.
SKELETON = [
    ("Hips", None, (0, 95, 0)),
    ("Spine1", "Hips", (0, 10, 0)), ("Spine2", "Spine1", (0, 12, 0)), ("Chest", "Spine2", (0, 12, 0)),
    ("Neck1", "Chest", (0, 15, 0)), ("Neck2", "Neck1", (0, 5, 0)), ("Head", "Neck2", (0, 6, 0)),
    ("LeftShoulder", "Chest", (5, 10, 0)), ("LeftArm", "LeftShoulder", (12, 0, 0)),
    ("LeftForeArm", "LeftArm", (27, 0, 0)), ("LeftHand", "LeftForeArm", (25, 0, 0)),
    ("RightShoulder", "Chest", (-5, 10, 0)), ("RightArm", "RightShoulder", (-12, 0, 0)),
    ("RightForeArm", "RightArm", (-27, 0, 0)), ("RightHand", "RightForeArm", (-25, 0, 0)),
    ("LeftLeg", "Hips", (10, -5, 0)), ("LeftShin", "LeftLeg", (0, -42, 0)),
    ("LeftFoot", "LeftShin", (0, -42, 0)), ("LeftToeBase", "LeftFoot", (0, -6, 12)),
    ("RightLeg", "Hips", (-10, -5, 0)), ("RightShin", "RightLeg", (0, -42, 0)),
    ("RightFoot", "RightShin", (0, -42, 0)), ("RightToeBase", "RightFoot", (0, -6, 12)),
]
END_SITES = {"Head": (0, 15, 0), "LeftHand": (10, 0, 0), "RightHand": (-10, 0, 0),
             "LeftToeBase": (0, 0, 5), "RightToeBase": (0, 0, 5)}


def _children(name):
    return [s for s in SKELETON if s[1] == name]


def _hierarchy(lines, name, offset, depth):
    pad = "  " * depth
    kind = "ROOT" if depth == 0 else "JOINT"
    lines.append(f"{pad}{kind} {name}")
    lines.append(pad + "{")
    lines.append(f"{pad}  OFFSET {offset[0]:.4f} {offset[1]:.4f} {offset[2]:.4f}")
    if depth == 0:
        lines.append(f"{pad}  CHANNELS 6 Xposition Yposition Zposition Zrotation Xrotation Yrotation")
    else:
        lines.append(f"{pad}  CHANNELS 3 Zrotation Xrotation Yrotation")
    kids = _children(name)
    for k in kids:
        _hierarchy(lines, k[0], k[2], depth + 1)
    if not kids:
        e = END_SITES.get(name, (0, 5, 0))
        lines += [f"{pad}  End Site", pad + "  {", f"{pad}    OFFSET {e[0]} {e[1]} {e[2]}", pad + "  }"]
    lines.append(pad + "}")


def _root_path(n, constraints):
    """Per-frame (x, z, yaw) in metres/radians at 30 fps."""
    dense = next((c for c in constraints if c.get("type") == "root2d" and len(c["frame_indices"]) > 2), None)
    keys = []
    if dense is not None:
        keys = list(zip(dense["frame_indices"], dense["smooth_root_2d"]))
    else:
        sparse = [c for c in constraints if c.get("type") == "root2d"]
        keys = sorted((f, p) for c in sparse for f, p in zip(c["frame_indices"], c["smooth_root_2d"]))
        if keys and keys[0][0] > 0:
            keys = [(0, [0.0, 0.0])] + keys
    if len(keys) < 2:
        keys = [(0, [0.0, 0.0]), (max(n - 1, 1), [0.0, 1.2 * max(n - 1, 1) / 30.0])]
    out = []
    for f in range(n):
        k = 0
        while k < len(keys) - 2 and keys[k + 1][0] <= f:
            k += 1
        (f0, a), (f1, b) = keys[k], keys[k + 1]
        u = min(max((f - f0) / max(f1 - f0, 1), 0.0), 1.0)
        out.append([a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u])
    res = []
    for i, (x, z) in enumerate(out):
        a, b = out[max(i - 1, 0)], out[min(i + 1, n - 1)]
        dx, dz = b[0] - a[0], b[1] - a[1]
        yaw = math.atan2(dx, dz) if math.hypot(dx, dz) > 1e-6 else (res[-1][2] if res else 0.0)
        res.append((x, z, yaw))
    return res


def write_take(stem, n_frames, constraints, seed, floating=False):
    """Write <stem>.bvh and <stem>.npz; returns (npz, bvh)."""
    rng = random.Random(seed)
    sway = rng.uniform(0.0, 0.05)
    lift = 0.9 if floating else 0.0
    path = _root_path(n_frames, constraints)
    lines = ["HIERARCHY"]
    _hierarchy(lines, "Hips", SKELETON[0][2], 0)
    lines += ["MOTION", f"Frames: {n_frames}", "Frame Time: 0.0333333"]
    joints, roots = [], []
    names = [s[0] for s in SKELETON]
    for f, (x, z, yaw) in enumerate(path):
        ph = f / 30.0 * 2 * math.pi * 1.6
        x += sway * math.sin(ph / 2)
        y = 0.95 + 0.02 * abs(math.sin(ph)) + lift
        rot = {n: (0.0, 0.0, 0.0) for n in names}   # (z, x, y) degrees
        rot["Hips"] = (0.0, 0.0, math.degrees(yaw))
        rot["LeftLeg"] = (0.0, 25 * math.sin(ph), 0.0)
        rot["RightLeg"] = (0.0, -25 * math.sin(ph), 0.0)
        rot["LeftShin"] = (0.0, max(0.0, 30 * math.sin(ph + 1.2)), 0.0)
        rot["RightShin"] = (0.0, max(0.0, -30 * math.sin(ph + 1.2)), 0.0)
        rot["LeftArm"] = (-70.0, -20 * math.sin(ph), 0.0)
        rot["RightArm"] = (70.0, 20 * math.sin(ph), 0.0)
        vals = [x * 100, y * 100, z * 100]
        for n in names:
            vals += list(rot[n])
        lines.append(" ".join(f"{v:.4f}" for v in vals))
        roots += [x, y, z]
        # Coarse joints for ranking: rest offsets (no rotation) around the root.
        pos = {}
        for name, parent, off in SKELETON:
            base = pos[parent] if parent else (x, y - 0.95, z)
            pos[name] = (base[0] + off[0] / 100, base[1] + off[1] / 100, base[2] + off[2] / 100)
        for name in names:
            joints += list(pos[name])
    with open(stem + ".bvh", "w") as fh:
        fh.write("\n".join(lines) + "\n")
    save_npz(stem + ".npz", {"posed_joints": (joints, (n_frames, len(names), 3)),
                             "root_positions": (roots, (n_frames, 3))})
    return stem + ".npz", stem + ".bvh"
