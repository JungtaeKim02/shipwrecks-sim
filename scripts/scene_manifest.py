"""씬 매니페스트 — "어떤 지형 위에, 어떤 오브젝트를, 어디에, 어떤 물성으로" 를 한 줄로 기술한다.

한 줄(JSON) = 한 씬. acquire_sss.py 가 이 줄을 받아 씬을 구성하고 취득한다.
취득 파라미터(경로/각도/고도)를 랜덤화하듯, 씬 자체도 여기서 랜덤화한다.

사용:
    python3 scripts/scene_manifest.py --n 20 --out data/scenes/train.jsonl
    python3 scripts/scene_manifest.py --n 1 --seed 7 --terrain mado_report_v1 --print
    python3 scripts/scene_manifest.py --n 200 --out data/scenes/train.jsonl --split train

설계 메모:
  * 고도는 직접 뽑지 않고 range_max 의 10~20% 로 유도한다. 실제 사이드스캔 운용의
    "10% 룰"(고도 = 레인지 스케일의 10~20%) 에서 온 것이라 임의 범위가 아니다.
  * 오브젝트 배치는 최소 간격(min_separation_m)을 지키는 거절 샘플링. 겹치면 그림자와
    layover 가 뒤엉켜 GT 해석이 모호해진다.
  * 매립(burial_ratio)은 실제 난파선처럼 일부를 해저에 묻어 그림자를 자연스럽게 만든다.
"""
import argparse
import json
import math
import pathlib
import random
import sys

import object_catalog as oc
import project_paths as pp

ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST_VERSION = 1


def _pick_range(rng, pair):
    lo, hi = pair
    if isinstance(lo, int) and isinstance(hi, int):
        return rng.randint(lo, hi)
    return rng.uniform(lo, hi)


_TERRAIN_CACHE = {}


def _terrain_sampler(terrain):
    """지형 heightfield 를 읽어 (x, y) -> 해저면 z [m] 보간 함수를 만든다.

    2026-08-04 추가. 그 전에는 모든 오브젝트의 z 를 `seabed_top_m`(지형 **마루** 높이)
    으로 고정했다. 엔진에도 지형 스냅이 없어서(SpawnSceneObjectCommand 는 받은 좌표에
    그대로 스폰한다), 기복이 큰 지형에서는

      * 골짜기 위 오브젝트가 최대 '기복' 만큼 **공중에 뜨고**
      * 마루 위 오브젝트는 지형에 파묻혀 윗부분만 나온다

    실제로 "언덕 위 난파선은 누워 보이고, 옮기면 우뚝 선다"로 드러났다(기복 34 m 지형).
    소나 데이터로 보면 뜬 오브젝트는 그림자와 높이가 전부 틀린다.
    """
    csv = terrain.get("heightfield_csv")
    if not csv:
        z = float(terrain["seabed_top_m"])
        return lambda x, y: z
    key = csv
    if key not in _TERRAIN_CACHE:
        path = pp.find_config_file(csv)
        if not path.exists():
            z = float(terrain["seabed_top_m"])
            _TERRAIN_CACHE[key] = None
            print(f"[warn] heightfield 없음: {csv} — 오브젝트 z 를 마루 높이로 둡니다.",
                  file=sys.stderr)
        else:
            import numpy as np
            raw = np.genfromtxt(path, delimiter=",", names=True)
            ix = raw["ix"].astype(int); iy = raw["iy"].astype(int)
            nx, ny = ix.max() + 1, iy.max() + 1
            Z = np.full((ny, nx), np.nan)
            Z[iy, ix] = -raw["depth_m"]           # CSV 는 수심(양수) -> z(음수)
            _TERRAIN_CACHE[key] = (Z, float(raw["x_m"].min()), float(raw["x_m"].max()),
                                   float(raw["y_m"].min()), float(raw["y_m"].max()))
    got = _TERRAIN_CACHE[key]
    if got is None:
        z = float(terrain["seabed_top_m"])
        return lambda x, y: z
    Z, x0, x1, y0, y1 = got
    ny, nx = Z.shape

    def sample(x, y):
        fx = (x - x0) / (x1 - x0) * (nx - 1)
        fy = (y - y0) / (y1 - y0) * (ny - 1)
        fx = min(max(fx, 0.0), nx - 1.0); fy = min(max(fy, 0.0), ny - 1.0)
        i0, j0 = int(fx), int(fy)
        i1, j1 = min(i0 + 1, nx - 1), min(j0 + 1, ny - 1)
        u, v = fx - i0, fy - j0
        return float((Z[j0, i0] * (1 - u) + Z[j0, i1] * u) * (1 - v)
                     + (Z[j1, i0] * (1 - u) + Z[j1, i1] * u) * v)
    return sample


