"""정식 SSS 취득 — config 기반. ASV 하단부(수면)에 2 트랜스듀서, EdgeTech 2205 계열.

설정: scripts/sss_config.json (또는 CLI 로 덮어쓰기). 예:
  python3 scripts/acquire_sss.py asv
  python3 scripts/acquire_sss.py ideal --depression_deg 30 --altitude_m 10
  DISPLAY=:0 python3 scripts/view_survey.py asv --tvg false --slant_range_correction true --range_res_m auto

가변 파라미터(빔폭/depression/고도/주파수/대역폭/해상도/range 등)와 보정 on/off(빔패턴/TVG/
흡수/slant보정/강도정규화)를 config 로 조절. 산출 워터폴 상단에 설정값+취득시간을 라벨링.

해상도(ai4shipwrecks §3.4): R_x=R·sin(θ_h) (세로), R_y=c/2BW (가로) 산출·표기.
후처리(§3.6): TVG(거리보정), 강도 정규화((x-μ)/σ). 논문은 slant-range 보정 안 함
  -> 기본 slant 유지(중앙=수주=검은 나디르밴드). 원하면 slant_range_correction 로 켬.
좌표: 센서는 옆-아래(depression). Elevation=수직부채꼴(across), Azimuth=along-track(θ_h).
"""
import sys, os, json, pathlib, datetime, time, re
import numpy as np, holoocean
import sss_common as sc

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW, PROC, IMG = ROOT/"data/raw", ROOT/"data/processed", ROOT/"data/images"
for _d in (RAW, PROC, IMG):
    _d.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = pathlib.Path(__file__).resolve().parent/"sss_config.json"

# 해저 윗면 z [m]. 씬마다 다르므로 config 키(seabed_top_m)이며, 매니페스트가 있으면
# 그쪽 terrain.seabed_top_m 이 우선한다. 아래 상수는 config 가 없을 때의 폴백일 뿐이다.
SEABED_TOP_DEFAULT = -20.0
SENSOR_Z_DEFAULT = -0.5      # 플랫폼 기준 트랜스듀서 마운트 z [m] (음수=아래)
MIN_SENSOR_DEPTH = 0.3       # 수면 바로 아래 최소 잠김 [m]
MIN_CLEARANCE = 1.0          # 해저와의 최소 간격 [m]


def sensor_z(cfg):
    """플랫폼(수면 z=0) 기준 트랜스듀서 마운트 깊이 [m].

    ASV 선체에 직접 붙이면 수면 바로 아래(-0.5m)로 고정되지만, 실제 사이드스캔은
    토우피시를 케이블로 내려 고도를 조절한다. 여기서는 그 토우피시를 ASV 아래
    매달린 트랜스듀서로 모사하므로, altitude_m 을 주면 그 고도가 되도록 마운트
    깊이를 역산한다. ASV 자체는 수면에 남아 파도의 영향을 그대로 받는다.
    """
    if "sensor_z_m" in cfg:                       # 직접 지정하면 그대로 쓴다
        z = float(cfg["sensor_z_m"])
    elif "altitude_m" in cfg:
        seabed = float(cfg.get("seabed_top_m", SEABED_TOP_DEFAULT))
        z = float(cfg["altitude_m"]) + seabed     # 예: alt 10, seabed -20 -> -10
    else:
        z = SENSOR_Z_DEFAULT
    seabed = float(cfg.get("seabed_top_m", SEABED_TOP_DEFAULT))
    # 수면 위로 나오거나 해저를 뚫지 않도록 클램프
    return min(-MIN_SENSOR_DEPTH, max(seabed + MIN_CLEARANCE, z))


def base_altitude(cfg):
    """실제 취득 고도 [m] = 해저 윗면에서 트랜스듀서까지."""
    return abs(float(cfg.get("seabed_top_m", SEABED_TOP_DEFAULT))) + sensor_z(cfg)


def range_reference_altitude(cfg):
    """범위/ray/crop 계산에 쓰는 최대 해저 간격 [m].

    평탄 지형은 base_altitude와 같다. 기복 지형의 대규모 취득은 센서와 지형
    마루 사이의 최소 고도만으로 range를 정하면 골짜기 해저가 range 밖으로
    잘린다. 그 경우 매니페스트가 ``range_reference_altitude_m``에 가장 낮은
    해저까지의 보수적 간격을 명시한다. 센서의 실제 z 위치는 바꾸지 않는다.
    """
    return float(cfg.get("range_reference_altitude_m", base_altitude(cfg)))


# --------------------------- config 로드/파생 ---------------------------
def _coerce(val, ref):
    if isinstance(ref, bool):
        return str(val).strip().lower() in ("1", "true", "yes", "on")
    if isinstance(ref, (int, float)) and not isinstance(ref, bool):
        try: return float(val)
        except ValueError: return val
    if isinstance(ref, (list, tuple)):
        # 리스트 값(current_ms 등)은 JSON 으로 받는다. 이게 없으면 문자열이 그대로
        # 들어가 set_ocean_currents 에서 터진다 — 웹 UI 가 해류를 넘길 수 있게 필요하다.
        if isinstance(val, (list, tuple)):
            return list(val)
        try:
            out = json.loads(val)
            return out if isinstance(out, list) else ref
        except (ValueError, TypeError):
            return ref
    return val


def load_config(argv):
    cfg = json.loads(CONFIG_PATH.read_text())
    args = argv[1:]
    explicit = set()          # CLI 로 직접 준 키. 매니페스트보다 우선한다.
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("--"):
            key = a[2:]
            if "=" in key:
                key, v = key.split("=", 1); i += 1
            else:
                v = args[i + 1] if i + 1 < len(args) else "true"; i += 2
            cfg[key] = _coerce(v, cfg.get(key, v))
            explicit.add(key)
        else:
            cfg["platform"] = a; i += 1
            explicit.add("platform")

    # --manifest <path> [--manifest_index N]: 씬 매니페스트를 읽어 지형/주행 파라미터를
    # 덮어쓰고, 오브젝트 목록을 cfg["_scene"] 으로 실어 보낸다(run() 에서 런타임 스폰).
    #
    # 우선순위: CLI > 매니페스트 > sss_config.json.
    # 매니페스트가 CLI 를 덮어쓰면 디버깅 중 파라미터를 바꿔볼 수 없다(실제로
    # --platform ideal 을 줬는데 매니페스트의 asv 가 이겨서 원인 추적이 어긋난 적 있음).
    mpath = cfg.pop("manifest", None)
    tname = cfg.pop("terrain", None)
    explicit.discard("manifest"); explicit.discard("manifest_index")
    explicit.discard("terrain")

    # --terrain <id>: 매니페스트 없이 지형만 지정한다.
    #
    # 2026-08-03: 이 경로가 없어서, 씬 생성을 거치지 않고 취득/미리보기를 돌리면
    # **지형이 통째로 안 올라갔다**. 지형 정보가 매니페스트에만 실려 있었기 때문이다.
    # 그 상태로 flat_seabed 까지 꺼져 있으면 빈 물속을 찍게 된다(실측: 밝기 21).
    # 여기서 scene_config.json 의 지형 항목을 그대로 _scene 에 실어, 아래 매니페스트
    # 경로와 **같은 배선**(프리셋 env, flat_seabed, seabed_top_m)을 타게 한다.
    if tname and not mpath:
        tcfg = json.loads((pathlib.Path(__file__).resolve().parent
                           / "scene_config.json").read_text())
        ent = next((t for t in tcfg.get("terrains", []) if t.get("id") == tname), None)
        if ent is None:
            raise SystemExit(f"알 수 없는 terrain id: {tname} "
                             f"(가능: {[t['id'] for t in tcfg.get('terrains', [])]})")
        if "seabed_top_m" not in explicit:
            cfg["seabed_top_m"] = float(ent["seabed_top_m"])
        if "flat_seabed" not in explicit:
            cfg["flat_seabed"] = not bool(ent.get("heightfield_csv"))
        cfg["_scene"] = {"scene_id": f"terrain_only:{tname}", "terrain": ent,
                         "objects": []}
        print(f"[terrain] {tname} (오브젝트 없음, 지형만)")
    elif tname and mpath:
        # 지형은 오브젝트 배치 좌표·고도의 기준이라 매니페스트와 떼어 바꾸면
        # 오브젝트가 해저를 뚫거나 뜬다. 여기서만 CLI 우선 규칙의 예외를 둔다.
        print(f"[warn] --terrain {tname} 는 매니페스트가 있을 때 무시됩니다 "
              f"(지형과 오브젝트 배치는 함께 정해집니다).")

    if mpath:
        import scene_runtime
        scene = scene_runtime.load_manifest_line(mpath, int(cfg.pop("manifest_index", 0)))
        if "seabed_top_m" not in explicit:
            cfg["seabed_top_m"] = float(scene["terrain"]["seabed_top_m"])
        # 지형 heightfield 가 있는 씬에 평탄 해저까지 스폰하면, 평판이 마루 높이
        # (seabed_top_m)에 깔려 지형과 오브젝트를 통째로 덮어버린다. 소나는 그 평판만
        # 보게 되어 어떤 씬을 써도 똑같이 밋밋한 이미지가 나온다(2026-07-31 발견).
        if "flat_seabed" not in explicit:
            cfg["flat_seabed"] = not bool(scene["terrain"].get("heightfield_csv"))
        overridden = []
        for k, v in scene.get("survey", {}).items():
            # 기록용 메타데이터. 취득 파라미터가 아니므로 cfg 로 넘기지 않는다.
            if k in ("altitude_ratio", "altitude_worst_m", "terrain_relief_m",
                     "beam_reach_m", "range_envelope_source",
                     "range_footprint_samples", "range_far_depression_deg"):
                continue
            if k in explicit:                   # CLI 가 이긴다
                overridden.append(k)
                continue
            cfg[k] = v
        cfg["_scene"] = scene
        print(f"[manifest] {scene['scene_id']} 적용 — 지형 {scene['terrain']['id']}, "
              f"오브젝트 {len(scene['objects'])}개"
              + (f" (CLI 우선: {', '.join(overridden)})" if overridden else ""))
    return cfg


