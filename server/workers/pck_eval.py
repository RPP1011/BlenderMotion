"""PCK@0.1 of a generated video against Blender ground-truth keypoints.

    python pck_eval.py <kp2d.json> <video.mp4> <out.json> [char_id ...] [--device cpu|cuda]

Same method as eval/p0/pck.py (kept separate so the orchestrator does not
depend on the P0 scripts): DWPose (rtmlib Wholebody, performance mode) on
every frame, the detected person nearest each character's GT box is matched,
PCK@0.1 of the GT bbox diagonal over visible joints. Face points are null in
kp2d.json and skipped. Run with an interpreter that has rtmlib, opencv-python
and onnxruntime(-gpu). Prints `PCK_OK <json>` on success.
"""
import json
import sys

import cv2
import numpy as np
from rtmlib import Wholebody

# kp2d COCO-18 (OpenPose order) -> COCO-17 index. Neck is the shoulder midpoint.
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
    device = "cpu"
    if "--device" in argv:
        i = argv.index("--device")
        device = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    kp_path, video, out_path = argv[:3]
    with open(kp_path) as f:
        kp = json.load(f)
    chars = argv[3:] or list(kp["frames"][0]["people"])
    cap = cv2.VideoCapture(video)
    W, H = cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    sx, sy = W / kp["resolution"][0], H / kp["resolution"][1]
    model = Wholebody(mode="performance", backend="onnxruntime", device=device)
    hits = {c: np.zeros(18) for c in chars}
    tot = {c: np.zeros(18) for c in chars}
    n_frames = 0
    for fr in kp["frames"]:
        ok, img = cap.read()
        if not ok:
            break
        n_frames += 1
        kps, scores = model(img)
        for c in chars:
            gt = fr["people"].get(c) or []
            vis = [(j, np.array([p[0] * sx, p[1] * sy])) for j, p in enumerate(gt) if p and p[2]]
            if len(vis) < 4:
                continue
            pts = np.array([v for _, v in vis])
            lo, hi = pts.min(0), pts.max(0)
            diag = float(np.linalg.norm(hi - lo))
            for j, _ in vis:
                tot[c][j] += 1
            if len(kps) == 0:
                continue
            centre = (lo + hi) / 2
            best = int(np.argmin([np.linalg.norm(k[:17][scores[i][:17] > 0.3].mean(0) - centre)
                                  if (scores[i][:17] > 0.3).any() else 1e9 for i, k in enumerate(kps)]))
            k = kps[best]
            for j, g in vis:
                if np.linalg.norm(det_point(k, j) - g) <= 0.1 * diag:
                    hits[c][j] += 1
    per_char = {c: round(float(hits[c].sum() / max(tot[c].sum(), 1)), 4) for c in chars}
    all_hits = sum(float(hits[c].sum()) for c in chars)
    all_tot = sum(float(tot[c].sum()) for c in chars)
    res = {"pck@0.1": round(all_hits / max(all_tot, 1), 4), "frames": n_frames, "per_char": per_char,
           "per_joint": {c: {NAMES[j]: round(hits[c][j] / tot[c][j], 3) for j in range(18) if tot[c][j]}
                         for c in chars}}
    with open(out_path, "w") as f:
        json.dump(res, f, indent=1)
    print("PCK_OK " + json.dumps({"pck@0.1": res["pck@0.1"], "frames": n_frames}), flush=True)


if __name__ == "__main__":
    main()
