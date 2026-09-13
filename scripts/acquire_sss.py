import sys, os, json, pathlib, datetime, time, re
import numpy as np, holoocean
import sss_common as sc

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW, PROC, IMG = ROOT/"data/raw", ROOT/"data/processed", ROOT/"data/images"
for _d in (RAW, PROC, IMG):
    _d.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = pathlib.Path(__file__).resolve().parent/"sss_config.json"



SEABED_TOP_DEFAULT = -20.0
SENSOR_Z_DEFAULT = -0.5
MIN_SENSOR_DEPTH = 0.3
MIN_CLEARANCE = 1.0


def sensor_z(cfg):
    if "sensor_z_m" in cfg:
        z = float(cfg["sensor_z_m"])
    elif "altitude_m" in cfg:
        seabed = float(cfg.get("seabed_top_m", SEABED_TOP_DEFAULT))
        z = float(cfg["altitude_m"]) + seabed
    else:
        z = SENSOR_Z_DEFAULT
    seabed = float(cfg.get("seabed_top_m", SEABED_TOP_DEFAULT))

    return min(-MIN_SENSOR_DEPTH, max(seabed + MIN_CLEARANCE, z))


def base_altitude(cfg):
    return abs(float(cfg.get("seabed_top_m", SEABED_TOP_DEFAULT))) + sensor_z(cfg)


def range_reference_altitude(cfg):
    return float(cfg.get("range_reference_altitude_m", base_altitude(cfg)))



def _coerce(val, ref):
    if isinstance(ref, bool):
        return str(val).strip().lower() in ("1", "true", "yes", "on")
    if isinstance(ref, (int, float)) and not isinstance(ref, bool):
        try: return float(val)
        except ValueError: return val
    if isinstance(ref, (list, tuple)):


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
    explicit = set()
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







    mpath = cfg.pop("manifest", None)
    tname = cfg.pop("terrain", None)
    explicit.discard("manifest"); explicit.discard("manifest_index")
    explicit.discard("terrain")








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


        print(f"[warn] --terrain {tname} 는 매니페스트가 있을 때 무시됩니다 "
              f"(지형과 오브젝트 배치는 함께 정해집니다).")

    if mpath:
        import scene_runtime
        scene = scene_runtime.load_manifest_line(mpath, int(cfg.pop("manifest_index", 0)))
        if "seabed_top_m" not in explicit:
            cfg["seabed_top_m"] = float(scene["terrain"]["seabed_top_m"])



        if "flat_seabed" not in explicit:
            cfg["flat_seabed"] = not bool(scene["terrain"].get("heightfield_csv"))
        overridden = []
        for k, v in scene.get("survey", {}).items():

            if k in ("altitude_ratio", "altitude_worst_m", "terrain_relief_m",
                     "beam_reach_m", "range_envelope_source",
                     "range_footprint_samples", "range_far_depression_deg"):
                continue
            if k in explicit:
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




    ratio = cfg["_range_res_m"] / max(cfg["_Ry_m"], 1e-12)
    cfg["_range_sample_to_intrinsic_ratio"] = ratio
    if ratio > 1.25:
        print(f"[range] intrinsic c/(2B)={cfg['_Ry_m']:.4f} m, output spacing="
              f"{cfg['_range_res_m']:.4f} m ({ratio:.1f}x coarser; decimated output)",
              flush=True)
    elif ratio < 0.8:
        print(f"[range] output spacing {cfg['_range_res_m']:.4f} m oversamples the "
              f"intrinsic c/(2B)={cfg['_Ry_m']:.4f} m response", flush=True)



    seed = int(cfg.get("speckle_seed", 0))
    if seed == 0:
        seed = int(np.random.SeedSequence().generate_state(1)[0] & 0x7fffffff) or 1
    cfg["_resolved_speckle_seed"] = seed
    return cfg


def coherent_signal_model(cfg):
    return str(cfg.get("signal_model", "coherent")).lower() in {
        "coherent", "coherent_field", "coherent-field-v3", "coherent-cell-v3.1"
    }



