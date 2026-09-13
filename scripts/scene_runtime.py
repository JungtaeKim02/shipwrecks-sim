import json
import pathlib

from holoocean.command import Command

ROOT = pathlib.Path(__file__).resolve().parents[1]











_ENGINE_ANGLE_SCALE = 1.0




_SPAWN_BATCH_SIZE = 750


class SpawnSceneObjectCommand(Command):

    def __init__(self, mesh_path, location, rotation, scale, tags=None, burial_ratio=0.0):
        super().__init__()
        self.set_command_type("SpawnSceneObject")
        sx = sy = sz = float(scale) if not isinstance(scale, (list, tuple)) else None
        if sx is None:
            sx, sy, sz = [float(v) for v in scale]
        rot = [float(v) / _ENGINE_ANGLE_SCALE for v in rotation]
        self.add_number_parameters(
            [float(v) for v in location]
            + rot
            + [sx, sy, sz]

            + [float(burial_ratio)]
        )
        self.add_string_parameters([str(mesh_path)] + [str(t) for t in (tags or [])])


class ClearSceneObjectsCommand(Command):

    def __init__(self):
        super().__init__()
        self.set_command_type("ClearSceneObjects")


def load_manifest_line(path, index=0):
    p = pathlib.Path(path)
    if not p.is_absolute() and not p.is_file():

        p = ROOT / p
    with p.open() as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            if i == index:
                return json.loads(line)
    raise IndexError(f"{p} 에 {index}번 씬이 없습니다")



FLAT_SEABED_MESH = "/Engine/BasicShapes/Cube"


def spawn_flat_seabed(env, seabed_top_m, extent_m=(340.0, 260.0), thickness_m=0.5,
                      material="M_Brown_Sand", verbose=True):
    sx, sy = float(extent_m[0]), float(extent_m[1])

    center_z = float(seabed_top_m) - thickness_m / 2.0
    env._enqueue_command(
        SpawnSceneObjectCommand(
            mesh_path=FLAT_SEABED_MESH,
            location=[0.0, 0.0, center_z],
            rotation=[0.0, 0.0, 0.0],
            scale=[sx, sy, thickness_m],
            tags=[f"sssmat:{material}"],
            burial_ratio=0.0,
        )
    )
    for _ in range(5):
        env.tick()
    if verbose:
        print(f"[scene] 평탄 해저 스폰: {sx:.0f}x{sy:.0f}x{thickness_m} m, "
              f"윗면 z={seabed_top_m:.2f} m, 재질 {material}")


def apply_scene(env, scene, verbose=True):
    env._enqueue_command(ClearSceneObjectsCommand())



    env.tick()

    spawned = 0
    for obj in scene.get("objects", []):
        env._enqueue_command(
            SpawnSceneObjectCommand(
                mesh_path=obj["ue_mesh"],
                location=obj["position_m"],
                rotation=obj["rotation_deg"],
                scale=obj["scale"],
                tags=obj["tags"],



                burial_ratio=obj.get("burial_ratio", 0.0),
            )
        )
        spawned += 1

        if spawned % _SPAWN_BATCH_SIZE == 0:
            env.tick()




    for _ in range(5):
        env.tick()

    if verbose:
        print(f"[scene] {scene['scene_id']}: 오브젝트 {spawned}개 스폰 "
              f"({_SPAWN_BATCH_SIZE}개씩 분할, 지형 {scene['terrain']['id']})")
    return spawned


def describe(scene):
    lines = [
        f"scene_id : {scene['scene_id']}  (seed {scene['seed']})",
        f"terrain  : {scene['terrain']['id']}  seabed_top={scene['terrain']['seabed_top_m']}m",
    ]
    facies = scene["terrain"].get("facies_config")
    lines.append(f"facies   : {facies or '(없음 — 균질 재질)'}")

    s = scene["survey"]
    lines.append(
        f"survey   : range_max={s['range_max_m']}m  altitude={s['altitude_m']}m "
        f"({s['altitude_ratio']:.0%} of range)  dep={s['depression_deg']}deg  "
        f"legs={s['n_legs']}x{s['leg_spacing_m']}m  hd={s['survey_heading_deg']}deg"
    )
    lines.append(f"objects  : {len(scene['objects'])}개")
    for o in scene["objects"]:
        gt = "GT" if "wreck" in o["tags"] else "  "
        mat = next((t.split(":", 1)[1] for t in o["tags"] if t.startswith("sssmat:")), "-")
        x, y, _z = o["position_m"]
        lines.append(
            f"   [{gt}] {o['catalog_id']:<45} ({x:7.1f},{y:7.1f}) "
            f"yaw={o['rotation_deg'][2]:6.1f} s={o['scale']:.2f} mat={mat}"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="매니페스트 dry-run (엔진 없이 확인)")
    ap.add_argument("manifest", help=".jsonl 경로")
    ap.add_argument("--index", type=int, default=0)
    args = ap.parse_args()
    print(describe(load_manifest_line(args.manifest, args.index)))