def _burial_ratio_for_engine(desired, bbox_m, scale, rot_deg):
    """엔진에 보낼 매몰 비율.

    엔진은 `메쉬 로컬 Z 범위 x 배율 x 비율` 만큼 내린다
    (SpawnSceneObjectCommand: `Mesh->GetBounds().BoxExtent.Z * 2 * scale`).
    그런데 자산마다 로컬 Z 가 가리키는 물리적 치수가 다르다 — 실측(최대변 대비):

        mado_1_ship_remaining             1.000

    그래서 같은 비율을 줘도 어떤 난파선은 거의 안 묻히고 어떤 것은 통째로 들어갔다
    ("매몰률을 올려도 더 안 들어간다" — 2026-08-04 지적).

    의도는 "**세워진 높이**의 몇 %를 묻는다" 이므로, 회전 후 **월드 수직 범위**를
    기준으로 삼아야 한다. 엔진은 로컬 Z 만 보므로, 여기서 비율을 환산해 보낸다.
    (엔진이 [0,1] 로 클램프하므로 환산값이 1 을 넘으면 그만큼만 묻힌다 — 회전이
     작을 때는 환산값이 1 근처라 실질 문제가 없다. 엔진이 월드 범위를 쓰도록
     고치는 것이 정공법이고, 그건 C++ 재빌드가 필요하다.)
    """
    if not desired or not bbox_m or len(bbox_m) < 3:
        return desired
    hz_local = abs(float(bbox_m[2])) * abs(scale)
    if hz_local <= 1e-9:
        return desired
    r = [math.radians(v) for v in (rot_deg or [0, 0, 0])]
    cx, sx = math.cos(r[0]), math.sin(r[0])
    cy, sy = math.cos(r[1]), math.sin(r[1])
    cz, sz = math.cos(r[2]), math.sin(r[2])
    # R = Rz*Ry*Rx 의 마지막 행 (월드 z 가 로컬 축들에서 받는 성분)
    row_z = (-sy, cy * sx, cy * cx)
    h = [abs(float(b)) * abs(scale) / 2.0 for b in bbox_m]
    hz_world = 2.0 * sum(abs(row_z[k]) * h[k] for k in range(3))
    return min(desired * hz_world / hz_local, 1.0)


def _track_half_length(terrain, heading_deg, spacing, n_legs, margin=8.0):
    """이 방향/간격에서 모든 leg 가 지형 안에 들어오는 track 반길이 [m].

    주행 격자는 원점 중심이므로, 지형이 원점에 대해 비대칭이어도 안전하도록
    원점에서 각 변까지의 **최소** 거리를 반폭으로 쓴다(내접 사각형).
    """
    x0, y0, x1, y1 = terrain["extent_m"]
    hx = min(abs(x0), abs(x1))
    hy = min(abs(y0), abs(y1))

    h = math.radians(heading_deg)
    ch, sh = abs(math.cos(h)), abs(math.sin(h))
    off = (n_legs - 1) / 2.0 * spacing          # 중심에서 가장 바깥 leg 까지
    lim = []
    if ch > 1e-9:
        lim.append((hx - margin - off * sh) / ch)
    if sh > 1e-9:
        lim.append((hy - margin - off * ch) / sh)
    return max(10.0, round(min(lim))) if lim else 10.0


def _tags_with_material(tags, material):
    """카탈로그 태그에서 `sssmat:` 만 현재 설정 재질로 갈아끼운다.

    `wreck` 같은 GT 태그는 그대로 두고, 재질 선언 하나만 교체한다.
    material 이 None/빈값이면 카탈로그 태그를 그대로 쓴다(재질 미선언 카테고리).
    """
    out = [t for t in tags if not str(t).startswith("sssmat:")]
    if material:
        out.append(f"sssmat:{material}")
    elif len(out) != len(tags):
        return list(tags)                       # 지울 게 있었는데 대체값이 없으면 원본 유지
    return out


