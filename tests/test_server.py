"""Tests for the self-hosted orchestrator (server/). Plain Python, no GPU:

    python -m pytest tests/test_server.py      # or: python tests/test_server.py

Drives the server over HTTP with the add-on's own client (addon/chimera_director/
client.py, stdlib only), using fake backends plus a fake `kimodo_gen` CLI and a
fake ComfyUI so the real Kimodo and ComfyUI backend code paths run too. The
Blender retarget tests run when BLENDER points at a Blender executable.
"""

import importlib.util
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(HERE, "fixtures"))

from server import config, fakemotion, h3, kimodo, npyio, orchestrator, takes  # noqa: E402
from server.state import cache_key  # noqa: E402
from fake_comfyui import FakeComfy  # noqa: E402

_spec = importlib.util.spec_from_file_location("chimera_client", os.path.join(REPO, "addon", "chimera_director",
                                                                              "client.py"))
client_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(client_mod)
Client, OrchestratorError = client_mod.Client, client_mod.OrchestratorError

FAKE_KIMODO = os.path.join(HERE, "fixtures", "fake_kimodo_gen.py")
BLENDER_RIG = os.path.join(HERE, "fixtures", "blender_rig.py")
BLENDER = os.environ.get("BLENDER") or shutil.which("blender")


# -- helpers ------------------------------------------------------------------------

def dense_path(n=96, length=2.0):
    return {"type": "root2d", "frame_indices": list(range(n)),
            "smooth_root_2d": [[0.0, length * i / (n - 1)] for i in range(n)],
            "global_root_heading": [[1.0, 0.0]] * n}


def shot_json(sid="sh_010", prompt="a man walks forward. he stops", duration=4.0, seed=7, variants=2, **extra):
    d = {"id": sid, "project": "demo", "order": 10, "duration_s": duration, "fps": 24,
         "characters": [{"char_id": "hero", "rig": "", "refs": [], "lora": None}],
         "action_prompt": prompt,
         "motion": {"backend": "kimodo", "seed": seed, "variants": variants, "selected_variant": None,
                    "source": "generated", "continuity": False},
         "camera": {"mode": "authored", "template": "", "object": f"CAM_{sid}"},
         "look": {"prompt": "overcast rooftop, 35mm"},
         "render": {"model": "h3", "mode": "ref2v", "audio_prompt": "wind", "resolution": [960, 544]},
         "eval": {"identity_manual": None}}
    d.update(extra)
    return d


def motion_body(frames=96, rig=None, effector=True):
    cons = [dense_path(frames), {"type": "root2d", "frame_indices": [24], "smooth_root_2d": [[0.0, 0.5]]}]
    if effector:
        cons.append({"type": "end_effector", "joint": "right_hand", "frame": 40, "position": [0.1, 1.0, 1.0]})
    ch = {"start_matrix_world": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], "scale": 1.0,
          "constraints": cons}
    if rig:
        ch["rig"] = rig
    return {"frames": frames, "continuity": False, "scene": "Scene", "variant_offset": 0, "characters": {"hero": ch}}


def passes_body(snapshot, frames=96, scene_hash="abc123"):
    return {"snapshot_path": snapshot, "scene_hash": scene_hash, "scene": "Scene", "frame_in": 1,
            "frame_out": 1 + frames, "frames": frames, "frames_gen": h3.snap_frames(frames), "camera": "CAM_sh_010",
            "characters": {"hero": "HeroRig"}, "bonemaps": {"hero": {"pelvis": "Hips"}}, "resolution": [960, 544]}


