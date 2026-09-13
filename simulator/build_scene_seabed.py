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

SEABED_TOP_CM = -2000.0
SEABED_X, SEABED_Y = 340, 260
MAT = "/Game/StarterContent/Materials/"
SEABED_MAT = MAT + "M_Brown_Sand"


def A(path):
    a = unreal.EditorAssetLibrary.load_asset(path)
    if a is None:
        log("WARN asset not found: " + path)
    return a


def set_complex_as_simple(mesh):
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



unreal.EditorLoadingAndSavingUtils.load_map("/Game/ExampleLevel")
log("loaded /Game/ExampleLevel")


removed = 0
for a in eas.get_all_level_actors():
    if isinstance(a, unreal.StaticMeshActor):
        eas.destroy_actor(a)
        removed += 1
log("removed static mesh actors: %d" % removed)














log("해저 슬랩은 굽지 않음 — 런타임 스폰으로 이관 (scene_runtime.spawn_flat_seabed)")


saved = les.save_current_level()
log("save_current_level = %s" % saved)
log("DONE")
