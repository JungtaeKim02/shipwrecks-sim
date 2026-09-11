"""커스텀 해저 지형 생성 — 파라미터로 만들고, 설치하고, 바로 쓸 수 있게 등록한다.

기존 `make_test_terrain.py` 는 프로파일 2개가 하드코딩돼 있고, 소스 트리에만 파일을
쓰고, `scene_config.json` 등록은 사람이 손으로 복사해야 했다. 그래서 "지형을 하나
새로 만든다"가 여러 수동 단계로 흩어져 있었다. 이 모듈이 그 전부를 담당한다.

핵심: 엔진은 **실행 시점에** 패키지의 `Content/Config/` 에서 씬 JSON 과 지형 CSV 를
읽고(MadoSceneConfig.cpp: ResolveConfigPath / ResolveTerrainCsvPath), 씬 이름을
하드코딩으로 검사하지 않는다. 따라서 그 폴더에 파일만 떨어뜨리면
**C++ 재빌드도, 재패킹도 필요 없다.**

CSV 형식: ix,iy,x_m,y_m,depth_m   (depth_m 양수 = 수심, 해저면 z = -depth_m)
  ix 가 안쪽 루프, iy 가 바깥 루프. x_m 은 ix 증가에 따라, y_m 은 iy 증가에 따라 오름차순.
  ※ 이 정렬이 깨지면 삼각형 winding 이 뒤집혀 지형이 위에서 안 보인다
    (실제로 겪은 사고 — docs/FIELD_IMPLEMENTATION_GUIDE.md §6).

사용:
    python3 scripts/make_terrain.py --name my_field_v1 --preset sand_waves
    python3 scripts/make_terrain.py --name flat_ish --slope-deg 0.3 --roughness-amp 0.2
"""
import argparse
import json
import math
import pathlib
import sys

import numpy as np
import project_paths as pp

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC_TERRAIN = pp.source_config_root() / "mado_terrain"
SRC_SCENES = pp.source_config_root() / "mado_scenes"
SCENE_CONFIG = ROOT / "scripts/scene_config.json"

# 패키지된 시뮬레이터가 실행 중에 읽는 위치. 여기에 없으면 지형이 안 뜬다.
RUNTIME_ROOTS = [
    pp.runtime_config_root(),
]

# ---------------------------------------------------------------------------
# 퇴적물 재질. 값은 이미 씬 파일에 들어 있는 것들만 쓴다(APL-UW TR9407 Ch.IV Table 2
# 계열). 새 숫자를 지어내지 않는다 — 임의 값은 소나 밝기를 근거 없이 바꾼다.
# R^2 은 해수(1024x1500) 대비 강도 반사계수.
# ---------------------------------------------------------------------------
MATERIALS = [
    {"id": "very_fine_silt",  "name": "Very Fine Silt (연니)",   "rho": 1175.0, "c": 1456.0},
    {"id": "soft_mud",        "name": "Soft Mud (연니, 어두움)", "rho": 1146.0, "c": 1459.0},
    {"id": "sandy_silt",      "name": "Sandy Silt / Gravelly Mud", "rho": 1197.1, "c": 1479.8},
    {"id": "coarse_silt",     "name": "Coarse Silt (Mz 4.5)",    "rho": 1195.0, "c": 1506.0},
    {"id": "rubble_shell",    "name": "Rubble + Shell",          "rho": 1224.0, "c": 1506.0},
    {"id": "fine_sand",       "name": "Fine Sand (Mz 2.5)",      "rho": 1298.0, "c": 1564.0},
    {"id": "medium_sand",     "name": "Medium Sand (Hamilton)",  "rho": 1889.3, "c": 1743.7},
    {"id": "gravel",          "name": "Gravel (자갈질, 밝음)",   "rho": 2203.0, "c": 1812.0},
]
WATER_Z = 1024.0 * 1500.0


def material_db():
    out = []
    for m in MATERIALS:
        z = m["rho"] * m["c"]
        r = (z - WATER_Z) / (z + WATER_Z)
        out.append({**m, "impedance": z, "r2": r * r,
                    "r2_db": 10 * math.log10(max(r * r, 1e-12))})
    return out


def _mat(mid):
    m = next((x for x in MATERIALS if x["id"] == mid), None)
    if m is None:
        raise SystemExit(f"알 수 없는 재질 id: {mid} (가능: {[x['id'] for x in MATERIALS]})")
    return m


