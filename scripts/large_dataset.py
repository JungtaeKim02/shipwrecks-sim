"""재현 가능한 SSS 데이터셋 취득.

한 번의 실행에서 생기는 매니페스트, 고정 센서 스냅샷, 로그, PNG/GT, raw/processed
배열과 결과 인덱스를 모두 ``data/datasets/<dataset_id>/`` 아래에 보관한다.

다양성 계약
------------
* available 지형을 섞은 뒤 순환하므로 충분한 장 수에서는 모든 맵을 사용한다.
* track 길이 1/4, 1/2, 전체를 섞은 뒤 순환한다.
* 씬 seed마다 방위, leg 수/간격, 고도, 오브젝트 종류/위치/자세/매몰을 다시 뽑는다.
* 음향/수신/후처리 설정은 시작 시 sss_config.json 스냅샷으로 얼린다.
* speckle/noise seed만 씬마다 결정적으로 바꿔 재현 가능한 서로 다른 realization을 만든다.

사용:
  python3 scripts/large_dataset.py --spec spec.json --dry-run
  python3 scripts/large_dataset.py --spec spec.json
  python3 scripts/large_dataset.py --dataset-root data/datasets/<id> --resume
"""
from __future__ import annotations

import argparse
import copy
import csv
import datetime as dt
import json
import math
import pathlib
import random
import re
import shutil
import signal
import subprocess
import sys
import time
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
DATASETS = ROOT / "data" / "datasets"
ACQUIRE = SCRIPTS / "acquire_sss.py"
PREVIEW = SCRIPTS / "preview_scene.py"
SENSOR_CFG = SCRIPTS / "sss_config.json"
SCENE_CFG = SCRIPTS / "scene_config.json"
sys.path.insert(0, str(SCRIPTS))

import object_catalog as oc  # noqa: E402
import scene_manifest as sm  # noqa: E402

STOP_REQUESTED = False
ACTIVE_PROCESS = None

# 2026-08-09 이 머신의 coherent-v3 실제 smoke(12,494 elevation rays)에서 ping당
# 약 0.415 s. 2026-08-10 dense-object/35-degree smoke measured roughly 10 s total
# preview+acquisition startup and 6 s for 400 short-range pings.  These are only
# plan estimates; the first completed scene still recalibrates all remaining work.
REFERENCE_ELEV_RAYS = 12494.0
REFERENCE_SECONDS_PER_PING = 0.415
PREVIEW_BOOT_SECONDS = 6.0
ACQUIRE_BOOT_SECONDS = 4.0

DEFAULT_OUTPUTS = {
    "waterfall_png": True,
    "true_aspect_png": False,
    "raw_numpy": False,
    "processed_numpy": True,
    "mask": True,
    "bbox": True,
    "bbox_overlay": False,
    "scene_preview": True,
    "export_zip": False,
}


# 이 값들은 매니페스트(지형/조사 계획)가 정한다. 나머지 public sss_config 키는
# 시작 시점의 값으로 모든 취득에 명시 전달해, 실행 도중 설정 파일/UI가 바뀌어도
# 데이터셋 내부에서 센서 모델이 달라지지 않게 한다.
SCENE_CONTROLLED_KEYS = {
    "platform", "seabed_top_m", "flat_seabed", "altitude_m", "sensor_z_m",
    "track_x0_m", "track_x1_m", "n_legs", "leg_spacing_m", "track_dx_m",
    "survey_heading_deg",
    # Production geometry is resolved per scene.  In shallow West-Sea tiles a
    # single fixed range either points beyond the physical beam footprint or
    # throws away most of the usable swath.  Acoustic/signal settings remain
    # frozen; only this acquisition geometry follows the sampled altitude.
    "range_min_m", "range_max_m", "range_res_m", "depression_deg",
    "range_reference_altitude_m", "noise_level", "speckle_strength",
}


def _json(path):
    return json.loads(pathlib.Path(path).read_text())


def _safe_name(value):
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "sss_dataset")).strip("_-")
    return (name or "sss_dataset")[:48]


def _atomic_json(path, obj):
    path = pathlib.Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str))
    tmp.replace(path)


def _to_args(values):
    out = []
    for key, value in values.items():
        if isinstance(value, bool):
            value = "true" if value else "false"
        elif isinstance(value, (list, tuple)):
            value = json.dumps(list(value))
        out += [f"--{key}", str(value)]
    return out


def _balanced(values, n, seed):
    """각 cycle마다 한 번씩 쓰되 cycle 순서는 시드로 섞는다."""
    rng = random.Random(seed)
    out = []
    while len(out) < n:
        cycle = list(values)
        rng.shuffle(cycle)
        out.extend(cycle)
    return out[:n]


def _dataset_root(spec, explicit=None):
    if explicit:
        p = pathlib.Path(explicit).expanduser()
        return (p if p.is_absolute() else ROOT / p).resolve()
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    base = DATASETS / f"{_safe_name(spec.get('name'))}_{stamp}"
    p = base
    serial = 2
    while p.exists():
        p = pathlib.Path(str(base) + f"_{serial}")
        serial += 1
    return p.resolve()


def _fixed_sensor(snapshot):
    return {
        k: v for k, v in snapshot.items()
        if not str(k).startswith("_") and k not in SCENE_CONTROLLED_KEYS
    }


def _output_selection(spec):
    """Validate the public output contract and fill stable defaults."""
    raw = spec.get("outputs") or {}
    out = {key: bool(raw.get(key, default)) for key, default in DEFAULT_OUTPUTS.items()}
    if out["bbox_overlay"] and not out["waterfall_png"]:
        raise SystemExit("bbox 검수 오버레이를 저장하려면 워터폴 PNG를 선택해야 합니다.")
    if not any(value for key, value in out.items() if key != "export_zip"):
        raise SystemExit("저장할 산출물을 하나 이상 선택해야 합니다.")
    return out


def _deep_update(dst, src):
    """Recursively apply a JSON profile without sharing mutable sub-dicts."""
    for key, value in (src or {}).items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            _deep_update(dst[key], value)
        else:
            dst[key] = copy.deepcopy(value)
    return dst