def elev_ray_res_deg(cfg):
    rmax = float(cfg["range_max_m"]); rres = float(cfg["_range_res_m"])
    h = range_reference_altitude(cfg)
    th_min = np.arcsin(np.clip(h / rmax, 1e-6, 1.0))
    d_th = np.rad2deg(rres * np.tan(th_min) / rmax)
    ray_span = float(cfg.get("vertical_ray_span_deg", cfg["vertical_beam_deg"]))
    n = int(np.ceil(ray_span / d_th)) + 1
    cap = int(cfg.get("elev_ray_max", 8000))
    if n > cap:


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



            "Elevation": float(cfg.get("vertical_ray_span_deg",
                                       cfg["vertical_beam_deg"])),
            "VerticalBeamwidthDeg": cfg["vertical_beam_deg"],
            "ElevationRayRes": elev_ray_res,
            "BeamPattern": bool(cfg["beam_pattern"]),
            "IncidenceCosine": bool(cfg.get("incidence_cosine", True)),
            "SpeckleSpatialMode": str(cfg.get("speckle_spatial_mode", "world")),

            "MultiPath": bool(cfg.get("multipath", False)),
            "SemanticLabels": bool(cfg.get("gt_labels", True)),
            "LabelTag": str(cfg.get("gt_label_tag", "wreck")),



            "AddSigma": 0.0,
            "MultSigma": float(cfg.get("mult_sigma", 0.5)),

            "SpeckleStrength": float(cfg.get("speckle_strength", 1.0)),
            "SpeckleDistribution": str(
                cfg.get("speckle_distribution", "complex_gaussian")),


            "SpeckleSeed": int(cfg.get("_resolved_speckle_seed",
                                        cfg.get("speckle_seed", 0))),


            "SignalModel": str(cfg.get("signal_model", "coherent")),
            "FrequencyKhz": float(cfg["frequency_khz"]),
            "ScatterCorrelationLengthM": float(
                cfg.get("scatter_correlation_length_m", 0.05)),
            "TextureShape": float(cfg.get("texture_shape", 0.0)),
            "TextureCorrelationLengthM": float(
                cfg.get("texture_correlation_length_m", 1.0)),
            "WaterDensity": cfg["water_density"], "WaterSpeedSound": cfg["sound_speed_ms"]}


ASV_SPEED_MS = 1.47
TICKS_PER_SEC = 60
_HZ_DIVISORS = (1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30, 60)


def ping_hz(cfg):
    if cfg["platform"] == "ideal":
        return None
    need = ASV_SPEED_MS / float(cfg["track_dx_m"])
    for d in _HZ_DIVISORS:
        if d >= need:
            return d
    return TICKS_PER_SEC


def sensors(cfg, viz=False):
    dep = cfg["depression_deg"]
    hz = ping_hz(cfg)
    def one(name, yaw):
        c = sensor_cfg(cfg)



        if (str(cfg.get("speckle_spatial_mode", "world")).lower()
                in ("independent", "random") and name == "stbd"):
            c["SpeckleSeed"] = int(c["SpeckleSeed"]) + 1
        if viz:
            c["ViewRegion"] = True
        s = {"sensor_type": "RaycastSidescanSonar", "sensor_name": name,
             "location": [0, 0, sensor_z(cfg)], "rotation": [0, dep, yaw], "configuration": c}
        if hz is not None:
            s["Hz"] = hz
        return s
    return [one("port", 90), one("stbd", -90),
            {"sensor_type": "PoseSensor", "sensor_name": "pose"}]