# ---------------------------------------------------------------------------
# 지형 합성 — 층을 더한다. 각 층은 실제 해저에 존재하는 지형 요소에 대응한다.
# ---------------------------------------------------------------------------
def _gradient_noise(X, Y, cell, seed, octaves=4, gain=0.5, lacunarity=2.0):
    """그래디언트(Perlin) 잡음 + 옥타브 합. 미세 기복(퇴적물 표면 거칠기)용.

    값잡음(value noise)에서 바꿨다(2026-08-04). 값잡음은 격자점에 **스칼라**를 두고
    보간해서, 격자점마다 기울기가 0 이 된다. 그 결과 셀 경계가 평평한 이음매로 남고
    셀 안쪽만 부풀어 **축 정렬 네모 무늬**가 보인다(실측: 셀 경계 기울기가 셀 중앙의
    0.17 배). 취득 이미지에서 배경이 "옛날 게임 텍스처처럼 네모네모"하게 보인 원인이다.

    그래디언트 잡음은 격자점에 **임의 방향 단위벡터**를 두고 오프셋과 내적하므로
    격자점에서 기울기가 0 이 아니고, 특징이 등방적이다. 보간은 quintic fade
    (6t^5-15t^4+10t^3) 로 2차 도함수까지 연속이라 이음매가 남지 않는다.
    """
    rng = np.random.default_rng(seed)
    out = np.zeros_like(X)
    amp, cs, tot = 1.0, float(cell), 0.0
    for _ in range(max(1, octaves)):
        s_oct = int(rng.integers(1, 1 << 30))
        gx = np.floor(X / cs).astype(np.int64)
        gy = np.floor(Y / cs).astype(np.int64)
        fx = X / cs - gx
        fy = Y / cs - gy
        # quintic fade
        u = fx * fx * fx * (fx * (fx * 6 - 15) + 10)
        v = fy * fy * fy * (fy * (fy * 6 - 15) + 10)

        def grad_dot(ix, iy, dx, dy):
            """격자점 (ix,iy) 의 임의 방향 단위벡터와 (dx,dy) 의 내적."""
            k = (ix * 374761393 + iy * 668265263 + s_oct) & 0x7FFFFFFF
            k = (k ^ (k >> 13)) * 1274126177 & 0x7FFFFFFF
            k = k ^ (k >> 16)
            ang = (k & 0xFFFFFF) / 0xFFFFFF * (2.0 * np.pi)
            return np.cos(ang) * dx + np.sin(ang) * dy

        n00 = grad_dot(gx,     gy,     fx,       fy)
        n10 = grad_dot(gx + 1, gy,     fx - 1.0, fy)
        n01 = grad_dot(gx,     gy + 1, fx,       fy - 1.0)
        n11 = grad_dot(gx + 1, gy + 1, fx - 1.0, fy - 1.0)
        nx0 = n00 * (1 - u) + n10 * u
        nx1 = n01 * (1 - u) + n11 * u
        out += amp * (nx0 * (1 - v) + nx1 * v)
        tot += amp
        amp *= gain
        cs /= lacunarity
    # Perlin 2D 의 이론 최대는 sqrt(2)/2. 옥타브 합을 [-1,1] 로 정규화한다.
    return np.clip(out / (tot * 0.7071), -1.0, 1.0)