def _large_profile(scene_cfg):
    """Return the opt-in production profile stored beside the scene config."""
    profile = copy.deepcopy(scene_cfg.get("large_dataset_profile") or {})
    profile.pop("_note", None)
    return profile


def _number_control(spec, defaults, key, lo, hi):
    """UI/CLI 공용 대규모 취득 제어값을 검증해 float로 돌려준다."""
    raw = spec.get(key, defaults.get(key))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise SystemExit(f"{key}는 숫자여야 합니다.")
    if not lo <= value <= hi:
        raise SystemExit(f"{key}는 {lo:g}~{hi:g} 범위여야 합니다 (받은 값 {value:g}).")
    return value


def _scale_int_range(pair, scale):
    """논리 배치 수/패치 수의 정수 범위를 밀도 배율로 확대한다."""
    lo, hi = [max(0, int(v)) for v in pair]
    if scale == 0.0:
        return [0, 0]
    return [max(1, int(math.ceil(lo * scale))),
            max(1, int(math.ceil(hi * scale)))]


def _apply_floor_clutter_density(profile, floor_clutter_pct):
    """암석·자갈/퇴적물의 **스와스 내 배치량**을 UI 비율로 조절한다.

    logical object 수와 Gaussian 바닥 patch 수를 같은 배율로 키워, 단순히 한 군집만
    과밀하게 만드는 대신 조사 경로 전체에서 덮는 면적도 함께 넓힌다. 작은 자산의
    physical member 수는 별도로 제곱 확대하지 않는다.
    """
    scale = float(floor_clutter_pct) / 100.0
    cats = profile.setdefault("object_categories", {})
    patches = profile.setdefault("category_distribution_clusters", {})
    for category in ("geological_clutter", "sediment_feature"):
        if category in cats and "count_range" in cats[category]:
            cats[category]["count_range"] = _scale_int_range(
                cats[category]["count_range"], scale)
        if category in patches and "patch_count_range" in patches[category]:
            patches[category]["patch_count_range"] = _scale_int_range(
                patches[category]["patch_count_range"], scale)
            # 바닥 피복도 제어는 보이는 스와스 안의 밀도를 뜻한다.
            patches[category]["survey_corridor_fraction"] = 1.0
    return scale


def _apply_wreck_density(scene_cfg, wreck_pct):
    """Scale every GT wreck category after the production profile is merged.

    Wreck categories live in the base scene config while the production profile
    deliberately used to leave their counts untouched.  Applying this after the
    merge keeps the control independent of floor clutter and also automatically
    includes a future category carrying the ``wreck`` tag.
    """
    scale = float(wreck_pct) / 100.0
    for category in (scene_cfg.get("object_categories") or {}).values():
        tags = {str(tag) for tag in category.get("tags", [])}
        if "wreck" in tags and "count_range" in category:
            category["count_range"] = _scale_int_range(category["count_range"], scale)
    return scale


def _apply_wreck_tilt(scene_cfg, wreck_tilt_deg):
    """Apply the selected roll/pitch range to every large-dataset wreck.

    The actual burial offset is deliberately not calculated here: the engine
    rotates the mesh first, obtains its rotated world-space height, then applies
    ``burial_ratio`` to that height when it spawns the actor.
    """
    tilt = float(wreck_tilt_deg)
    for category in (scene_cfg.get("object_categories") or {}).values():
        tags = {str(tag) for tag in category.get("tags", [])}
        if "wreck" in tags:
            category["roll_range_deg"] = [-tilt, tilt]
            category["pitch_range_deg"] = [-tilt, tilt]
    return tilt


def _request_stop(signum, _frame):
    """웹 UI의 SIGTERM을 받아 현재 자식만 정리하고 index를 쓸 기회를 보장한다."""
    global STOP_REQUESTED
    STOP_REQUESTED = True
    proc = ACTIVE_PROCESS
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass


def _force_track_fraction(scene, fraction, full_length_m=None):
    survey = scene["survey"]
    full = float(full_length_m or survey.get("track_full_length_m") or
                 (float(survey["track_x1_m"]) - float(survey["track_x0_m"])))
    if full <= 0:
        raise SystemExit("track_full_length_m은 0보다 커야 합니다.")
    half = max(10.0, round(full * float(fraction) / 2.0, 1))
    survey["track_x0_m"] = -half
    survey["track_x1_m"] = half
    survey["track_length_fraction"] = float(fraction)
    survey["track_full_length_m"] = round(full, 1)


def _survey_range_envelope(scene, altitude_m, range_limit_m,
                           shallow_edge_deg, steep_edge_deg):
    """Sample the frozen survey corridor and return its farthest visible seabed.

    A tile-wide relief bound is safe but can include a valley nowhere near the
    selected track, leaving large empty outer bands.  This samples every leg and
    both sides of the sonar footprint.  A point contributes only when its local
    depression angle lies inside the vertical beam and its slant range lies inside
    ``range_limit_m``.  Objects do not need a farther range than their supporting
    seabed because they protrude toward the sensor.
    """
    terrain = scene["terrain"]
    survey = scene["survey"]
    ground = sm._terrain_sampler(terrain)
    sensor_z = float(terrain["seabed_top_m"]) + float(altitude_m)
    x0, y0, x1, y1 = [float(v) for v in terrain["extent_m"]]
    heading = math.radians(float(survey["survey_heading_deg"]))
    u = (math.cos(heading), math.sin(heading))
    v = (-math.sin(heading), math.cos(heading))
    t0, t1 = float(survey["track_x0_m"]), float(survey["track_x1_m"])
    n_legs = int(survey["n_legs"])
    spacing = float(survey["leg_spacing_m"])
    n_along = max(1, int(math.ceil(abs(t1 - t0) / 5.0)))
    n_cross = max(1, int(math.ceil(float(range_limit_m) / 1.0)))
    best = None
    sampled = 0
    for leg in range(n_legs):
        offset = (leg - (n_legs - 1) / 2.0) * spacing
        for ia in range(n_along + 1):
            along = t0 + (t1 - t0) * ia / n_along
            cx = along * u[0] + offset * v[0]
            cy = along * u[1] + offset * v[1]
            for side in (-1.0, 1.0):
                for ic in range(1, n_cross + 1):
                    cross = min(float(ic), float(range_limit_m))
                    px = cx + side * cross * v[0]
                    py = cy + side * cross * v[1]
                    if not (x0 <= px <= x1 and y0 <= py <= y1):
                        continue
                    clearance = sensor_z - ground(px, py)
                    if clearance <= 0.0:
                        continue
                    depression = math.degrees(math.atan2(clearance, cross))
                    slant = math.hypot(clearance, cross)
                    sampled += 1
                    if (shallow_edge_deg <= depression <= steep_edge_deg
                            and slant <= range_limit_m
                            and (best is None or slant > best[0])):
                        best = (slant, clearance, depression)
    return best, sampled