def derive(cfg):
    c = float(cfg["sound_speed_ms"]); bw = float(cfg["bandwidth_khz"]) * 1e3
    cfg["_Rx_m"], cfg["_Ry_m"] = sc.resolution_xy(
        cfg["range_max_m"], cfg["horizontal_beam_deg"], c, bw)
    rr = cfg["range_res_m"]
    cfg["_range_res_m"] = cfg["_Ry_m"] if str(rr).lower() == "auto" else float(rr)
    # c/(2B) is the compressed-pulse (physics) resolution; RangeRes is the stored
    # output sample spacing. A coarser grid is legitimate decimation, but it cannot
    # resolve two targets separated by c/(2B). Keep both numbers in session metadata
    # and say so explicitly instead of silently calling the pixel pitch "resolution".
    ratio = cfg["_range_res_m"] / max(cfg["_Ry_m"], 1e-12)
    cfg["_range_sample_to_intrinsic_ratio"] = ratio
    if ratio > 1.25:
        print(f"[range] intrinsic c/(2B)={cfg['_Ry_m']:.4f} m, output spacing="
              f"{cfg['_range_res_m']:.4f} m ({ratio:.1f}x coarser; decimated output)",
              flush=True)
    elif ratio < 0.8:
        print(f"[range] output spacing {cfg['_range_res_m']:.4f} m oversamples the "
              f"intrinsic c/(2B)={cfg['_Ry_m']:.4f} m response", flush=True)
    # Seed 0 means a fresh realization per acquisition, but port and starboard must
    # still see the same world-anchored seabed field. Resolve it once here and pass
    # the same non-zero seed to both engine sensors.
    seed = int(cfg.get("speckle_seed", 0))
    if seed == 0:
        seed = int(np.random.SeedSequence().generate_state(1)[0] & 0x7fffffff) or 1
    cfg["_resolved_speckle_seed"] = seed
    return cfg


def coherent_signal_model(cfg):
    return str(cfg.get("signal_model", "coherent")).lower() in {
        "coherent", "coherent_field", "coherent-field-v3", "coherent-cell-v3.1"
    }


# --------------------------- 시나리오/센서 ---------------------------
def elev_ray_res_deg(cfg):
    """수직 레이 간격 [deg]. 최대거리에서도 range bin 이 비지 않도록 정한다.

    2026-07-31 수정. 이전 공식은 `arcsin(range_res / range_max)` 였는데, 이건 빔
    **호(arc)를 따라 잰** 간격이지 해저면에 찍히는 간격이 아니다. 고도 h 에서
    복각 θ 로 쏜 레이의 경사거리는 R = h/sinθ 이므로

        dR/dθ = -h·cosθ / sin²θ  ->  ΔR = R·cot(θ)·Δθ

    즉 실제 간격에는 **cot(θ)** 배율이 붙는다. 원거리는 복각이 얕아 cot 이
    폭발하므로, 옛 공식은 원거리를 심하게 undersample 했다.

    실측(고도 10m, rmax 90m, rres 0.15m, 600 ping)으로 확인한 bin 충전율:
        경사거리   옛공식 예측 충전율   실측
          20 m         100 %          100 %
          40 m          58 %           46 %
          60 m          25 %           32 %
          90 m          11 %           14 %
    원거리 bin 의 90% 가 반사 없는 빈 칸(= 노이즈 바닥)이 되어, 이미지 절반이
    노이즈로 채워지고 정규화 통계까지 오염돼 전체가 균일한 회색으로 보였다.

    올바른 조건은 가장 얕은 복각 θmin = arcsin(h/rmax) 에서 ΔR ≤ range_res:
        Δθ ≤ range_res · tan(θmin) / range_max
    평탄 지형에서는 h=base_altitude()를 쓴다. 기복 지형의 production 취득은
    가장 낮은 해저까지의 간격을 ``range_reference_altitude_m``으로 넘긴다.
    이는 실제 센서 고도를 바꾸는 값이 아니라, 더 깊은 해저까지 확장한 range의
    최원거리 bin을 빠뜨리지 않기 위한 수치 샘플링 기준이다.
    """
    rmax = float(cfg["range_max_m"]); rres = float(cfg["_range_res_m"])
    h = range_reference_altitude(cfg)
    th_min = np.arcsin(np.clip(h / rmax, 1e-6, 1.0))
    d_th = np.rad2deg(rres * np.tan(th_min) / rmax)
    ray_span = float(cfg.get("vertical_ray_span_deg", cfg["vertical_beam_deg"]))
    n = int(np.ceil(ray_span / d_th)) + 1
    cap = int(cfg.get("elev_ray_max", 8000))
    if n > cap:
        # 레이를 무한정 늘릴 수는 없다. 상한에 걸리면 '어디부터 sparse 해지는지'를
        # 알려서, range_max 를 줄이거나 고도를 올리는 판단을 사용자가 하게 한다.
        d_th = ray_span / (cap - 1)
        th_ok = np.arctan(np.deg2rad(d_th) * rmax / rres)
        r_ok = h / np.sin(th_ok) if np.sin(th_ok) > 0 else rmax
        print(f"[ray] 레이 상한 {cap}개에 걸림 — 조밀 샘플링은 약 {r_ok:.0f} m 까지"
              f" (range_max {rmax:.0f} m). range_max 를 줄이거나 고도를 올릴 것.")
        return d_th
    print(f"[ray] 고도 {h:.1f} m, 최대거리 {rmax:.0f} m, ray span {ray_span:g}° "
          f"-> 수직 레이 간격 {d_th:.4f}° (레이 {n}개)")
    return float(d_th)


