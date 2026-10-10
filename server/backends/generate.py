"""Video generation: MiniMax H3 on a local ComfyUI (HTTP API), or fake.

`generate(job, req, gpu_env)` gets
  {mode, prompt, frames, width, height, seed, out_path, first, last, playblast, images: [paths]}
and returns {"video": path}. ComfyUI must already run (on the GPU named by
this backend's `device`; the orchestrator only serialises access to it).
"""

import base64
import json
import os
import shutil
import socket
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .. import h3
from .base import Backend


class ComfyError(RuntimeError):
    pass


class ComfyClient:
    def __init__(self, url, timeout=30):
        self.url = url.rstrip("/")
        self.timeout = timeout

    def _open(self, req, timeout=None):
        try:
            return urllib.request.urlopen(req, timeout=timeout or self.timeout)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:2000]
            raise ComfyError(f"ComfyUI {req.get_method()} {req.full_url}: HTTP {e.code} {detail}") from None
        except (urllib.error.URLError, OSError) as e:
            raise ComfyError(f"ComfyUI unreachable at {self.url}: {getattr(e, 'reason', e)}") from None

    def get(self, path, timeout=None):
        with self._open(urllib.request.Request(self.url + path), timeout) as r:
            return json.loads(r.read() or b"{}")

    def post(self, path, data, timeout=None):
        req = urllib.request.Request(self.url + path, json.dumps(data).encode(),
                                     {"Content-Type": "application/json"}, method="POST")
        with self._open(req, timeout) as r:
            raw = r.read()
        return json.loads(raw) if raw.strip() else {}

    def upload(self, path, name):
        boundary = "----chimera" + uuid.uuid4().hex
        with open(path, "rb") as f:
            payload = f.read()
        parts = []
        for k, v in (("type", "input"), ("overwrite", "true")):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{name}"\r\n'
                      "Content-Type: application/octet-stream\r\n\r\n").encode() + payload + b"\r\n")
        body = b"".join(parts) + f"--{boundary}--\r\n".encode()
        req = urllib.request.Request(self.url + "/upload/image", body,
                                     {"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST")
        with self._open(req, 600) as r:
            res = json.loads(r.read())
        return (res.get("subfolder") + "/" if res.get("subfolder") else "") + res["name"]

    def download(self, item, out_path):
        q = urllib.parse.urlencode({"filename": item["filename"], "subfolder": item.get("subfolder", ""),
                                    "type": item.get("type", "output")})
        with self._open(urllib.request.Request(f"{self.url}/view?{q}"), 600) as r, open(out_path, "wb") as f:
            shutil.copyfileobj(r, f)


class ProgressSocket:
    """Minimal RFC 6455 client for ComfyUI's /ws (server frames are unmasked).

    Feeds `progress` / `executing` messages for one prompt to a callback. Best
    effort: completion is still decided by /history polling."""

    def __init__(self, url, client_id, on_msg):
        u = urllib.parse.urlparse(url)
        self.host, self.port = u.hostname, u.port or 80
        self.path = (u.path.rstrip("/") or "") + "/ws?clientId=" + client_id
        self.on_msg = on_msg
        self.sock = None
        self.stop = threading.Event()

    def start(self):
        try:
            s = socket.create_connection((self.host, self.port), timeout=5)
            key = base64.b64encode(os.urandom(16)).decode()
            s.sendall((f"GET {self.path} HTTP/1.1\r\nHost: {self.host}:{self.port}\r\nUpgrade: websocket\r\n"
                       f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                       "Sec-WebSocket-Version: 13\r\n\r\n").encode())
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = s.recv(1024)
                if not chunk:
                    raise OSError("closed during handshake")
                head += chunk
            if b" 101 " not in head.split(b"\r\n", 1)[0]:
                raise OSError(head.split(b"\r\n", 1)[0].decode(errors="replace"))
            self._buf = head.split(b"\r\n\r\n", 1)[1]
            s.settimeout(1.0)
            self.sock = s
        except OSError:
            self.sock = None
            return False
        threading.Thread(target=self._loop, daemon=True).start()
        return True

    def _recv(self, n):
        while len(self._buf) < n:
            if self.stop.is_set():
                raise OSError("stopped")
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                raise OSError("closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _loop(self):
        frag = b""
        try:
            while not self.stop.is_set():
                b0, b1 = self._recv(2)
                n = b1 & 0x7F
                if n == 126:
                    n = struct.unpack(">H", self._recv(2))[0]
                elif n == 127:
                    n = struct.unpack(">Q", self._recv(8))[0]
                mask = self._recv(4) if b1 & 0x80 else None
                data = self._recv(n)
                if mask:
                    data = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
                op = b0 & 0x0F
                if op == 0x8:
                    break
                if op == 0x9:  # ping -> pong (client frames are masked)
                    m = os.urandom(4)
                    self.sock.sendall(bytes([0x8A, 0x80 | len(data)]) + m
                                      + bytes(c ^ m[i % 4] for i, c in enumerate(data)))
                    continue
                if op in (0x1, 0x0):
                    frag += data
                    if b0 & 0x80:
                        try:
                            self.on_msg(json.loads(frag))
                        except ValueError:
                            pass
                        frag = b""
        except (OSError, ValueError):
            pass
        finally:
            self.close()

    def close(self):
        self.stop.set()
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None


class ComfyH3(Backend):
    kind, impl = "generate", "comfyui"

    @property
    def client(self):
        return ComfyClient(self.cfg["url"])

    def health(self):
        c = ComfyClient(self.cfg["url"], timeout=3)
        try:
            st = c.get("/system_stats")
        except ComfyError as e:
            return f"down: {e}"
        dev = (st.get("devices") or [{}])[0]
        ver = (st.get("system") or {}).get("comfyui_version", "?")
        free = dev.get("vram_free")
        out = f"ok (ComfyUI {ver}, {dev.get('name', '?')}"
        out += f", {free / 2**30:.1f} GB free)" if free else ")"
        try:
            info = c.get("/object_info/MiniMaxH3ReferenceToVideo")
            if "MiniMaxH3ReferenceToVideo" not in info:
                out += "; H3 nodes missing"
        except ComfyError:
            out += "; H3 nodes missing"
        return out

    def _stage_input(self, path, tag):
        name = f"chimera_{tag}_{os.path.basename(path)}"
        root = self.cfg.get("comfy_root")
        if root:
            shutil.copyfile(path, os.path.join(os.path.expanduser(root), "input", name))
            return name
        return self.client.upload(path, name)

    def generate(self, job, req, gpu_env):
        c = self.client
        tag = job.id[-8:]
        mode = req["mode"]
        inputs = {}
        if mode == "flf":
            inputs["first"] = self._stage_input(req["first"], tag)
            inputs["last"] = self._stage_input(req["last"], tag)
        else:
            inputs["video"] = self._stage_input(req["playblast"], tag)
            inputs["images"] = [self._stage_input(p, f"{tag}_{k}") for k, p in enumerate(req.get("images") or [])]
        prefix = f"chimera/{job.get('project')}_{job.get('shot')}_{tag}"
        graph = h3.build_graph(self.cfg, mode, req["prompt"], req["frames"], req["width"], req["height"],
                               req["seed"], inputs, prefix=prefix)
        with open(os.path.splitext(req["out_path"])[0] + "_graph.json", "w") as f:
            json.dump(graph, f, indent=1)
        client_id = "chimera-" + uuid.uuid4().hex[:8]
        state = {"pid": None, "nodes_done": 0}
        base, span = job.get("progress") or 0.0, req.get("progress_span", 0.8)

        def on_msg(msg):
            d = msg.get("data") or {}
            if state["pid"] and d.get("prompt_id") not in (None, state["pid"]):
                return
            if msg.get("type") == "progress" and d.get("max"):
                job.set_progress(base + span * (0.15 + 0.8 * d["value"] / d["max"]))
            elif msg.get("type") == "executing" and d.get("node"):
                state["nodes_done"] += 1
                job.set_progress(base + span * min(0.15, 0.01 * state["nodes_done"]))

        ws = ProgressSocket(self.cfg["url"], client_id, on_msg) if self.cfg.get("websocket", True) else None
        if ws is not None and not ws.start():
            job.log("ComfyUI websocket unavailable; progress from /history only")
            ws = None
        try:
            r = c.post("/prompt", {"prompt": graph, "client_id": client_id})
            if r.get("node_errors"):
                raise ComfyError(f"ComfyUI rejected the graph: {json.dumps(r['node_errors'])[:1500]}")
            pid = state["pid"] = r["prompt_id"]
            job.log(f"ComfyUI prompt {pid} ({mode}, {req['frames']} frames, {req['width']}x{req['height']})")

            def interrupt():
                for path, data in (("/queue", {"delete": [pid]}), ("/interrupt", {"prompt_id": pid})):
                    try:
                        c.post(path, data, timeout=5)
                    except ComfyError as e:
                        job.log(f"cancel: {e}")
            job.on_cancel(interrupt)
            t0 = time.monotonic()
            while True:
                job.sleep(float(self.cfg.get("poll_s", 2.0)))
                if self.cfg.get("timeout_s") and time.monotonic() - t0 > self.cfg["timeout_s"]:
                    interrupt()
                    raise ComfyError(f"H3 timed out after {self.cfg['timeout_s']} s")
                try:
                    hist = c.get(f"/history/{pid}")
                except ComfyError as e:
                    job.log(f"history poll: {e}")
                    continue
                if pid not in hist:
                    continue
                st = hist[pid].get("status") or {}
                if st.get("status_str") == "error":
                    msgs = [m for m in st.get("messages", []) if m and m[0] == "execution_error"]
                    detail = msgs[-1][1].get("exception_message", "") if msgs else json.dumps(st)[:1500]
                    raise ComfyError(f"H3 failed in ComfyUI: {detail}")
                if st.get("completed") or st.get("status_str") == "success":
                    break
        finally:
            if ws is not None:
                ws.close()
        outs = (hist[pid].get("outputs") or {}).get("save") or {}
        items = outs.get("images") or outs.get("videos") or outs.get("gifs") or []
        if not items:
            raise ComfyError(f"ComfyUI finished but SaveVideo produced nothing: {json.dumps(outs)[:500]}")
        item = items[0]
        root = self.cfg.get("comfy_root")
        if root:
            shutil.copyfile(os.path.join(os.path.expanduser(root), "output", item.get("subfolder", ""),
                                         item["filename"]), req["out_path"])
        else:
            c.download(item, req["out_path"])
        job.log(f"H3 done in {time.monotonic() - t0:.0f} s -> {req['out_path']}")
        return {"video": req["out_path"]}


class FakeGenerate(Backend):
    kind, impl = "generate", "fake"

    def health(self):
        return "fake (copies the playblast)"

    def generate(self, job, req, gpu_env):
        steps = 5
        for i in range(steps):
            job.sleep(float(self.cfg.get("fake_delay_s") or 0.0) / steps)
            job.set_progress((job.get("progress") or 0.0) + 0.1)
        src = req.get("playblast")
        if src and os.path.isfile(src):
            shutil.copyfile(src, req["out_path"])
        else:
            with open(req["out_path"], "wb") as f:
                f.write(b"FAKE-MP4")
        with open(os.path.splitext(req["out_path"])[0] + "_request.json", "w") as f:
            json.dump(req, f, indent=1)
        return {"video": req["out_path"]}


IMPLS = {"comfyui": ComfyH3, "fake": FakeGenerate}