def build_plan(spec, root):
    n = int(spec.get("n", 100))
    seed0 = int(spec.get("seed", 100000))
    platform = str(spec.get("platform") or "ideal")
    preview_boot_seconds = (PREVIEW_BOOT_SECONDS
                            if _output_selection(spec)["scene_preview"] else 0.0)
    sensor = _json(SENSOR_CFG)
    scene_cfg = _json(SCENE_CFG)
    catalog = oc.load_catalog()
    terrains = [t["id"] for t in scene_cfg.get("terrains", [])
                if t.get("available", True)]
    if not terrains:
        raise SystemExit("available=true 인 지형이 없습니다.")

    # The production profile is intentionally applied only here.  Manual/UI
    # single-scene acquisition keeps its own current settings.
    profile = _large_profile(scene_cfg)
    controls = profile.pop("user_control_defaults", {})
    floor_clutter_pct = _number_control(spec, controls, "floor_clutter_pct", 0.0, 300.0)
    wreck_pct = _number_control(spec, controls, "wreck_pct", 0.0, 300.0)
    wreck_tilt_deg = _number_control(spec, controls, "wreck_tilt_deg", 0.0, 90.0)
    noise_level = _number_control(spec, controls, "noise_level", 0.0, 4.0)
    speckle_strength = _number_control(spec, controls, "speckle_strength", 0.0, 1.0)
    texture_cv = _number_control(spec, controls, "texture_cv", 0.0, 1.0)
    # Gamma texture has mean 1 and CV = 1/sqrt(shape).  0 is the explicit
    # engine convention for disabling the slow, spatially correlated texture.
    texture_shape = 0.0 if texture_cv == 0.0 else 1.0 / (texture_cv * texture_cv)
    _apply_floor_clutter_density(profile, floor_clutter_pct)
    geometry = profile.pop("sensor_geometry", {})
    acoustic_variation = profile.pop("scene_acoustic_variation", {})
    depression = _number_control(
        spec, {"depression_deg": geometry.get("depression_deg", 35.0)},
        "depression_deg", 25.1, 60.0)
    plan_cfg = copy.deepcopy(scene_cfg)
    plan_cfg.pop("large_dataset_profile", None)
    _deep_update(plan_cfg, profile)
    _apply_wreck_density(plan_cfg, wreck_pct)
    _apply_wreck_tilt(plan_cfg, wreck_tilt_deg)

    vertical_ray_span = float(geometry.get(
        "vertical_ray_span_deg", sensor["vertical_beam_deg"]))
    range_min = float(geometry.get("range_min_m", 0.5))
    range_cap = float(geometry.get("range_max_cap_m", 70.0))
    reach_fraction = float(geometry.get("beam_reach_fraction", 0.95))
    min_altitude_to_range = float(
        geometry.get("min_altitude_to_range_ratio", 0.10))
    altitude_fraction = [float(v) for v in
                         geometry.get("altitude_available_fraction", [0.80, 0.95])]
    track_dx_min = float(geometry.get("track_dx_min_m", sensor["track_dx_m"]))
    track_dx_max = float(geometry.get("track_dx_max_m", track_dx_min))
    track_dx_resolution_fraction = float(
        geometry.get("track_dx_resolution_fraction", 0.80))
    noise_multiplier = [float(v) for v in acoustic_variation.get(
        "noise_level_multiplier", [1.0, 1.0])]
    speckle_delta = [float(v) for v in acoustic_variation.get(
        "speckle_strength_delta", [0.0, 0.0])]
    if not 0.0 < reach_fraction <= 1.0:
        raise SystemExit("large_dataset_profile.sensor_geometry.beam_reach_fraction은 0~1이어야 합니다.")
    if not 0.0 < min_altitude_to_range < 1.0:
        raise SystemExit(
            "large_dataset_profile.sensor_geometry.min_altitude_to_range_ratio는 "
            "0~1 사이여야 합니다.")
    if (len(altitude_fraction) != 2 or not 0.0 < altitude_fraction[0]
            <= altitude_fraction[1] <= 1.0):
        raise SystemExit(
            "large_dataset_profile.sensor_geometry.altitude_available_fraction은 "
            "0 < min <= max <= 1인 두 값이어야 합니다.")
    if not 0.0 < track_dx_min <= track_dx_max:
        raise SystemExit("track_dx_min_m/max_m은 0보다 크고 min<=max여야 합니다.")
    if not 0.0 < track_dx_resolution_fraction <= 1.0:
        raise SystemExit("track_dx_resolution_fraction은 0~1 사이여야 합니다.")
    if (len(noise_multiplier) != 2 or noise_multiplier[0] < 0.0
            or noise_multiplier[0] > noise_multiplier[1]):
        raise SystemExit("noise_level_multiplier는 [0 이상 min, max]여야 합니다.")
    if len(speckle_delta) != 2 or speckle_delta[0] > speckle_delta[1]:
        raise SystemExit("speckle_strength_delta는 [min, max]여야 합니다.")
    half_vertical = float(sensor["vertical_beam_deg"]) / 2.0
    if vertical_ray_span < float(sensor["vertical_beam_deg"]):
        raise SystemExit("vertical_ray_span_deg는 nominal vertical_beam_deg보다 작을 수 없습니다.")
    if vertical_ray_span > 2.0 * depression:
        raise SystemExit("vertical_ray_span_deg/2가 복각보다 크면 수면 위로 ray가 향합니다.")
    shallow_edge_deg = depression - half_vertical
    if shallow_edge_deg <= 0.0:
        raise SystemExit(
            "대규모 취득 복각은 수직 빔폭/2보다 커야 유한한 빔 도달거리를 계산할 수 있습니다.")

    # Store effective production defaults in the dataset snapshot.  range_max_m
    # is the safety cap; each manifest/plan row carries the actual altitude-bound
    # value used by that scene.
    sensor["depression_deg"] = depression
    sensor["noise_level"] = noise_level
    sensor["speckle_strength"] = speckle_strength
    sensor["texture_shape"] = texture_shape
    sensor["vertical_ray_span_deg"] = vertical_ray_span
    sensor["range_min_m"] = range_min
    sensor["range_max_m"] = range_cap
    sensor["_large_dataset_range_policy"] = {
        "formula": ("min(range_max_cap_m, beam_reach_fraction * "
                    "max_visible_slant_range_over_survey_footprint, "
                    "altitude_m / min_altitude_to_range_ratio)"),
        "range_max_cap_m": range_cap,
        "beam_reach_fraction": reach_fraction,
        "min_altitude_to_range_ratio": min_altitude_to_range,
        "track_dx_policy": {
            "formula": ("clip(track_dx_resolution_fraction * range_max_m * "
                        "sin(horizontal_beam_deg), track_dx_min_m, track_dx_max_m)"),
            "track_dx_min_m": track_dx_min,
            "track_dx_max_m": track_dx_max,
            "track_dx_resolution_fraction": track_dx_resolution_fraction,
        },
        "altitude_available_fraction": altitude_fraction,
        "reason": ("cover relief actually crossed by the selected track/swath without "
                   "using an unrelated tile-wide valley or exceeding rated range"),
    }

    # 매니페스트 생성에도 고정 센서의 빔폭을 사용하고, track fraction 후보를 명시한다.
    plan_cfg["vertical_beam_deg"] = float(sensor["vertical_beam_deg"])
    ss = plan_cfg.setdefault("survey_sampling", {})
    ss["track_length_fraction"] = [0.25, 0.5, 1.0]
    ss["platform"] = platform
    # The manifest sampler needs a finite provisional range; the final range is
    # recomputed below after the shallow-water altitude has been sampled.
    ss["range_max_m"] = [range_cap, range_cap]
    ss["depression_deg"] = [depression, depression]
    ss["range_res_m"] = [sensor["range_res_m"]]

    terrain_plan = _balanced(terrains, n, seed0 ^ 0x54455252)
    fractions = [float(v) for v in spec.get("track_length_fractions", [0.25, 0.5, 1.0])]
    if not fractions or any(not 0 < v <= 1 for v in fractions):
        raise SystemExit("track_length_fractions는 0보다 크고 1 이하인 값이어야 합니다.")
    fraction_plan = _balanced(fractions, n, seed0 ^ 0x54524143)
    scenes, rows = [], []
    for i in range(n):
        scene_seed = seed0 + i
        # Let scene_manifest see the final track fraction before it places natural
        # background patches; otherwise a patch can be biased toward a part of the
        # full track that _force_track_fraction removes immediately afterwards.
        scene_plan_cfg = copy.deepcopy(plan_cfg)
        scene_plan_cfg.setdefault("survey_sampling", {})["track_length_fraction"] = [
            fraction_plan[i]]
        scene = sm.sample_scene(scene_seed, scene_plan_cfg, catalog, terrain_plan[i])
        _force_track_fraction(scene, fraction_plan[i], spec.get("track_full_length_m"))
        sv = scene["survey"]

        # Sensor acoustics are fixed, while range geometry follows the actual
        # altitude so a 35-degree beam does not pay for unreachable bins/rays.
        sv["range_res_m"] = sensor["range_res_m"]
        sv["depression_deg"] = depression
        # 서해 타일은 수심 2.5~10 m라 123.4 m range의 10~20% 고도는 물 밖이다.
        # acquire_sss.sensor_z가 조용히 수면 아래로 clamp하기 전에, 실제 물기둥 안에서
        # 가능한 고도를 명시적으로 뽑는다. production profile의 범위 안에서
        # 맵/seed마다 달라지며, 상한은 수면 아래 0.3 m 안전여유를 침범하지 않는다.
        water_depth = abs(float(scene["terrain"]["seabed_top_m"]))
        max_alt = max(1.0, water_depth - 0.3)       # 센서 최소 잠김 0.3 m와 같은 계약
        arng = random.Random(scene_seed ^ 0x414C5449)
        altitude = round(max(1.0, max_alt * arng.uniform(*altitude_fraction)), 2)
        altitude = min(altitude, max_alt)
        relief = float(sv.get("terrain_relief_m", 0.0))
        sv["altitude_m"] = altitude
        sv["altitude_worst_m"] = round(altitude + relief, 2)
        sv["altitude_available_fraction"] = round(altitude / max_alt, 4)
        sv["_altitude_note"] = (
            "센서가 수면 위로 나가지 않도록 마루 위 사용 가능 물기둥"
            f"(seabed depth-0.3m)의 {altitude_fraction[0]:.0%}~"
            f"{altitude_fraction[1]:.0%}에서 시드 샘플링함.")
        sv.pop("_range_clamp_note", None)
        sv["platform"] = platform

        rr_value = sensor["range_res_m"]
        if str(rr_value).lower() == "auto":
            rr_value = (float(sensor["sound_speed_ms"]) /
                        (2.0 * float(sensor["bandwidth_khz"]) * 1e3))
        rr_value = float(rr_value)

        # Range must cover relief crossed by this survey, not only the terrain
        # crest.  Conversely, a tile-wide minimum can lie nowhere near the track
        # and create empty outer bands.  Sample the actual frozen track/swath.
        search_limit = range_cap / reach_fraction
        envelope, footprint_samples = _survey_range_envelope(
            scene, altitude, search_limit, shallow_edge_deg,
            depression + half_vertical)
        if envelope is None:
            # Flat/missing-heightfield fallback; this is identical to the old
            # beam geometry for a locally flat bottom.
            range_reference_altitude = altitude
            beam_reach = (altitude /
                          math.sin(math.radians(shallow_edge_deg)))
            far_depression = shallow_edge_deg
            envelope_source = "flat_geometry_fallback"
        else:
            beam_reach, range_reference_altitude, far_depression = envelope
            envelope_source = "sampled_survey_footprint"
        # 기하적으로는 먼 골짜기 해저가 빔 안에 있을 수 있어도, 낮은 고도에서
        # 지나치게 긴 range를 유지하면 TVG-off 신호는 거리 손실로 거의 전부
        # 검정이 된다. 실제 운용처럼 고도/range 비가 최소값보다 작아지지 않게
        # 상한을 둔다. 예: 비율 0.10이면 고도 2 m -> 최대 20 m.
        range_altitude_cap = altitude / min_altitude_to_range
        range_max = min(range_cap, reach_fraction * beam_reach, range_altitude_cap)
        range_max = max(range_min + rr_value, range_max)
        range_max = round(range_max, 1)
        # range 밖의 더 깊은 골은 이번 ping의 대상이 아니므로, ray 밀도 계산에는
        # 실제로 저장되는 range 안에서 가능한 간격만 쓴다.
        range_reference_altitude = min(range_reference_altitude, range_max)
        # 넓은 swath에서 0.1 m ping 간격은 horizontal beam이 구분할 수 있는
        # along-track 해상도보다 과도하게 촘촘하다. R·sin(beamwidth)의 80% 간격까지
        # 늘려 중복 raycast만 줄이며, 근거리에서는 기존 최소 0.1 m를 유지한다.
        along_resolution = range_max * math.sin(math.radians(
            float(sensor["horizontal_beam_deg"])))
        track_dx = min(track_dx_max, max(
            track_dx_min, track_dx_resolution_fraction * along_resolution))
        track_dx = round(track_dx, 3)
        # Seed만 바꾸면 단지 난수 realization만 바뀐다. 여기서는 scene마다
        # 수신기 noise power와 diffuse speckle fraction 자체도 작은 범위에서 바꾼다.
        vrng = random.Random(scene_seed ^ 0x4E4F4953)
        scene_noise_level = round(noise_level * vrng.uniform(*noise_multiplier), 4)
        scene_speckle_strength = round(min(1.0, max(
            0.0, speckle_strength + vrng.uniform(*speckle_delta))), 4)
        sv["range_min_m"] = range_min
        sv["range_max_m"] = range_max
        sv["range_altitude_cap_m"] = round(range_altitude_cap, 1)
        sv["track_dx_m"] = track_dx
        sv["noise_level"] = scene_noise_level
        sv["speckle_strength"] = scene_speckle_strength
        sv["range_reference_altitude_m"] = round(range_reference_altitude, 2)
        sv["beam_reach_m"] = round(beam_reach, 1)
        sv["range_envelope_source"] = envelope_source
        sv["range_footprint_samples"] = footprint_samples
        sv["range_far_depression_deg"] = round(far_depression, 2)
        sv["altitude_ratio"] = round(altitude / range_max, 4)
        sv["_range_geometry_note"] = (
            f"복각 {depression:g}°, 수직빔 {float(sensor['vertical_beam_deg']):g}°의 "
            f"얕은 경계 {shallow_edge_deg:g}°에서 실제 항로·좌우 스와스를 표본화한 "
            f"최원거리 해저 {beam_reach:.1f} m(그 지점 간격 "
            f"{range_reference_altitude:.2f} m, 복각 {far_depression:.2f}°). "
            f"수치 여유 {reach_fraction:.0%}, 정격 상한 {range_cap:g} m, "
            f"고도/거리 하한 {min_altitude_to_range:.0%} "
            f"(고도 기반 상한 {range_altitude_cap:.1f} m)를 적용함.")

        # elev_ray_max는 센서 물리가 아니라 ray 근사 오차를 막는 계산 예산이다.
        # 얕은 고도에서도 range_res 간격을 지키는 데 필요한 정확한 수를 미리 계산한다.
        rr = rr_value
        rmax = range_max
        th_min = math.asin(min(max(range_reference_altitude / rmax, 1e-6), 1.0))
        d_th_deg = math.degrees(float(rr) * math.tan(th_min) / rmax)
        elev_required = int(math.ceil(vertical_ray_span /
                                      max(d_th_deg, 1e-12))) + 1
        elev_budget = max(int(sensor.get("elev_ray_max", 8000)), elev_required)
        scenes.append(scene)
        rows.append({
            "index": i, "scene_id": scene["scene_id"], "scene_seed": scene_seed,
            "terrain": terrain_plan[i], "track_length_fraction": fraction_plan[i],
            "track_length_m": round(float(sv["track_x1_m"]) - float(sv["track_x0_m"]), 1),
            "heading_deg": sv["survey_heading_deg"], "n_legs": sv["n_legs"],
            "leg_spacing_m": sv["leg_spacing_m"], "altitude_m": sv["altitude_m"],
            "altitude_worst_m": sv["altitude_worst_m"],
            "range_reference_altitude_m": round(range_reference_altitude, 2),
            "depression_deg": depression, "range_min_m": range_min,
            "floor_clutter_pct": floor_clutter_pct, "wreck_pct": wreck_pct,
            "wreck_tilt_deg": wreck_tilt_deg,
            "noise_level": scene_noise_level,
            "speckle_strength": scene_speckle_strength, "texture_cv": texture_cv,
            "texture_shape": texture_shape,
            "range_max_m": range_max, "beam_reach_m": round(beam_reach, 1),
            "range_altitude_cap_m": round(range_altitude_cap, 1),
            "track_dx_m": track_dx,
            "range_envelope_source": envelope_source,
            "range_footprint_samples": footprint_samples,
            "objects": len(scene["objects"]),
            "clusters": len(scene.get("object_clusters", [])),
            "elev_rays_required": elev_required,
            "elev_ray_max_execution": elev_budget,
            # 엔진/NumPy 양쪽에서 안전한 양의 int32 범위. 물리 파라미터는 같고
            # realization만 서로 다르며, dataset seed로 완전히 재현된다.
            "speckle_seed": (seed0 * 1009 + i * 2 + 1) % 2147483646 + 1,
            "noise_seed": (seed0 * 1009 + i * 2 + 2) % 2147483646 + 1,
        })
        pings = max(1, int(round(rows[-1]["track_length_m"] / track_dx))) * int(sv["n_legs"])
        scatter_spacing = float(sensor.get("scatter_correlation_length_m", 0.05))
        desired_az = math.degrees(math.atan2(scatter_spacing, rmax))
        az_required = max(3, int(math.ceil(float(sensor["horizontal_beam_deg"]) /
                                           max(desired_az, 1e-8))) + 1)
        az_required = min(az_required, int(sensor.get("azimuth_ray_max", 65)))
        rows[-1]["azimuth_rays_required"] = az_required
        # The reference smoke used the 123.4 m profile (13 azimuth samples).
        # The cap is only a guard; the engine casts ``elev_required`` rays when
        # the requirement is below that cap.  Using the cap here used to grossly
        # overestimate the new short-range profile.
        ray_scale = (elev_required * az_required) / (REFERENCE_ELEV_RAYS * 13.0)
        platform_scale = 1.2 if platform == "asv" else 1.0
        rows[-1]["estimated_pings"] = pings
        rows[-1]["estimated_seconds"] = round(
            preview_boot_seconds + ACQUIRE_BOOT_SECONDS
            + pings * REFERENCE_SECONDS_PER_PING * ray_scale * platform_scale, 1)
    plan_cfg["_large_dataset_profile_applied"] = {
        "sensor_geometry": copy.deepcopy(geometry),
        "user_controls": {
            "floor_clutter_pct": floor_clutter_pct,
            "wreck_pct": wreck_pct,
            "wreck_tilt_deg": wreck_tilt_deg,
            "noise_level": noise_level,
            "speckle_strength": speckle_strength,
            "texture_cv": texture_cv,
            "texture_shape": texture_shape,
            "depression_deg": depression,
            "scene_acoustic_variation": copy.deepcopy(acoustic_variation),
        },
        "source": "scene_config.json:large_dataset_profile",
    }
    return sensor, plan_cfg, catalog, scenes, rows