def azim_ray_res_deg(cfg):
    """Along-track beam sampling for coherent aperture integration.

    The old fixed ``Azimuth/2`` rule always produced only three rays. In coherent
    mode that is too sparse to integrate the world scattering field across the
    narrow-beam footprint. Sample the far-range footprint no coarser than the
    configured scatter correlation length, with an explicit ray-count guard.
    """
    beam = float(cfg["horizontal_beam_deg"])
    if not coherent_signal_model(cfg):
        return beam / 2.0
    spacing = float(cfg.get("scatter_correlation_length_m", 0.05))
    rmax = float(cfg["range_max_m"])
    desired = np.rad2deg(np.arctan2(spacing, rmax))
    n = max(3, int(np.ceil(beam / max(desired, 1e-8))) + 1)
    cap = max(3, int(cfg.get("azimuth_ray_max", 65)))
    if n > cap:
        n = cap
        print(f"[ray] along-track coherent ray cap {cap} 적용", flush=True)
    res = beam / (n - 1)
    print(f"[ray] along-track footprint sampling {res:.4f}° ({n} rays, "
          f"target spacing {spacing:.3f}m at {rmax:.1f}m)", flush=True)
    return float(res)


def sensor_cfg(cfg):
    rmax = cfg["range_max_m"]; rres = cfg["_range_res_m"]
    elev_ray_res = elev_ray_res_deg(cfg)
    azim_ray_res = azim_ray_res_deg(cfg)
    return {"RangeMin": cfg["range_min_m"], "RangeMax": rmax, "RangeRes": rres,
            "Azimuth": cfg["horizontal_beam_deg"],
            "AzimuthRayRes": azim_ray_res,
            # Cast low-energy shoulders beyond the nominal -3 dB width. The engine
            # still evaluates directivity with VerticalBeamwidthDeg, so widening
            # numerical support does not redefine the physical transducer beam.
            "Elevation": float(cfg.get("vertical_ray_span_deg",
                                       cfg["vertical_beam_deg"])),
            "VerticalBeamwidthDeg": cfg["vertical_beam_deg"],
            "ElevationRayRes": elev_ray_res,
            "BeamPattern": bool(cfg["beam_pattern"]),
            "IncidenceCosine": bool(cfg.get("incidence_cosine", True)),
            "SpeckleSpatialMode": str(cfg.get("speckle_spatial_mode", "world")),
            # 2차 반사(멀티패스). HoloOcean 소나 논문 III-B Algorithm 1.
            "MultiPath": bool(cfg.get("multipath", False)),
            "SemanticLabels": bool(cfg.get("gt_labels", True)),   # 이진 GT 마스크 채널
            "LabelTag": str(cfg.get("gt_label_tag", "wreck")),    # 씬에서 난파선에 붙인 태그
            # complex-power-v2: 엔진은 fused-bin mean power 와 coherent speckle 만
            # 만든다. 수신기 잡음은 전송손실 뒤 sss_common.process_side 에서 복소
            # Gaussian 으로 합치므로 레거시 엔진 AddSigma 는 항상 0이다.
            "AddSigma": 0.0,
            "MultSigma": float(cfg.get("mult_sigma", 0.5)),
            # diffuse power fraction. 1=완전발달 CN(0,1), 0=결정론적 coherent power.
            "SpeckleStrength": float(cfg.get("speckle_strength", 1.0)),
            "SpeckleDistribution": str(
                cfg.get("speckle_distribution", "complex_gaussian")),
            # 스페클 난수 시드. 0 이면 시드 없음(비결정적 — 기존 동작).
            # A/B 비교에서는 반드시 0 이 아닌 같은 값을 줘야 한다.
            "SpeckleSeed": int(cfg.get("_resolved_speckle_seed",
                                        cfg.get("speckle_seed", 0))),
            # coherent-field-v3 emits pre-propagation I/Q. The client then applies
            # TL, receiver noise, chirp matched filtering and square-law detection.
            "SignalModel": str(cfg.get("signal_model", "coherent")),
            "FrequencyKhz": float(cfg["frequency_khz"]),
            "ScatterCorrelationLengthM": float(
                cfg.get("scatter_correlation_length_m", 0.05)),
            "TextureShape": float(cfg.get("texture_shape", 0.0)),
            "TextureCorrelationLengthM": float(
                cfg.get("texture_correlation_length_m", 1.0)),
            "WaterDensity": cfg["water_density"], "WaterSpeedSound": cfg["sound_speed_ms"]}


ASV_SPEED_MS = 1.47        # SurfaceVessel PD 정상항주 속도(실측: 0.0245 m/tick @60Hz)
TICKS_PER_SEC = 60
_HZ_DIVISORS = (1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30, 60)   # ticks_per_sec(60)의 약수


def ping_hz(cfg):
    """소나 핑 레이트[Hz]. 실제 SSS 처럼 '일정 간격으로' 핑하도록 이동속도와 track_dx 에 맞춘다.
    이걸 안 주면 물리 틱마다(60Hz) 소나를 재계산해 asv 취득이 수 배 느려진다.
    ideal(텔레포트)은 매 틱 1핑이므로 제한하지 않음(None)."""
    if cfg["platform"] == "ideal":
        return None
    need = ASV_SPEED_MS / float(cfg["track_dx_m"])       # 초당 필요한 핑 수
    for d in _HZ_DIVISORS:                               # 필요치 이상 중 가장 작은 값
        if d >= need:
            return d
    return TICKS_PER_SEC


def sensors(cfg, viz=False):
    dep = cfg["depression_deg"]
    hz = ping_hz(cfg)
    def one(name, yaw):
        c = sensor_cfg(cfg)
        # Independent-per-cell control must not accidentally mirror the same RNG
        # stream on port and starboard. World mode intentionally shares a seed so
        # the same world patch has the same realization from either transducer.
        if (str(cfg.get("speckle_spatial_mode", "world")).lower()
                in ("independent", "random") and name == "stbd"):
            c["SpeckleSeed"] = int(c["SpeckleSeed"]) + 1
        if viz:
            c["ViewRegion"] = True
        s = {"sensor_type": "RaycastSidescanSonar", "sensor_name": name,
             "location": [0, 0, sensor_z(cfg)], "rotation": [0, dep, yaw], "configuration": c}
        if hz is not None:
            s["Hz"] = hz                                 # -> TicksPerCapture = 60/Hz
        return s
    return [one("port", 90), one("stbd", -90),
            {"sensor_type": "PoseSensor", "sensor_name": "pose"}]


def scenario(cfg, viz=False):
    plat = cfg["platform"]
    legs = legs_layout(cfg)
    p0, _, yaw0 = legs[0]                              # 첫 leg 시작점/방위에 스폰(초기 전이 최소화)
    x0, y0 = float(p0[0]), float(p0[1])
    if plat == "ideal":
        ag = {"agent_name": "plat", "agent_type": "HoveringAUV", "sensors": sensors(cfg, viz),
              "control_scheme": 0, "location": [x0, y0, 0], "rotation": [0, 0, yaw0]}
        return {"name": "sss_ideal", "world": "ExampleLevel", "package_name": "TestWorlds",
                "main_agent": "plat", "ticks_per_sec": 60, "agents": [ag]}
    ag = {"agent_name": "plat", "agent_type": "SurfaceVessel", "sensors": sensors(cfg, viz),
          "control_scheme": 1, "location": [x0, y0, 0], "rotation": [0, 0, yaw0]}
    # 파도: ASV 가 수면에서 흔들리며 트랜스듀서 자세/고도가 미세하게 변한다.
    # 이 동요가 워터폴에 실제 취득과 같은 왜곡을 만든다 -> 해상상태를 파라미터로 노출.
    return {"name": "sss_asv", "world": "ExampleLevel", "package_name": "TestWorlds",
            "main_agent": "plat", "ticks_per_sec": 60,
            "fft_waves": {"wind_speed": float(cfg.get("wind_speed_ms", 0.1))},
            "agents": [ag]}


