"""씬 런타임 — 매니페스트 한 줄을 실행 중인 HoloOcean 월드에 실제로 반영한다.

엔진에 추가한 두 커맨드(patches/sss-scene-runtime.diff)를 호출한다:
    ClearSceneObjects   이전에 스폰한 씬 오브젝트 전부 제거
    SpawnSceneObject    StaticMesh 액터 1개 스폰 + 액터 태그 부여

왜 런타임 스폰이 가능한가:
    RaycastSidescanSonar 는 octree 캐시를 쓰지 않고 매 tick LineTraceSingleByChannel
    로 월드를 직접 훑는다. 따라서 월드 시작 이후 스폰된 액터도 소나에 잡힌다.
    (octree 기반 소나 — ImagingSonar 등 — 는 그렇지 않으니 그대로 가정하면 안 된다.)
    덕분에 씬 조합이 바뀔 때마다 재쿠킹(35~40분)할 필요가 없다.

전제:
    메시 애셋이 월드 패키지에 이미 쿠킹돼 있어야 한다. 카탈로그에 새 모델이
    추가될 때만 재쿠킹이 필요하고, 조합/배치/물성이 바뀔 때는 필요 없다.
    임포트는 patches/import_objects.py 커맨드렛이 담당한다.
"""
import json
import pathlib

from holoocean.command import Command

ROOT = pathlib.Path(__file__).resolve().parents[1]


# 엔진 회전 변환 보정 계수.
#
# 2026-08-04 이전의 엔진은 SpawnSceneObjectCommand 에서 회전을
# `ConvertAngularVector(FVector, ...)` 로 변환했는데, 그 오버로드는 선형 단위 배율
# UEUnitsPerMeter(=100) 을 각도에까지 곱했다(yaw 90 -> -9000 deg = 0 deg, 즉 무회전).
# 그때는 여기서 100 으로 나눠 상쇄했다.
#
# 엔진을 FRotator 오버로드(배율 없이 Roll/Yaw 부호만 반전)로 고쳤으므로 보정은
# 더 필요 없다. 구 패키지로 되돌릴 일이 있으면 100.0 으로 바꾸면 된다.
_ENGINE_ANGLE_SCALE = 1.0

# HoloOcean 0.5.x 의 클라이언트→엔진 명령 버퍼는 한 tick 당 1 MiB이다.
# 대규모 자연물 씬(수천 개)을 한꺼번에 enqueue 하면 JSON 직렬화 결과가 이 한도를
# 넘는다. 오브젝트 수를 줄이는 대신, 충분히 보수적인 개수로 나눠 여러 tick 에 보낸다.
_SPAWN_BATCH_SIZE = 750


class SpawnSceneObjectCommand(Command):
    """엔진의 USpawnSceneObjectCommand 에 대응. 좌표계는 클라이언트 기준(m, deg)."""

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
            # 매립은 메시 높이의 비율. 실제 높이는 엔진만 알기 때문에 비율로 넘긴다.
            + [float(burial_ratio)]
        )
        self.add_string_parameters([str(mesh_path)] + [str(t) for t in (tags or [])])


class ClearSceneObjectsCommand(Command):
    """엔진의 UClearSceneObjectsCommand 에 대응."""

    def __init__(self):
        super().__init__()
        self.set_command_type("ClearSceneObjects")


def load_manifest_line(path, index=0):
    """.jsonl 에서 index 번째 씬을 읽는다."""
    p = pathlib.Path(path)
    if not p.is_absolute() and not p.is_file():
        # cwd 기준으로 없으면 워크스페이스 루트 기준으로 해석한다.
        p = ROOT / p
    with p.open() as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            if i == index:
                return json.loads(line)
    raise IndexError(f"{p} 에 {index}번 씬이 없습니다")


# 평탄 해저용 메시. 엔진 기본 애셋이라 어떤 패키지에도 항상 존재한다(1 m 큐브).
FLAT_SEABED_MESH = "/Engine/BasicShapes/Cube"


def spawn_flat_seabed(env, seabed_top_m, extent_m=(340.0, 260.0), thickness_m=0.5,
                      material="M_Brown_Sand", verbose=True):
    """평탄 해저 슬랩을 런타임에 스폰한다.

    이전에는 build_scene_seabed.py 가 이걸 레벨에 구웠다. 그런데 환경 프로젝트 씬은
    자기 지형을 런타임에 스폰하면서 기존 액터를 제거하지 않아, 해저면이 두 겹이 되어
    (a) 화면에서 지형이 떠 보이고 (b) 그 지형 범위를 벗어난 레이가 구워진 해저에 맞아
    데이터가 오염됐다. 그래서 평탄 해저도 런타임으로 옮겼다 — 지형은 항상 하나만 뜬다.

    재질은 `sssmat:` 태그로 선언한다. 그러면 ComputeDetection 이 태그를 먼저 보고
    즉시 반환하므로, 메시의 머티리얼 슬롯이나 FaceIndex(=CTF_USE_COMPLEX_AS_SIMPLE)에
    의존하지 않는다. 엔진 기본 큐브를 그대로 써도 재질 조회가 정확히 된다.
    """
    sx, sy = float(extent_m[0]), float(extent_m[1])
    # 액터 원점은 큐브 중심이므로, 윗면을 seabed_top_m 에 맞추려면 두께의 절반만큼 내린다.
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
    """매니페스트 한 줄을 월드에 적용한다. 스폰이 반영되도록 몇 tick 돌린다."""
    env._enqueue_command(ClearSceneObjectsCommand())

    # Clear 와 수천 개 Spawn 을 같은 명령 JSON 에 넣지 않는다. 이전 씬을 먼저 지운
    # 뒤 각 묶음을 독립적으로 처리해야 1 MiB command buffer 를 넘지 않는다.
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
                # 매립 비율. 엔진이 회전 적용 후 월드 AABB 의 Z 범위로 환산하므로
                # (2026-08-04 엔진 수정) 클라이언트 환산값(burial_ratio_engine)은
                # 더 쓰지 않는다 — 쓰면 이중 보정이 된다.
                burial_ratio=obj.get("burial_ratio", 0.0),
            )
        )
        spawned += 1

        if spawned % _SPAWN_BATCH_SIZE == 0:
            env.tick()

    # 마지막 부분 묶음을 처리하고, 콜리전/렌더 상태가 첫 ping 전에 안정되도록 기존과
    # 동일하게 여분 tick 을 둔다. 정확히 배수이면 마지막 queue 는 비어 있으므로 첫
    # tick 은 단순 안정화 tick 이 된다.
    for _ in range(5):
        env.tick()

    if verbose:
        print(f"[scene] {scene['scene_id']}: 오브젝트 {spawned}개 스폰 "
              f"({_SPAWN_BATCH_SIZE}개씩 분할, 지형 {scene['terrain']['id']})")
    return spawned


def describe(scene):
    """dry-run 용 — 엔진 없이 무엇이 스폰될지 사람이 읽게 출력."""
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