def _write_contract(root, spec, sensor, scene_cfg, catalog, scenes, rows):
    for sub in ("manifests", "samples"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    _atomic_json(root / "dataset_spec.json", spec)
    _atomic_json(root / "sensor_config_snapshot.json", sensor)
    _atomic_json(root / "scene_config_snapshot.json", scene_cfg)
    _atomic_json(root / "object_catalog_snapshot.json", catalog)
    _atomic_json(root / "numerical_sampling_policy.json", {
        "policy": "raise-elevation-ray-budget-to-required",
        "reason": ("씬 고도와 선택한 복각의 빔 도달거리에 정합된 range_max에서 고정 range_res가 "
                   "요구하는 이웃 ray 간격을 유지한다. elev_ray_max는 센서 물리가 아닌 "
                   "수치 근사 예산이다."),
        "sensor_snapshot_default_elev_ray_max": sensor.get("elev_ray_max", 8000),
        "per_scene_field": "plan.elev_ray_max_execution",
    })
    manifest = root / "manifests" / "scenes.jsonl"
    with manifest.open("w") as f:
        for scene in scenes:
            f.write(json.dumps(scene, ensure_ascii=False) + "\n")
    _atomic_json(root / "plan.json", rows)
    if rows:
        with (root / "plan.csv").open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
            w.writeheader(); w.writerows(rows)
    (root / "README.txt").write_text(
        "SSS 데이터셋 실행 루트\n\n"
        "samples/scene_NNNNNN/ : 한 장면의 설정·미리보기·SSS·annotation 전체\n"
        "  sss/waterfall/       : 기본 워터폴 PNG (선택)\n"
        "  sss/true_aspect/     : 실제 종횡비 PNG (선택)\n"
        "  sss/raw/             : 수신 처리 전 NumPy (선택)\n"
        "  sss/processed/       : 최종 dB NumPy (선택)\n"
        "  annotations/masks/   : 일반 pixel mask GT (선택)\n"
        "  annotations/bboxes/  : YOLO TXT + pixel JSON (선택)\n"
        "manifests/scenes.jsonl : 지형·오브젝트·조사계획의 완전한 장면 기록\n"
        "plan.csv/json     : 세션별 다양화 계획\n"
        "index.json        : 성공/실패와 실제 session 폴더 인덱스\n"
        "numerical_sampling_policy.json : 얕은 수심의 ray 계산 예산 정책\n"
        "*_snapshot.json   : 실행 시작 때 고정한 설정/카탈로그\n",
        encoding="utf-8")
    return manifest


def _stream_command(cmd, log):
    global ACTIVE_PROCESS
    proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1)
    ACTIVE_PROCESS = proc
    lines = []
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="", flush=True)
            log.write(line); log.flush()
            lines.append(line)
        rc = proc.wait()
    finally:
        ACTIVE_PROCESS = None
    return rc, "".join(lines)


