"""crop.py <video> <kp2d.json> <char> <out_prefix> <frame> [frame ...]: person crops (GT bbox + margin)."""
import json
import sys
import cv2
vid, kpf, char, pre = sys.argv[1:5]
kp = json.load(open(kpf))
cap = cv2.VideoCapture(vid)
W, H = cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
sx, sy = W / kp["resolution"][0], H / kp["resolution"][1]
for n, f in enumerate(int(x) for x in sys.argv[5:]):
    cap.set(cv2.CAP_PROP_POS_FRAMES, f)
    ok, img = cap.read()
    pts = [(p[0] * sx, p[1] * sy) for p in kp["frames"][f]["people"][char] if p]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    h = max(ys) - min(ys)
    y0, y1 = max(0, min(ys) - 0.35 * h), min(H, max(ys) + 0.2 * h)  # head top / feet are outside joints
    cx, half = (min(xs) + max(xs)) / 2, (y1 - y0) * 0.38
    x0, x1 = max(0, cx - half), min(W, cx + half)
    cv2.imwrite(f"{pre}{n + 1}.png", img[int(y0):int(y1), int(x0):int(x1)])
    print("crop", f"{pre}{n + 1}.png", int(x1 - x0), int(y1 - y0))
