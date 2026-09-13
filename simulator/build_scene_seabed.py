"""UE5.3 커맨드렛: 레벨을 비워 "지형은 전부 런타임" 상태로 만든다.

컨테이너 안에서 실행:
  UnrealEditor-Cmd /drone/src/engine/Holodeck.uproject -run=pythonscript \
      -script=/drone/src/engine/build_scene_seabed.py -unattended -nopause -nosplash \
      -RenderOffScreen -stdout
  ※ -nullrhi 금지. Landscape 를 가진 레벨 로드 시 크래시한다. --gpus all 필요.

★ 아무것도 굽지 않는 이유 (프로토타입 아키텍처)

  RaycastSidescanSonar 는 octree 캐시가 아니라 매 tick 라인트레이스로 월드를 훑으므로
  런타임 스폰 액터도 소나에 잡힌다. 따라서 지형·오브젝트를 레벨에 구울 필요가 없고,
  굽지 않으면 조합을 바꿀 때 재쿠킹(수십 분)이 사라진다.

    난파선·클러터 -> 매니페스트 기반 런타임 스폰 (scene_runtime.apply_scene)
    평탄 해저     -> 런타임 스폰 (scene_runtime.spawn_flat_seabed)
    Mado 지형     -> 환경 프로젝트 GameMode 가 런타임 스폰

  메시는 패키지에 **애셋으로는** 들어가야 한다(난파선은 import_objects.py, 평탄 해저는
  /Engine/BasicShapes/Cube 로 엔진 기본 애셋이라 항상 존재). 즉 카탈로그에 새 모델이
  추가될 때만 재쿠킹이 필요하고, 배치가 바뀔 때는 필요 없다.

★ 해저면도 굽지 않는다(2026-07-30 변경):
    평탄 해저   -> scene_runtime.spawn_flat_seabed()  (우리 flat_v1)
    Mado 지형   -> 환경 프로젝트 GameMode 가 스폰
  구워두면 환경 프로젝트 지형과 두 겹이 되어 (a) 화면에서 지형이 떠 보이고
  (b) 그 지형 범위를 벗어난 레이가 구워진 해저에 맞아 데이터가 오염된다.
  이 레벨에는 조명/매니저/HolodeckWorldSettings 만 남긴다.
"""
import unreal

LOG = "/drone/src/engine/build_scene_seabed.log"


def log(m):
    unreal.log_warning("[SEABED] " + str(m))
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(str(m) + "\n")
        f.flush()


open(LOG, "w").close()
log("START build_scene_seabed")

eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)

SEABED_TOP_CM = -2000.0            # -20 m. scene_config.json 의 seabed_top_m 과 일치
SEABED_X, SEABED_Y = 340, 260      # m
MAT = "/Game/StarterContent/Materials/"
SEABED_MAT = MAT + "M_Brown_Sand"  # materials.csv 에 등록돼 있어야 함


def A(path):
    a = unreal.EditorAssetLibrary.load_asset(path)
    if a is None:
        log("WARN asset not found: " + path)
    return a


def set_complex_as_simple(mesh):
    """bTraceComplex=false 인 소나 레이가 유효 FaceIndex + 재질을 얻게 한다."""
    try:
        bs = mesh.get_editor_property("body_setup")
        bs.set_editor_property(
            "collision_trace_flag",
            unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE)
        unreal.EditorAssetLibrary.save_loaded_asset(mesh)
        return True
    except Exception as e:
        log("WARN complex-as-simple 실패 %s: %s" % (mesh.get_name(), e))
        return False


# 1) ExampleLevel 제자리 로드 (HolodeckWorldSettings/조명/매니저를 이미 갖고 있음)
unreal.EditorLoadingAndSavingUtils.load_map("/Game/ExampleLevel")
log("loaded /Game/ExampleLevel")

# 2) 기존 StaticMeshActor 전부 제거 — 이전 bake 잔재 포함
removed = 0
for a in eas.get_all_level_actors():
    if isinstance(a, unreal.StaticMeshActor):
        eas.destroy_actor(a)
        removed += 1
log("removed static mesh actors: %d" % removed)

# 3) 해저는 굽지 않는다.
#
# 이전에는 여기서 340x260 m 평판을 z=-20 m 에 구워 넣었다. 그런데 환경 프로젝트 씬
# (Mado)은 자기 지형을 z=-8.45~-9.42 m 에 **런타임 스폰**하고 기존 액터를 제거하지
# 않는다. 결과적으로 해저면이 두 겹으로 존재해서
#   (a) 화면에서 지형이 얇은 판처럼 떠 보이고 그 아래로 또 바닥이 보임 (실제 확인됨)
#   (b) Mado 지형 범위(x=[-143,107], y=[-113,108]) 밖으로 나간 레이가 구워진 해저에
#       맞아 데이터가 오염됨
# 이 되었다.
#
# 그래서 평탄 해저도 런타임 스폰으로 옮겼다(scene_runtime.spawn_flat_seabed).
# 지형 = 항상 런타임 = 매니페스트/환경변수로 하나만 고르는 구조가 되어 겹칠 수 없다.
# 레벨에는 조명/매니저/HolodeckWorldSettings 만 남는다.
log("해저 슬랩은 굽지 않음 — 런타임 스폰으로 이관 (scene_runtime.spawn_flat_seabed)")

# 4) 저장
saved = les.save_current_level()
log("save_current_level = %s" % saved)
log("DONE")