def scenario(cfg, viz=False):
    plat = cfg["platform"]
    legs = legs_layout(cfg)
    p0, _, yaw0 = legs[0]
    x0, y0 = float(p0[0]), float(p0[1])
    if plat == "ideal":
        ag = {"agent_name": "plat", "agent_type": "HoveringAUV", "sensors": sensors(cfg, viz),
              "control_scheme": 0, "location": [x0, y0, 0], "rotation": [0, 0, yaw0]}
        return {"name": "sss_ideal", "world": "ExampleLevel", "package_name": "TestWorlds",
                "main_agent": "plat", "ticks_per_sec": 60, "agents": [ag]}
    ag = {"agent_name": "plat", "agent_type": "SurfaceVessel", "sensors": sensors(cfg, viz),
          "control_scheme": 1, "location": [x0, y0, 0], "rotation": [0, 0, yaw0]}


    return {"name": "sss_asv", "world": "ExampleLevel", "package_name": "TestWorlds",
            "main_agent": "plat", "ticks_per_sec": 60,
            "fft_waves": {"wind_speed": float(cfg.get("wind_speed_ms", 0.1))},
            "agents": [ag]}



def split_channels(buf, gt, coherent=False):
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
    RB = len(buf) // 2
    a = np.asarray(buf[RB:])
    return np.clip(a, 0, None) if clip else a


def legs_layout(cfg):
    t0, t1 = float(cfg["track_x0_m"]), float(cfg["track_x1_m"])
    h = np.deg2rad(float(cfg.get("survey_heading_deg", 0.0)))
    u = np.array([np.cos(h), np.sin(h)])
    v = np.array([-np.sin(h), np.cos(h)])

    def mk(a, b):
        d = b - a
        return (a, b, float(np.rad2deg(np.arctan2(d[1], d[0]))))

    if str(cfg.get("survey", "lawnmower")).lower() != "lawnmower":
        return [mk(t0 * u, t1 * u)]
    n = int(cfg["n_legs"]); sp = float(cfg["leg_spacing_m"])
    legs = []
    for k in range(n):
        off = (k - (n - 1) / 2.0) * sp * v
        a, b = (off + t0 * u, off + t1 * u) if k % 2 == 0 else (off + t1 * u, off + t0 * u)
        legs.append(mk(a, b))
    return legs