class Harness:
    """An orchestrator on an ephemeral port with its own data dir."""

    def __init__(self, sets=(), backends=(), fake=True, cfg_over=None):
        self.tmp = tempfile.mkdtemp(prefix="orch_test_")
        self.root = os.path.join(self.tmp, "proj")
        over = {"data_dir": os.path.join(self.tmp, "data"), "port": 0}
        over.update(cfg_over or {})
        self.cfg = config.build(None, over, sets, fake, backends)
        self.start()

    def start(self):
        self.app = orchestrator.Orchestrator(self.cfg)
        self.app.refresh_health()
        self.srv = orchestrator.serve(self.app, "127.0.0.1", 0)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.c = Client(self.url, timeout=10)

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()

    def close(self):
        self.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def setup_shot(self, shot=None):
        self.c.put_project("demo", {"project": "demo", "fps": 24, "root": self.root, "look": {"prompt": "x"}})
        shot = shot or shot_json()
        self.c.put_shot("demo", shot["id"], shot)
        snap = os.path.join(self.root, "snapshots", "snap.blend")
        os.makedirs(os.path.dirname(snap), exist_ok=True)
        with open(snap, "wb") as f:
            f.write(b"BLENDER-fake")
        return snap

    def wait(self, job_id, timeout=60, until=("done", "failed", "cancelled")):
        t0 = time.time()
        while time.time() - t0 < timeout:
            j = self.c.job(job_id)
            if j["status"] in until:
                return j
            time.sleep(0.05)
        raise AssertionError(f"job {job_id} did not reach {until}: {self.c.job(job_id)}")

    def raw(self, method, path, data=None):
        req = urllib.request.Request(self.url + path, data=data, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")


# -- pure: constraints, takes, H3 -------------------------------------------------------

class ConstraintConversion(unittest.TestCase):
    def test_dense_resampled_to_30fps(self):
        r = kimodo.resample_root2d(dense_path(96, 2.0), 24, 30, n_dst=kimodo.frames_at(96))
        self.assertEqual(kimodo.frames_at(96), 120)
        self.assertEqual(r["frame_indices"], list(range(0, 119)))  # 95/24 s = 118.75 frames at 30 fps
        self.assertEqual(len(r["smooth_root_2d"]), len(r["frame_indices"]))
        self.assertEqual(len(r["global_root_heading"]), len(r["frame_indices"]))
        # 30 fps frame 5 = 24 fps frame 4.0 exactly; frame 7 = 5.6 -> interpolated
        self.assertAlmostEqual(r["smooth_root_2d"][5][1], 2.0 * 4 / 95, places=6)
        self.assertAlmostEqual(r["smooth_root_2d"][7][1], 2.0 * 5.6 / 95, places=6)
        # same time -> same place: last 30 fps sample sits at 24 fps frame 94.4
        self.assertAlmostEqual(r["smooth_root_2d"][-1][1], 2.0 * 94.4 / 95, places=6)

    def test_heading_interpolates_across_pi(self):
        a, b = math.radians(170), math.radians(-170)
        e = {"type": "root2d", "frame_indices": [0, 1, 2, 3], "smooth_root_2d": [[0, 0]] * 4,
             "global_root_heading": [[math.cos(a), math.sin(a)], [math.cos(a), math.sin(a)],
                                     [math.cos(b), math.sin(b)], [math.cos(b), math.sin(b)]]}
        r = kimodo.resample_root2d(e, 24, 30)
        for c, s in r["global_root_heading"]:
            self.assertGreater(abs(math.degrees(math.atan2(s, c))), 165)  # never swings through 0
            self.assertAlmostEqual(c * c + s * s, 1.0, places=5)

    def test_sparse_indices_map_and_clamp(self):
        e = {"type": "root2d", "frame_indices": [0, 24, 95], "smooth_root_2d": [[0, 0], [0, 1], [0, 2]],
             "global_root_heading": [[1, 0], [0, 1], [1, 0]]}
        r = kimodo.resample_root2d(e, 24, 30, n_dst=119)
        self.assertEqual(r["frame_indices"], [0, 30, 118])  # 118.75 -> 119 clamped to n_dst-1
        self.assertEqual(r["smooth_root_2d"], [[0, 0], [0, 1], [0, 2]])
        self.assertEqual(len(r["global_root_heading"]), 3)
        w = kimodo.resample_root2d({"type": "root2d", "frame_indices": [10], "smooth_root_2d": [[1, 2]]}, 24, 30)
        self.assertEqual(w, {"type": "root2d", "frame_indices": [12], "smooth_root_2d": [[1, 2]]})

    def test_convert_drops_end_effector(self):
        body = motion_body()["characters"]["hero"]
        out, warns = kimodo.convert_constraints(body, 24, 96)
        self.assertEqual([c["type"] for c in out], ["root2d", "root2d"])
        self.assertTrue(any("end_effector" in w for w in warns))
        for c in out:
            self.assertEqual(len(c["frame_indices"]), len(c["smooth_root_2d"]))
        with self.assertRaises(ValueError):
            kimodo.resample_root2d({"frame_indices": [1, 2], "smooth_root_2d": [[0, 0]]})

    def test_prompt_and_durations(self):
        self.assertEqual(kimodo.split_prompt("a man walks. He stops!\nthen sits"),
                         ["a man walks", "He stops", "then sits"])
        ds = kimodo.durations(3, 4.0)
        self.assertAlmostEqual(sum(ds), 4.0, places=6)
        cmd = kimodo.build_command("kimodo_gen", "walk. stop", 4.0, 3, 4, "/o/x", "/o/c.json")
        self.assertEqual(cmd[:4], ["kimodo_gen", "walk. stop.", "--duration", "2 2"])
        self.assertIn("--bvh", cmd)
        self.assertEqual(cmd[cmd.index("--constraints") + 1], "/o/c.json")
        self.assertEqual(cmd[cmd.index("--num_samples") + 1], "4")

    def test_discover_outputs(self):
        d = tempfile.mkdtemp()
        try:
            for k in (0, 1, 10):
                for ext in (".npz", ".bvh"):
                    open(os.path.join(d, f"x_{k}{ext}"), "w").close()
            pairs = kimodo.discover_outputs(os.path.join(d, "x"))
            self.assertEqual([os.path.basename(n) for n, _ in pairs], ["x_0.npz", "x_1.npz", "x_10.npz"])
            self.assertTrue(all(b and os.path.splitext(b)[0] == os.path.splitext(n)[0] for n, b in pairs))
        finally:
            shutil.rmtree(d)


class TakeRanking(unittest.TestCase):
    def take(self, d, name, floating=False, teleport=False, offset=0.0):
        cons = [kimodo.resample_root2d(dense_path(48, 1.0), 24, 30, 60)]
        npz, _ = fakemotion.write_take(os.path.join(d, name), 60, cons, 1, floating=floating)
        pj, rp = takes.load_takes(npz)[0]
        if teleport:
            rp.data[30 * 3 + 0] += 2.0
        if offset:
            for t in range(60):
                rp.data[t * 3 + 0] += offset
        return {"id": name, "posed_joints": pj, "root_positions": rp}, cons

    def test_rank_rejects_floating_and_teleport(self):
        d = tempfile.mkdtemp()
        try:
            good, cons = self.take(d, "good")
            off, _ = self.take(d, "off", offset=0.3)
            flo, _ = self.take(d, "float", floating=True)
            tel, _ = self.take(d, "tele", teleport=True)
            ok, bad = takes.rank([flo, off, tel, good], cons)
            self.assertEqual([t["id"] for t in ok], ["good", "off"])  # sorted by root error
            self.assertEqual({t["id"] for t in bad}, {"float", "tele"})
            self.assertIn("floating", " ".join(flo["metrics"]["reasons"]))
            self.assertIn("teleport", " ".join(tel["metrics"]["reasons"]))
            self.assertGreater(flo["metrics"]["median_low"], 0.7)
            self.assertAlmostEqual(off["metrics"]["root_err"], 0.3, delta=0.06)
        finally:
            shutil.rmtree(d)

    def test_thresholds_configurable(self):
        d = tempfile.mkdtemp()
        try:
            flo, cons = self.take(d, "float", floating=True)
            ok, _ = takes.rank([flo], cons, {"float_height": 2.0})
            self.assertEqual(len(ok), 1)
        finally:
            shutil.rmtree(d)

    def test_npz_roundtrip_and_batched(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "b.npz")
            vals = [float(i) for i in range(2 * 3 * 4 * 3)]
            npyio.save_npz(p, {"posed_joints": (vals, (2, 3, 4, 3)), "root_positions": (vals[:18], (2, 3, 3))})
            ts = takes.load_takes(p)
            self.assertEqual(len(ts), 2)
            self.assertEqual(ts[1][0].shape, (3, 4, 3))
            self.assertEqual(ts[1][0].data[0], 36.0)
            self.assertEqual(ts[1][1].data, [9.0 + i for i in range(9)])
        finally:
            shutil.rmtree(d)


class H3Graph(unittest.TestCase):
    def test_snap_17k_plus_5(self):
        self.assertEqual([h3.snap_frames(n) for n in (1, 5, 6, 22, 23, 96, 124, 125, 362)],
                         [5, 5, 22, 22, 39, 107, 124, 141, 362])
        for n in range(1, 400):
            s = h3.snap_frames(n)
            self.assertTrue(s >= n and (s - 5) % 17 == 0 and s - n < 17)

    def test_ref2v_graph(self):
        cfg = config.DEFAULTS["generate"]
        g = h3.build_graph(cfg, "ref2v", "p", 107, 960, 544, 3, {"video": "v.mp4", "images": ["a.png", "b.png"]})
        cin = g["cond"]["inputs"]
        self.assertEqual(g["cond"]["class_type"], "MiniMaxH3ReferenceToVideo")
        self.assertEqual(cin["ref_videos.ref_video_0"], ["refc", 0])
        self.assertEqual(cin["ref_images.ref_image_1"], ["refi1", 0])
        self.assertEqual(g["refi1"]["inputs"]["image"], "b.png")
        self.assertEqual(g["unet"]["inputs"]["unet_name"], "minimax_h3_ref2va_pruned_int8_convrot.safetensors")
        self.assertEqual(g["sched"]["inputs"]["model"], ["lora", 0])
        self.assertEqual(g["sched"]["inputs"]["steps"], 8)
        self.assertEqual(g["lora"]["inputs"]["lora_name"],
                         "minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors")
        self.assertEqual(g["sampler"]["inputs"]["sampler_name"], "res_multistep")
        self.assertEqual(cin["length"], 107)

    def test_flf_graph_and_prompt(self):
        cfg = config.DEFAULTS["generate"]
        g = h3.build_graph(cfg, "flf", "p", 124, 960, 544, 3, {"first": "f.png", "last": "l.png"})
        self.assertEqual(g["cond"]["class_type"], "MiniMaxH3ImageToVideo")
        self.assertNotIn("lora", g)
        self.assertEqual(g["guider"]["inputs"]["model"], ["unet", 0])
        shot = shot_json()
        p = h3.build_prompt(shot, "ref2v", ["hero", "hero", "style"])
        self.assertIn("<Picture 1> <Picture 2> show the character hero", p)
        self.assertIn("<Picture 3> shows the look and style", p)
        self.assertIn("<Video 1>", p)
        self.assertIn("Audio: wind.", p)
        self.assertNotIn("<Video 1>", h3.build_prompt(shot, "flf"))
        shot["render"]["prompt"] = "override"
        self.assertEqual(h3.build_prompt(shot, "ref2v"), "override")
        self.assertEqual(h3.snap_resolution(1280, 718), (1280, 720))

    def test_reference_images(self):
        d = tempfile.mkdtemp()
        try:
            for n in ("a.png", "b.png", "s.png"):
                open(os.path.join(d, n), "w").close()
            shot = shot_json(look={"prompt": "", "style_refs": ["//s.png", "missing.png"]})
            shot["characters"][0]["refs"] = ["a.png", os.path.join(d, "b.png")] + ["a.png"] * 3
            refs = h3.reference_images(shot, d)
            self.assertEqual([(os.path.basename(p), lab) for p, lab in refs],
                             [("a.png", "hero"), ("b.png", "hero"), ("s.png", "style")])
        finally:
            shutil.rmtree(d)

    def test_example_config_loads(self):
        cfg = config.build(os.path.join(REPO, "server", "config.example.json"), {"data_dir": tempfile.mkdtemp()},
                           ["motion.num_samples=6"], backends=["eval=fake"])
        self.assertEqual((cfg["motion"]["device"], cfg["generate"]["device"]), ("gpu1", "gpu0"))
        self.assertEqual(cfg["gpus"]["gpu1"]["env"]["CUDA_VISIBLE_DEVICES"], "1")
        self.assertEqual(cfg["motion"]["num_samples"], 6)
        self.assertEqual(cfg["generate"]["scheduler"], "simple")  # defaults merged under the file
        app = orchestrator.Orchestrator(cfg)
        self.assertEqual((app.motion.impl, app.generate.impl, app.evaluate.impl), ("kimodo", "comfyui", "fake"))
        shutil.rmtree(cfg["data_dir"])

    def test_cache_key_stable(self):
        self.assertEqual(cache_key("motion", {"a": 1, "b": [1, 2]}), cache_key("motion", {"b": [1, 2], "a": 1}))
        self.assertNotEqual(cache_key("motion", {"a": 1}), cache_key("passes", {"a": 1}))


# -- HTTP, fake backends ------------------------------------------------------------------

class HttpFake(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.c = self.h.c

    def tearDown(self):
        self.h.close()

    def test_health(self):
        hl = self.c.health()
        for k in ("plan", "motion", "retarget", "passes", "generate", "eval", "gpu gpu0", "gpu gpu1", "jobs"):
            self.assertIn(k, hl)
        self.assertTrue(hl["motion"].startswith("fake"))
        self.assertEqual(hl["gpu gpu0"], "idle")

    def test_project_shot_roundtrip(self):
        self.h.setup_shot()
        got = self.c.get_shot("demo", "sh_010")
        self.assertEqual(got["shot"]["action_prompt"], shot_json()["action_prompt"])
        self.assertEqual(got["stages"], {})
        self.assertTrue(os.path.isfile(os.path.join(self.h.root, "shots", "sh_010.json")))
        self.assertTrue(os.path.isfile(os.path.join(self.h.root, "project.json")))
        code, body = self.h.raw("GET", "/projects/demo")
        self.assertEqual((code, body["root"], body["shots"]), (200, self.h.root, ["sh_010"]))

    def test_plan(self):
        shots = self.c.plan("demo", "A man runs to the wall. He vaults over it.\nHe lands in a crouch",
                            [{"char_id": "hero", "rig": "x.blend#Hero"}])["shots"]
        self.assertEqual([s["id"] for s in shots], ["sh_010", "sh_020", "sh_030"])
        self.assertEqual(shots[1]["action_prompt"], "He vaults over it")
        self.assertEqual(shots[0]["characters"][0]["rig"], "x.blend#Hero")

    def test_full_pipeline_and_cache(self):
        snap = self.h.setup_shot()
        r = self.c.motion("demo", "sh_010", motion_body())
        self.assertIn(r["status"], ("queued", "running"))
        j = self.h.wait(r["job_id"])
        self.assertEqual(j["status"], "done", j)
        self.assertEqual(j["stage"], "retarget")
        self.assertEqual(j["progress"], 1.0)
        acts = j["artifact"]["actions"]
        self.assertEqual([(a["char"], a["variant"]) for a in acts], [("hero", 0), ("hero", 1)])
        self.assertTrue(os.path.isfile(j["artifact"]["blend"]))
        self.assertTrue(any("end_effector" in w for w in j["warnings"]))
        takes_json = os.path.join(os.path.dirname(j["artifact"]["blend"]), "takes.json")
        self.assertEqual(len(json.load(open(takes_json))["hero"]), 4)  # num_samples default 4

        # passes
        p = self.h.wait(self.c.passes("demo", "sh_010", passes_body(snap))["job_id"])
        self.assertEqual(p["status"], "done", p)
        for k in ("playblast", "first", "kp2d"):
            self.assertTrue(os.path.isfile(p["artifact"][k]), k)
        # generate: video then pck in one job (stage generate -> eval)
        g = self.h.wait(self.c.generate("demo", "sh_010", "ref2v")["job_id"])
        self.assertEqual(g["status"], "done", g)
        self.assertEqual(g["stage"], "eval")
        self.assertTrue(os.path.isfile(g["artifact"]["video"]))
        self.assertTrue(0.0 <= g["artifact"]["pck"] <= 1.0)
        req = json.load(open(os.path.join(os.path.dirname(g["artifact"]["video"]), "request.json")))
        self.assertEqual(req["frames"], 107)
        self.assertEqual((req["width"], req["height"]), (960, 544))

        st = self.c.get_shot("demo", "sh_010")
        self.assertEqual({k: v["status"] for k, v in st["stages"].items()},
                         {"motion": "done", "retarget": "done", "passes": "done", "generate": "done", "eval": "done"})
        self.assertEqual(set(st["shot"]["hashes"]), {"motion", "passes", "generate"})

        # same inputs -> cache hits with the same artifacts
        j2 = self.h.wait(self.c.motion("demo", "sh_010", motion_body())["job_id"])
        self.assertTrue(j2["cache_hit"])
        self.assertEqual(j2["artifact"], j["artifact"])
        p2 = self.h.wait(self.c.passes("demo", "sh_010", passes_body(snap))["job_id"])
        self.assertTrue(p2["cache_hit"])
        g2 = self.h.wait(self.c.generate("demo", "sh_010", "ref2v")["job_id"])
        self.assertTrue(g2["cache_hit"])
        self.assertEqual(g2["artifact"], g["artifact"])
        # changed inputs -> miss
        self.c.put_shot("demo", "sh_010", shot_json(seed=8))
        j3 = self.h.wait(self.c.motion("demo", "sh_010", motion_body())["job_id"])
        self.assertFalse(j3["cache_hit"])
        self.assertNotEqual(j3["artifact"]["blend"], j["artifact"]["blend"])
        g3 = self.h.wait(self.c.generate("demo", "sh_010", "flf")["job_id"])
        self.assertFalse(g3["cache_hit"])
        # force bypasses the cache
        code, r = self.h.raw("POST", "/projects/demo/shots/sh_010/passes",
                             json.dumps({**passes_body(snap), "force": True}).encode())
        self.assertEqual(code, 202)
        self.assertFalse(self.h.wait(r["job_id"])["cache_hit"])
        # GET /jobs lists them
        code, lst = self.h.raw("GET", "/jobs")
        self.assertGreaterEqual(len(lst["jobs"]), 8)

    def test_errors(self):
        self.assertEqual(self.h.raw("GET", "/jobs/nope")[0], 404)
        self.assertEqual(self.h.raw("POST", "/jobs/nope/cancel", b"{}")[0], 404)
        self.assertEqual(self.h.raw("GET", "/nothing")[0], 404)
        with self.assertRaises(OrchestratorError) as cm:
            self.c.motion("demo", "sh_404", motion_body())
        self.assertIn("404", str(cm.exception))
        self.assertEqual(self.h.raw("PUT", "/projects/demo/shots/sh_010", b"{not json")[0], 400)
        self.assertEqual(self.h.raw("PUT", "/projects/demo/shots/sh_010", b'{"id": "other"}')[0], 400)
        self.h.setup_shot()
        self.assertEqual(self.h.raw("POST", "/projects/demo/shots/sh_010/passes", b"{}")[0], 400)
        g = self.h.wait(self.c.generate("demo", "sh_010", "ref2v")["job_id"])
        self.assertEqual(g["status"], "failed")
        self.assertIn("render passes first", g["error"])
        self.assertEqual(self.c.get_shot("demo", "sh_010")["stages"]["generate"]["status"], "failed")

    def test_persistence_across_restart(self):
        self.h.setup_shot()
        j = self.h.wait(self.c.motion("demo", "sh_010", motion_body())["job_id"])
        self.h.stop()
        self.h.start()
        again = Client(self.h.url).job(j["id"])
        self.assertEqual((again["status"], again["artifact"]), ("done", j["artifact"]))
        st = Client(self.h.url).get_shot("demo", "sh_010")
        self.assertEqual(st["stages"]["retarget"]["status"], "done")
        j2 = self.h.wait(Client(self.h.url).motion("demo", "sh_010", motion_body())["job_id"])
        self.assertTrue(j2["cache_hit"])  # the cache index survives too


class HttpBehaviour(unittest.TestCase):
    def tearDown(self):
        self.h.close()

    def test_floating_takes_rejected(self):
        self.h = Harness(sets=["motion.fake_floating=[0,2]"])
        self.h.setup_shot()
        j = self.h.wait(self.h.c.motion("demo", "sh_010", motion_body())["job_id"])
        self.assertEqual(j["status"], "done", j)
        self.assertEqual(len(j["artifact"]["actions"]), 2)
        rep = json.load(open(os.path.join(os.path.dirname(j["artifact"]["blend"]), "takes.json")))["hero"]
        self.assertEqual([t["rejected"] for t in rep], [False, False, True, True])
        self.assertTrue(all("_s7_1" in t["bvh"] or "_s7_3" in t["bvh"] for t in rep[:2]))

    def test_all_rejected_fails_or_keeps(self):
        self.h = Harness(sets=["motion.fake_floating=[0,1,2,3]", "motion.max_rounds=1"])
        self.h.setup_shot()
        j = self.h.wait(self.h.c.motion("demo", "sh_010", motion_body())["job_id"])
        self.assertEqual(j["status"], "failed")
        self.assertIn("all 8 Kimodo takes rejected", j["error"])
        self.assertIn("floating", j["error"])
        self.h.app.cfg["motion"]["keep_rejected"] = True
        j = self.h.wait(self.h.c.motion("demo", "sh_010", motion_body())["job_id"])
        self.assertEqual(j["status"], "done")
        self.assertEqual(len(j["artifact"]["actions"]), 1)
        self.assertTrue(any("least bad" in w for w in j["warnings"]))

    def test_cancel_running_job(self):
        self.h = Harness(sets=["motion.fake_delay_s=2.0"])
        self.h.setup_shot()
        jid = self.h.c.motion("demo", "sh_010", motion_body())["job_id"]
        self.h.wait(jid, until=("running",))
        t0 = time.time()
        self.assertTrue(self.h.c.cancel(jid)["cancelled"])
        j = self.h.wait(jid)
        self.assertEqual(j["status"], "cancelled")
        self.assertLess(time.time() - t0, 3.0)
        self.assertEqual(self.h.c.get_shot("demo", "sh_010")["stages"]["motion"]["status"], "cancelled")
        self.assertFalse(self.h.c.cancel(jid)["cancelled"])  # already terminal

    def test_gpu_serialises_same_device(self):
        # motion and generate both on gpu0: the second job waits for the card
        self.h = Harness(sets=["motion.fake_delay_s=0.4", "motion.device=\"gpu0\"",
                               "generate.fake_delay_s=1.0"])
        spans = []

        def timed(fn, name):
            def wrapper(*a, **k):
                t = time.monotonic()
                try:
                    return fn(*a, **k)
                finally:
                    spans.append((name, t, time.monotonic()))
            return wrapper
        self.h.app.motion.generate = timed(self.h.app.motion.generate, "kimodo")
        self.h.app.generate.generate = timed(self.h.app.generate.generate, "h3")
        snap = self.h.setup_shot()
        self.h.c.put_shot("demo", "sh_020", shot_json("sh_020"))
        self.h.wait(self.h.c.passes("demo", "sh_020", passes_body(snap))["job_id"])
        g = self.h.c.generate("demo", "sh_020", "ref2v")["job_id"]
        self.h.wait(g, until=("running",))
        m = self.h.c.motion("demo", "sh_010", motion_body())["job_id"]
        self.assertEqual(self.h.wait(m, until=("waiting_gpu", "running", "done"))["status"], "waiting_gpu")
        self.assertEqual(self.h.wait(g)["status"], "done")
        self.assertEqual(self.h.wait(m)["status"], "done")
        (_, h0, h1), = [s for s in spans if s[0] == "h3"]
        for _, k0, k1 in (s for s in spans if s[0] == "kimodo"):
            self.assertTrue(k0 >= h1 or k1 <= h0, "Kimodo and H3 overlapped on gpu0")

    def test_different_devices_overlap(self):
        self.h = Harness(sets=["motion.fake_delay_s=0.6"])
        self.h.setup_shot()
        self.h.c.put_shot("demo", "sh_020", shot_json("sh_020"))
        a = self.h.c.motion("demo", "sh_010", motion_body())["job_id"]
        self.h.wait(a, until=("running",))
        b = self.h.c.motion("demo", "sh_020", motion_body())["job_id"]
        self.assertEqual(self.h.wait(b, until=("waiting_gpu", "running", "done"))["status"], "waiting_gpu")
        self.h.wait(a)
        self.h.wait(b)
        self.h.app.cfg["motion"]["device"] = None  # no GPU lock: both run at once
        self.h.app.motion.cfg["device"] = None
        a = self.h.c.motion("demo", "sh_010", {**motion_body(), "force": True})["job_id"]
        b = self.h.c.motion("demo", "sh_020", {**motion_body(), "force": True})["job_id"]
        self.h.wait(a, until=("running",))
        self.assertEqual(self.h.wait(b, until=("waiting_gpu", "running"))["status"], "running")


class KimodoCli(unittest.TestCase):
    """The real Kimodo backend against a fake kimodo_gen script."""

    def test_command_env_and_outputs(self):
        tmp = tempfile.mkdtemp()
        args_path = os.path.join(tmp, "args.json")
        self.h = Harness(backends=["motion=kimodo"], sets=[
            f"motion.command={json.dumps([sys.executable, FAKE_KIMODO])}",
            f"motion.env={json.dumps({'FAKE_KIMODO_ARGS': args_path, 'FAKE_KIMODO_FLOAT': '1'})}"])
        try:
            self.assertTrue(self.h.c.health()["motion"].startswith("kimodo: ok"))
            self.h.setup_shot()
            j = self.h.wait(self.h.c.motion("demo", "sh_010", motion_body())["job_id"])
            self.assertEqual(j["status"], "done", j)
            got = json.load(open(args_path))
            argv = got["argv"]
            self.assertEqual(got["cuda"], "1")  # motion.device gpu1 -> CUDA_VISIBLE_DEVICES=1
            self.assertEqual(argv[0], "a man walks forward. he stops.")
            self.assertEqual(argv[argv.index("--duration") + 1], "2 2")
            self.assertEqual(argv[argv.index("--seed") + 1], "7")
            self.assertEqual(argv[argv.index("--num_samples") + 1], "4")
            self.assertIn("--bvh", argv)
            cons = json.load(open(argv[argv.index("--constraints") + 1]))
            self.assertEqual([c["type"] for c in cons], ["root2d", "root2d"])
            self.assertEqual(cons[1]["frame_indices"], [30])
            self.assertEqual(cons[0]["frame_indices"][-1], 118)
            self.assertEqual(len(j["artifact"]["actions"]), 2)
            rep = json.load(open(os.path.join(os.path.dirname(j["artifact"]["blend"]), "takes.json")))["hero"]
            self.assertEqual(sum(t["rejected"] for t in rep), 1)
        finally:
            self.h.close()
            shutil.rmtree(tmp)

    def test_missing_kimodo_reports_and_fails(self):
        self.h = Harness(backends=["motion=kimodo"], sets=["motion.venv=\"/nonexistent/venv\""])
        try:
            self.assertIn("missing venv", self.h.c.health()["motion"])
            self.h.setup_shot()
            j = self.h.wait(self.h.c.motion("demo", "sh_010", motion_body())["job_id"])
            self.assertEqual(j["status"], "failed")
            self.assertIn("kimodo_gen", j["error"])
        finally:
            self.h.close()


class ComfyH3(unittest.TestCase):
    """The real ComfyUI backend against a fake ComfyUI."""

    def setUp(self):
        self.comfy = FakeComfy(delay=0.6)
        self.h = Harness(backends=["generate=comfyui"],
                         sets=[f"generate.url=\"{self.comfy.url}\"", "generate.poll_s=0.05"])

    def tearDown(self):
        self.h.close()
        self.comfy.close()

    def _prep(self, mode="ref2v"):
        shot = shot_json(look={"prompt": "rooftop", "style_refs": ["refs/style.png"]})
        shot["render"]["mode"] = mode
        shot["characters"][0]["refs"] = ["refs/front.png", "refs/side.png"]
        os.makedirs(os.path.join(self.h.root, "refs"), exist_ok=True)
        for n in ("front", "side", "style"):
            open(os.path.join(self.h.root, "refs", n + ".png"), "wb").write(b"PNG" + n.encode())
        snap = self.h.setup_shot(shot)
        self.h.wait(self.h.c.passes("demo", "sh_010", passes_body(snap))["job_id"])

    def test_ref2v(self):
        self.assertIn("ComfyUI 0.0-fake", self.h.c.health()["generate"])
        self._prep()
        jid = self.h.c.generate("demo", "sh_010", "ref2v")["job_id"]
        progress = set()
        while True:
            j = self.h.c.job(jid)
            progress.add(round(j["progress"], 2))
            if j["status"] in ("done", "failed"):
                break
            time.sleep(0.02)
        self.assertEqual(j["status"], "done", j)
        self.assertGreater(len(progress), 3)  # websocket step progress came through
        with open(j["artifact"]["video"], "rb") as f:
            self.assertTrue(f.read().startswith(b"FAKEVIDEO"))
        self.assertEqual(j["artifact"]["audio"], j["artifact"]["video"])
        self.assertIn("pck", j["artifact"])
        (pid, p), = self.comfy.prompts.items()
        g = p["graph"]
        cin = g["cond"]["inputs"]
        self.assertEqual(cin["length"], 107)
        self.assertEqual(sorted(k for k in cin if k.startswith("ref_images.")),
                         ["ref_images.ref_image_0", "ref_images.ref_image_1", "ref_images.ref_image_2"])
        self.assertIn("<Picture 1> <Picture 2> show the character hero", cin["prompt"])
        self.assertIn("<Picture 3> shows the look and style", cin["prompt"])
        self.assertIn("<Video 1>", cin["prompt"])
        self.assertEqual(self.comfy.uploads[g["refi0"]["inputs"]["image"]], b"PNGfront")
        self.assertEqual(self.comfy.uploads[g["refv"]["inputs"]["file"]], b"FAKE-MP4")
        self.assertEqual(g["sched"]["inputs"]["steps"], 8)

    def test_flf(self):
        self._prep("flf")
        j = self.h.wait(self.h.c.generate("demo", "sh_010", "flf")["job_id"])
        self.assertEqual(j["status"], "done", j)
        (pid, p), = self.comfy.prompts.items()
        self.assertEqual(p["graph"]["cond"]["class_type"], "MiniMaxH3ImageToVideo")
        self.assertEqual(p["graph"]["unet"]["inputs"]["unet_name"], "minimax_h3_fl2va_pruned_int8_convrot.safetensors")
        self.assertTrue(self.comfy.uploads[p["graph"]["first"]["inputs"]["image"]].startswith(b"\x89PNG"))

    def test_cancel_interrupts_comfy(self):
        self.comfy.delay = 30
        self._prep()
        jid = self.h.c.generate("demo", "sh_010", "ref2v")["job_id"]
        t0 = time.time()
        while not self.comfy.prompts and time.time() - t0 < 10:
            time.sleep(0.02)
        self.h.c.cancel(jid)
        self.assertEqual(self.h.wait(jid)["status"], "cancelled")
        self.assertGreaterEqual(self.comfy.interrupts, 1)
        self.assertTrue(all(p["interrupted"] for p in self.comfy.prompts.values()))

    def test_comfy_down(self):
        self.comfy.close()
        self.h.app.refresh_health()
        self.assertIn("down", self.h.c.health()["generate"])


@unittest.skipUnless(BLENDER and os.path.exists(BLENDER), "set BLENDER to run the Blender retarget tests")
class BlenderRetarget(unittest.TestCase):
    """Fake Kimodo takes -> real retarget worker -> actions checked in Blender."""

    def blender(self, *args):
        r = subprocess.run([BLENDER, "-b", "--factory-startup", "--python", BLENDER_RIG, "--", *args],
                           capture_output=True, text=True, timeout=300)
        self.assertIn("BLENDER_RIG_OK", r.stdout, r.stdout[-2000:] + r.stderr[-2000:])

    def run_motion(self, rig_json, start, scale):
        self.h = Harness(backends=["retarget=blender"], cfg_over={"blender": BLENDER})
        self.addCleanup(self.h.close)
        self.h.setup_shot(shot_json(prompt="walks forward"))
        with open(rig_json) as f:
            rig = json.load(f)
        body = motion_body(rig=rig)
        body["characters"]["hero"]["start_matrix_world"] = start
        body["characters"]["hero"]["scale"] = scale
        j = self.h.wait(self.h.c.motion("demo", "sh_010", body)["job_id"], timeout=300)
        self.assertEqual(j["status"], "done", j.get("error"))
        out = os.path.join(self.h.tmp, "check.json")
        self.blender("check", rig_json, j["artifact"]["blend"],
                     os.path.join(os.path.dirname(j["artifact"]["blend"]), "actions.json"), out)
        with open(out) as f:
            return j, json.load(f)

    def test_bonemap_rig(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        rig_json = os.path.join(tmp, "rig.json")
        self.blender("dump", rig_json)
        # START at (5, 1, 0) facing world -X (its +Y); the demo rig's hip is 0.95 m like SOMA
        start = [[0, -1, 0, 5], [1, 0, 0, 1], [0, 0, 1, 0], [0, 0, 0, 1]]
        j, res = self.run_motion(rig_json, start, 1.0)
        self.assertEqual([a["action"] for a in j["artifact"]["actions"]], ["mo_hero_0", "mo_hero_1"])
        frames = res["mo_hero_0"]["frames"]
        self.assertEqual(len(frames), 96)
        hips0, hips_end = frames[0]["pelvis"], frames[-1]["pelvis"]
        self.assertAlmostEqual(hips0[0], 5.0, delta=0.06)
        self.assertAlmostEqual(hips0[1], 1.0, delta=0.06)
        # 2 m along START's facing (world -X) over the shot, at hip height
        self.assertAlmostEqual(hips0[0] - hips_end[0], 2.0 * 94.4 / 95, delta=0.15)
        self.assertAlmostEqual(hips_end[2], 0.96, delta=0.06)
        low = min(min(f["l_ankle"][2], f["r_ankle"][2]) for f in frames)
        self.assertLess(low, 0.15)  # feet reach the ground
        # left hip is on the character's left: facing -X, left is -Y
        self.assertLess(frames[10]["l_hip"][1], frames[10]["r_hip"][1])

    def test_soma_rig_one_to_one(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        stem = os.path.join(tmp, "soma")
        fakemotion.write_take(stem, 10, [], 1)
        rig_json = os.path.join(tmp, "rig.json")
        self.blender("dump", rig_json, "soma", stem + ".bvh")
        ident = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
        j, res = self.run_motion(rig_json, ident, 1.0)
        with open(os.path.join(self.h.cfg["data_dir"], "jobs", j["id"] + ".log")) as f:
            self.assertIn("SOMA 1:1", f.read())
        frames = res["mo_hero_0"]["frames"]
        # identity START: canonical +Z forward -> world +Y; walks 2 m, hips at 0.95 m
        self.assertAlmostEqual(frames[0]["pelvis"][1], 0.0, delta=0.06)
        self.assertAlmostEqual(frames[-1]["pelvis"][1], 2.0 * 94.4 / 95, delta=0.15)
        self.assertAlmostEqual(frames[0]["pelvis"][2], 0.95, delta=0.06)


if __name__ == "__main__":
    unittest.main()
