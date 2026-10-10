"""Headless tests for the add-on. Run inside Blender:

    BLENDER_USER_SCRIPTS=<tmp> blender -b --factory-startup --python tests/test_addon.py

`run_tests.sh` sets that up (symlinks the add-on into <tmp>/addons) and,
with E2E=1, starts the mock orchestrator for the end-to-end section.
"""

import json
import math
import os
import sys
import tempfile
import time
import traceback

import addon_utils
import bpy


addon_utils.enable("chimera_director", default_set=True, handle_error=lambda e: traceback.print_exc())

from chimera_director import (  # noqa: E402
    compat, constraints, contract, events, prefs, scene_io, sync,
)
from chimera_director.ops import rig_map  # noqa: E402

FAILS = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def make_rig(name="HeroRig", hip=1.0):
    arm = bpy.data.armatures.new(name)
    rig = bpy.data.objects.new(name, arm)
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    for bname, head, tail in (("Hips", (0, 0, hip), (0, 0, hip + 0.1)),
                              ("Spine", (0, 0, hip + 0.1), (0, 0, hip + 0.4))):
        b = arm.edit_bones.new(bname)
        b.head, b.tail = head, tail
    bpy.ops.object.mode_set(mode="OBJECT")
    return rig


def setup_project():
    p = bpy.context.scene.chimera
    p.project_name = "test"
    p.root = tempfile.mkdtemp(prefix="chimera_test_")
    return p


# -----------------------------------------------------------------------------

def test_registration():
    print("registration")
    check(hasattr(bpy.types.Scene, "chimera"), "Scene.chimera registered")
    check(hasattr(bpy.ops.chimera, "generate_motion"), "operators registered")
    check(prefs.get() is not None, "preferences available")


def test_pure():
    print("pure helpers")
    check([scene_io.wan_frame_count(n) for n in (1, 2, 4, 5, 96, 97, 144)] == [1, 5, 5, 5, 97, 97, 145],
          "wan_frame_count snaps up to 4n+1")
    mixamo = ["mixamorig:Hips", "mixamorig:Spine", "mixamorig:Spine1", "mixamorig:Spine2", "mixamorig:Neck",
              "mixamorig:Head", "mixamorig:LeftShoulder", "mixamorig:LeftArm", "mixamorig:LeftForeArm",
              "mixamorig:LeftHand", "mixamorig:RightShoulder", "mixamorig:RightArm", "mixamorig:RightForeArm",
              "mixamorig:RightHand", "mixamorig:LeftUpLeg", "mixamorig:LeftLeg", "mixamorig:LeftFoot",
              "mixamorig:LeftToeBase", "mixamorig:RightUpLeg", "mixamorig:RightLeg", "mixamorig:RightFoot",
              "mixamorig:RightToeBase"]
    g = rig_map.guess(mixamo)
    check(len(g) == len(rig_map.CANONICAL_JOINTS), f"mixamo auto-map complete ({len(g)}/22)")
    check(g.get("l_knee") == "mixamorig:LeftLeg" and g.get("r_wrist") == "mixamorig:RightHand",
          "mixamo sides/limbs correct")
    check(g.get("spine1") == "mixamorig:Spine" and g.get("spine3") == "mixamorig:Spine2", "mixamo spine chain")
    rigify = ["DEF-spine", "DEF-spine.001", "DEF-thigh.L", "DEF-shin.L", "DEF-foot.L", "DEF-thigh.R",
              "DEF-upper_arm.L", "DEF-forearm.L", "DEF-hand.L", "DEF-head"]
    g = rig_map.guess(rigify)
    check(g.get("l_hip") == "DEF-thigh.L" and g.get("l_knee") == "DEF-shin.L" and g.get("r_hip") == "DEF-thigh.R",
          "rigify legs with .L/.R suffix")
    check(g.get("l_elbow") == "DEF-forearm.L" and g.get("l_wrist") == "DEF-hand.L", "rigify arm")


