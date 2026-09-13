import json
import os
import unreal

HOST_WS = "/host_ws"
CATALOG = os.path.join(HOST_WS, "data/objects/catalog.json")
DEST_ROOT = "/Game/SssObjects"

MAX_TRIANGLES = 150000
NATIVE_EXTS = (".fbx", ".obj")


LOGFILE = "/drone/src/engine/import_objects.log"
open(LOGFILE, "w").close()


def log(msg):
    line = "[import_objects] {}".format(msg)
    unreal.log_warning(line)
    with open(LOGFILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()


def import_one(source_path, dest_dir, asset_name):
    task = unreal.AssetImportTask()
    task.filename = source_path
    task.destination_path = dest_dir
    task.destination_name = asset_name
    task.automated = True
    task.replace_existing = True
    task.save = True



    if source_path.lower().endswith(".fbx"):
        options = unreal.FbxImportUI()
        options.import_mesh = True
        options.import_textures = False
        options.import_materials = False
        options.import_as_skeletal = False
        options.static_mesh_import_data.combine_meshes = True
        options.static_mesh_import_data.generate_lightmap_u_vs = False
        options.static_mesh_import_data.auto_generate_collision = False
        task.options = options

    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    if not task.imported_object_paths:
        return None
    return task.imported_object_paths[0]


def enforce_sonar_collision(mesh):
    body_setup = mesh.get_editor_property("body_setup")
    if body_setup is None:
        log("  !! body_setup 없음 — 콜리전 설정 불가")
        return False
    body_setup.set_editor_property(
        "collision_trace_flag",
        unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE,
    )
    unreal.EditorAssetLibrary.save_loaded_asset(mesh)
    return True


def main():
    if not os.path.isfile(CATALOG):
        log("카탈로그 없음: {} — scripts/object_catalog.py 를 먼저 실행하세요".format(CATALOG))
        return



    with open(CATALOG, encoding="utf-8") as f:
        catalog = json.load(f)

    ok, skipped, failed = 0, 0, 0
    for entry in catalog["objects"]:
        cid = entry["id"]

        if not entry.get("available"):
            log("SKIP {} — 파일 미배치".format(cid))
            skipped += 1
            continue

        source = os.path.join(HOST_WS, entry["source"])
        source = os.path.realpath(source)
        if not os.path.isfile(source):
            log("FAIL {} — 소스 없음: {}".format(cid, source))
            failed += 1
            continue

        ext = os.path.splitext(source)[1].lower()
        if not entry.get("importable", True) or ext not in NATIVE_EXTS:
            log("SKIP {} — {} 는 UE 직접 임포트 비권장. 호스트에서 "
                "scripts/convert_wrecks.py 로 OBJ 변환 후 재시도".format(cid, ext))
            skipped += 1
            continue

        dest_dir = "{}/{}".format(DEST_ROOT, entry["category"])
        imported = import_one(source, dest_dir, entry["name"])
        if not imported:
            log("FAIL {} — 임포트 실패".format(cid))
            failed += 1
            continue

        mesh = unreal.EditorAssetLibrary.load_asset(imported)
        if not isinstance(mesh, unreal.StaticMesh):
            log("FAIL {} — StaticMesh 가 아님: {}".format(cid, imported))
            failed += 1
            continue

        if not enforce_sonar_collision(mesh):
            failed += 1
            continue

        tris = mesh.get_num_triangles(0) if hasattr(mesh, "get_num_triangles") else -1
        warn = ""
        if tris > MAX_TRIANGLES:
            warn = "  [경고] 삼각형 {} > {} — 취득이 느려집니다. 감폭 권장".format(
                tris, MAX_TRIANGLES)


        if imported.split(".")[0] != entry["ue_mesh"]:
            log("  [주의] 애셋 경로 불일치: 기대 {} / 실제 {}".format(
                entry["ue_mesh"], imported.split(".")[0]))



        try:
            b = mesh.get_bounds()
            ext = b.box_extent



            log("     bbox(m) = {:.4f} x {:.4f} x {:.4f}   tris={}".format(
                ext.x * 2 / 100.0, ext.y * 2 / 100.0, ext.z * 2 / 100.0, tris))
        except Exception as e:
            log("     bbox 조회 실패: {}".format(e))

        log("OK   {} -> {}{}".format(cid, imported, warn))
        ok += 1

    log("=" * 60)
    log("완료: 성공 {} / 건너뜀 {} / 실패 {}".format(ok, skipped, failed))
    log("DONE")




try:
    main()
except Exception:
    import traceback
    log("EXCEPTION:\n" + traceback.format_exc())
    raise
