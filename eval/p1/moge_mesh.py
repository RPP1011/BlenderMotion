"""moge_mesh.py <image> <out.obj>: single image -> metric textured set mesh (MoGe-2).

Vertices are in Blender camera-local axes (x right, y up, -z forward); the OBJ is imported without axis
conversion. Two layers (LAYERS=2, default):
  plate_fg  the image on its depth, with triangles across depth edges dropped (no stretched curtains)
  plate_bg  a background layer behind it: depth max-filtered so thin near objects (posts, trees, signs)
            vanish into what stands behind them, texture inpainted where they were. The holes the
            foreground leaves when the camera moves show this layer instead of the empty world.
LAYERS=1 writes only the foreground with EDGE (relative depth jump) cutting; EDGE=100 keeps curtains.
Prints JSON: fov, camera height, facade depth, a 4x4 "level" matrix that puts the fitted ground plane at
y=0 (camera-local), and the median linear colour for the world background.
"""
import json
import os
import sys

import cv2
import numpy as np
import torch
from moge.model.v2 import MoGeModel

img_path, out = sys.argv[1], sys.argv[2]
STRIDE = int(os.environ.get("STRIDE", "3"))
EDGE = float(os.environ.get("EDGE", "0.06"))
LAYERS = int(os.environ.get("LAYERS", "2"))
KERNEL = int(os.environ.get("BG_KERNEL", "61"))  # px at full res; wider than the thin objects to remove
bgr = cv2.imread(img_path)
rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
H, W = rgb.shape[:2]
model = MoGeModel.from_pretrained("Ruicheng/moge-2-vitl-normal").cuda().eval()
with torch.no_grad():
    o = model.infer(torch.tensor(rgb / 255.0, dtype=torch.float32).permute(2, 0, 1).cuda())
P = o["points"].cpu().numpy()  # H W 3, OpenCV camera: x right, y down, z forward, metres
M = o["mask"].cpu().numpy() > 0.5
fx = float(o["intrinsics"].cpu().numpy()[0, 0])  # normalized
Z = P[..., 2]
far = np.percentile(Z[M], 99)
M &= np.isfinite(Z) & (Z > 0.3) & (Z < far * 1.5)
Zf = np.where(M, Z, far).astype(np.float32)
base = os.path.splitext(out)[0]


def grid_mesh(pts, mask, edge):
    """Regular-grid triangulation at STRIDE; drops triangles whose relative depth spread exceeds edge."""
    pts, mask = pts[::STRIDE, ::STRIDE], mask[::STRIDE, ::STRIDE].copy()
    h, w = mask.shape
    z = pts[..., 2]
    idx = -np.ones((h, w), np.int64)
    idx[mask] = np.arange(mask.sum())
    v = pts[mask] * np.array([1, -1, -1])
    uv = np.stack(np.meshgrid(np.arange(w) * STRIDE / (W - 1), 1 - np.arange(h) * STRIDE / (H - 1)), -1)[mask]
    faces = []
    for tri in (((0, 0), (1, 0), (0, 1)), ((1, 0), (1, 1), (0, 1))):
        ii = [idx[dy:h - 1 + dy, dx:w - 1 + dx] for dx, dy in tri]
        zs = np.stack([z[dy:h - 1 + dy, dx:w - 1 + dx] for dx, dy in tri])
        ok = (ii[0] >= 0) & (ii[1] >= 0) & (ii[2] >= 0) & ((zs.max(0) - zs.min(0)) / zs.min(0).clip(1e-3) < edge)
        faces.append(np.stack([ii[0][ok], ii[1][ok], ii[2][ok]], -1))
    return v, uv, np.concatenate(faces), mask


layers = [("plate_fg", bgr, P, M, EDGE)]
if LAYERS > 1:
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (KERNEL, KERNEL))
    zbg = cv2.dilate(Zf, k)  # farthest depth nearby: thin near objects disappear
    zbg = cv2.GaussianBlur(zbg, (0, 0), KERNEL / 4)
    zbg = np.maximum(zbg, Zf)
    thin = ((Zf < zbg * 0.85) & M).astype(np.uint8)
    thin = cv2.dilate(thin, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    tex_bg = cv2.inpaint(bgr, thin * 255, 9, cv2.INPAINT_TELEA)
    pbg = P * (zbg / np.maximum(Zf, 1e-3) * 1.01)[..., None]  # same rays, pushed back to the far depth
    layers.append(("plate_bg", tex_bg, pbg, M, 1e9))

off = 0
with open(base + ".mtl", "w") as fm, open(out, "w") as f:
    f.write(f"mtllib {os.path.basename(base)}.mtl\n")
    for name, tex, pts, mask, edge in layers:
        cv2.imwrite(f"{base}_{name}.png", tex)
        fm.write(f"newmtl {name}\nKd 1 1 1\nmap_Kd {os.path.basename(base)}_{name}.png\n")
        v, uv, faces, m = grid_mesh(pts, mask, edge)
        f.write(f"o {name}\n")
        np.savetxt(f, v, "v %.4f %.4f %.4f")
        np.savetxt(f, uv, "vt %.5f %.5f")
        f.write(f"usemtl {name}\n")
        fi = faces + 1 + off
        np.savetxt(f, np.repeat(fi, 2, axis=1), "f %d/%d %d/%d %d/%d")
        off += len(v)
        if name == "plate_fg":
            vfg, mfg = v, m
cv2.imwrite(base + ".png", bgr)

# Level the set: fit the ground plane (y = a x + b z + c, camera-local) on the bottom rows, iterating to drop
# outliers, and return the 4x4 that maps that plane to y = 0 with its normal along +y.
h = mfg.shape[0]
g = vfg[np.nonzero(mfg)[0] >= int(h * 0.7)]
keep = np.ones(len(g), bool)
for _ in range(4):
    A_ = np.c_[g[keep, 0], g[keep, 2], np.ones(keep.sum())]
    coef = np.linalg.lstsq(A_, g[keep, 1], rcond=None)[0]
    res = np.abs(g[:, 1] - np.c_[g[:, 0], g[:, 2], np.ones(len(g))] @ coef)
    keep = res < max(np.percentile(res, 60), 0.02)
n = np.array([-coef[0], 1.0, -coef[1]])
n /= np.linalg.norm(n)
up = np.array([0.0, 1.0, 0.0])
ax = np.cross(n, up)
s_, c_ = np.linalg.norm(ax), float(n @ up)
K = np.array([[0, -ax[2], ax[1]], [ax[2], 0, -ax[0]], [-ax[1], ax[0], 0]]) / max(s_, 1e-9)
R = np.eye(3) + s_ * K + (1 - c_) * K @ K
level = np.eye(4)
level[:3, :3] = R
level[1, 3] = -(R @ np.array([0.0, coef[2], 0.0]))[1]
cy = Z.shape[0]
mid = Z[int(cy * 0.3):int(cy * 0.6), W // 3: 2 * W // 3][M[int(cy * 0.3):int(cy * 0.6), W // 3: 2 * W // 3]]
lin = (rgb / 255.0) ** 2.2
print(json.dumps({
    "mean_rgb": [float(x) for x in np.median(lin.reshape(-1, 3), 0)],
    "fov_x_deg": float(np.degrees(2 * np.arctan(0.5 / fx))), "cam_height": float(coef[2]) * -1,
    "facade_depth": float(np.median(mid)), "level": level.tolist(), "ground_tilt_deg": float(np.degrees(np.arccos(c_))),
    "layers": [name for name, *_ in layers]}))