def synthesize(p, X, Y):
    """파라미터 dict -> 수심 배열 [m] (양수, 클수록 깊음).

    층 구성 (전부 선택적, 진폭 0 이면 꺼짐):
      slope      광역 경사. 대륙붕은 보통 0.02~1° 라 기본값을 그 범위에 둔다.
      sand_wave  사구/모래파. 실제 대륙붕에 흔한 규칙적 기복(파장 10~500 m,
                 파고 0.5~10 m — Ashley 1990 의 dune 분류 범위).
      ridge      능선/골 2D 사인. 방향성이 뚜렷해 파이프라인 확인이 쉽다.
      bowl       분지(양수) 또는 둔덕(음수). 등심선이 동심원.
      channel    수로/골. 한 방향으로 뻗은 가우시안 트로프.
      roughness  프랙탈 미세기복. 소나 입사각을 국소적으로 흔든다.
    """
    d = np.full(X.shape, float(p.get("base_depth_m", 18.0)))

    # 광역 경사
    sd = math.radians(float(p.get("slope_deg", 0.0)))
    sa = math.radians(float(p.get("slope_dir_deg", 0.0)))
    d += math.tan(sd) * (X * math.cos(sa) + Y * math.sin(sa))

    # 사구/모래파
    a = float(p.get("sand_wave_amp_m", 0.0))
    if a:
        lam = max(float(p.get("sand_wave_len_m", 60.0)), 1e-6)
        th = math.radians(float(p.get("sand_wave_dir_deg", 0.0)))
        u = X * math.cos(th) + Y * math.sin(th)
        # 비대칭(가파른 하류면)은 실제 사구의 특징이라 살짝 넣는다.
        ph = 2 * math.pi * u / lam
        d -= a * (np.sin(ph) + 0.25 * np.sin(2 * ph))

    # 능선/골
    a = float(p.get("ridge_amp_m", 0.0))
    if a:
        lx = max(float(p.get("ridge_len_x_m", 280.0)), 1e-6)
        ly = max(float(p.get("ridge_len_y_m", 380.0)), 1e-6)
        d -= a * np.sin(2 * math.pi * X / lx) * np.cos(2 * math.pi * Y / ly)

    # 분지 / 둔덕
    a = float(p.get("bowl_amp_m", 0.0))
    if a:
        r = max(float(p.get("bowl_radius_m", 90.0)), 1e-6)
        cx = float(p.get("bowl_cx_m", 0.0)); cy = float(p.get("bowl_cy_m", 0.0))
        q = ((X - cx) ** 2 + (Y - cy) ** 2) / (r * r)
        d += a * (1.0 - np.exp(-q))

    # 수로
    a = float(p.get("channel_depth_m", 0.0))
    if a:
        w = max(float(p.get("channel_width_m", 30.0)), 1e-6)
        th = math.radians(float(p.get("channel_dir_deg", 0.0)))
        cx = float(p.get("channel_offset_m", 0.0))
        perp = -X * math.sin(th) + Y * math.cos(th) - cx
        d += a * np.exp(-(perp / (w * 0.5)) ** 2)

    # 미세 기복
    a = float(p.get("roughness_amp_m", 0.0))
    if a:
        d += a * _gradient_noise(X, Y, float(p.get("roughness_cell_m", 8.0)),
                              int(p.get("seed", 0)),
                              int(p.get("roughness_octaves", 4)))

    # 수면 위로 솟지 않게. 0.5 m 는 make_test_terrain 과 같은 하한.
    return np.maximum(d, 0.5)


def grid(p):
    x0 = float(p.get("x0_m", -150.0)); x1 = float(p.get("x1_m", 150.0))
    y0 = float(p.get("y0_m", -120.0)); y1 = float(p.get("y1_m", 120.0))
    step = max(float(p.get("cell_m", 1.0)), 0.1)
    nx = max(int(round((x1 - x0) / step)) + 1, 2)
    ny = max(int(round((y1 - y0) / step)) + 1, 2)
    xs = np.linspace(x0, x1, nx)
    ys = np.linspace(y0, y1, ny)
    X, Y = np.meshgrid(xs, ys)            # X[iy, ix]
    return xs, ys, X, Y


def build(p):
    """파라미터 -> (xs, ys, depth[ny,nx]). 미리보기와 실제 생성이 같은 경로를 쓴다."""
    xs, ys, X, Y = grid(p)
    return xs, ys, synthesize(p, X, Y)