def _sample_survey(rng, cfg, terrain, vertical_beam_deg=50.0):
    s = cfg["survey_sampling"]
    range_max = float(_pick_range(rng, s["range_max_m"]))
    # 10% 룰: 고도는 레인지의 10~20%.
    ratio = rng.uniform(*s["altitude_ratio"])
    altitude = round(range_max * ratio, 2)

    # ---- 지형 최저점 기준 최악의 경우 확보 (2026-07-31) ----
    # altitude_m 은 해저 **윗면**(seabed_top_m = 융기부 마루) 기준 여유다. 토우피시는
    # 정심(定深) 예인이라 항주 중 깊이가 일정하므로, 실제 고도는 지형에 따라
    #     마루 위  : a
    #     골짜기 위: a + 기복        <- 최저점, 가장 높이 뜬 상태 = 최악
    # 로 변한다. 10% 룰(고도 = 레인지의 10~20%)을 마루 기준으로만 맞추면 골짜기에서는
    # 20% 를 넘어 너무 높이 뜬 셈이 되어 그림자가 짧아지고 표적 탐지가 나빠진다.
    #
    # 두 경계를 **동시에** 만족시키는 조건:
    #     (a + 기복) / range <= 0.20     (최저점에서 너무 높지 않을 것)
    #     a / range         >= 0.10      (최고점에서 너무 낮지 않을 것)
    # 두 식을 합치면 a + 기복 <= 2a, 즉 **a >= 기복** 이어야 해가 존재한다.
    # 따라서 마루 기준 고도를 최소 기복만큼 확보하고, range 는 상한식에서 정한다.
    # a == 기복 이면 두 부등식이 동시에 등호가 되는 유일해가 된다(임의 상수 없음).
    lo_r, hi_r = float(s["altitude_ratio"][0]), float(s["altitude_ratio"][1])
    relief = abs(float(terrain.get("seabed_bottom_m", terrain["seabed_top_m"]))
                 - float(terrain["seabed_top_m"]))
    if relief > 0:
        altitude = round(max(altitude, relief), 2)
        range_max = (altitude + relief) / hi_r
        ratio = altitude / range_max
    altitude_worst = round(altitude + relief, 2)

    # 센서 정격 최대거리 제한.
    #
    # 10% 룰만 풀면 기복이 큰 지형에서 레인지가 폭주한다(기복 34 m -> 343 m).
    # 그런데 EdgeTech 2205 는 540 kHz 에서 정격 150 m 다. 정격 밖은 해저 반사가
    # 노이즈 위로 나오지 않으므로, 그 거리까지 찍으면 **스와스 바깥이 통째로
    # 노이즈**가 된다(2026-08-04 실측: 200 m 너머 반사 있는 ping 1%).
    #
    # 두 조건이 동시에 성립하려면
    #     range = 2*기복/0.20 <= 정격   ->   기복 <= 정격 * 0.10
    # 즉 이 센서로 정심 예인해서 10% 룰을 지킬 수 있는 기복 한계는 15 m 다.
    # 그보다 큰 기복은 레인지를 정격으로 자르고, 골짜기에서 10% 룰을 벗어난다는
    # 사실을 매니페스트에 남긴다(조용히 이상한 데이터를 만들지 않기 위해).
    rated = float(s.get("rated_max_range_m", 150.0))
    rule_note = None
    if range_max > rated:
        rule_note = (f"기복 {relief:.1f} m 는 10% 룰과 센서 정격({rated:.0f} m)을 "
                     f"동시에 만족할 수 없다(한계 기복 {rated*0.10:.1f} m). "
                     f"레인지를 {range_max:.0f} -> {rated:.0f} m 로 자름. "
                     f"골짜기 위 고도/레인지 = {altitude_worst/rated*100:.0f}% 로 "
                     f"10% 룰 상한(20%)을 벗어난다.")
        range_max = rated
        ratio = altitude / range_max

    # 하향각은 독립적으로 뽑는다. 실제 조사도 목적에 따라 넓게 훑기도 하고(낮은 복각)
    # 좁은 구간을 자세히 보기도 한다(높은 복각). 다양한 조사 형태를 담는 것 자체가
    # 데이터로서 의미가 있으므로 기하를 하나로 묶지 않는다.
    #
    # 빔 도달거리(reach = altitude / sin(depression - vertical_beam/2))가 range_max
    # 보다 짧으면 그 바깥에는 반사가 없다. 이것은 잘못된 설정이 아니라 실제 취득에서도
    # 일어나는 일이며, 워터폴을 만들 때 데이터가 있는 구간까지만 잘라내면 된다
    # (sss_common.effective_range_bins). reach 는 그 판단용 메타데이터로 기록한다.
    half_vb = vertical_beam_deg / 2.0
    depression = round(rng.uniform(*s["depression_deg"]), 1)
    lo = max(depression - half_vb, 0.1)             # lo<=0 이면 사실상 무한
    reach = altitude / math.sin(math.radians(lo))

    heading = round(rng.uniform(*s["survey_heading_deg"]), 1)
    n_legs = int(_pick_range(rng, s["n_legs"]))
    spacing = round(_pick_range(rng, s["leg_spacing_m"]), 1)
    # 주행이 지형 밖으로 나가면 빈(검은) 데이터가 생기므로 track 길이를 지형에 맞춘다.
    full_half = _track_half_length(terrain, heading, spacing, n_legs)
    fractions = s.get("track_length_fraction", [1.0])
    if isinstance(fractions, (int, float)):
        track_fraction = float(fractions)
    else:
        track_fraction = float(rng.choice(list(fractions)))
    track_fraction = min(1.0, max(0.05, track_fraction))
    half = max(10.0, round(full_half * track_fraction, 1))
    return {
        "platform": s.get("platform", "ideal"),
        "range_max_m": round(range_max, 1),
        "altitude_m": altitude,                      # 융기부 마루 기준 여유
        "altitude_worst_m": altitude_worst,          # 골짜기 위 실제 고도(최악)
        "terrain_relief_m": round(relief, 2),
        "altitude_ratio": round(ratio, 4),
        **({"_range_clamp_note": rule_note} if rule_note else {}),
        "depression_deg": depression,
        # 기록용: 이 기하에서 빔이 실제 닿는 거리. range_max 보다 커야 정상이다.
        "beam_reach_m": round(reach, 1),
        "survey_heading_deg": heading,
        "n_legs": n_legs,
        "leg_spacing_m": spacing,
        "track_x0_m": -half,
        "track_x1_m": half,
        "track_length_fraction": track_fraction,
        "track_full_length_m": round(2.0 * full_half, 1),
        "range_res_m": rng.choice(s["range_res_m"]),
        # 해상상태: ASV 동요 -> 트랜스듀서 자세/고도 변동 -> 워터폴 왜곡
        "wind_speed_ms": round(rng.uniform(*s.get("wind_speed_ms", [0.1, 0.1])), 2),
    }


def _apply_survey_override(survey, override):
    """사용자가 직접 정한 취득 파라미터를 무작위 표본 위에 덮어쓴다.

    기본 동작은 랜덤 샘플링이다(데이터셋 다양성이 목적). 하지만 웹 UI 에서 센서 값을
    조정해 두고 씬을 생성하면 그 값이 전부 무작위 값으로 덮여, "내가 맞춘 설정으로
    취득"이 불가능했다(2026-08-04 지적). 여기서 덮어쓰면 10% 룰/정격 제한 로직을
    거친 뒤이므로, 사용자가 규칙을 벗어난 값을 넣어도 그대로 존중하고 경고는 UI 가 낸다.
    """
    if not override:
        return survey
    used = []
    for k, v in override.items():
        if v is None or k not in survey:
            continue
        if survey[k] != v:
            used.append(k)
        survey[k] = v
    if used:
        survey["_override_note"] = "사용자 지정 값으로 덮어씀: " + ", ".join(sorted(used))
    return survey


_SCALE_WARNED = set()