def _artifact_counts(sample_dir):
    """Count public artifacts used to decide whether a scene really completed."""
    return {
        "waterfall_png": len(list((sample_dir / "sss/waterfall").glob("*.png"))),
        "true_aspect_png": len(list((sample_dir / "sss/true_aspect").glob("*.png"))),
        "raw_numpy": len(list((sample_dir / "sss/raw").glob("*.npy"))),
        "processed_numpy": len(list((sample_dir / "sss/processed").glob("*.npy"))),
        "mask": len(list((sample_dir / "annotations/masks").glob("*.png"))),
        "bbox": len([p for p in (sample_dir / "annotations/bboxes").glob("*.txt")
                     if p.name != "classes.txt"]),
        "bbox_overlay": len(list(
            (sample_dir / "annotations/bbox_overlays").glob("*.png"))),
        "scene_preview": int((sample_dir / "preview.png").is_file()),
    }


def _selected_artifacts_complete(counts, outputs):
    return all(not outputs[key] or counts.get(key, 0) > 0
               for key in counts)


def _run_one(root, manifest, row, fixed, platform, outputs):
    i = int(row["index"])
    sample_id = f"scene_{i:06d}"
    sample_dir = root / "samples" / sample_id
    sample_dir.mkdir(parents=True, exist_ok=True)
    overrides = dict(fixed)
    overrides.update({
        "dataset_root": str(root),
        "dataset_sample_id": sample_id,
        "speckle_seed": int(row["speckle_seed"]),
        "noise_seed": int(row["noise_seed"]),
        "elev_ray_max": int(row["elev_ray_max_execution"]),
        "save_waterfall_png": outputs["waterfall_png"],
        "save_true_aspect_png": outputs["true_aspect_png"],
        "save_raw_numpy": outputs["raw_numpy"],
        "save_processed_numpy": outputs["processed_numpy"],
        "save_mask": outputs["mask"],
        "save_bbox": outputs["bbox"],
        "save_bbox_overlay": outputs["bbox_overlay"],
        "gt_labels": outputs["mask"] or outputs["bbox"] or outputs["bbox_overlay"],
    })
    # New production plans record altitude-bound geometry explicitly.  Keep
    # resume compatible with pre-profile datasets whose rows lack these fields;
    # their manifest still carries the original survey geometry.
    for key in ("depression_deg", "range_min_m", "range_max_m",
                "range_reference_altitude_m", "track_dx_m", "noise_level",
                "speckle_strength"):
        if key in row:
            overrides[key] = float(row[key])
    cmd = [sys.executable, "-u", str(ACQUIRE), platform,
           "--manifest", str(manifest), "--manifest_index", str(i)] + _to_args(overrides)
    log_path = sample_dir / "run.log"
    preview_path = sample_dir / "preview.png"
    t0 = time.time()
    with log_path.open("w") as log:
        preview_ok = True
        preview_rc = 0
        if outputs["scene_preview"]:
            preview_cmd = [sys.executable, "-u", str(PREVIEW),
                           "--manifest", str(manifest), "--manifest_index", str(i),
                           "--out", str(preview_path), "--size", "768"]
            log.write("$ " + " ".join(preview_cmd) + "\n\n"); log.flush()
            print(f"[dataset] preview {i+1} -> {sample_id}/preview.png", flush=True)
            preview_rc, _ = _stream_command(preview_cmd, log)
            preview_ok = preview_rc == 0 and preview_path.is_file()
        if STOP_REQUESTED or not preview_ok:
            return {
                **row, "ok": False, "rc": preview_rc, "session": sample_id,
                "session_dir": f"samples/{sample_id}", "waterfalls": 0,
                "preview": (str(preview_path.relative_to(root))
                            if outputs["scene_preview"] and preview_ok else None),
                "preview_ok": preview_ok, "elapsed_s": round(time.time() - t0, 1),
                "log": str(log_path.relative_to(root)),
                "interrupted": bool(STOP_REQUESTED),
            }

        log.write("\n$ " + " ".join(cmd) + "\n\n"); log.flush()
        rc, text = _stream_command(cmd, log)
    artifacts = _artifact_counts(sample_dir)
    completed = (sample_dir / "acquisition_config.json").is_file()
    return {
        **row, "ok": (rc == 0 and completed
                       and _selected_artifacts_complete(artifacts, outputs)),
        "rc": rc, "session": sample_id,
        "session_dir": f"samples/{sample_id}",
        "preview": (str(preview_path.relative_to(root))
                    if outputs["scene_preview"] else None),
        "preview_ok": preview_ok,
        "waterfalls": artifacts["waterfall_png"], "artifacts": artifacts,
        "elapsed_s": round(time.time() - t0, 1),
        "log": str(log_path.relative_to(root)),
        "interrupted": bool(STOP_REQUESTED),
    }


