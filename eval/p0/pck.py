"""PCK of a generated video against Blender ground-truth keypoints (architecture §4.5).

python pck.py <kp2d.json> <video.mp4> [char_id] [--dump det.json]
DWPose (rtmlib Wholebody, performance mode) on every frame; the detected person
nearest the GT box is matched. PCK@0.1 of the GT bbox diagonal, visible joints only.
"""
import json
import sys

import cv2
import numpy as np
from rtmlib import Wholebody

# kp2d COCO-18 (OpenPose order) -> COCO-17 index (body part of COCO-WholeBody).
# neck (1) has no COCO-17 point: compared against the shoulder midpoint.
O2C = {0: 0, 2: 6, 3: 8, 4: 10, 5: 5, 6: 7, 7: 9, 8: 12, 9: 14, 10: 16, 11: 11, 12: 13, 13: 15,
       14: 2, 15: 1, 16: 4, 17: 3}
NAMES = ["nose", "neck", "r_shoulder", "r_elbow", "r_wrist", "l_shoulder", "l_elbow", "l_wrist",
         "r_hip", "r_knee", "r_ankle", "l_hip", "l_knee", "l_ankle", "r_eye", "l_eye", "r_ear", "l_ear"]


def det_point(kps, j):
    if j == 1:
        return (kps[5] + kps[6]) / 2
    return kps[O2C[j]]


def main():
    argv = sys.argv[1:]
    dump_path = None
    if "--dump" in argv:
        i = argv.index("--dump")
        dump_path = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    args = argv
    kp = json.load(open(args[0]))
    char = args[2] if len(args) > 2 else next(iter(kp["frames"][0]["people"]))
    cap = cv2.VideoCapture(args[1])
    W, H = cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    sx, sy = W / kp["resolution"][0], H / kp["resolution"][1]
    model = Wholebody(mode="performance", backend="onnxruntime", device="cpu")
    hits = np.zeros(18)
    tot = np.zeros(18)
    per_frame, dump = [], []
    for fr in kp["frames"]:
        ok, img = cap.read()
        if not ok:
            break
        gt = fr["people"][char]
        vis = [(j, np.array([p[0] * sx, p[1] * sy])) for j, p in enumerate(gt) if p and p[2]]
        if len(vis) < 4:
            continue
        pts = np.array([v for _, v in vis])
        lo, hi = pts.min(0), pts.max(0)
        diag = float(np.linalg.norm(hi - lo))
        kps, scores = model(img)
        if len(kps) == 0:
            for j, _ in vis:
                tot[j] += 1
            per_frame.append(0.0)
            continue
        centre = (lo + hi) / 2
        best = int(np.argmin([np.linalg.norm(k[:17][scores[i][:17] > 0.3].mean(0) - centre)
                              if (scores[i][:17] > 0.3).any() else 1e9 for i, k in enumerate(kps)]))
        k = kps[best]
        f_hit = 0
        for j, g in vis:
            tot[j] += 1
            if np.linalg.norm(det_point(k, j) - g) <= 0.1 * diag:
                hits[j] += 1
                f_hit += 1
        per_frame.append(f_hit / len(vis))
        dump.append({"i": fr["i"], "det": k[:17].round(1).tolist()})
    joint = {NAMES[j]: round(hits[j] / tot[j], 3) for j in range(18) if tot[j]}
    res = {"pck@0.1": round(float(hits.sum() / max(tot.sum(), 1)), 3), "frames": len(per_frame),
           "per_joint": joint, "per_frame": [round(x, 2) for x in per_frame]}
    print(json.dumps(res))
    if dump_path:
        json.dump(dump, open(dump_path, "w"))


if __name__ == "__main__":
    main()