def _object_scale(rng, entry, cat_cfg):
    """자산 배율 = (목표 실물 크기 / 모델 실측 최대변) x 개체 변동.

    나눗셈 한 번이고, 분자와 분모의 출처가 다르다.

      분자  목표 실물 크기 [m] — 이 유물·선박이 현실에서 몇 m 인가.
            **자산별** `target_size_m`(카탈로그가 asset_database.csv /
            local_assets.json 에서 병합)을 쓴다. 2026-08-09 이전에는 카테고리 상수
            (예: shipwreck→45 m)를 썼는데 근거가 없었고, 한 카테고리에 10 m 급
            마도선과 60 m 급 대형선이 섞이면 전부 45 m 로 깔렸다.
      분모  모델 실측 최대변 — 이 FBX 가 자기 단위로 얼마인가.
            `measured_max_m`(UE 임포트 로그에서 재서 measure_assets.py 가 병합).
            FBX 는 호스트에서 못 읽어 이 경로 말고는 알 방법이 없다.

    오브젝트 프로젝트 FBX 는 최대변이 ≈1.96 m 로 **정규화**되어 실제 치수가 아니고,
    우리 STL->OBJ 변환본은 실치수(17~67 m)다. 두 종류가 한 카탈로그에 섞여 있으므로
    자산별로 나눠야 둘 다 올바른 크기가 나온다.

    분모가 없으면(아직 UE 임포트 전) 개체 변동만 적용하는데, 그건 **크기가 틀린
    씬**이다. 예전에는 조용히 넘어갔지만 지금은 경고를 찍는다 — measure_assets.py
    를 안 돌린 채 뽑은 매니페스트를 그대로 학습에 쓰는 사고를 막기 위해서다.
    """
    jitter = rng.uniform(*cat_cfg["scale_range"])
    # 자산별 실물 치수 우선, 없으면 카테고리 상수(구 방식) 폴백.
    target = entry.get("target_size_m") or cat_cfg.get("target_size_m")
    measured = entry.get("measured_max_m")
    if not target:
        if entry["id"] not in _SCALE_WARNED:
            _SCALE_WARNED.add(entry["id"])
            print(f"[warn] {entry['id']}: 실물 치수 출처가 없어 배율을 정할 수 없다 "
                  f"— 개체 변동만 적용한다.")
        return jitter
    if not measured or measured <= 0:
        if entry["id"] not in _SCALE_WARNED:
            _SCALE_WARNED.add(entry["id"])
            print(f"[warn] {entry['id']}: 모델 실측값(measured_max_m)이 없다 "
                  f"— UE 임포트 후 scripts/measure_assets.py 를 돌려야 크기가 맞는다.")
        return jitter
    return (target / measured) * jitter


def _sample_placements(rng, cfg, terrain, n):
    """지형 범위 안쪽에 최소 간격을 지켜 n개 위치를 뽑는다. 못 채우면 뽑힌 만큼만 반환."""
    p = cfg["placement"]
    x0, y0, x1, y1 = terrain["extent_m"]
    m = p["margin_m"]
    lo_x, hi_x, lo_y, hi_y = x0 + m, x1 - m, y0 + m, y1 - m
    if lo_x >= hi_x or lo_y >= hi_y:
        return []

    placed = []
    for _ in range(n):
        for _attempt in range(p["max_attempts_per_object"]):
            x = rng.uniform(lo_x, hi_x)
            y = rng.uniform(lo_y, hi_y)
            if all(math.hypot(x - px, y - py) >= p["min_separation_m"] for px, py in placed):
                placed.append((x, y))
                break
    return placed