def _emit_estimate(rows, results, started):
    """계획 추정치를 완료 씬 실측으로 계속 보정해 종료 시각을 출력한다."""
    attempted = {int(r["index"]) for r in results}
    est_done = sum(float(r.get("estimated_seconds", 0)) for r in rows
                   if int(r["index"]) in attempted)
    actual_done = sum(float(r.get("elapsed_s", 0)) for r in results)
    correction = actual_done / est_done if est_done > 0 and actual_done > 0 else 1.0
    remaining = sum(float(r.get("estimated_seconds", 0)) for r in rows
                    if int(r["index"]) not in attempted) * correction
    now = time.time()
    payload = {
        "remaining_seconds": round(remaining),
        "finish_epoch": round(now + remaining),
        "total_seconds": round((now - started) + remaining),
        "correction": round(correction, 3),
        "basis": "plan" if not results else f"{len(results)} completed scene(s)",
    }
    print("[dataset-estimate] " + json.dumps(payload, ensure_ascii=False), flush=True)
    finish = dt.datetime.fromtimestamp(payload["finish_epoch"]).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[dataset] 예상 종료 {finish} · 남은 시간 약 {remaining/3600:.1f}시간 "
          f"(보정 {correction:.2f}x)", flush=True)
    return payload


def _directory_size(path):
    return sum(p.stat().st_size for p in pathlib.Path(path).rglob("*") if p.is_file())