def test_shots_and_contract():
    print("shots, markers, contract")
    p = setup_project()
    scene = bpy.context.scene
    bpy.ops.chimera.shot_add()
    bpy.ops.chimera.shot_add()
    check([s.name for s in p.shots] == ["sh_010", "sh_020"], "shot ids")
    s1, s2 = p.shots
    s1.duration_s, s2.duration_s = 2.0, 3.0
    bpy.ops.chimera.layout()
    check(scene_io.shot_range(scene, "sh_010") == (1, 49), "sh_010 range 1..49")
    check(scene_io.shot_range(scene, "sh_020") == (49, 121), "sh_020 packed after sh_010")
    check(scene.timeline_markers["sh_020_in"].camera == bpy.data.objects["CAM_sh_020"], "camera bound to in-marker")
    check("CAM_sh_010" in bpy.data.collections["SHOT_sh_010"].objects, "camera in SHOT_ collection")

    s1.action_prompt = "a man vaults over a low wall"
    s1.render_path = "B"
    s1.extra_json = json.dumps({"hashes": {"motion": "abc"}, "look": {"style_refs": ["x.png"]}})
    d = contract.shot_to_dict(s1, p)
    check(d["hashes"] == {"motion": "abc"}, "unmodelled fields preserved")
    check(d["look"]["style_refs"] == ["x.png"], "nested unmodelled fields preserved")
    check(d["render"]["path"] == "B" and d["render"]["a"]["controls"] == ["pose", "depth"], "render block")
    check(d["motion"]["selected_variant"] is None, "no variant selected -> null")
    tmp = p.shots.add()
    contract.dict_to_shot(d, tmp, p)
    d2 = contract.shot_to_dict(tmp, p)
    check(d2 == d, "contract round trip is lossless")
    p.shots.remove(len(p.shots) - 1)

    # markers authoritative once dragged
    scene.timeline_markers["sh_010_out"].frame = 73
    scene_io.durations_from_markers(scene, p)
    check(abs(s1.duration_s - 3.0) < 1e-6, "duration follows dragged out-marker")
    bpy.ops.chimera.layout()


def test_constraints():
    print("constraints export")
    scene = bpy.context.scene
    p = scene.chimera
    shot = p.shots["sh_010"]
    rig = make_rig(hip=1.9)  # ~2x SOMA
    shot.characters[0].rig_object = rig
    p.active_shot_index = 0
    scene.cursor.location = (5.0, 2.0, 0.0)
    bpy.ops.chimera.add_start()
    start = constraints.helpers("sh_010", "hero", "start")[0]
    start.rotation_euler = (0, 0, math.radians(90))  # facing world -X
    bpy.context.view_layer.update()
    # waypoint 2m ahead of the start along its facing (world -X)
    scene.cursor.location = (3.0, 2.0, 0.0)
    scene.frame_set(1 + 24)
    bpy.ops.chimera.add_waypoint()
    bpy.ops.chimera.add_effector(joint="right_hand")
    bpy.ops.chimera.add_path()
    bpy.context.view_layer.update()
    payload, warnings = constraints.export(scene, p, shot, 0.95)
    c = payload["characters"]["hero"]
    check(abs(c["scale"] - 0.95 / 1.9) < 1e-4, f"scale = soma/rig ({c['scale']:.3f})")
    wp = next(x for x in c["constraints"] if x["type"] == "root2d" and x["mode"] == "sparse")
    check(wp["frames"] == [24], "waypoint frame is shot-local")
    x, z = wp["xz"][0]
    check(abs(x) < 1e-4 and abs(z - 2.0 * 0.5) < 1e-4, f"waypoint 2m ahead -> canonical +Z 1.0 (got {x:.3f},{z:.3f})")
    dense = next(x for x in c["constraints"] if x["type"] == "root2d" and x["mode"] == "dense")
    n = scene_io.frames_for(shot, p.fps)
    check(len(dense["xz"]) == n, f"path sampled per frame ({len(dense['xz'])}/{n})")
    check(abs(dense["xz"][0][1]) < 1e-3 and abs(dense["xz"][-1][1] - 4.0 * 0.5) < 1e-2,
          "path runs along canonical +Z from origin")
    check(all(abs(h) < 1e-3 for h in dense["heading"]), "path heading 0 along facing")
    eff = next(x for x in c["constraints"] if x["type"] == "end_effector")
    check(eff["joint"] == "right_hand" and abs(eff["position"][2] - 1.0) < 1e-4, "effector position")
    check(not warnings, f"no warnings ({warnings})")


def test_hash_and_stale():
    print("hashing & staleness")
    scene = bpy.context.scene
    shot = scene.chimera.shots["sh_010"]
    sync.refresh_hashes(scene)
    h0 = shot.scene_hash
    st = shot.stage("passes")
    st.status, st.input_hash = "done", h0
    shot.stage("generate").status = "done"
    cam = bpy.data.objects["CAM_sh_010"]
    cam.location.x += 1.0
    bpy.context.view_layer.update()
    sync.refresh_hashes(scene)
    check(shot.scene_hash != h0, "camera move changes scene hash")
    check(st.status == "stale" and shot.stages["generate"].status == "stale", "passes+generate marked stale")
    other = scene.chimera.shots["sh_020"].scene_hash
    cam.location.x -= 1.0
    bpy.context.view_layer.update()
    sync.refresh_hashes(scene)
    check(shot.scene_hash == h0, "hash is deterministic")
    check(scene.chimera.shots["sh_020"].scene_hash == other, "other shot unaffected")