def _cluster_environment_placements(rng, cfg, terrain, chosen, positions, survey=None):
    """Regroup selected background categories into reproducible natural patches.

    This operates on *logical* placements.  A logical geological placement may
    later expand again through ``small_object_clusters`` when its mesh is under
    one metre.  Target/wreck categories retain the ordinary minimum-separation
    placement and are never moved by this helper.
    """
    rules = cfg.get("category_distribution_clusters") or {}
    if not rules or not positions:
        return positions, []

    p = cfg["placement"]
    x0, y0, x1, y1 = [float(v) for v in terrain["extent_m"]]
    margin = float(p.get("margin_m", 25.0))
    out = list(positions)
    summaries = []
    usable = min(len(chosen), len(out))

    for category, rule in rules.items():
        if not isinstance(rule, dict) or not rule.get("enabled", True):
            continue
        indices = [i for i, (entry, _cat_cfg) in enumerate(chosen[:usable])
                   if entry.get("category") == category]
        if not indices:
            continue

        patch_range = rule.get("patch_count_range", [1, 1])
        patch_count = min(len(indices), rng.randint(int(patch_range[0]),
                                                    int(patch_range[1])))
        radius_range = [float(v) for v in rule.get("radius_m", [5.0, 15.0])]
        max_radius = max(radius_range)
        lo_x, hi_x = x0 + margin + max_radius, x1 - margin - max_radius
        lo_y, hi_y = y0 + margin + max_radius, y1 - margin - max_radius
        if lo_x >= hi_x or lo_y >= hi_y:
            continue

        centres = []
        min_patch_sep = float(rule.get("min_patch_separation_m", max_radius * 1.5))
        corridor_fraction = float(rule.get("survey_corridor_fraction", 0.0))
        corridor_fraction = min(1.0, max(0.0, corridor_fraction))

        def random_centre():
            return rng.uniform(lo_x, hi_x), rng.uniform(lo_y, hi_y), "terrain"

        def corridor_centre():
            if not survey:
                return random_centre()
            heading = math.radians(float(survey.get("survey_heading_deg", 0.0)))
            u = (math.cos(heading), math.sin(heading))
            v = (-math.sin(heading), math.cos(heading))
            n_legs = max(1, int(survey.get("n_legs", 1)))
            spacing = float(survey.get("leg_spacing_m", 0.0))
            leg = rng.randrange(n_legs)
            leg_offset = (leg - (n_legs - 1) / 2.0) * spacing
            t0 = float(survey.get("track_x0_m", 0.0))
            t1 = float(survey.get("track_x1_m", 0.0))
            along = rng.uniform(min(t0, t1), max(t0, t1))
            cross_range = [float(vv) for vv in
                           rule.get("survey_cross_track_m", [6.0, 32.0])]
            cross = rng.uniform(min(cross_range), max(cross_range))
            cross *= -1.0 if rng.random() < 0.5 else 1.0
            total_cross = leg_offset + cross
            cx = along * u[0] + total_cross * v[0]
            cy = along * u[1] + total_cross * v[1]
            return cx, cy, "survey_corridor"

        centre_scopes = []
        for _ in range(patch_count):
            for _attempt in range(int(p.get("max_attempts_per_object", 200))):
                if survey and rng.random() < corridor_fraction:
                    cx, cy, scope = corridor_centre()
                else:
                    cx, cy, scope = random_centre()
                if (lo_x <= cx <= hi_x and lo_y <= cy <= hi_y
                        and all(math.hypot(cx - px, cy - py) >= min_patch_sep
                                for px, py in centres)):
                    centres.append((cx, cy))
                    centre_scopes.append(scope)
                    break
        if not centres:
            continue

        members_per_patch = [0 for _ in centres]
        radii = [rng.uniform(*radius_range) for _ in centres]
        rng.shuffle(indices)
        for seq, index in enumerate(indices):
            patch_i = seq % len(centres)
            cx, cy = centres[patch_i]
            radius = radii[patch_i]
            # A clipped Gaussian gives a dense core and a few edge objects,
            # unlike a uniform rectangle that looks procedurally scattered.
            sigma = radius / 2.5
            ox = max(-radius, min(radius, rng.gauss(0.0, sigma)))
            oy = max(-radius, min(radius, rng.gauss(0.0, sigma)))
            out[index] = (cx + ox, cy + oy)
            members_per_patch[patch_i] += 1

        for patch_i, ((cx, cy), radius) in enumerate(zip(centres, radii)):
            summaries.append({
                "category": category,
                "patch_index": patch_i,
                "center_m": [round(cx, 3), round(cy, 3)],
                "radius_m": round(radius, 3),
                "logical_members": members_per_patch[patch_i],
                "distribution": "clipped_gaussian",
                "placement_scope": centre_scopes[patch_i],
            })
    return out, summaries


def _place_wrecks_in_survey_corridor(rng, cfg, terrain, chosen, positions, survey=None):
    """Move individual GT wrecks into the acquired swath without clustering them.

    Background clutter benefits from Gaussian patches, whereas putting wrecks in
    those same patches would create implausible piles and ambiguous detection
    labels.  This rule instead samples each wreck along an actual survey leg and
    offsets it to port or starboard.  It is opt-in, so ordinary/manual scenes keep
    their existing uniform placement behavior.
    """
    rule = cfg.get("wreck_corridor_distribution") or {}
    if not rule.get("enabled", False) or not survey or not positions:
        return positions, []
    fraction = min(1.0, max(0.0, float(rule.get("survey_corridor_fraction", 0.0))))
    if fraction <= 0.0:
        return positions, []
    x0, y0, x1, y1 = [float(v) for v in terrain["extent_m"]]
    margin = float(cfg.get("placement", {}).get("margin_m", 25.0))
    lo_x, hi_x, lo_y, hi_y = x0 + margin, x1 - margin, y0 + margin, y1 - margin
    usable = min(len(chosen), len(positions))
    indices = [i for i, (entry, _cat_cfg) in enumerate(chosen[:usable])
               if "wreck" in {str(tag) for tag in entry.get("tags", [])}]
    if not indices:
        return positions, []

    heading = math.radians(float(survey.get("survey_heading_deg", 0.0)))
    along_u = (math.cos(heading), math.sin(heading))
    cross_u = (-math.sin(heading), math.cos(heading))
    n_legs = max(1, int(survey.get("n_legs", 1)))
    leg_spacing = float(survey.get("leg_spacing_m", 0.0))
    t0 = float(survey.get("track_x0_m", 0.0))
    t1 = float(survey.get("track_x1_m", 0.0))
    cross_range = [float(v) for v in rule.get("survey_cross_track_m", [8.0, 55.0])]
    cross_lo, cross_hi = min(cross_range), max(cross_range)
    min_sep = float(rule.get("min_separation_m", 35.0))
    attempts = int(cfg.get("placement", {}).get("max_attempts_per_object", 200))
    out = list(positions)
    placed = []
    summaries = []
    for index in indices:
        # A small terrain-wide remainder retains rare off-track targets while the
        # default 95% makes a typical acquired waterfall target-rich.
        if rng.random() > fraction:
            placed.append(out[index])
            summaries.append({"index": index, "placement_scope": "terrain"})
            continue
        for _attempt in range(attempts):
            leg = rng.randrange(n_legs)
            leg_offset = (leg - (n_legs - 1) / 2.0) * leg_spacing
            along = rng.uniform(min(t0, t1), max(t0, t1))
            cross = rng.uniform(cross_lo, cross_hi)
            cross *= -1.0 if rng.random() < 0.5 else 1.0
            total_cross = leg_offset + cross
            px = along * along_u[0] + total_cross * cross_u[0]
            py = along * along_u[1] + total_cross * cross_u[1]
            if not (lo_x <= px <= hi_x and lo_y <= py <= hi_y):
                continue
            if all(math.hypot(px - qx, py - qy) >= min_sep for qx, qy in placed):
                out[index] = (px, py)
                placed.append((px, py))
                summaries.append({"index": index, "placement_scope": "survey_corridor",
                                  "leg": leg, "cross_track_m": round(cross, 3)})
                break
        else:
            # Retain the already valid global position rather than silently
            # dropping a target if an unusually small terrain cannot fit it.
            placed.append(out[index])
            summaries.append({"index": index, "placement_scope": "terrain_fallback"})
    return out, summaries