def _export_zip(root):
    """Create a portable dataset bundle beside the dataset directory."""
    size = _directory_size(root)
    free = shutil.disk_usage(root.parent).free
    if free < max(size, 1) * 1.05:
        raise RuntimeError(
            f"ZIP 생성에 필요한 여유 공간이 부족합니다 "
            f"(데이터셋 {size/2**30:.1f} GiB, 여유 {free/2**30:.1f} GiB).")
    archive = root.with_suffix(".zip")
    print(f"[export] ZIP 생성 시작 -> {archive}", flush=True)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=1, allowZip64=True) as zf:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                zf.write(path, pathlib.Path(root.name) / path.relative_to(root))
    print(f"[export] ZIP 완료 ({archive.stat().st_size/2**30:.1f} GiB) -> {archive}",
          flush=True)
    return archive


def main(argv=None):
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    ap = argparse.ArgumentParser(description="SSS 데이터셋 취득")
    ap.add_argument("--spec", help="{name,n,seed,platform} JSON")
    ap.add_argument("--dataset-root", help="기존 실행 루트 (--resume와 함께 사용)")
    ap.add_argument("--dry-run", action="store_true", help="계획/폴더만 만들고 취득하지 않음")
    ap.add_argument("--resume", action="store_true", help="index.json의 성공 씬을 건너뛰고 재개")
    a = ap.parse_args(argv)

    if a.resume:
        if not a.dataset_root:
            raise SystemExit("--resume에는 --dataset-root가 필요합니다.")
        root = _dataset_root({}, a.dataset_root)
        spec = _json(root / "dataset_spec.json")
        spec["outputs"] = _output_selection(spec)
        sensor = _json(root / "sensor_config_snapshot.json")
        scenes = [json.loads(x) for x in (root / "manifests/scenes.jsonl").read_text().splitlines()]
        rows = _json(root / "plan.json")
        manifest = root / "manifests/scenes.jsonl"
    else:
        if not a.spec:
            raise SystemExit("--spec가 필요합니다.")
        spec = _json(a.spec)
        spec["outputs"] = _output_selection(spec)
        n = int(spec.get("n", 100))
        if not 1 <= n <= 5000:
            raise SystemExit(f"n은 1~5000이어야 합니다: {n}")
        root = _dataset_root(spec, a.dataset_root)
        root.mkdir(parents=True, exist_ok=False)
        sensor, scene_cfg, catalog, scenes, rows = build_plan(spec, root)
        manifest = _write_contract(root, spec, sensor, scene_cfg, catalog, scenes, rows)

    print(f"[dataset-root] {root}", flush=True)
    print(f"[dataset] 씬 {len(rows)}개 · 맵 {len(set(r['terrain'] for r in rows))}종 · "
          f"음향 설정 고정/취득 range는 씬 고도에 정합 -> "
          f"{root / 'sensor_config_snapshot.json'}", flush=True)
    for r in rows:
        print(f"  [{r['index']:04d}] {r['terrain']:<27s} track={r['track_length_fraction']:g} "
              f"({r['track_length_m']:.0f}m) alt={r['altitude_m']:.1f}m "
              f"range={r.get('range_max_m', sensor.get('range_max_m')):.1f}m "
              f"hd={r['heading_deg']:.0f}° legs={r['n_legs']} obj={r['objects']} "
              f"elev_rays={r.get('elev_rays_required', r['elev_ray_max_execution'])}",
              flush=True)

    if a.dry_run:
        index = {"format": "sss-dataset-v2", "status": "planned", "root": str(root),
                 "n": len(rows), "completed": 0, "outputs": spec["outputs"],
                 "results": []}
        _atomic_json(root / "index.json", index)
        _emit_estimate(rows, [], time.time())
        print(f"[dataset] 계획 완료 (취득 안 함) -> {root}", flush=True)
        return 0

    prior = _json(root / "index.json") if (root / "index.json").exists() else {}
    results = list(prior.get("results") or [])
    by_index = {int(r["index"]): r for r in results}
    fixed = _fixed_sensor(sensor)
    outputs = _output_selection(spec)
    platform = str(spec.get("platform") or sensor.get("platform") or "ideal")
    t0 = time.time()
    done = sum(1 for r in by_index.values() if r.get("ok"))
    print(f"[progress] dataset {done}/{len(rows)} {100*done/max(len(rows),1):.1f}% "
          f"경과 0s 남음 -s", flush=True)
    _atomic_json(root / "index.json", {
        "format": "sss-dataset-v2", "status": "running", "root": str(root),
        "n": len(rows), "completed": done, "outputs": outputs,
        "results": results,
    })
    _emit_estimate(rows, results, t0)
    for row in rows:
        if STOP_REQUESTED:
            break
        i = int(row["index"])
        if by_index.get(i, {}).get("ok"):
            continue
        print(f"[dataset] {i+1}/{len(rows)} 시작 — {row['terrain']}", flush=True)
        result = _run_one(root, manifest, row, fixed, platform, outputs)
        by_index[i] = result
        results = [by_index[k] for k in sorted(by_index)]
        done = sum(1 for r in results if r.get("ok"))
        elapsed = time.time() - t0
        attempted = len(results)
        eta = elapsed / max(attempted, 1) * max(len(rows) - attempted, 0)
        index = {"format": "sss-dataset-v2", "status": "running",
                 "root": str(root), "n": len(rows), "completed": done,
                 "outputs": outputs, "results": results}
        _atomic_json(root / "index.json", index)
        print(f"[dataset] {i+1}/{len(rows)} {'OK' if result['ok'] else 'FAIL'} "
              f"session={result['session']} waterfalls={result['waterfalls']}", flush=True)
        print(f"[progress] dataset {attempted}/{len(rows)} "
              f"{100*attempted/max(len(rows),1):.1f}% 경과 {elapsed:.0f}s 남음 {eta:.0f}s", flush=True)
        _emit_estimate(rows, results, t0)
        if STOP_REQUESTED:
            break

    completed_count = sum(1 for r in by_index.values() if r.get("ok"))
    failed_count = sum(1 for r in by_index.values() if not r.get("ok"))
    pending_count = max(0, len(rows) - len(by_index))
    failures = [r for r in by_index.values() if not r.get("ok")]
    status = ("interrupted" if STOP_REQUESTED else
              ("complete" if not failures and len(by_index) == len(rows) else "partial"))
    final = {"format": "sss-dataset-v2", "status": status,
             "root": str(root), "n": len(rows),
             "completed": completed_count, "failed": failed_count,
             "pending": pending_count,
             "outputs": outputs,
             "results": [by_index[k] for k in sorted(by_index)]}
    _atomic_json(root / "index.json", final)
    if STOP_REQUESTED:
        print(f"[dataset] 사용자 중단: 완료 데이터 {final['completed']}개 보존 -> {root}",
              flush=True)
        return 130
    if outputs["export_zip"] and status == "complete":
        try:
            archive = _export_zip(root)
            final["archive"] = str(archive)
        except (OSError, RuntimeError) as exc:
            final["archive_error"] = str(exc)
            print(f"[export][warn] {exc}", flush=True)
        _atomic_json(root / "index.json", final)
    print(f"[dataset] 완료: {final['completed']}/{len(rows)} 성공 -> {root}", flush=True)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
