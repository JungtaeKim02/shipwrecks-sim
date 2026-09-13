"""UE5.3 커맨드렛 — 오브젝트 카탈로그를 StaticMesh 애셋으로 일괄 임포트한다.

컨테이너 안에서 실행:
  UnrealEditor-Cmd /drone/src/engine/Holodeck.uproject -run=pythonscript \
      -script=/drone/src/engine/import_objects.py -unattended -nopause -nosplash \
      -RenderOffScreen -stdout

입력 : /host_ws/data/objects/catalog.json  (scripts/object_catalog.py 산출)
출력 : /Game/SssObjects/<category>/<name>  StaticMesh 애셋

★ 이 스크립트의 존재 이유 = 소나가 요구하는 두 가지를 애셋에 강제로 걸어주는 것:

  1) collision_trace_flag = CTF_USE_COMPLEX_AS_SIMPLE
     소나는 bTraceComplex=false 로 트레이스한다. 이 설정이 없으면 레이가 단순
     콜리전(박스)에 맞아 (a) 실루엣이 박스로 뭉개지고 (b) FaceIndex 가 무효라
     재질 조회가 실패해 기본 임피던스 1e8 -> 전 물체가 새하얗게 나온다.
     에러가 안 나고 조용히 틀린 이미지가 나오는 유형이라 특히 위험하다.

  2) 삼각형 수 상한
     소나 레이 트레이싱 비용은 삼각형 수에 직접 비례한다. 과밀 메시는 취득을
     느리게만 만들고 소나 해상도(수 cm)보다 세밀한 부분은 어차피 보이지 않는다.

과거 교훈(실측):
  * STL 직접 임포트는 커맨드렛에서 크래시(Assertion IsValid). 호스트에서 trimesh 로
    OBJ 변환 후 임포트하는 경로가 안정적이다. FBX 는 네이티브라 이 문제가 없다.
  * spawn_actor_from_object() 는 세그폴트. 액터 스폰은 런타임 커맨드가 담당한다.
  * Interchange 가 destination_name 을 무시하는 경우가 있어 임포트 후 실제 애셋명을
    확인한다.
"""
import json
import os
import unreal

HOST_WS = "/host_ws"
CATALOG = os.path.join(HOST_WS, "data/objects/catalog.json")
DEST_ROOT = "/Game/SssObjects"

MAX_TRIANGLES = 150000          # 이보다 많으면 경고 (감폭 권장)
NATIVE_EXTS = (".fbx", ".obj")  # UE 가 직접 읽는 포맷


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

    # FbxImportUI 는 FBX 전용이다. OBJ 는 Interchange 경로로 들어가므로 옵션을 주면
    # 임포트가 실패한다 (build_scene.py 가 옵션 없이 OBJ 를 임포트해 성공한 전례).
    if source_path.lower().endswith(".fbx"):
        options = unreal.FbxImportUI()
        options.import_mesh = True
        options.import_textures = False
        options.import_materials = False    # 재질은 sssmat: 태그로 선언하므로 불필요
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
    """소나가 실제 삼각형을 맞추도록 강제. 이 함수가 이 스크립트의 핵심."""
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

    # encoding 을 명시하지 않으면 UE 내장 파이썬의 로케일(ascii 일 수 있음)로 읽어
    # 한글이 포함된 카탈로그에서 UnicodeDecodeError 로 죽는다. 실제로 겪은 함정.
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
        source = os.path.realpath(source)          # 심볼릭 링크 해제
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

        # 매니페스트의 ue_mesh 와 실제 경로가 다르면 런타임 스폰이 실패한다.
        if imported.split(".")[0] != entry["ue_mesh"]:
            log("  [주의] 애셋 경로 불일치: 기대 {} / 실제 {}".format(
                entry["ue_mesh"], imported.split(".")[0]))

        # 실제 크기를 남긴다. FBX 는 호스트(trimesh)에서 못 읽어 배치 스케일을 미리
        # 알 수 없으므로, 여기서 찍힌 값을 보고 scene_config 의 scale_range 를 정한다.
        try:
            b = mesh.get_bounds()
            ext = b.box_extent
            # 소수 2자리로 찍으면 5 mm 미만 자산이 전부 0.00 이 되어, 뒤이어
            # measure_assets.py 의 퇴화 자산 판정이 0 나눗셈으로 걸린다. 실제로
            # cm 단위로 저작된 자산 37종이 이 형식 때문에 크기를 잃었다(2026-08-09).
            log("     bbox(m) = {:.4f} x {:.4f} x {:.4f}   tris={}".format(
                ext.x * 2 / 100.0, ext.y * 2 / 100.0, ext.z * 2 / 100.0, tris))
        except Exception as e:
            log("     bbox 조회 실패: {}".format(e))

        log("OK   {} -> {}{}".format(cid, imported, warn))
        ok += 1

    log("=" * 60)
    log("완료: 성공 {} / 건너뜀 {} / 실패 {}".format(ok, skipped, failed))
    log("DONE")


# 커맨드렛에서 예외가 나면 UE 는 "error code: -1" 만 남기고 원인을 숨긴다.
# 로그 파일에 트레이스백을 직접 남겨야 진단이 가능하다.
try:
    main()
except Exception:
    import traceback
    log("EXCEPTION:\n" + traceback.format_exc())
    raise