# --------------------------- 취득 ---------------------------
def split_channels(buf, gt, coherent=False):
    """센서 버퍼 -> (measurement, label) 단일측 trace.

    Every channel has the mirrored ``2*RB`` layout. Legacy/intensity mode has one
    power channel; coherent-field-v3 has I then Q and returns a complex trace.
    Semantic GT, when enabled, is always the final channel.
    """
    buf = np.asarray(buf, dtype=np.float32)
    channels = (2 if coherent else 1) + (1 if gt else 0)
    if len(buf) % channels:
        raise ValueError(f"sensor buffer length {len(buf)} not divisible by {channels} channels")
    channel_len = len(buf) // channels
    if coherent:
        i = side_trace(buf[:channel_len], clip=False)
        q = side_trace(buf[channel_len:2 * channel_len], clip=False)
        measurement = (i + 1j * q).astype(np.complex64)
        label_offset = 2 * channel_len
    else:
        measurement = side_trace(buf[:channel_len])
        label_offset = channel_len
    label = side_trace(buf[label_offset:label_offset + channel_len], clip=False) if gt else None
    return measurement, label


def side_trace(buf, clip=True):
    """한 engine channel의 mirrored 2*RangeBins 출력 -> 단일측 range trace.

    엔진이 `fused-bin-v1` 부터 **두 반쪽에 같은 최종값을 미러링**한다. 현재
    `complex-power-v2`도 이 버퍼 계약을 그대로 유지하므로 한쪽만
    읽으면 되고, 더하면 안 된다.

    왜 바뀌었나: `h<0` / `h>=0` 두 반쪽은 서로 다른 수신기가 아니라 **하나의 수평 빔을
    수치적으로 샘플링한 내부 ray group** 이다(azimuth -0.13 / 0 / +0.13 deg, 세 레이의
    지면 이격이 항상 along-track 해상도 셀 이내). 예전에는 반쪽마다 독립 speckle+가산
    노이즈를 적용한 뒤 여기서 더해서, 선형 평균이 2배(+3.01 dB)가 되고 speckle
    contrast 가 이론값의 1/sqrt(2) 로 줄었다. 지금은 엔진이 합친 뒤 확률 모델을 한 번만
    적용했다. 현재는 엔진에서 `I_signal = I0*|sqrt(1-s)+sqrt(s)G|^2`를 한 번
    계산하고, 수신기 잡음은 전송손실 뒤 후처리에서 복소 신호에 한 번 합산한다.

    coherent-field-v3에서는 이 함수를 I와 Q 채널에 각각 적용한 뒤 복소 trace로
    조립한다. 각 채널의 2*RangeBins 미러 계약 자체는 바뀌지 않는다.
    """
    RB = len(buf) // 2
    a = np.asarray(buf[RB:])
    return np.clip(a, 0, None) if clip else a


def legs_layout(cfg):
    """잔디깎이 leg 목록. 각 leg = (p0, p1, yaw_deg) — 시작점/끝점(월드 x,y)과 진행방위.
    survey_heading_deg 로 격자 전체를 회전(0=+X 방향, 90=+Y 수직격자, 45=대각).
    survey='single' 이면 중심을 지나는 단일 leg."""
    t0, t1 = float(cfg["track_x0_m"]), float(cfg["track_x1_m"])
    h = np.deg2rad(float(cfg.get("survey_heading_deg", 0.0)))
    u = np.array([np.cos(h), np.sin(h)])            # leg 진행방향
    v = np.array([-np.sin(h), np.cos(h)])           # leg 간 이격방향(진행에 수직)

    def mk(a, b):
        d = b - a
        return (a, b, float(np.rad2deg(np.arctan2(d[1], d[0]))))

    if str(cfg.get("survey", "lawnmower")).lower() != "lawnmower":
        return [mk(t0 * u, t1 * u)]
    n = int(cfg["n_legs"]); sp = float(cfg["leg_spacing_m"])
    legs = []
    for k in range(n):
        off = (k - (n - 1) / 2.0) * sp * v           # 0 중심 대칭 이격
        a, b = (off + t0 * u, off + t1 * u) if k % 2 == 0 else (off + t1 * u, off + t0 * u)
        legs.append(mk(a, b))                        # boustrophedon(교대 방향)
    return legs