def _is_small_cluster_member(entry, cfg):
    """Whether an asset must be placed only as part of a resolvable cluster."""
    cc = cfg.get("small_object_clusters") or {}
    if not cc.get("enabled", False):
        return False
    size = float(entry.get("target_size_m") or 0.0)
    return 0.0 < size < float(cc.get("member_max_size_m", 1.0))


def _cluster_offsets(rng, entry, cfg):
    """Generate one logical small-object distribution as many actor offsets.

    The individual meshes keep their documented physical size.  Visibility comes
    from a physically plausible concentration of many returns, not from scaling a
    tiny artefact into a fake giant one.  Returned coordinates are relative to the
    logical cluster centre and are deterministic for the scene seed.
    """
    cc = cfg.get("small_object_clusters") or {}
    size = float(entry.get("target_size_m") or 0.0)
    threshold = float(cc.get("resolution_size_m", 0.15))
    count_range = (cc.get("tiny_count_range") if size < threshold
                   else cc.get("count_range")) or [12, 28]
    count = rng.randint(int(count_range[0]), int(count_range[1]))
    extent = rng.uniform(*[float(v) for v in cc.get("extent_m", [1.5, 4.5])])
    patterns = list(cc.get("patterns") or
                    ["compact", "linear", "ring", "arc", "grid", "paired_rows"])
    pattern = rng.choice(patterns)
    yaw = math.radians(rng.uniform(0.0, 360.0))

    def rotate(x, y):
        return (x * math.cos(yaw) - y * math.sin(yaw),
                x * math.sin(yaw) + y * math.cos(yaw))

    offsets = []
    if pattern == "compact":
        sigma = extent / 4.0
        for _ in range(count):
            # Gaussian patch, clipped so one outlier cannot escape the cluster.
            x = max(-extent/2, min(extent/2, rng.gauss(0.0, sigma)))
            y = max(-extent/2, min(extent/2, rng.gauss(0.0, sigma)))
            offsets.append(rotate(x, y))
    elif pattern == "linear":
        for i in range(count):
            u = -0.5 + (i + rng.uniform(-0.3, 0.3)) / max(count - 1, 1)
            offsets.append(rotate(u * extent, rng.gauss(0.0, extent * 0.055)))
    elif pattern == "ring":
        radius = extent * rng.uniform(0.28, 0.48)
        for i in range(count):
            a = 2 * math.pi * i / count + rng.uniform(-0.12, 0.12)
            r = radius * rng.uniform(0.82, 1.18)
            offsets.append(rotate(r * math.cos(a), r * math.sin(a)))
    elif pattern == "arc":
        radius = extent * rng.uniform(0.35, 0.55)
        span = rng.uniform(math.pi * 0.65, math.pi * 1.35)
        for i in range(count):
            a = -span/2 + span * i / max(count - 1, 1) + rng.uniform(-0.08, 0.08)
            r = radius * rng.uniform(0.85, 1.15)
            offsets.append(rotate(r * math.cos(a), r * math.sin(a)))
    elif pattern == "grid":
        cols = max(2, int(math.ceil(math.sqrt(count))))
        rows = int(math.ceil(count / cols))
        sx = extent / max(cols - 1, 1); sy = extent / max(rows - 1, 1)
        for i in range(count):
            x = (i % cols - (cols - 1) / 2) * sx + rng.uniform(-0.18, 0.18) * sx
            y = (i // cols - (rows - 1) / 2) * sy + rng.uniform(-0.18, 0.18) * sy
            offsets.append(rotate(x, y))
    else:  # paired_rows — two loose parallel lines, common for dumped/laid gear.
        half = max(1, int(math.ceil(count / 2)))
        sep = extent * rng.uniform(0.18, 0.35)
        for i in range(count):
            row = -1 if i % 2 == 0 else 1
            j = i // 2
            u = -0.5 + j / max(half - 1, 1)
            offsets.append(rotate(u * extent + rng.gauss(0, extent * 0.025),
                                  row * sep/2 + rng.gauss(0, extent * 0.035)))
    return pattern, extent, offsets


def sample_scene(seed, cfg, catalog, terrain_id=None, allow_unavailable=False,
                 survey_override=None, verbose=False):
    rng = random.Random(seed)

    # 패키지에 실재하는 지형만 뽑는다. 선언만 된 지형을 뽑으면 seabed_top_m 이 실제
    # 씬과 달라 고도가 통째로 틀어진 데이터가 조용히 생성된다.
    terrains = cfg["terrains"]
    if terrain_id:
        matches = [t for t in terrains if t["id"] == terrain_id]
        if not matches:
            raise SystemExit(f"알 수 없는 terrain id: {terrain_id} "
                             f"(가능: {[t['id'] for t in terrains]})")
        terrain = matches[0]
        if not terrain.get("available", True):
            print(f"[warn] 지형 '{terrain_id}' 은 available=false 입니다. "
                  f"패키지에 없으면 고도가 틀어진 데이터가 나옵니다.", file=sys.stderr)
    else:
        usable = [t for t in terrains if t.get("available", True)]
        if not usable:
            raise SystemExit("available=true 인 지형이 없습니다 (scene_config.json)")
        terrain = rng.choice(usable)

    # Survey geometry is sampled before background placement so production profiles
    # can put most rock/gravel/sediment patches inside the area the sonar will
    # actually observe. Previously thousands of actors were scattered over the full
    # 0.25–1 km² tile and only a few percent happened to fall inside a short swath.
    survey = _apply_survey_override(
        _sample_survey(rng, cfg, terrain, float(cfg.get("vertical_beam_deg", 50.0))),
        survey_override)

    pool = catalog["objects"] if allow_unavailable else [
        o for o in catalog["objects"] if o["available"]
    ]

    # 자산 제외 목록. 웹 UI 에서 메쉬를 눈으로 보고 뺀 것들이 여기 들어온다.
    # (예: 임포트 단위가 깨져 쓸 수 없는 자산)
    excluded = set(cfg.get("excluded_objects", []))
    if excluded:
        pool = [o for o in pool if o["id"] not in excluded]

    # 분해능 미만 자산 제외. small_object_clusters가 켜져 있으면 작은 자산은 단독으로
    # 버리는 대신 아래에서 실제 크기의 복제 actor 군집으로 확장하므로 여기서 제외하지
    # 않는다. 군집을 끈 레거시 설정에서만 이 필터가 작동한다.
    min_size = float(cfg.get("min_object_size_m", 0.0) or 0.0)
    if min_size > 0 and not (cfg.get("small_object_clusters") or {}).get("enabled", False):
        kept = [o for o in pool
                if (o.get("target_size_m") or 0) >= min_size]
        dropped = len(pool) - len(kept)
        if dropped and verbose:
            names = sorted(o["id"] for o in pool if o not in kept)
            print(f"[filter] 분해능 미만({min_size} m) 자산 {dropped}종 제외: "
                  + ", ".join(names[:6]) + (" ..." if dropped > 6 else ""))
        pool = kept

    # 카테고리별로 개수를 뽑고, 그만큼 카탈로그에서 (중복 허용) 고른다.
    chosen = []
    for category, cat_cfg in cfg["object_categories"].items():
        cands = [o for o in pool if o["category"] == category]
        if not cands:
            continue
        count = int(_pick_range(rng, cat_cfg["count_range"]))
        for _ in range(count):
            chosen.append((rng.choice(cands), cat_cfg))

    # 자산별 재질 오버라이드 ("_" 로 시작하는 키는 주석이므로 뺀다)
    mat_override = {k: v for k, v in cfg.get("object_material_overrides", {}).items()
                    if not k.startswith("_")}

    positions = _sample_placements(rng, cfg, terrain, len(chosen))
    positions, environment_patches = _cluster_environment_placements(
        rng, cfg, terrain, chosen, positions, survey=survey)
    positions, wreck_placements = _place_wrecks_in_survey_corridor(
        rng, cfg, terrain, chosen, positions, survey=survey)
    seabed = terrain["seabed_top_m"]
    ground = _terrain_sampler(terrain)          # (x,y) -> 실제 해저면 z

    objects = []
    clusters = []
    for (entry, cat_cfg), (x, y) in zip(chosen, positions):
        if _is_small_cluster_member(entry, cfg):
            pattern, extent, offsets = _cluster_offsets(rng, entry, cfg)
            cluster_id = f"cluster_{seed:06d}_{len(clusters):03d}"
            clusters.append({
                "cluster_id": cluster_id, "catalog_id": entry["id"],
                "category": entry["category"], "pattern": pattern,
                "center_m": [round(x, 3), round(y, 3), round(ground(x, y), 3)],
                "extent_m": round(extent, 3), "members": len(offsets),
                "member_target_size_m": entry.get("target_size_m"),
            })
        else:
            pattern, cluster_id, offsets = None, None, [(0.0, 0.0)]

        for member_index, (ox, oy) in enumerate(offsets):
            px, py = x + ox, y + oy
            # Draw the final pose first.  At spawn time the engine likewise
            # applies this rotation, measures the rotated world-space height,
            # and only then lowers the actor by burial_ratio × that height.
            # Keeping the manifest order aligned with that contract matters
            # once wreck roll/pitch can approach 90 degrees.
            rot = [
                round(rng.uniform(*cat_cfg["roll_range_deg"]), 2),
                round(rng.uniform(*cat_cfg["pitch_range_deg"]), 2),
                round(rng.uniform(*cat_cfg["yaw_range_deg"]), 2),
            ]
            burial = rng.uniform(*cat_cfg["burial_ratio_range"])
            scale = round(_object_scale(rng, entry, cat_cfg), 4)
            tags = _tags_with_material(
                entry["tags"], mat_override.get(entry["id"]) or cat_cfg.get("material"))
            if cluster_id:
                tags = list(tags) + [f"ssscluster:{cluster_id}"]
            obj = {
                "catalog_id": entry["id"], "ue_mesh": entry["ue_mesh"],
                # z follows the real terrain under each member, not only the cluster centre.
                "position_m": [round(px, 3), round(py, 3), round(ground(px, py), 3)],
                "rotation_deg": rot, "scale": scale,
                "burial_ratio": round(burial, 3),
                "burial_ratio_engine": round(
                    _burial_ratio_for_engine(burial, entry.get("measured_bbox_m"),
                                             scale, rot), 4),
                "tags": tags,
            }
            if cluster_id:
                obj.update({"cluster_id": cluster_id, "cluster_pattern": pattern,
                            "cluster_member_index": member_index})
            objects.append(obj)

    # 배율이 검증되지 않은 자산을 **매니페스트 자체에 남긴다**.
    #
    # 콘솔 경고만으로는 웹 UI 나 배치 스윕에서 놓친다. 크기가 틀린 씬은 오류 없이
    # 그럴듯한 이미지를 만들어 내므로, 산출물만 보고는 알 수 없다. 여기 기록해 두면
    # 나중에 매니페스트만 보고도 "이건 UE 임포트 전에 뽑은 것" 이라고 판별된다.
    unverified = sorted({entry["id"] for entry, _cat in chosen
                         if not (entry.get("measured_max_m") or 0) > 0})
    out = {
        "manifest_version": MANIFEST_VERSION,
        "scene_id": f"scene_{seed:06d}",
        "seed": seed,
        "terrain": terrain,
        "objects": objects,
        "survey": survey,
    }
    if unverified:
        out["scale_unverified"] = unverified
        out["_scale_unverified_note"] = (
            "이 자산들은 모델 실측값(measured_max_m)이 없어 배율이 개체 변동만 "
            "적용된 상태다 — 실제 크기가 틀렸을 수 있다. UE 임포트 후 "
            "scripts/measure_assets.py 를 돌리고 매니페스트를 다시 뽑아야 한다.")
    if clusters:
        out["object_clusters"] = clusters
        out["_object_cluster_note"] = (
            "1 m 미만 자산은 단독 배치하지 않고 실제 크기를 유지한 복제 actor 군집으로 "
            "배치했다. object count에는 모든 물리 member가 포함된다.")
    if wreck_placements:
        out["wreck_placements"] = wreck_placements
        out["_wreck_placement_note"] = (
            "wreck 태그 대상의 개별 배치 범위. survey_corridor는 실제 취득 leg의 "
            "좌우 스와스에 배치한 대상이며 terrain/terrain_fallback은 그 외 대상이다.")
    if environment_patches:
        out["environment_object_patches"] = environment_patches
        out["_environment_object_patch_note"] = (
            "암석·퇴적물·생물/인공 클러터의 논리적 배치 중심을 여러 Gaussian 패치로 "
            "묶었다. 1 m 미만 자산은 각 중심에서 small-object member 군집으로 한 번 더 "
            "확장되며, 모든 패치 파라미터는 scene seed로 재현된다.")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="씬 매니페스트 생성")
    ap.add_argument("--n", type=int, default=1, help="생성할 씬 수")
    ap.add_argument("--seed", type=int, default=0, help="시작 시드 (씬마다 +1)")
    ap.add_argument("--out", type=str, default=None, help="출력 .jsonl 경로")
    ap.add_argument("--terrain", type=str, default=None, help="지형 고정 (기본: 랜덤)")
    ap.add_argument("--print", action="store_true", help="첫 씬을 보기 좋게 출력")
    ap.add_argument("--survey-override", default=None,
                    help='취득 파라미터를 고정 (JSON). 예: \'{"range_max_m":90,"altitude_m":12}\'')
    ap.add_argument("--wreck-tilt-deg", type=float, default=None,
                    help="난파선 roll/pitch 랜덤 범위의 최대 절대각(0~90). 이번 생성에만 적용")
    ap.add_argument("--allow-unavailable", action="store_true",
                    help="아직 파일이 없는 자산도 포함 (FBX 도착 전 파이프라인 점검용)")
    args = ap.parse_args(argv)

    cfg = oc.load_config()
    catalog = oc.load_catalog()

    if args.wreck_tilt_deg is not None:
        if not 0.0 <= args.wreck_tilt_deg <= 90.0:
            ap.error("--wreck-tilt-deg는 0~90°여야 합니다")
        for category in (cfg.get("object_categories") or {}).values():
            if "wreck" in {str(tag) for tag in category.get("tags", [])}:
                category["roll_range_deg"] = [-args.wreck_tilt_deg, args.wreck_tilt_deg]
                category["pitch_range_deg"] = [-args.wreck_tilt_deg, args.wreck_tilt_deg]

    scenes = [
        sample_scene(args.seed + i, cfg, catalog, args.terrain, args.allow_unavailable,
                     survey_override=json.loads(args.survey_override)
                     if args.survey_override else None)
        for i in range(args.n)
    ]
    if args.wreck_tilt_deg is not None:
        for scene in scenes:
            scene["manual_wreck_tilt_deg"] = args.wreck_tilt_deg

    if args.print:
        print(json.dumps(scenes[0], indent=2, ensure_ascii=False))

    if args.out:
        out = ROOT / args.out
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w") as f:
            for s in scenes:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")
        n_obj = sum(len(s["objects"]) for s in scenes)
        terrains = sorted({s["terrain"]["id"] for s in scenes})
        print(f"{len(scenes)} 씬 -> {out.relative_to(ROOT)}")
        print(f"  오브젝트 총 {n_obj}개 (씬당 평균 {n_obj / len(scenes):.1f})")
        print(f"  지형: {terrains}")
    elif not args.print:
        print(json.dumps(scenes[0], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
