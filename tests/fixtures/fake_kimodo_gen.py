#!/usr/bin/env python3
"""Stand-in for Kimodo's `kimodo_gen` CLI (tests only): same arguments, writes
<output>_<k>.npz / .bvh with server/fakemotion.py and prints tqdm-like progress.

FAKE_KIMODO_FLOAT="0,2" makes those sample indices float; FAKE_KIMODO_SLEEP
adds a delay per sample; FAKE_KIMODO_ARGS=<path> dumps argv as JSON."""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from server import fakemotion  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("prompt")
ap.add_argument("--duration", required=True)
ap.add_argument("--constraints")
ap.add_argument("--seed", type=int, default=0)
ap.add_argument("--num_samples", type=int, default=1)
ap.add_argument("--output", required=True)
ap.add_argument("--bvh", action="store_true")
a = ap.parse_args()
if os.environ.get("FAKE_KIMODO_ARGS"):
    with open(os.environ["FAKE_KIMODO_ARGS"], "w") as f:
        json.dump({"argv": sys.argv[1:], "cuda": os.environ.get("CUDA_VISIBLE_DEVICES")}, f)
cons = []
if a.constraints:
    with open(a.constraints) as f:
        cons = json.load(f)
frames = int(round(sum(float(d) for d in a.duration.split()) * 30))
floating = {int(x) for x in os.environ.get("FAKE_KIMODO_FLOAT", "").split(",") if x.strip()}
for k in range(a.num_samples):
    time.sleep(float(os.environ.get("FAKE_KIMODO_SLEEP", "0")))
    fakemotion.write_take(f"{a.output}_{k}", frames, cons, a.seed * 31 + k, floating=k in floating)
    print(f"sampling: {int(100 * (k + 1) / a.num_samples)}%|####| {k + 1}/{a.num_samples}", flush=True)
print("done")
