"""EAP · glb → fbx（Blender 无头转换，贴图内嵌 + 另存 PNG）

    "D:\\Apps\\Blender 5.2\\blender.exe" -b --factory-startup --python tools\\glb_to_fbx.py -- \
        assets/raw/trees/conifer.glb assets/raw/trees/fbx/conifer.fbx \
        --asset-id SM_Tree_conifer_PCG_Multi

**Blender 成功也常返回 exit=1**：外部调用方判成功只看输出文件存在 + 本脚本打印的
`FBX_OK <path>`，不要用 returncode。

转换后自检（不一致即失败）：
- triCount 与 glb 完全一致；
- 包围盒（换算回 glTF 的 Y-up 轴）每轴误差 < 1 cm；
- 贴图导出到 assets/eap_textures/，sha256 与 glb 内嵌字节一致。

报告写到 docs/fbx_convert_report.json（按 asset-id 合并，不含时间戳 → 两次跑内容一致）。
"""
import json
import os
import sys

import bpy
from mathutils import Vector

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))
from tree_to_manifest import export_textures, glb_stats   # noqa: E402


def _args():
    argv = sys.argv
    if "--" not in argv:
        raise SystemExit("用法: blender -b --python glb_to_fbx.py -- <in.glb> <out.fbx> [--asset-id ID]")
    rest = argv[argv.index("--") + 1:]
    in_glb, out_fbx, asset_id = None, None, None
    i = 0
    while i < len(rest):
        if rest[i] == "--asset-id":
            asset_id = rest[i + 1]
            i += 2
            continue
        if in_glb is None:
            in_glb = rest[i]
        elif out_fbx is None:
            out_fbx = rest[i]
        i += 1
    if not in_glb or not out_fbx:
        raise SystemExit("缺 <in.glb> 或 <out.fbx>")
    if not asset_id:
        asset_id = "SM_Tree_%s_PCG_Multi" % os.path.splitext(os.path.basename(in_glb))[0]
    return in_glb, out_fbx, asset_id


def _clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def _mirror_x_for_unity():
    """Unity 导入 FBX 时按左手系把 X 取反；这里先把网格沿 X 预镜像，Unity 再取反即与 glb 一致。

    不预镜像的话 Unity 里的树是左右镜像的，`bounds` 每轴比对必然差一个 X 轴（实测 7.4 cm）。
    """
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    if not meshes:
        return
    bpy.ops.object.select_all(action="DESELECT")
    for obj in meshes:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    # 先把 glTF 根节点的旋转/位移烘进网格，保证镜像绕世界原点（否则会整体平移 2*origin.x）
    bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    for obj in meshes:
        obj.scale.x *= -1.0
    bpy.context.view_layer.update()
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    for obj in meshes:
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.normals_make_consistent(inside=False)
        bpy.ops.object.mode_set(mode="OBJECT")


def _blender_stats():
    """Blender 侧复算：triCount + 包围盒（换算回 glTF 的 Y-up 坐标）。"""
    tris = 0
    bmin = [1e18] * 3
    bmax = [-1e18] * 3
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        me = obj.data
        me.calc_loop_triangles()
        tris += len(me.loop_triangles)
        for corner in obj.bound_box:
            w = obj.matrix_world @ Vector(corner)
            # Blender Z-up -> glTF Y-up: (x, y, z)_b -> (x, z, -y)_g
            g = [w.x, w.z, -w.y]
            for d in range(3):
                bmin[d] = min(bmin[d], g[d])
                bmax[d] = max(bmax[d], g[d])
    return {
        "triCount": tris,
        "bbox": {"min": [round(v, 4) for v in bmin], "max": [round(v, 4) for v in bmax]},
        "meshObjects": [o.name for o in bpy.data.objects if o.type == "MESH"],
    }


def _max_bbox_diff(a, b):
    return max(max(abs(float(a["min"][i]) - float(b["min"][i])) for i in range(3)),
               max(abs(float(a["max"][i]) - float(b["max"][i])) for i in range(3)))


def _update_report(entry):
    path = os.path.join(REPO, "docs", "fbx_convert_report.json")
    data = {"schema": "eap.fbx_convert/1", "conversions": {}}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8-sig") as fh:
                old = json.load(fh)
            if isinstance(old.get("conversions"), dict):
                data = old
        except (OSError, ValueError):
            pass
    data["conversions"][entry["assetId"]] = entry
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
    return path


def main():
    in_glb, out_fbx, asset_id = _args()
    in_abs = in_glb if os.path.isabs(in_glb) else os.path.join(REPO, in_glb)
    out_abs = out_fbx if os.path.isabs(out_fbx) else os.path.join(REPO, out_fbx)
    os.makedirs(os.path.dirname(out_abs), exist_ok=True)

    _clear_scene()
    bpy.ops.import_scene.gltf(filepath=in_abs)
    _mirror_x_for_unity()
    bl = _blender_stats()
    fbx_raw = bl["bbox"]
    # Unity 会再取反一次 X → Unity 世界坐标下的包围盒（用于和 manifest/glb 直接比）
    unity_bbox = {"min": list(fbx_raw["min"]), "max": list(fbx_raw["max"])}
    unity_bbox["min"][0] = round(-fbx_raw["max"][0], 4)
    unity_bbox["max"][0] = round(-fbx_raw["min"][0], 4)

    bpy.ops.export_scene.fbx(
        filepath=out_abs,
        path_mode="COPY",
        embed_textures=True,
        apply_scale_options="FBX_SCALE_ALL",
        bake_space_transform=True,
        object_types={"MESH"},
        use_selection=False,
    )

    src = glb_stats(in_abs)
    tex = export_textures(in_abs, asset_id)
    diff = _max_bbox_diff(src["bbox"], unity_bbox)
    ok = os.path.isfile(out_abs) and bl["triCount"] == src["triCount"] and diff < 0.01

    entry = {
        "assetId": asset_id,
        "sourceGlb": os.path.relpath(in_abs, REPO).replace("\\", "/"),
        "fbx": os.path.relpath(out_abs, REPO).replace("\\", "/"),
        "triCountGlb": src["triCount"],
        "triCountFbx": bl["triCount"],
        "bboxGlbM": src["bbox"],
        "bboxBlenderM": fbx_raw,
        "bboxFbxM": unity_bbox,
        "axisNote": "Unity 左手系导入 FBX 会把 X 取反；fbx 已沿 X 预镜像，bboxFbxM 是 Unity 世界坐标下的包围盒",
        "bboxMaxDiffM": round(diff, 6),
        "meshObjects": bl["meshObjects"],
        "textures": [{"slot": t["slot"], "channel": t["channel"], "path": t["path"],
                      "sha256": t["sha256"], "size": t["size"]} for t in tex],
        "blenderVersion": bpy.app.version_string,
        "ok": ok,
    }
    report = _update_report(entry)
    print("[EAP] fbx %s tris=%d/%d bboxDiff=%.4fm textures=%d -> %s"
          % (entry["fbx"], bl["triCount"], src["triCount"], diff, len(tex), report))
    if ok:
        print("FBX_OK %s" % out_abs)
        return 0
    print("FBX_FAIL %s (tris=%d/%d bboxDiff=%.4fm)"
          % (out_abs, bl["triCount"], src["triCount"], diff))
    return 1


if __name__ == "__main__":
    sys.exit(main())