class Progress:
    def __init__(self, stage, total, every=None):
        self.stage, self.total = stage, max(int(total), 1)
        self.t0 = time.time()
        self.every = every or max(1, self.total // 50)
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
    d = p1 - p0
    L = float(np.linalg.norm(d))
    return d / L, L


def acquire_ideal_leg(env, p0, p1, yaw, DX, tele_z, gt=False, coherent=False):
    u, L = _leg_frame(p0, p1)
    P, S, XY, GP, GS = [], [], [], [], []
    steps = np.arange(0.0, L, DX)
    prog = Progress("ping", len(steps))
    for k, s in enumerate(steps):
        prog.step(k)
        p = p0 + u * s
        env.agents["plat"].teleport([float(p[0]), float(p[1]), float(tele_z)], [0, 0, yaw])
        st = env.tick()
        for _ in range(60):
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
    u, L = _leg_frame(p0, p1)

    def proj(pos):
        w = pos - p0
        return float(np.dot(w, u)), float(u[0] * w[1] - u[1] * w[0])


    st = env.step(np.array([float(p0[0]), float(p0[1])]))
    for _ in range(settle_steps):
        pos = np.array(st["pose"])[:2, 3]
        s, e = proj(pos)
        if abs(e) < etol and s <= 4.0:
            break
        st = env.step(np.array([float(p0[0]), float(p0[1])]))



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
        tgt = p0 + u * min(s + 4.0, L + 4.0)
        st = env.step(np.array([float(tgt[0]), float(tgt[1])]))
        if s >= L:
            break
    return (np.array(P), np.array(S), np.array(XY),
            (np.array(GP), np.array(GS)) if gt else None)


def to_ground(img_side, altitude, range_min, range_max):
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



def param_tag(cfg):
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
    cfg = derive(load_config(argv))
    plat = cfg["platform"]; DX = cfg["track_dx_m"]; altitude = float(cfg["altitude_m"])
    legs = legs_layout(cfg)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    multi = len(legs) > 1


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



    gt = bool(cfg.get("gt_labels", True)) and (save_mask or save_bbox or save_overlay)
    session.mkdir(parents=True, exist_ok=True)
    for enabled, directory in (
            (save_png, raw_dir), (save_ta, ta_dir), (save_raw_npy, rawnpy_dir),
            (save_proc_npy, procnpy_dir), (save_mask, mask_dir),
            (save_bbox, bbox_dir), (save_overlay, bbox_overlay_dir)):
        if enabled:
            directory.mkdir(parents=True, exist_ok=True)
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
    print(f"[sss {plat}] survey={cfg.get('survey')} legs={len(legs)} "
          f"heading={float(cfg.get('survey_heading_deg', 0.0)):.0f}deg "
          f"spacing={cfg.get('leg_spacing_m')}m -> {session}")
    BASE_ALT = base_altitude(cfg)
    if plat != "ideal":

        print(f"[geom] 트랜스듀서 마운트 z={sensor_z(cfg):.2f}m -> 고도 {BASE_ALT:.2f}m "
              f"(해저 {cfg.get('seabed_top_m', SEABED_TOP_DEFAULT)}m), "
              f"파도 wind_speed={cfg.get('wind_speed_ms', 0.1)}")
        if abs(altitude - BASE_ALT) > 0.1:
            print(f"[warn] 요청 고도 {altitude}m 가 클램프되어 {BASE_ALT:.2f}m 로 취득됩니다.")









    preset = (cfg.get("_scene") or {}).get("terrain", {}).get("scene_preset")
    if preset and not cfg.get("flat_seabed", True):
        os.environ["HOLOOCEAN_SHIPWRECK_SPAWN"] = "1"
        os.environ["HOLOOCEAN_SHIPWRECK_SCENE_PRESET"] = str(preset)
        print(f"[scene] 필드 지형 프리셋 = {preset}", flush=True)
    else:
        os.environ.pop("HOLOOCEAN_SHIPWRECK_SPAWN", None)
        os.environ.pop("HOLOOCEAN_SHIPWRECK_SCENE_PRESET", None)

    env = holoocean.make(scenario_cfg=scenario(cfg, viz), show_viewport=show_viewport)





    cur = cfg.get("current_ms")
    if cur and cfg["platform"] != "ideal":
        cur = [float(v) for v in (cur if isinstance(cur, (list, tuple)) else [cur, 0, 0])]
        env.set_ocean_currents("plat", cur)
        print(f"[env] 해류 {cur} m/s 적용 (플랫폼 표류에만 작용, 음향에는 영향 없음)",
              flush=True)




    import scene_runtime
    scene = cfg.get("_scene")
    if scene is not None:
        scene_runtime.apply_scene(env, scene)




    if bool(cfg.get("flat_seabed", True)):
        if scene is not None:
            x0, y0, x1, y1 = scene["terrain"]["extent_m"]
            extent = (x1 - x0, y1 - y0)
        else:
            extent = (340.0, 260.0)
        scene_runtime.spawn_flat_seabed(
            env, float(cfg.get("seabed_top_m", SEABED_TOP_DEFAULT)), extent_m=extent)

    if show_viewport:










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
                                        save_png=save_png, save_ta=save_ta,
                                        save_processed=save_proc_npy,
                                        save_mask=save_mask, save_bbox=save_bbox,
                                        save_overlay=save_overlay))
    print(f"[sss {plat}] 완료: {len(outputs)} 워터폴 저장 -> {session}", flush=True)
    return outputs