# ---------------------------------------------------------------------------
# 씬 JSON
# ---------------------------------------------------------------------------
def scene_json(name, p, note=None):
    base = _mat(p.get("baseline_material", "coarse_silt"))
    soft = _mat(p.get("soft_material", "very_fine_silt"))
    zones = []
    for z in p.get("facies_zones", []):
        m = _mat(z["material"])
        zones.append({
            "evidence": z.get("evidence") or f"사용자 지정 존 ({m['name']})",
            "center_x_m": float(z["cx"]), "center_y_m": float(z["cy"]),
            "yaw_deg": float(z.get("yaw", 0.0)),
            "radius_x_m": float(z["rx"]), "radius_y_m": float(z["ry"]),
            "target_material": {"density_kgm3": m["rho"], "sound_speed_mps": m["c"]},
        })
    x0, x1 = float(p.get("x0_m", -150)), float(p.get("x1_m", 150))
    y0, y1 = float(p.get("y0_m", -120)), float(p.get("y1_m", 120))
    return {
        "scene_name": name,
        "_comment": note or ("scripts/make_terrain.py 로 생성한 합성 지형. 실측이 아니므로 "
                             "학습 데이터에 쓸 때는 합성임을 기록할 것."),
        "_generator_params": p,          # 재현용. 어떤 값으로 만들었는지 남긴다.
        "terrain_data_source": f"{name}_terrain.csv",
        "baseline_material": {"comment": base["name"],
                              "density_kgm3": base["rho"], "sound_speed_mps": base["c"]},
        "soft_mud_baseline_material": {"comment": soft["name"],
                                       "density_kgm3": soft["rho"], "sound_speed_mps": soft["c"]},
        "active_window": {
            "comment": "중앙부를 soft 쪽으로 기울이는 falloff",
            "x_falloff_start_m": abs(x1 - x0) * 0.23, "x_falloff_end_m": abs(x1 - x0) * 0.37,
            "y_falloff_start_m": abs(y1 - y0) * 0.25, "y_falloff_end_m": abs(y1 - y0) * 0.40,
        },
        "blend_strength": float(p.get("blend_strength", 0.85)),
        "texture_noise": {
            "fine_cell_size_m": 3.0, "coarse_cell_size_m": 11.0, "coarse_freq_scale": 0.35,
            "fine_weight": 0.6, "coarse_weight": 0.4,
            "amp_base": 0.05, "amp_zone_weight_scale": 0.06,
            "near_nadir_fade_start_m": 2.0, "near_nadir_fade_end_m": 12.0,
        },
        "domain_warp_cell_size_m": 7.0,
        "domain_warp_fraction_of_radius": 0.22,
        "facies_zones": zones,
        # 오브젝트는 우리 매니페스트(scene_manifest/scene_runtime)로 스폰한다.
        "anchor_stones": [], "reef_edge_cues": [], "wreck_spawns": [],
    }