def make_motion_blend(path, frames=48, variants=2):
    """Fake retarget output: actions animating object location."""
    names = []
    tmp = bpy.data.objects.new("tmp", None)
    bpy.context.scene.collection.objects.link(tmp)
    for v in range(variants):
        tmp.animation_data_clear()
        for f in range(frames):
            tmp.location = (0.0, 0.05 * f * (v + 1), 0.0)
            tmp.keyframe_insert("location", frame=f)
        act = tmp.animation_data.action
        act.name = f"mo_hero_{v}"
        act.use_fake_user = True
        names.append(act.name)
    tmp.animation_data_clear()
    bpy.data.objects.remove(tmp)
    bpy.data.libraries.write(path, {bpy.data.actions[n] for n in names}, fake_user=True)
    for n in names:
        bpy.data.actions.remove(bpy.data.actions[n])
    return [{"char": "hero", "action": n, "variant": i} for i, n in enumerate(names)]


def test_motion_import_offline():
    print("motion import (no server)")
    scene = bpy.context.scene
    p = scene.chimera
    shot = p.shots["sh_010"]
    path = os.path.join(bpy.path.abspath(p.root), "fake_motion.blend")
    entries = make_motion_blend(path)
    sync.apply_event({"type": "job", "job_id": "j1", "scene": scene.name, "shot": "sh_010", "stage": "retarget",
                      "status": "done", "progress": 1.0, "artifact": {"blend": path, "actions": entries}})
    acts = scene_io.variant_actions("sh_010", "hero")
    check(sorted(acts) == [0, 1], f"variants imported as v0,v1 ({sorted(acts)})")
    rig = shot.characters[0].rig_object
    track = rig.animation_data.nla_tracks.get("SHOT_sh_010")
    check(track is not None and len(track.strips) == 1, "NLA track SHOT_sh_010 with one strip")
    strip = track.strips[0]
    check(strip.action == acts[0] and strip.frame_start == scene_io.shot_range(scene, "sh_010")[0],
          "strip starts at in-marker with v0")
    # second generation appends, never overwrites
    sync.apply_event({"type": "job", "job_id": "j2", "scene": scene.name, "shot": "sh_010", "stage": "retarget",
                      "status": "done", "progress": 1.0, "artifact": {"blend": path, "actions": entries}})
    check(sorted(scene_io.variant_actions("sh_010", "hero")) == [0, 1, 2, 3], "regeneration appends v2,v3")
    bpy.ops.chimera.pick_variant(variant=1)
    check(track.strips[0].action.name == "MO_sh_010_hero_v1" and shot.selected_variant == 1, "pick variant 1")
    # manual edit detection
    act = track.strips[0].action
    fc = next(iter(compat.iter_fcurves(act)))
    fc.keyframe_points[3].co.y += 0.5
    sync.refresh_hashes(scene)
    check(shot.motion_source == "manual", "editing keys flips source to manual")
    # timeline re-layout moves strips with markers
    p.shots["sh_010"].order, p.shots["sh_020"].order = 30, 20
    bpy.ops.chimera.layout()
    check(track.strips[0].frame_start == scene_io.shot_range(scene, "sh_010")[0], "strip follows re-layout")
    p.shots["sh_010"].order, p.shots["sh_020"].order = 10, 20
    bpy.ops.chimera.layout()


