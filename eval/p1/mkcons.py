"""mkcons.py: root2d constraint JSONs for the handshake scene (Kimodo frame: x, z; heading (cos, sin))."""
import json
import math
import sys

FPS, N = 30, 270
SPAN = 0.9  # root-to-root distance at the handshake


def hd(a):
    return [math.cos(a), math.sin(a)]


def path(frames, pts, head):
    return {"type": "root2d", "frame_indices": frames, "smooth_root_2d": pts,
            "global_root_heading": [head] * len(frames)}


sgn = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0  # flip if heading convention is mirrored
px, mx = hd(sgn * math.pi / 2), hd(-sgn * math.pi / 2)  # facing +X / -X


def walk_x(f):
    """Walk in at 1.25 m/s for 3.4 s, then decelerate linearly to a stop at frame 150."""
    t = f / FPS
    if t <= 3.4:
        x = 1.25 * t
    elif t <= 5.0:
        x = 1.25 * 3.4 + 1.25 * (t - 3.4) - 1.25 / (2 * 1.6) * (t - 3.4) ** 2
    else:
        x = 1.25 * 3.4 + 1.25 * 1.6 / 2
    return x


total = walk_x(N)
a = [path(list(range(N)), [[walk_x(f) - total - SPAN, 0.0] for f in range(N)], px)]
b = [path(list(range(0, N)), [[0.0, 0.0]] * N, mx)]
json.dump(a, open("consA.json", "w"))
json.dump(b, open("consB.json", "w"))
