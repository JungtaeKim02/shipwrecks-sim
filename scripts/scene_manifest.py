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
            Z[iy, ix] = -raw["depth_m"]
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
    if not desired or not bbox_m or len(bbox_m) < 3:
        return desired
    hz_local = abs(float(bbox_m[2])) * abs(scale)
    if hz_local <= 1e-9:
        return desired
    r = [math.radians(v) for v in (rot_deg or [0, 0, 0])]
    cx, sx = math.cos(r[0]), math.sin(r[0])
    cy, sy = math.cos(r[1]), math.sin(r[1])
    cz, sz = math.cos(r[2]), math.sin(r[2])

    row_z = (-sy, cy * sx, cy * cx)
    h = [abs(float(b)) * abs(scale) / 2.0 for b in bbox_m]
    hz_world = 2.0 * sum(abs(row_z[k]) * h[k] for k in range(3))
    return min(desired * hz_world / hz_local, 1.0)


def _track_half_length(terrain, heading_deg, spacing, n_legs, margin=8.0):
    x0, y0, x1, y1 = terrain["extent_m"]
    hx = min(abs(x0), abs(x1))
    hy = min(abs(y0), abs(y1))

    h = math.radians(heading_deg)
    ch, sh = abs(math.cos(h)), abs(math.sin(h))
    off = (n_legs - 1) / 2.0 * spacing
    lim = []
    if ch > 1e-9:
        lim.append((hx - margin - off * sh) / ch)
    if sh > 1e-9:
        lim.append((hy - margin - off * ch) / sh)
    return max(10.0, round(min(lim))) if lim else 10.0


def _tags_with_material(tags, material):
    out = [t for t in tags if not str(t).startswith("sssmat:")]
    if material:
        out.append(f"sssmat:{material}")
    elif len(out) != len(tags):
        return list(tags)
    return out


def _sample_survey(rng, cfg, terrain, vertical_beam_deg=50.0):
    s = cfg["survey_sampling"]
    range_max = float(_pick_range(rng, s["range_max_m"]))

    ratio = rng.uniform(*s["altitude_ratio"])
    altitude = round(range_max * ratio, 2)















    lo_r, hi_r = float(s["altitude_ratio"][0]), float(s["altitude_ratio"][1])
    relief = abs(float(terrain.get("seabed_bottom_m", terrain["seabed_top_m"]))
                 - float(terrain["seabed_top_m"]))
    if relief > 0:
        altitude = round(max(altitude, relief), 2)
        range_max = (altitude + relief) / hi_r
        ratio = altitude / range_max
    altitude_worst = round(altitude + relief, 2)













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









    half_vb = vertical_beam_deg / 2.0
    depression = round(rng.uniform(*s["depression_deg"]), 1)
    lo = max(depression - half_vb, 0.1)
    reach = altitude / math.sin(math.radians(lo))

    heading = round(rng.uniform(*s["survey_heading_deg"]), 1)
    n_legs = int(_pick_range(rng, s["n_legs"]))
    spacing = round(_pick_range(rng, s["leg_spacing_m"]), 1)

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
        "altitude_m": altitude,
        "altitude_worst_m": altitude_worst,
        "terrain_relief_m": round(relief, 2),
        "altitude_ratio": round(ratio, 4),
        **({"_range_clamp_note": rule_note} if rule_note else {}),
        "depression_deg": depression,

        "beam_reach_m": round(reach, 1),
        "survey_heading_deg": heading,
        "n_legs": n_legs,
        "leg_spacing_m": spacing,
        "track_x0_m": -half,
        "track_x1_m": half,
        "track_length_fraction": track_fraction,
        "track_full_length_m": round(2.0 * full_half, 1),
        "range_res_m": rng.choice(s["range_res_m"]),

        "wind_speed_ms": round(rng.uniform(*s.get("wind_speed_ms", [0.1, 0.1])), 2),
    }


def _apply_survey_override(survey, override):
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
    jitter = rng.uniform(*cat_cfg["scale_range"])

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


            placed.append(out[index])
            summaries.append({"index": index, "placement_scope": "terrain_fallback"})
    return out, summaries


def _is_small_cluster_member(entry, cfg):
    cc = cfg.get("small_object_clusters") or {}
    if not cc.get("enabled", False):
        return False
    size = float(entry.get("target_size_m") or 0.0)
    return 0.0 < size < float(cc.get("member_max_size_m", 1.0))


def _cluster_offsets(rng, entry, cfg):
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
    else:
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





    survey = _apply_survey_override(
        _sample_survey(rng, cfg, terrain, float(cfg.get("vertical_beam_deg", 50.0))),
        survey_override)

    pool = catalog["objects"] if allow_unavailable else [
        o for o in catalog["objects"] if o["available"]
    ]



    excluded = set(cfg.get("excluded_objects", []))
    if excluded:
        pool = [o for o in pool if o["id"] not in excluded]




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


    chosen = []
    for category, cat_cfg in cfg["object_categories"].items():
        cands = [o for o in pool if o["category"] == category]
        if not cands:
            continue
        count = int(_pick_range(rng, cat_cfg["count_range"]))
        for _ in range(count):
            chosen.append((rng.choice(cands), cat_cfg))


    mat_override = {k: v for k, v in cfg.get("object_material_overrides", {}).items()
                    if not k.startswith("_")}

    positions = _sample_placements(rng, cfg, terrain, len(chosen))
    positions, environment_patches = _cluster_environment_placements(
        rng, cfg, terrain, chosen, positions, survey=survey)
    positions, wreck_placements = _place_wrecks_in_survey_corridor(
        rng, cfg, terrain, chosen, positions, survey=survey)
    seabed = terrain["seabed_top_m"]
    ground = _terrain_sampler(terrain)

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