def test_e2e(url):
    print(f"end to end against {url}")
    pr = prefs.get()
    pr.server_url = url
    pr.poll_interval = 0.2
    events.WATCHER.base_url = url
    scene = bpy.context.scene
    p = scene.chimera
    p.active_shot_index = 1  # sh_020: fresh
    shot = p.active_shot
    shot.action_prompt = "walks forward"
    shot.motion_variants = 2
    shot.characters[0].rig_object = p.shots["sh_010"].characters[0].rig_object
    scene.cursor.location = (0, 0, 0)
    bpy.ops.chimera.add_start()
    bpy.ops.chimera.add_path()
    bpy.ops.chimera.push()
    events.WATCHER.start()

    def wait(stage, timeout=180):
        t0 = time.time()
        while time.time() - t0 < timeout:
            sync.tick()
            st = shot.stages.get(stage)
            if st and st.status in ("done", "failed", "cancelled") and not events.WATCHER.watching():
                return st.status
            time.sleep(0.2)
        return "timeout"

    check(bpy.ops.chimera.generate_motion() == {"FINISHED"}, "motion job submitted")
    status = wait("motion")
    check(status == "done", f"motion job done ({status}: {shot.stage('motion').message})")
    check(len(scene_io.variant_actions(shot.name, "hero")) == 2, "two variants imported from worker")
    check(scene.chimera.server_status == "ok", "health reported ok")

    check(bpy.ops.chimera.render_passes() == {"FINISHED"}, "passes job submitted")
    status = wait("passes")
    check(status == "done", f"passes done ({status}: {shot.stage('passes').message})")
    edit = bpy.data.scenes.get(scene.name + "_EDIT")
    strips = compat.vse_strips(edit.sequence_editor) if edit else {}
    pb = strips.get(f"PB_{shot.name}") if edit else None
    check(pb is not None and pb.mute and pb.channel == 1, "playblast strip on ch1, muted")
    check(not scene.sequence_editor or not len(compat.vse_strips(scene.sequence_editor)),
          "animation scene has no VSE strips")

    check(bpy.ops.chimera.generate_video() == {"FINISHED"}, "generate job submitted")
    status = wait("generate")
    vid = strips.get(f"VID_{shot.name}")
    check(vid is not None and vid.channel == 2 and vid.frame_start == scene_io.shot_range(scene, shot.name)[0],
          "VID strip on ch2 at in-marker")
    check(0.0 <= shot.pck <= 1.0 and shot.stages["eval"].status == "done", f"PCK recorded ({shot.pck})")
    events.WATCHER.stop()


class FakeLayout:
    """Records nothing; validates every prop/operator a draw() references."""

    def __init__(self, errors):
        self.errors = errors

    def __getattr__(self, name):  # alert, scale_x, enabled, ... and unknown calls
        if name in ("row", "column", "box", "split", "column_flow", "grid_flow"):
            return lambda *a, **k: FakeLayout(self.errors)
        if name in ("label", "separator", "template_list", "menu"):
            return lambda *a, **k: None
        raise AttributeError(name)

    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)

    def _check_prop(self, data, prop):
        if data is None or prop not in data.bl_rna.properties:
            self.errors.append(f"prop {type(data).__name__}.{prop}")

    def prop(self, data, prop, **kw):
        self._check_prop(data, prop)

    def prop_search(self, data, prop, sdata, sprop, **kw):
        self._check_prop(data, prop)
        self._check_prop(sdata, sprop)

    def operator(self, idname, **kw):
        mod, op = idname.split(".")
        try:
            getattr(getattr(bpy.ops, mod), op).get_rna_type()
        except Exception:
            self.errors.append(f"operator {idname}")
        return type("OpProps", (), {})()

    def operator_menu_enum(self, idname, prop, **kw):
        self.operator(idname)


def test_ui_draw():
    print("UI draw (fake layout)")
    from chimera_director import ui
    errors = []
    scene = bpy.context.scene
    p = scene.chimera
    for shot_index in range(len(p.shots)):
        p.active_shot_index = shot_index
        for path in ("A", "B"):
            p.active_shot.render_path = path
            for cls in ui.classes:
                if not issubclass(cls, bpy.types.Panel):
                    continue
                if hasattr(cls, "poll") and not cls.poll(bpy.context):
                    continue
                fake = type("P", (), {"layout": FakeLayout(errors)})()
                if hasattr(cls, "draw_header"):
                    cls.draw_header(fake, bpy.context)
                cls.draw(fake, bpy.context)
    shot = p.shots[0]
    ui.list_shots.CHIMERA_UL_shots.draw_item(None, bpy.context, FakeLayout(errors), p, shot, 0, p, "", 0)
    ui.list_shots.CHIMERA_UL_characters.draw_item(None, bpy.context, FakeLayout(errors), shot,
                                                   shot.characters[0], 0, shot, "", 0)
    p.bonemap_rig = shot.characters[0].rig_object
    bpy.ops.chimera.build_bonemap()
    ui.list_shots.CHIMERA_UL_bonemap.draw_item(None, bpy.context, FakeLayout(errors), p, p.bonemap[0], 0, p, "", 0)
    check(not errors, f"all drawn props/operators exist {errors}")


def main():
    try:
        test_registration()
        test_pure()
        test_shots_and_contract()
        test_constraints()
        test_hash_and_stale()
        test_motion_import_offline()
        test_ui_draw()
        if os.environ.get("CHIMERA_E2E_URL"):
            test_e2e(os.environ["CHIMERA_E2E_URL"])
        addon_utils.disable("chimera_director")
        check(not hasattr(bpy.types.Scene, "chimera"), "clean unregister")
    except Exception:
        traceback.print_exc()
        FAILS.append("exception")
    print(f"\n{'FAILED' if FAILS else 'PASSED'} ({len(FAILS)} failures)")
    sys.stdout.flush()
    os._exit(1 if FAILS else 0)


main()
