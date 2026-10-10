"""Submit one H3 Ref2V job to the SGLang server, poll, save the mp4.
python sgreq.py <ref.mp4> <out.mp4> [steps] [short_edge]"""
import json, sys, time, urllib.request
ref, out = sys.argv[1], sys.argv[2]
steps = int(sys.argv[3]) if len(sys.argv) > 3 else 20
edge = int(sys.argv[4]) if len(sys.argv) > 4 else 544
B = "http://127.0.0.1:30011/v1/videos"
P = ("A man in a dark grey hoodie, jeans and sneakers on an overcast concrete rooftop, cinematic 35mm film look. "
     "<Video 1> is the motion reference: the man's body movement, timing, position in frame and the camera must "
     "follow <Video 1> exactly.")
body = {"model": "MiniMaxAI/MiniMax-H3", "prompt": P, "seconds": 5, "task": "ref2va",
        "conditions": [{"type": "video", "uri": "file://" + ref, "role": "reference"}],
        "target": {"short_edge": edge, "aspect_ratio": "16:9", "duration_seconds": 124 / 24},
        "num_outputs_per_prompt": 1, "num_inference_steps": steps, "flow_shift": 12.0,
        "audio_flow_shift": 3.0, "seed": 42}
t0 = time.time()
req = urllib.request.Request(B, json.dumps(body).encode(), {"Content-Type": "application/json"})
try:
    r = json.load(urllib.request.urlopen(req))
except urllib.error.HTTPError as e:
    print("SG_ERROR", e.code, e.read().decode()[:2000]); sys.exit(1)
vid = r["id"]
while True:
    s = json.load(urllib.request.urlopen(f"{B}/{vid}"))
    if s["status"] in ("completed", "failed"):
        break
    time.sleep(2)
if s["status"] == "failed":
    print("SG_ERROR", json.dumps(s)[:2000]); sys.exit(1)
open(out, "wb").write(urllib.request.urlopen(f"{B}/{vid}/content").read())
print("SG_OK", out, round(time.time() - t0), "s")