def process_and_save(cfg, P, S, raw_dir, ta_dir, procnpy_dir, leg=None,
                     G=None, mask_dir=None, bbox_dir=None,
                     bbox_overlay_dir=None, *, save_png=True, save_ta=True,
                     save_processed=True, save_mask=True, save_bbox=True,
                     save_overlay=True):
    plat = cfg["platform"]; DX = cfg["track_dx_m"]; altitude = float(cfg["altitude_m"])

    range_min, range_max = cfg["range_min_m"], cfg["range_max_m"]

    alpha = sc.francois_garrison_alpha(cfg["frequency_khz"]) if cfg["absorption"] else 0.0
    nf_cfg = cfg.get("noise_power", cfg.get("noise_floor", None))
    base_noise_power = (sc.noise_floor_from_rated_range(range_min, alpha)
                        if nf_cfg in (None, "", "auto") else float(nf_cfg))


    noise_level = max(0.0, float(cfg.get("noise_level", 1.0)))
    noise_power = base_noise_power * noise_level
    if noise_level != 1.0:
        print(f"   [수신기 노이즈] power x{noise_level:g} "
              f"({base_noise_power:.3e} -> {noise_power:.3e})", flush=True)




    mode = str(cfg.get("crop_mode", "geometric")).lower()
    if mode != "off":
        n_full = P.shape[1]
        if mode == "data":



            crop_p = np.abs(P) ** 2 if np.iscomplexobj(P) else P
            crop_s = np.abs(S) ** 2 if np.iscomplexobj(S) else S
            n_eff = sc.effective_range_bins(
                crop_p, crop_s, noise_power,
                range_res_m=float(cfg["_range_res_m"]),
                range_min=range_min, range_max=range_max, alpha_db_m=alpha)
            why = "신호가 노이즈에 묻히는 지점"
        else:




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
    else:
        pg, sg = pp, sp
        across_px = (range_max - range_min) / pp.shape[1]

    img = np.concatenate([pg[:, ::-1], sg], axis=1)





    vmin, vmax = sc.display_window_db(
        range_min, range_max, alpha,
        apply_tvg=bool(cfg["tvg"]),
        reference_range_m=base_altitude(cfg),
        noise_quantile=float(cfg.get(
            "display_noise_quantile", sc.DISPLAY_NOISE_BLACK_QUANTILE)))





    physical_vmin = vmin
    display_span = float(cfg.get("display_dynamic_range_db", 0.0) or 0.0)
    if display_span > 0.0:
        vmin = max(float(vmin), float(vmax) - display_span)
    print(f"   [표시] 창 {vmin:.1f} ~ {vmax:.1f} dB "
          f"(폭 {vmax-vmin:.1f} dB, 물리 하한 {physical_vmin:.1f} dB"
          + (f", 고정 표시폭 {display_span:.1f} dB" if display_span > 0 else "")
          + ")", flush=True)

    legstr = f"_leg{leg:02d}" if leg is not None else ""
    fname = f"waterfall_sss_{plat}_{param_tag(cfg)}{legstr}.png"
    if save_processed:
        np.save(procnpy_dir/f"wf_sss_{plat}{legstr}_db.npy", img)
    raw_path = raw_dir / fname if save_png else None
    ta_path = ta_dir / fname if save_ta else None
    raw_p, ta_p = sc.save_waterfall(img, raw_path, ta_path, across_px, DX,
                                    vmin=vmin, vmax=vmax)
    if raw_p is not None:
        print(f"   -> waterfall/{raw_p.name}  ({img.shape[0]}x{img.shape[1]}px)", flush=True)

    if G is not None and (save_mask or save_bbox or save_overlay):
        gp, gs = G
        if cfg["slant_range_correction"]:
            gp, _ = to_ground(gp, altitude, cfg["range_min_m"], cfg["range_max_m"])
            gs, _ = to_ground(gs, altitude, cfg["range_min_m"], cfg["range_max_m"])
        mask = np.concatenate([gp[:, ::-1], gs], axis=1)
        mask = (mask >= 0.5).astype(np.uint8)

        filled = sc.fill_label_holes(mask, int(cfg.get("gt_fill_radius_px", 5)))
        from PIL import Image
        filled_saved = np.flipud(filled)
        if save_mask and mask_dir is not None:
            Image.fromarray(filled_saved * 255).save(str(mask_dir/fname))
            np.save(mask_dir/f"{pathlib.Path(fname).stem}_mask.npy", filled_saved)






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