class Progress:
    """취득 진행 표시. UI 가 파싱할 수 있게 한 줄 형식을 고정한다.

        [progress] <단계> <완료>/<전체> <퍼센트>% 경과 <초>s 남음 <초>s

    이게 없을 때는 로그가 스폰 단계에서 멈춘 것처럼 보여, 정상 동작 중인 취득을
    '멈췄다'고 오해했다(2026-08-03). ping 수천 개를 도는 동안 아무 출력이 없었다.
    """
    def __init__(self, stage, total, every=None):
        self.stage, self.total = stage, max(int(total), 1)
        self.t0 = time.time()
        self.every = every or max(1, self.total // 50)   # 대략 50줄
        self.last = -1
        self.emit(0)

    def emit(self, done):
        el = time.time() - self.t0
        rate = done / el if el > 0 and done > 0 else 0.0
        eta = (self.total - done) / rate if rate > 0 else float("nan")
        print(f"[progress] {self.stage} {done}/{self.total} "
              f"{100.0*done/self.total:.1f}% 경과 {el:.0f}s 남음 "
              + (f"{eta:.0f}s" if eta == eta else "-"), flush=True)

    def step(self, done):
        if done - self.last >= self.every or done >= self.total:
            self.last = done
            self.emit(done)


def _leg_frame(p0, p1):
    """leg 의 진행 단위벡터 u 와 길이 L."""
    d = p1 - p0
    L = float(np.linalg.norm(d))
    return d / L, L


def acquire_ideal_leg(env, p0, p1, yaw, DX, tele_z, gt=False, coherent=False):
    """이상(텔레포트) 한 leg 직선 취득. 기체를 leg 방위(yaw)로 향하게 해 센서가 항상
    진행방향에 수직이 되도록 한다. 회전 없음(다음 leg로 점프)."""
    u, L = _leg_frame(p0, p1)
    P, S, XY, GP, GS = [], [], [], [], []
    steps = np.arange(0.0, L, DX)
    prog = Progress("ping", len(steps))
    for k, s in enumerate(steps):
        prog.step(k)
        p = p0 + u * s
        env.agents["plat"].teleport([float(p[0]), float(p[1]), float(tele_z)], [0, 0, yaw])
        st = env.tick()
        for _ in range(60):                          # 소나 캡처 틱까지 대기(ideal은 보통 즉시)
            if "port" in st and "stbd" in st:
                break
            st = env.tick()
        pv, pl = split_channels(st["port"], gt, coherent)
        sv, sl = split_channels(st["stbd"], gt, coherent)
        P.append(pv); S.append(sv)
        if gt:
            GP.append(pl); GS.append(sl)
        XY.append([float(p[0]), float(p[1])])
    prog.step(len(steps))
    return (np.array(P), np.array(S), np.array(XY),
            (np.array(GP), np.array(GS)) if gt else None)


def acquire_asv_leg(env, p0, p1, DX, gt=False, coherent=False, etol=6.0,
                    settle_steps=20000, drive_steps=80000):
    """ASV 한 직선 leg 취득(임의 방위).
    1) 접근/회전(기록X): leg 시작점으로 유도, leg 선에 올라탈 때까지.
    2) 직선주행(기록): leg 선 위(수직편차 |e|<etol)일 때만 기록, 전진 lookahead 4m.
    회전구간은 leg 선을 벗어나 있으므로 자동 제외됨."""
    u, L = _leg_frame(p0, p1)

    def proj(pos):
        """leg 좌표계: s=진행거리, e=수직편차."""
        w = pos - p0
        return float(np.dot(w, u)), float(u[0] * w[1] - u[1] * w[0])

    # 1) 접근/회전
    st = env.step(np.array([float(p0[0]), float(p0[1])]))
    for _ in range(settle_steps):
        pos = np.array(st["pose"])[:2, 3]
        s, e = proj(pos)
        if abs(e) < etol and s <= 4.0:
            break                                    # leg 선 위 & 시작점 부근
        st = env.step(np.array([float(p0[0]), float(p0[1])]))
    # 2) 직선주행 + 기록
    # 소나는 ping_hz 로 제한돼 '캡처한 틱'에만 st 에 키가 존재한다(없는 틱은 그냥 지나감).
    # 캡처 간격이 이미 track_dx 에 맞춰져 있으므로, 거리 게이트는 중복방지용(0.5*DX)만 둔다.
    P, S, XY, GP, GS = [], [], [], [], []; last = -1e9
    for _ in range(drive_steps):
        pos = np.array(st["pose"])[:2, 3]
        s, e = proj(pos)
        if ("port" in st and "stbd" in st) and abs(e) < etol and s - last >= 0.5 * DX:
            pv, pl = split_channels(st["port"], gt, coherent)
            sv, sl = split_channels(st["stbd"], gt, coherent)
            P.append(pv); S.append(sv)
            if gt:
                GP.append(pl); GS.append(sl)
            XY.append([float(pos[0]), float(pos[1])]); last = s
        tgt = p0 + u * min(s + 4.0, L + 4.0)         # 진행방향 4m 앞 (proven)
        st = env.step(np.array([float(tgt[0]), float(tgt[1])]))
        if s >= L:                                   # leg 끝 도달
            break
    return (np.array(P), np.array(S), np.array(XY),
            (np.array(GP), np.array(GS)) if gt else None)


def to_ground(img_side, altitude, range_min, range_max):
    """slant-range -> ground-range 재격자 (g=sqrt(slant^2-alt^2)). 반환 (out, gmax)."""
    RB = img_side.shape[1]
    slant = np.linspace(range_min, range_max, RB)
    m = slant >= altitude
    ground = np.sqrt(slant[m] ** 2 - altitude ** 2)
    gmax = float(ground.max())
    g_grid = np.linspace(0, gmax, RB)
    out = np.empty_like(img_side)
    for i in range(img_side.shape[0]):
        out[i] = np.interp(g_grid, ground, img_side[i, m])
    return out, gmax


# --------------------------- run ---------------------------
def param_tag(cfg):
    """가변 파라미터/보정을 파일명용 문자열로 인코딩."""
    xmode = "gnd" if cfg["slant_range_correction"] else "slant"
    smode = "coh3" if coherent_signal_model(cfg) else "pow2"
    ob = lambda k: "on" if cfg[k] else "off"
    return (f"{smode}_f{cfg['frequency_khz']:.0f}_bw{cfg['bandwidth_khz']:.0f}"
            f"_vb{cfg['vertical_beam_deg']:.0f}_hb{cfg['horizontal_beam_deg']:.2f}"
            f"_dep{float(cfg['depression_deg']):.1f}_alt{float(cfg['altitude_m']):.1f}"
            f"_r{cfg['range_min_m']:.0f}-{cfg['range_max_m']:.0f}_res{cfg['_range_res_m']*100:.1f}cm"
            f"_hd{float(cfg.get('survey_heading_deg', 0.0)):.0f}_sp{float(cfg['leg_spacing_m']):.0f}"
            f"_{xmode}_beam{ob('beam_pattern')}_tvg{ob('tvg')}_abs{ob('absorption')}"
            f"_norm{ob('intensity_normalization')}")


def run(argv, show_viewport=False, viz=False):
    """잔디깎이(또는 단일) 주행. 직선 leg 마다 워터폴 1장씩 저장(회전구간 제외).
    파일명에 파라미터+취득시간+leg번호. 반환 저장된 raw 경로 리스트."""
    cfg = derive(load_config(argv))
    plat = cfg["platform"]; DX = cfg["track_dx_m"]; altitude = float(cfg["altitude_m"])
    legs = legs_layout(cfg)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    multi = len(legs) > 1
    # 데이터셋 취득은 장면별 산출물을 samples/<scene_id>/ 하나에 모은다. CLI 직접
    # 취득은 기존 data/{images,raw,processed} 위치를 유지한다.
    dataset_root = cfg.get("dataset_root")
    if dataset_root:
        output_root = pathlib.Path(str(dataset_root)).expanduser()
        if not output_root.is_absolute():
            output_root = ROOT / output_root
        output_root = output_root.resolve()
        sample_id = re.sub(r"[^A-Za-z0-9_-]+", "_", str(
            cfg.get("dataset_sample_id") or f"scene_{stamp}"))[:80]
        session = output_root / "samples" / sample_id
        sss_root = session / "sss"
        raw_dir = sss_root / "waterfall"
        ta_dir = sss_root / "true_aspect"
        rawnpy_dir = sss_root / "raw"
        procnpy_dir = sss_root / "processed"
        mask_dir = session / "annotations" / "masks"
        bbox_dir = session / "annotations" / "bboxes"
        bbox_overlay_dir = session / "annotations" / "bbox_overlays"
        cfg["dataset_root"] = str(output_root)
        cfg["dataset_sample_id"] = sample_id
    else:
        output_root = None
        session = IMG / stamp
        raw_dir = session / "waterfall"
        ta_dir = session / "true_aspect"
        rawnpy_dir = RAW / stamp
        procnpy_dir = PROC / stamp
        mask_dir = session / "mask"
        bbox_dir = session / "bbox_labels"
        bbox_overlay_dir = session / "bbox_overlay"

    save_png = bool(cfg.get("save_waterfall_png", True))
    save_ta = bool(cfg.get("save_true_aspect_png", True))
    save_raw_npy = bool(cfg.get("save_raw_numpy", True))
    save_proc_npy = bool(cfg.get("save_processed_numpy", True))
    save_mask = bool(cfg.get("save_mask", True))
    save_bbox = bool(cfg.get("save_bbox", True))
    save_overlay = bool(cfg.get("save_bbox_overlay", True))
    if save_overlay and not save_png:
        raise SystemExit("bbox 검수 오버레이에는 워터폴 PNG 저장이 필요합니다.")

    # filled mask나 bbox가 필요하면 엔진의 strict ray-hit 채널을 메모리에서 받는다.
    # strict 중간 결과 자체는 파일로 저장하지 않는다.
    gt = bool(cfg.get("gt_labels", True)) and (save_mask or save_bbox or save_overlay)
    session.mkdir(parents=True, exist_ok=True)
    for enabled, directory in (
            (save_png, raw_dir), (save_ta, ta_dir), (save_raw_npy, rawnpy_dir),
            (save_proc_npy, procnpy_dir), (save_mask, mask_dir),
            (save_bbox, bbox_dir), (save_overlay, bbox_overlay_dir)):
        if enabled:
            directory.mkdir(parents=True, exist_ok=True)
    # Canonical PNG is always raw/ + trueaspect/.  A large-dataset scene can
    # additionally request one display-only clipping variant from identical dB.
    variant_span = float(cfg.get("display_variant_dynamic_range_db", 0.0) or 0.0)
    variant_raw_dir = (session / "sss" / "waterfall_variant"
                       if output_root and variant_span > 0.0 and save_png else
                       session / "waterfall_variant" if variant_span > 0.0 and save_png else None)
    variant_ta_dir = (session / "sss" / "true_aspect_variant"
                      if output_root and variant_span > 0.0 and save_ta else
                      session / "true_aspect_variant" if variant_span > 0.0 and save_ta else None)
    if variant_raw_dir is not None:
        variant_raw_dir.mkdir(parents=True, exist_ok=True)
    if variant_ta_dir is not None:
        variant_ta_dir.mkdir(parents=True, exist_ok=True)
    if gt and save_bbox:
        (bbox_dir / "classes.txt").write_text(
            str(cfg.get("gt_bbox_class_name", "wreck")) + "\n")
    model_id = "coherent-cell-v3.1" if coherent_signal_model(cfg) else "complex-power-v2"
    metadata = {**cfg, "bin_model": model_id,
                "output_layout": "dataset-root-v1" if output_root else "legacy-v1",
                "resolved_speckle_seed": int(cfg["_resolved_speckle_seed"])}
    metadata_text = json.dumps(metadata, ensure_ascii=False, indent=2, default=str)
    (session / "acquisition_config.json").write_text(metadata_text)
    if cfg.get("_scene") is not None:
        (session / "scene.json").write_text(json.dumps(
            cfg["_scene"], ensure_ascii=False, indent=2, default=str) + "\n")
    if output_root is None and save_raw_npy:
        (rawnpy_dir / "acquisition_config.json").write_text(metadata_text)
    if variant_raw_dir is not None or variant_ta_dir is not None:
        (session / "display_variants.json").write_text(json.dumps({
            "canonical": {"directory": "sss/waterfall" if output_root else "waterfall",
                          "dynamic_range_db": float(
                cfg.get("display_dynamic_range_db", 0.0) or 0.0)},
            "clip_randomized": {"directory": "sss/waterfall_variant" if output_root else "waterfall_variant",
                                "dynamic_range_db": variant_span,
                                "same_linear_db_as_canonical": True},
        }, ensure_ascii=False, indent=2) + "\n")
    print(f"[sss {plat}] survey={cfg.get('survey')} legs={len(legs)} "
          f"heading={float(cfg.get('survey_heading_deg', 0.0)):.0f}deg "
          f"spacing={cfg.get('leg_spacing_m')}m -> {session}")
    BASE_ALT = base_altitude(cfg)
    if plat != "ideal":
        # ASV 는 수면에 남고, 트랜스듀서를 그 아래로 내려 고도를 맞춘다(토우피시 모사).
        print(f"[geom] 트랜스듀서 마운트 z={sensor_z(cfg):.2f}m -> 고도 {BASE_ALT:.2f}m "
              f"(해저 {cfg.get('seabed_top_m', SEABED_TOP_DEFAULT)}m), "
              f"파도 wind_speed={cfg.get('wind_speed_ms', 0.1)}")
        if abs(altitude - BASE_ALT) > 0.1:
            print(f"[warn] 요청 고도 {altitude}m 가 클램프되어 {BASE_ALT:.2f}m 로 취득됩니다.")

    # 환경 프로젝트(필드) 지형은 엔진이 **기동 시** 환경변수를 보고 스폰한다. 시나리오
    # JSON 으로는 전달되지 않으므로 holoocean.make() 전에 os.environ 에 넣어야 한다.
    #
    # 2026-07-31: 이 배선이 통째로 빠져 있었다. 매니페스트의 terrain 은 seabed_top_m 과
    # 오브젝트 배치 좌표에만 쓰이고 heightfield 는 시뮬에 올라간 적이 없다. 그래서
    # 지금까지 "해저"로 보이던 것은 평탄 해저 판(flat_seabed=true 일 때)이거나, 그것마저
    # 끄면 **아무것도 없는 물속**이었다(실측: 반사가 들어온 bin 이 1~2%뿐, 나머지는
    # TVG 로 증폭된 노이즈). 이미지가 균일했던 진짜 이유다.
    preset = (cfg.get("_scene") or {}).get("terrain", {}).get("scene_preset")
    if preset and not cfg.get("flat_seabed", True):
        os.environ["HOLOOCEAN_SHIPWRECK_SPAWN"] = "1"
        os.environ["HOLOOCEAN_SHIPWRECK_SCENE_PRESET"] = str(preset)
        print(f"[scene] 필드 지형 프리셋 = {preset}", flush=True)
    else:
        os.environ.pop("HOLOOCEAN_SHIPWRECK_SPAWN", None)
        os.environ.pop("HOLOOCEAN_SHIPWRECK_SCENE_PRESET", None)

    env = holoocean.make(scenario_cfg=scenario(cfg, viz), show_viewport=show_viewport)

    # 해류. HoloOcean 은 set_ocean_currents(agent, [vx,vy,vz]) 로 **플랫폼에 힘**을 준다.
    # 음향(임피던스/감쇠)에는 영향이 없고, ASV 가 흐름에 밀려 침로에서 벗어나는(crab)
    # 효과만 만든다 — 실제 취득에서 트랙 이탈과 워터폴 왜곡의 원인이 되는 바로 그것이다.
    # ideal(텔레포트)은 물리를 타지 않으므로 적용하지 않는다.
    cur = cfg.get("current_ms")
    if cur and cfg["platform"] != "ideal":
        cur = [float(v) for v in (cur if isinstance(cur, (list, tuple)) else [cur, 0, 0])]
        env.set_ocean_currents("plat", cur)
        print(f"[env] 해류 {cur} m/s 적용 (플랫폼 표류에만 작용, 음향에는 영향 없음)",
              flush=True)

    # 씬 구성은 전부 런타임 스폰이고 첫 ping 전에 끝나야 한다.
    # 순서 주의: apply_scene() 이 먼저 ClearSceneObjects 를 보내므로 해저는 그 뒤에
    # 스폰해야 한다. 반대로 하면 방금 만든 해저가 지워진다.
    import scene_runtime
    scene = cfg.get("_scene")
    if scene is not None:
        scene_runtime.apply_scene(env, scene)

    # 평탄 해저(우리 flat_v1). 환경 프로젝트 씬을 쓸 때는 그쪽이 지형을 스폰하므로
    # --flat_seabed false 로 꺼야 한다 — 켜두면 해저가 두 겹이 되어 화면에서 지형이
    # 떠 보이고, 그 지형 범위 밖으로 나간 레이가 우리 해저에 맞아 데이터가 오염된다.
    if bool(cfg.get("flat_seabed", True)):
        if scene is not None:
            x0, y0, x1, y1 = scene["terrain"]["extent_m"]
            extent = (x1 - x0, y1 - y0)
        else:
            extent = (340.0, 260.0)
        scene_runtime.spawn_flat_seabed(
            env, float(cfg.get("seabed_top_m", SEABED_TOP_DEFAULT)), extent_m=extent)

    if show_viewport:
        # 기본 카메라는 플랫폼(ASV, 수면 z=0)에서 수평을 본다. 해저는 altitude_m 만큼
        # 아래에 있어 화면 밖일 수 있다(얕은 지형일수록 심함 — 실제로 안 보인 사고가
        # 있었다). 매 씬마다 수동으로 맞추지 않도록 첫 leg 시작점 위 상공에서 비스듬히
        # 내려다보게 자동 배치한다.
        #
        # ⚠️ move_viewport 의 두 번째 인자는 "바라볼 방향 벡터"지만, 엔진의
        # ConvertAngularVector(ClientToUE) 를 거치며 **X 와 Z 가 부호 반전**된다
        # (Conversion.cpp: USE_RHS 일 때 Vector.X *= -1; Vector.Z *= -1).
        # 그래서 아래를 보려면 z 성분을 **양수**로 줘야 한다. 음수로 줬다가 카메라가
        # 하늘을 보고 파란 화면만 나온 사고가 있었다.
        p0, _p1, _yaw0 = legs[0]
        cam_h = max(3.0 * BASE_ALT, 30.0)
        env.move_viewport([float(p0[0]), float(p0[1]) - cam_h * 0.7, cam_h],
                          [-0.15, 0.55, 0.75])
        for _ in range(5):
            env.tick()

    tele_z = altitude - BASE_ALT
    coherent = coherent_signal_model(cfg)
    outputs = []
    for k, (p0, p1, yaw) in enumerate(legs):
        if plat == "ideal":
            P, S, XY, G = acquire_ideal_leg(
                env, p0, p1, yaw, DX, tele_z, gt, coherent)
        else:
            P, S, XY, G = acquire_asv_leg(env, p0, p1, DX, gt, coherent)
        if P.shape[0] < 10:
            print(f"[leg {k+1}] pings={P.shape[0]} 너무 적음 -> 건너뜀", flush=True); continue
        print(f"[leg {k+1}/{len(legs)}] start=({p0[0]:.0f},{p0[1]:.0f}) "
              f"yaw={yaw:.0f}  pings={P.shape[0]}", flush=True)
        leg_id = (k + 1) if multi else None
        legstr = ("_leg%02d" % leg_id) if leg_id else ""
        if save_raw_npy:
            np.save(rawnpy_dir/f"wf_sss_{plat}{legstr}_raw.npy", np.stack([P, S]))
        outputs.append(process_and_save(cfg, P, S, raw_dir, ta_dir, procnpy_dir,
                                        leg=leg_id, G=G, mask_dir=mask_dir,
                                        bbox_dir=bbox_dir,
                                        bbox_overlay_dir=bbox_overlay_dir,
                                        variant_raw_dir=variant_raw_dir,
                                        variant_ta_dir=variant_ta_dir,
                                        save_png=save_png, save_ta=save_ta,
                                        save_processed=save_proc_npy,
                                        save_mask=save_mask, save_bbox=save_bbox,
                                        save_overlay=save_overlay))
    print(f"[sss {plat}] 완료: {len(outputs)} 워터폴 저장 -> {session}", flush=True)
    return outputs


def process_and_save(cfg, P, S, raw_dir, ta_dir, procnpy_dir, leg=None,
                     G=None, mask_dir=None, bbox_dir=None,
                     bbox_overlay_dir=None, variant_raw_dir=None,
                     variant_ta_dir=None, *, save_png=True, save_ta=True,
                     save_processed=True, save_mask=True, save_bbox=True,
                     save_overlay=True):
    """raw 좌/우 trace(P,S) -> 후처리(§3.6) -> 선택 산출물 저장.
    G=(라벨_port, 라벨_stbd) 가 주어지면 강도와 같은 기하 변환을 거쳐
    빈 실루엣 내부를 채운 일반 GT 마스크로 저장한다."""
    plat = cfg["platform"]; DX = cfg["track_dx_m"]; altitude = float(cfg["altitude_m"])

    range_min, range_max = cfg["range_min_m"], cfg["range_max_m"]
    # 수신기 noise power 와 흡수계수는 crop/data 와 후처리가 같은 값을 공유한다.
    alpha = sc.francois_garrison_alpha(cfg["frequency_khz"]) if cfg["absorption"] else 0.0
    nf_cfg = cfg.get("noise_power", cfg.get("noise_floor", None))
    base_noise_power = (sc.noise_floor_from_rated_range(range_min, alpha)
                        if nf_cfg in (None, "", "auto") else float(nf_cfg))
    # UI/대규모 데이터셋의 noise_level은 전자 수신기 잡음의 **power** 배율이다.
    # speckle은 해저 산란장의 물리적 대비라 이 값으로 같이 흔들지 않는다.
    noise_level = max(0.0, float(cfg.get("noise_level", 1.0)))
    noise_power = base_noise_power * noise_level
    if noise_level != 1.0:
        print(f"   [수신기 노이즈] power x{noise_level:g} "
              f"({base_noise_power:.3e} -> {noise_power:.3e})", flush=True)

    # 유효거리 크롭: 빔 도달거리 밖 bin 에는 어떤 ping 에서도 반사가 없다. 실제 취득도
    # 유효거리 밖은 비어 있고 후처리에서 잘라내므로, 기하를 제약하는 대신 여기서 자른다.
    # 라벨도 같은 폭으로 잘라야 픽셀 정합이 유지된다.
    mode = str(cfg.get("crop_mode", "geometric")).lower()
    if mode != "off":
        n_full = P.shape[1]
        if mode == "data":
            # 노이즈 모델로 판정: 신호가 노이즈에 묻히는 지점까지. 더 바짝 자르지만
            # 희미한 표적까지 지울 수 있다.
            # effective_range_bins operates on pre-propagation signal power.
            crop_p = np.abs(P) ** 2 if np.iscomplexobj(P) else P
            crop_s = np.abs(S) ** 2 if np.iscomplexobj(S) else S
            n_eff = sc.effective_range_bins(
                crop_p, crop_s, noise_power,
                range_res_m=float(cfg["_range_res_m"]),
                range_min=range_min, range_max=range_max, alpha_db_m=alpha)
            why = "신호가 노이즈에 묻히는 지점"
        else:
            # 기하로 판정(기본): 레이가 물리적으로 닿을 수 있는 한계. 실제 데이터를
            # 잘라낼 위험이 없는 하드 상한이다.
            # 기복 지형에서는 어떤 ping/방향에서라도 도달 가능한 가장 낮은 해저를
            # 보존해야 한다. 마루 기준 최소 고도로 crop하면 골짜기 반사를 잘라낸다.
            reach = sc.beam_reach_m(range_reference_altitude(cfg), cfg["depression_deg"],
                                    cfg["vertical_beam_deg"])
            n_eff = (n_full if reach >= range_max else
                     max(1, int(round(n_full * (reach - range_min)
                                      / (range_max - range_min)))))
            why = "빔이 닿을 수 있는 한계"
        if 0 < n_eff < n_full:
            r_eff = range_min + (range_max - range_min) * (n_eff / n_full)
            print(f"   [crop:{mode}] 유효거리 {r_eff:.1f}m ({why}) "
                  f"-> {n_eff}/{n_full} bins", flush=True)
            range_max = r_eff
            P, S = P[:, :n_eff], S[:, :n_eff]
            if G is not None:
                G = (G[0][:, :n_eff], G[1][:, :n_eff])

    # 후처리 (§3.6): 흡수/TVG -> (선택)slant보정 -> 이중측 결합 -> (선택)강도정규화
    # 수신기/앰비언트 노이즈 바닥.
    #
    # 기본(None)은 센서 정격 최대거리에서 SNR=0 dB 가 되도록 역산한 값이다
    # (sss_common.noise_floor_from_rated_range). 스페클만 따로 보고 싶을 때처럼
    # **후처리 노이즈를 명시적으로 끄고 싶은 실험**을 위해 설정에서 0 을 줄 수 있게 한다.
    # complex-power-v2 에서는 이 값이 Rayleigh sigma 가 아니라 평균 noise power다.
    noise_seed = int(cfg.get("noise_seed", 0))
    port_seed = None if noise_seed == 0 else noise_seed
    stbd_seed = None if noise_seed == 0 else noise_seed + 1
    if np.iscomplexobj(P):
        common = dict(
            range_min=range_min, range_max=range_max, alpha_db_m=alpha,
            noise_power=noise_power, apply_tvg=bool(cfg["tvg"]),
            geometric_spreading=bool(cfg.get("geometric_spreading", True)),
            bandwidth_hz=float(cfg["bandwidth_khz"]) * 1e3,
            sound_speed_ms=float(cfg["sound_speed_ms"]),
            pulse_duration_s=float(cfg.get("pulse_duration_ms", 1.0)) * 1e-3,
            matched_filter=bool(cfg.get("matched_filter", True)),
            matched_filter_max_taps=int(cfg.get("matched_filter_max_taps", 129)))
        pp = sc.process_complex_side(P, seed=port_seed, **common)
        sp = sc.process_complex_side(S, seed=stbd_seed, **common)
    else:
        pp = sc.process_side(P, range_min, range_max, alpha, noise_floor=noise_power,
                             seed=port_seed, apply_tvg=bool(cfg["tvg"]))
        sp = sc.process_side(S, range_min, range_max, alpha, noise_floor=noise_power,
                             seed=stbd_seed, apply_tvg=bool(cfg["tvg"]))

    if cfg["slant_range_correction"]:
        pg, gmax = to_ground(pp, altitude, range_min, range_max)
        sg, _ = to_ground(sp, altitude, range_min, range_max)
        across_px = gmax / pg.shape[1]
    else:                                            # 논문 기본: slant 유지(검은 나디르밴드)
        pg, sg = pp, sp
        across_px = (range_max - range_min) / pp.shape[1]

    img = np.concatenate([pg[:, ::-1], sg], axis=1)  # port(좌)|starboard(우), nadir=중앙

    # 표시 창은 물리 앵커로 고정한다(sss_common.display_window_db 참고).
    # 논문 §3.6 의 표준화는 **데이터셋 전체** 통계로 하는 학습 전처리라, 이미지 한 장을
    # 저장하는 이 자리에서 쓰면 안 된다(장면과 무관하게 평균이 회색으로 고정됨).
    # dB 배열은 그대로 .npy 로 남기므로, 학습 시 데이터셋 통계로 표준화하면 된다.
    vmin, vmax = sc.display_window_db(
        range_min, range_max, alpha,
        apply_tvg=bool(cfg["tvg"]),
        reference_range_m=base_altitude(cfg),
        noise_quantile=float(cfg.get(
            "display_noise_quantile", sc.DISPLAY_NOISE_BLACK_QUANTILE)))
    # 물리 하한~기준 해저 상한이 지나치게 넓으면, 과거 8-bit 영상에서는 검게
    # 잘리던 매우 약한 ray-hit/부분 음영층까지 중간 회색으로 펼쳐져 물체 둘레에
    # '베일' 또는 중복 윤곽처럼 보인다. PNG 표시 동적 범위만 고정 폭으로 제한한다.
    # 장면별 percentile을 쓰지 않으므로 서로 다른 장면의 밝기 비교는 유지되고,
    # 저장되는 complex raw와 dB npy는 전혀 clipping하지 않는다.
    physical_vmin = vmin
    display_span = float(cfg.get("display_dynamic_range_db", 0.0) or 0.0)
    if display_span > 0.0:
        vmin = max(float(vmin), float(vmax) - display_span)
    print(f"   [표시] 창 {vmin:.1f} ~ {vmax:.1f} dB "
          f"(폭 {vmax-vmin:.1f} dB, 물리 하한 {physical_vmin:.1f} dB"
          + (f", 고정 표시폭 {display_span:.1f} dB" if display_span > 0 else "")
          + ")", flush=True)

    legstr = f"_leg{leg:02d}" if leg is not None else ""
    fname = f"waterfall_sss_{plat}_{param_tag(cfg)}{legstr}.png"  # 시간은 폴더명에
    if save_processed:
        np.save(procnpy_dir/f"wf_sss_{plat}{legstr}_db.npy", img)
    raw_path = raw_dir / fname if save_png else None
    ta_path = ta_dir / fname if save_ta else None
    raw_p, ta_p = sc.save_waterfall(img, raw_path, ta_path, across_px, DX,
                                    vmin=vmin, vmax=vmax)
    if raw_p is not None:
        print(f"   -> waterfall/{raw_p.name}  ({img.shape[0]}x{img.shape[1]}px)", flush=True)
    variant_span = float(cfg.get("display_variant_dynamic_range_db", 0.0) or 0.0)
    if variant_span > 0.0 and (variant_raw_dir is not None or variant_ta_dir is not None):
        variant_vmin = max(float(physical_vmin), float(vmax) - variant_span)
        sc.save_waterfall(img,
                          variant_raw_dir/fname if variant_raw_dir is not None else None,
                          variant_ta_dir/fname if variant_ta_dir is not None else None,
                          across_px, DX,
                          vmin=variant_vmin, vmax=vmax)
        print(f"   -> waterfall_variant/{fname}  "
              f"(scene clipping {variant_span:.1f} dB)", flush=True)

    # --- GT 이진 마스크: strict ray-hit을 메모리에서 filled mask로 변환 ---
    if G is not None and (save_mask or save_bbox or save_overlay):
        gp, gs = G
        if cfg["slant_range_correction"]:
            gp, _ = to_ground(gp, altitude, cfg["range_min_m"], cfg["range_max_m"])
            gs, _ = to_ground(gs, altitude, cfg["range_min_m"], cfg["range_max_m"])
        mask = np.concatenate([gp[:, ::-1], gs], axis=1)      # 강도와 동일 결합
        mask = (mask >= 0.5).astype(np.uint8)                 # 이진화 (1=wreck)
        # filled: 선체 윤곽에 둘러싸인 내부까지 정답으로 (반경은 config)
        filled = sc.fill_label_holes(mask, int(cfg.get("gt_fill_radius_px", 5)))
        from PIL import Image
        filled_saved = np.flipud(filled)
        if save_mask and mask_dir is not None:
            Image.fromarray(filled_saved * 255).save(str(mask_dir/fname))
            np.save(mask_dir/f"{pathlib.Path(fname).stem}_mask.npy", filled_saved)

        # YOLO detection GT.  The binary engine channel labels one class (`wreck`).
        # One physical hull can nevertheless produce separated ray-hit islands;
        # small nearby islands are grouped before one class-0 box is emitted.
        # Normalized boxes are identical for raw and true-aspect images because
        # true-aspect only rescales the whole row axis.
        boxes = []
        if save_bbox or save_overlay:
            boxes = sc.mask_to_boxes(
                filled_saved,
                min_area_px=int(cfg.get("gt_bbox_min_area_px", 16)),
                padding_px=int(cfg.get("gt_bbox_padding_px", 2)),
                merge_gap_px=int(cfg.get("gt_bbox_merge_gap_px", 24)),
                fragment_max_area_px=int(cfg.get("gt_bbox_fragment_max_area_px", 64)))
            class_id = int(cfg.get("gt_bbox_class_id", 0))
            class_name = str(cfg.get("gt_bbox_class_name", "wreck"))
            stem = pathlib.Path(fname).stem
            lines = sc.boxes_to_yolo(boxes, filled_saved.shape[1],
                                     filled_saved.shape[0], class_id)
            if save_bbox and bbox_dir is not None:
                (bbox_dir / f"{stem}.txt").write_text(
                    "\n".join(lines) + ("\n" if lines else ""))
            meta = {
                "image": fname, "image_width": int(filled_saved.shape[1]),
                "image_height": int(filled_saved.shape[0]),
                "mask_source": "mask", "class_id": class_id,
                "class_name": class_name, "boxes": boxes,
                "box_grouping": {
                    "min_area_px": int(cfg.get("gt_bbox_min_area_px", 16)),
                    "merge_gap_px": int(cfg.get("gt_bbox_merge_gap_px", 24)),
                    "fragment_max_area_px": int(cfg.get("gt_bbox_fragment_max_area_px", 64)),
                },
                "yolo_txt": f"{stem}.txt",
                "normalized_labels_apply_to": ["raw", "trueaspect"],
            }
            if save_bbox and bbox_dir is not None:
                (bbox_dir / f"{stem}.json").write_text(
                    json.dumps(meta, ensure_ascii=False, indent=2) + "\n")

            if save_overlay and bbox_overlay_dir is not None:
                from PIL import ImageDraw
                overlay = Image.open(raw_p).convert("RGB")
                draw = ImageDraw.Draw(overlay)
                for b in boxes:
                    draw.rectangle([b["x1"], b["y1"], max(b["x1"], b["x2"] - 1),
                                    max(b["y1"], b["y2"] - 1)],
                                   outline=(255, 64, 64), width=2)
                overlay.save(bbox_overlay_dir / fname)
        print(f"      mask/{fname}  (wreck {filled.mean()*100:.2f}%, "
              f"bbox {len(boxes)})",
              flush=True)
    return raw_p or ta_p or procnpy_dir/f"wf_sss_{plat}{legstr}_db.npy"


def main():
    argv = [a for a in sys.argv if a != "--view"]
    show = "--view" in sys.argv
    run(argv, show_viewport=show, viz=show)


if __name__ == "__main__":
    main()