# ---------------------------------------------------------------------------
# 설치 + 등록
# ---------------------------------------------------------------------------
def write_csv(path, xs, ys, depth):
    ny, nx = depth.shape
    # ix 안쪽 / iy 바깥 순서를 반드시 지킨다(엔진의 격자 폭 자동검출 전제).
    IX, IY = np.meshgrid(np.arange(nx), np.arange(ny))
    X, Y = np.meshgrid(xs, ys)
    cols = np.stack([IX.ravel(), IY.ravel(), X.ravel(), Y.ravel(), depth.ravel()], 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        fh.write("ix,iy,x_m,y_m,depth_m\n")
        np.savetxt(fh, cols, fmt=["%d", "%d", "%.6f", "%.6f", "%.6f"], delimiter=",")


def register(name, p, dmin, dmax):
    """scene_config.json 의 terrains 에 넣거나 갱신한다."""
    import collections
    cfg = json.loads(SCENE_CONFIG.read_text(),
                     object_pairs_hook=collections.OrderedDict)
    entry = collections.OrderedDict([
        ("id", name),
        ("seabed_top_m", round(-dmin, 2)),
        ("seabed_bottom_m", round(-dmax, 2)),
        ("extent_m", [float(p.get("x0_m", -150)), float(p.get("y0_m", -120)),
                      float(p.get("x1_m", 150)), float(p.get("y1_m", 120))]),
        ("heightfield_csv", f"mado_terrain/{name}_terrain.csv"),
        ("facies_config", f"mado_scenes/{name}.json"),
        ("scene_preset", f"{name}.json"),
        ("available", True),
        ("_depth_note", f"scripts/make_terrain.py 생성. 기복 {dmax-dmin:.2f} m "
                        f"(수심 {dmin:.2f}~{dmax:.2f} m). 고도 산정은 최저점 기준."),
    ])
    ts = cfg.setdefault("terrains", [])
    for i, t in enumerate(ts):
        if t.get("id") == name:
            ts[i] = entry
            break
    else:
        ts.append(entry)
    SCENE_CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
    return entry


def install(name, p, note=None, quiet=False):
    """CSV + 씬 JSON 을 소스와 **런타임 패키지 양쪽**에 쓰고 scene_config 에 등록."""
    xs, ys, depth = build(p)
    dmin, dmax = float(depth.min()), float(depth.max())
    sj = scene_json(name, p, note)

    targets = [(SRC_TERRAIN, SRC_SCENES)]
    missing_runtime = []
    for r in RUNTIME_ROOTS:
        if r.exists():
            targets.append((r / "mado_terrain", r / "mado_scenes"))
        else:
            missing_runtime.append(str(r))

    written = []
    for tdir, sdir in targets:
        csv_p = tdir / f"{name}_terrain.csv"
        write_csv(csv_p, xs, ys, depth)
        sdir.mkdir(parents=True, exist_ok=True)
        js_p = sdir / f"{name}.json"
        js_p.write_text(json.dumps(sj, indent=2, ensure_ascii=False) + "\n")
        written += [str(csv_p), str(js_p)]

    entry = register(name, p, dmin, dmax)
    info = {
        "name": name, "entry": entry, "written": written,
        "nx": int(depth.shape[1]), "ny": int(depth.shape[0]),
        "depth_min_m": round(dmin, 3), "depth_max_m": round(dmax, 3),
        "relief_m": round(dmax - dmin, 3),
        "seabed_top_m": round(-dmin, 2), "seabed_bottom_m": round(-dmax, 2),
        "missing_runtime": missing_runtime,
    }
    if not quiet:
        print(f"생성: {name}  격자 {info['nx']}x{info['ny']} = {info['nx']*info['ny']:,} 정점")
        print(f"  수심 {dmin:.2f} ~ {dmax:.2f} m  (기복 {info['relief_m']:.2f} m)")
        print(f"  해저면 z = {-dmax:.2f} ~ {-dmin:.2f} m")
        for w in written:
            print(f"  쓰기 {w}")
        if missing_runtime:
            print(f"  [warn] 런타임 폴더 없음(패키지 미설치?): {missing_runtime}")
        print(f"  scene_config.json 등록 완료 -> 바로 사용 가능 (재빌드/재패킹 불필요)")
    return info


# ---------------------------------------------------------------------------
PRESETS = {
    "ridge_valley": dict(base_depth_m=18.0, ridge_amp_m=6.0, ridge_len_x_m=283.0,
                         ridge_len_y_m=377.0, roughness_amp_m=0.35, slope_deg=0.25),
    "sand_waves":   dict(base_depth_m=22.0, sand_wave_amp_m=2.2, sand_wave_len_m=55.0,
                         sand_wave_dir_deg=20.0, roughness_amp_m=0.15, slope_deg=0.15),
    "bowl":         dict(base_depth_m=14.0, bowl_amp_m=8.0, bowl_radius_m=90.0,
                         roughness_amp_m=0.2),
    "channel":      dict(base_depth_m=16.0, channel_depth_m=7.0, channel_width_m=45.0,
                         channel_dir_deg=25.0, roughness_amp_m=0.25, slope_deg=0.2),
    "near_flat":    dict(base_depth_m=20.0, slope_deg=0.08, roughness_amp_m=0.12,
                         roughness_cell_m=14.0),
}


def main(argv=None):
    ap = argparse.ArgumentParser(description="커스텀 해저 지형 생성·설치·등록")
    ap.add_argument("--name", help="지형 id (파일명에도 쓰임). --list-materials 외에는 필수")
    ap.add_argument("--preset", choices=sorted(PRESETS), default=None)
    for k, d in [("x0-m", -150.0), ("x1-m", 150.0), ("y0-m", -120.0), ("y1-m", 120.0),
                 ("cell-m", 1.0), ("base-depth-m", 18.0), ("slope-deg", 0.0),
                 ("slope-dir-deg", 0.0), ("sand-wave-amp-m", 0.0),
                 ("sand-wave-len-m", 60.0), ("sand-wave-dir-deg", 0.0),
                 ("ridge-amp-m", 0.0), ("ridge-len-x-m", 280.0), ("ridge-len-y-m", 380.0),
                 ("bowl-amp-m", 0.0), ("bowl-radius-m", 90.0),
                 ("channel-depth-m", 0.0), ("channel-width-m", 30.0),
                 ("channel-dir-deg", 0.0), ("roughness-amp-m", 0.0),
                 ("roughness-cell-m", 8.0)]:
        ap.add_argument("--" + k, type=float, default=None)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--baseline-material", default="coarse_silt")
    ap.add_argument("--soft-material", default="very_fine_silt")
    ap.add_argument("--list-materials", action="store_true")
    a = ap.parse_args(argv)

    if a.list_materials:
        for m in material_db():
            print(f"  {m['id']:16s} rho={m['rho']:7.1f} c={m['c']:7.1f}  "
                  f"R^2={m['r2_db']:6.1f} dB   {m['name']}")
        return

    if not a.name:
        ap.error("--name 이 필요합니다")
    p = dict(PRESETS.get(a.preset, {}))
    for k, v in vars(a).items():
        if k in ("name", "preset", "list_materials") or v is None:
            continue
        p[k] = v
    install(a.name, p)


if __name__ == "__main__":
    main()
