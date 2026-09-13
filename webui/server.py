import argparse
import json
import mimetypes
import os
import pathlib
import shlex
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
WEB = ROOT / "webui"
SCRIPTS = ROOT / "scripts"
DATA = ROOT / "data"
sys.path.insert(0, str(SCRIPTS))

import project_paths as pp

SENSOR_CFG = SCRIPTS / "sss_config.json"
SCENE_CFG = SCRIPTS / "scene_config.json"
RUN_DIR = DATA / "webui_runs"
RUN_DIR.mkdir(parents=True, exist_ok=True)
ACTIVE_JOB_FILE = RUN_DIR / "active_job.json"

import numpy as np
import sss_common as sc
import acquire_sss as A





def load_json(p):
    return json.loads(pathlib.Path(p).read_text())


def merge_save(path, updates):
    cur = load_json(path)
    _deep_update(cur, updates)
    pathlib.Path(path).write_text(json.dumps(cur, ensure_ascii=False, indent=2))
    return cur


def _deep_update(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_update(dst[k], v)
        else:
            dst[k] = v






def derive(sensor):
    cfg = dict(load_json(SENSOR_CFG))
    cfg.update(sensor)
    cfg = A.derive(cfg)

    c = float(cfg["sound_speed_ms"])
    rho = float(cfg["water_density"])
    f_khz = float(cfg["frequency_khz"])
    rmin = float(cfg["range_min_m"])
    rmax = float(cfg["range_max_m"])
    rres = float(cfg["_range_res_m"])
    vb = float(cfg["vertical_beam_deg"])
    dep = float(cfg["depression_deg"])
    alt = A.base_altitude(cfg)
    alpha = sc.francois_garrison_alpha(f_khz) if cfg.get("absorption", True) else 0.0


    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        d_th = A.elev_ray_res_deg(cfg)
    n_rays = int(np.ceil(vb / d_th)) + 1
    ray_note = buf.getvalue().strip()

    reach = sc.beam_reach_m(alt, dep, vb)











    lo, hi = dep - vb / 2.0, dep + vb / 2.0
    hi_eff = min(max(hi, 0.05), 90.0)
    slant_near = alt / np.sin(np.radians(hi_eff))
    slant_near = float(min(max(slant_near, rmin), rmax))
    slant_far = (alt / np.sin(np.radians(lo)) if lo > 0.05 else np.inf)
    slant_far = float(min(slant_far, rmax))
    if slant_far < slant_near:
        slant_far = slant_near
    ground_near = float(np.sqrt(max(slant_near ** 2 - alt ** 2, 0.0)))
    ground_far = float(np.sqrt(max(slant_far ** 2 - alt ** 2, 0.0)))

    blank_frac = float(min(max((slant_near - rmin) / max(rmax - rmin, 1e-9), 0.0), 1.0))
    vmin_db, vmax_db = sc.display_window_db(rmin, rmax, alpha)
    ref_bs = sc.reference_backscatter()
    ref_db = 10 * np.log10(ref_bs)
    nf = sc.noise_floor_from_rated_range(rmin, alpha)


    rr = np.linspace(max(rmin, 0.5), rmax, 80)
    TL = 20 * np.log10(rr / rmin) + alpha * rr
    noise_db = 10 * np.log10(nf) + 2 * TL
    snr = ref_db - noise_db


    fan = []
    for th in np.linspace(max(lo, 0.05), min(hi, 89.9), 40):
        R = alt / np.sin(np.radians(th))
        fan.append({"dep": float(th), "slant": float(min(R, rmax * 3)),
                    "ground": float(min(R, rmax * 3) * np.cos(np.radians(th))),
                    "inside": bool(R <= rmax)})


    try:
        legs = [{"p0": [float(p0[0]), float(p0[1])],
                 "p1": [float(p1[0]), float(p1[1])], "yaw": float(yaw)}
                for p0, p1, yaw in A.legs_layout(cfg)]
    except Exception:
        legs = []

    return {
        "legs": legs,

        "swath_m": ground_far,
        "ground_near_m": ground_near,
        "ground_far_m": ground_far,
        "slant_near_m": slant_near,
        "slant_far_m": slant_far,
        "blank_frac": blank_frac,
        "impedance_water": rho * c,
        "alpha_db_m": alpha,
        "altitude_m": alt,
        "sensor_z_m": A.sensor_z(cfg),
        "range_res_m": rres,
        "range_res_theory_m": cfg["_Ry_m"],
        "along_res_m": cfg["_Rx_m"],
        "range_bins": int(round((rmax - rmin) / rres)),
        "elev_ray_res_deg": d_th,
        "elev_rays": n_rays,
        "ray_note": ray_note,
        "beam_reach_m": reach if np.isfinite(reach) else None,
        "beam_lo_deg": lo, "beam_hi_deg": hi,
        "nadir_frac": blank_frac,
        "nadir_ground_m": ground_far,
        "altitude_ratio": alt / rmax,
        "display_vmin_db": vmin_db, "display_vmax_db": vmax_db,
        "ref_backscatter_db": float(ref_db),
        "noise_floor": float(nf),
        "snr_curve": {"r": rr.tolist(), "snr_db": snr.tolist(),
                      "noise_db": noise_db.tolist(), "signal_db": float(ref_db)},
        "fan": fan,
        "warnings": _warnings(cfg, alt, rmax, reach, n_rays, rres),
    }


def _warnings(cfg, alt, rmax, reach, n_rays, rres):
    w = []
    ratio = alt / rmax
    if ratio < 0.10:
        w.append(f"고도/레인지 = {ratio*100:.1f}% — 10% 룰 하한 미만(너무 낮게 예인). "
                 "원거리가 극단적으로 스치듯 입사한다.")
    elif ratio > 0.20:
        w.append(f"고도/레인지 = {ratio*100:.1f}% — 10% 룰 상한 초과(너무 높게 예인). "
                 "그림자가 짧아져 표적 탐지가 나빠진다.")
    if np.isfinite(reach) and reach < rmax:
        lost = (1 - reach / rmax) * 100
        w.append(f"빔이 {reach:.1f} m 까지만 닿는데 레인지는 {rmax:.0f} m — "
                 f"스와스의 {lost:.0f}% 가 크롭으로 버려진다. 복각을 낮출 것.")
    if n_rays >= int(cfg.get("elev_ray_max", 8000)):
        w.append(f"수직 레이 {n_rays}개 — 상한에 걸렸다. 원거리 range bin 이 비어 "
                 "노이즈로 채워진다. 레인지를 줄이거나 고도를 올릴 것.")
    if abs(float(cfg["depression_deg"]) - 25.0) > 0.01:
        w.append("복각이 25° 가 아니다. EdgeTech 4205 는 틸트가 25° 로 기계 고정이라 "
                 "실제 장비에는 없는 기하다.")
    if rmax > sc.RATED_MAX_RANGE_M:
        w.append(f"레인지 {rmax:.0f} m 가 센서 정격 최대 {sc.RATED_MAX_RANGE_M:.0f} m "
                 "(EdgeTech 2205, 540 kHz)를 넘습니다. 정격 밖은 해저 반사가 노이즈 "
                 "위로 나오지 않아 표시 창이 뒤집힙니다(정격까지만 보고 계산합니다). "
                 "지형 기복이 커서 10% 룰이 큰 레인지를 요구했다면, 고도를 낮추거나 "
                 "기복이 작은 지형을 쓰세요.")
    if rres < 0.05:
        w.append(f"range_res {rres*100:.2f} cm — 레이 수가 급증해 취득이 매우 느려진다.")
    return w





_terrain_cache = {}
_mesh_cache = {}
PREVIEW_MAX_FACES = 15000


def mesh_preview(catalog_id, target_size=None):
    key = (catalog_id, target_size)
    if key in _mesh_cache:
        return _mesh_cache[key]
    import object_catalog as oc
    import meshio
    ent = next((o for o in oc.load_catalog().get("objects", [])
                if o["id"] == catalog_id), None)
    if ent is None:
        return {"error": "unknown id " + catalog_id}
    src = ROOT / ent["source"]
    if not src.exists():
        return {"error": "파일 없음: " + ent["source"]}
    try:
        out = meshio.preview(src, max_faces=PREVIEW_MAX_FACES, target_size=target_size)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    out["id"] = catalog_id
    out["category"] = ent["category"]
    out["source"] = ent["source"]
    out["tags"] = ent.get("tags", [])
    _mesh_cache[key] = out
    return out


def _mesh_from_grid(xs, ys, depth, max_cells=110):
    ny, nx = depth.shape
    sx = max(1, nx // max_cells); sy = max(1, ny // max_cells)
    z = -depth[::sy, ::sx]
    X, Y = np.meshgrid(xs[::sx], ys[::sy])
    zmin, zmax = float(z.min()), float(z.max())
    zr = (zmax - zmin) or 1.0
    verts = np.stack([X.ravel(), Y.ravel(), z.ravel()], 1)
    u = ((verts[:, 2] - zmin) / zr)[:, None]
    col = np.hstack([0.10 + u * 0.72, 0.26 + u * 0.42, 0.34 + u * 0.10, np.ones_like(u)])
    gy, gx = z.shape
    idx = np.arange(gx * gy).reshape(gy, gx)
    a = idx[:-1, :-1].ravel(); b = idx[:-1, 1:].ravel()
    c = idx[1:, 1:].ravel(); d = idx[1:, :-1].ravel()
    faces = np.concatenate([np.stack([a, b, c], 1), np.stack([a, c, d], 1)])
    return {
        "verts": [round(float(v), 3) for v in verts.ravel()],
        "faces": [int(i) for i in faces.ravel()],
        "colors": [round(float(v), 4) for v in col.ravel()],
        "nx": int(gx), "ny": int(gy), "zmin": zmin, "zmax": zmax,
        "x0": float(xs[0]), "x1": float(xs[-1]),
        "y0": float(ys[0]), "y1": float(ys[-1]),
    }


def terrain_preview(params):
    import make_terrain as mt
    xs, ys, depth = mt.build(params)
    m = _mesh_from_grid(xs, ys, depth)
    m["depth_min_m"] = round(float(depth.min()), 3)
    m["depth_max_m"] = round(float(depth.max()), 3)
    m["relief_m"] = round(float(depth.max() - depth.min()), 3)
    m["seabed_top_m"] = round(-float(depth.min()), 2)
    m["seabed_bottom_m"] = round(-float(depth.max()), 2)
    m["grid"] = [int(depth.shape[1]), int(depth.shape[0])]
    return m


def terrain_mesh(csv_name, max_cells=110):
    g = terrain_grid(csv_name, max_cells=max_cells)
    if not g:
        return None
    nx, ny = g["nx"], g["ny"]
    z = np.array([np.nan if v is None else v for v in g["z"]], dtype=float).reshape(ny, nx)
    xs = np.linspace(g["x0"], g["x1"], nx)
    ys = np.linspace(g["y0"], g["y1"], ny)
    X, Y = np.meshgrid(xs, ys)
    zmin, zmax = g["zmin"], g["zmax"]
    zr = (zmax - zmin) or 1.0

    verts = np.stack([X.ravel(), Y.ravel(), np.nan_to_num(z.ravel(), nan=zmin)], 1)

    u = ((verts[:, 2] - zmin) / zr)[:, None]
    col = np.hstack([0.10 + u * 0.72, 0.26 + u * 0.42, 0.34 + u * 0.10,
                     np.ones_like(u)])

    idx = np.arange(nx * ny).reshape(ny, nx)
    a = idx[:-1, :-1].ravel(); b = idx[:-1, 1:].ravel()
    c = idx[1:, 1:].ravel(); d = idx[1:, :-1].ravel()
    faces = np.concatenate([np.stack([a, b, c], 1), np.stack([a, c, d], 1)])

    return {
        "verts": [round(float(v), 3) for v in verts.ravel()],
        "faces": [int(i) for i in faces.ravel()],
        "colors": [round(float(v), 4) for v in col.ravel()],
        "nx": nx, "ny": ny, "zmin": zmin, "zmax": zmax,
        "x0": g["x0"], "x1": g["x1"], "y0": g["y0"], "y1": g["y1"],
    }


def terrain_grid(csv_name, max_cells=140):
    if csv_name in _terrain_cache:
        return _terrain_cache[csv_name]
    p = pp.find_config_file(pathlib.Path("mado_terrain") / csv_name)
    if not p.exists():
        return None
    raw = np.genfromtxt(p, delimiter=",", names=True)
    ix = raw["ix"].astype(int); iy = raw["iy"].astype(int)
    nx, ny = ix.max() + 1, iy.max() + 1
    z = np.full((ny, nx), np.nan)
    z[iy, ix] = -raw["depth_m"]
    sx = max(1, nx // max_cells); sy = max(1, ny // max_cells)
    zs = z[::sy, ::sx]
    out = {
        "nx": int(zs.shape[1]), "ny": int(zs.shape[0]),
        "x0": float(raw["x_m"].min()), "x1": float(raw["x_m"].max()),
        "y0": float(raw["y_m"].min()), "y1": float(raw["y_m"].max()),
        "zmin": float(np.nanmin(z)), "zmax": float(np.nanmax(z)),
        "z": [None if np.isnan(v) else round(float(v), 3) for v in zs.ravel()],
    }
    _terrain_cache[csv_name] = out
    return out





class Job:
    def __init__(self, jid, cmd, logpath, *, started=None, pid=None, recovered=False):
        self.id = jid
        self.cmd = cmd
        self.logpath = logpath
        self.proc = None
        self.pid = pid
        self.recovered = recovered
        self.started = float(started) if started is not None else time.time()
        self.finished = None
        self.rc = None
        self.outdir = None
        self.preview_path = None

    def running(self):
        if self.proc is not None:
            return self.proc.poll() is None
        if not self.pid:
            return False
        try:
            os.kill(int(self.pid), 0)
        except (OSError, ValueError):
            return False

        try:
            cmdline = pathlib.Path(f"/proc/{int(self.pid)}/cmdline").read_bytes()
            return b"large_dataset.py" in cmdline
        except OSError:
            return False

    def to_dict(self):
        log = ""
        try:
            log = pathlib.Path(self.logpath).read_text(errors="replace")
        except OSError:
            pass




        if self.outdir is None:
            for line in log.splitlines():
                if "완료:" in line and "data/images" in line:
                    self.outdir = line.rsplit("->", 1)[-1].strip()
                elif line.startswith("[dataset-root] "):

                    self.outdir = line.split("]", 1)[1].strip()
        dataset_status = None
        if self.outdir:
            try:
                op = pathlib.Path(self.outdir)
                if not op.is_absolute():
                    op = ROOT / op
                ip = op / "index.json"
                if ip.is_file():
                    dataset_status = json.loads(ip.read_text()).get("status")
            except (OSError, ValueError):
                pass
        return {
            "id": self.id, "cmd": self.cmd, "log": log,
            "running": self.running(),
            "rc": self.rc, "outdir": self.outdir,
            "elapsed": round((self.finished or time.time()) - self.started, 1),
            "images": self._images(),
            "preview": self._preview_img(),
            "dataset_status": dataset_status,
            "recovered": self.recovered,
        }

    def _preview_img(self):
        if not self.preview_path:
            return None
        p = ROOT / self.preview_path
        return self.preview_path if p.is_file() else None

    def _images(self):
        if not self.outdir:
            return []
        d = pathlib.Path(self.outdir)
        if not d.is_absolute():
            d = ROOT / d
        out = []
        samples = d / "samples"
        if samples.is_dir():
            patterns = (
                ("preview", "*/preview.png"),
                ("waterfall", "*/sss/waterfall/*.png"),
                ("mask", "*/annotations/masks/*.png"),
                ("bbox_overlay", "*/annotations/bbox_overlays/*.png"),
                ("true_aspect", "*/sss/true_aspect/*.png"),
            )
            for kind, pattern in patterns:
                for f in sorted(samples.glob(pattern))[-12:]:
                    sample_id = f.relative_to(samples).parts[0]
                    out.append({"kind": kind, "path": str(f.relative_to(ROOT)),
                                "name": f"{sample_id}/{f.name}"})
        return out


JOBS = {}
JOB_LOCK = threading.Lock()


def _atomic_json(path, value):
    path = pathlib.Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    tmp.replace(path)


def _remember_active_job(job):
    _atomic_json(ACTIVE_JOB_FILE, {
        "id": job.id, "cmd": job.cmd, "logpath": str(job.logpath),
        "started": job.started, "pid": job.pid,
    })


def _active_job():
    with JOB_LOCK:
        running = [j for j in JOBS.values() if j.running()]
    return max(running, key=lambda j: j.started) if running else None


def _restore_active_job():
    try:
        raw = json.loads(ACTIVE_JOB_FILE.read_text())
        job = Job(str(raw["id"]), list(raw.get("cmd") or []), raw["logpath"],
                  started=raw.get("started"), pid=raw.get("pid"), recovered=True)
        if job.running():
            JOBS[job.id] = job
    except (OSError, ValueError, KeyError, TypeError):
        pass


def start_job(cmd):
    jid = time.strftime("%Y%m%d-%H%M%S")
    logpath = RUN_DIR / f"{jid}.log"
    job = Job(jid, cmd, logpath)
    with JOB_LOCK:
        JOBS[jid] = job

    def _run():
        with open(logpath, "w") as lf:
            lf.write("$ " + " ".join(shlex.quote(c) for c in cmd) + "\n\n")
            lf.flush()
            job.proc = subprocess.Popen(
                cmd, cwd=str(ROOT), stdout=lf, stderr=subprocess.STDOUT,
                env={**os.environ, "PYTHONUNBUFFERED": "1"})
            job.pid = job.proc.pid
            _remember_active_job(job)
            job.rc = job.proc.wait()
            job.finished = time.time()

    threading.Thread(target=_run, daemon=True).start()
    return job




_restore_active_job()


def list_runs(limit=40):
    out = []
    dataset_root = DATA / "datasets"
    if dataset_root.is_dir():
        for d in sorted((x for x in dataset_root.iterdir() if x.is_dir()),
                        reverse=True)[:limit]:
            raws = sorted((d / "samples").glob("*/sss/waterfall/*.png"))
            previews = sorted((d / "samples").glob("*/preview.png"))
            thumbs = raws or previews
            if not thumbs:
                continue
            status = None
            try:
                status = json.loads((d / "index.json").read_text()).get("status")
            except (OSError, ValueError):
                pass
            out.append({
                "id": d.name,
                "n": len(list((d / "samples").glob("scene_*"))),
                "status": status,
                "thumb": str(thumbs[0].relative_to(ROOT)),
                "images": [str(f.relative_to(ROOT)) for f in raws],
            })
    return out





class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass


    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")


    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            if u.path == "/" or u.path == "/index.html":
                return self._static("index.html")
            if u.path in ("/app.js", "/style.css", "/gl.js", "/viz3d.js"):
                return self._static(u.path.lstrip("/"))
            if u.path == "/api/state":
                return self._send(200, self._state())
            if u.path == "/api/active_job":
                job = _active_job()
                return self._send(200, job.to_dict() if job else {"job": None})
            if u.path == "/api/terrain":
                t = terrain_grid(q.get("csv", [""])[0])
                return self._send(200, t or {})
            if u.path == "/api/terrain3d":
                t = terrain_mesh(q.get("csv", [""])[0])
                return self._send(200, t or {})
            if u.path == "/api/mesh":
                ts = q.get("size", [None])[0]
                return self._send(200, mesh_preview(
                    q.get("id", [""])[0], float(ts) if ts else None))
            if u.path == "/api/runs":
                return self._send(200, list_runs())
            if u.path == "/api/job":
                jid = q.get("id", [""])[0]
                job = JOBS.get(jid)
                return self._send(200, job.to_dict() if job else {"error": "no such job"})
            if u.path == "/file":
                return self._file(q.get("path", [""])[0])
            return self._send(404, {"error": "not found"})
        except Exception as e:
            import traceback
            return self._send(500, {"error": str(e), "trace": traceback.format_exc()})

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        try:
            b = self._body()
            if u.path == "/api/derive":
                return self._send(200, derive(b.get("sensor", {})))
            if u.path == "/api/save_sensor":
                merge_save(SENSOR_CFG, b.get("sensor", {}))
                return self._send(200, {"ok": True})
            if u.path == "/api/save_scene":
                merge_save(SCENE_CFG, b.get("scene", {}))
                return self._send(200, {"ok": True})
            if u.path == "/api/terrain_preview":
                return self._send(200, terrain_preview(b.get("params", {})))
            if u.path == "/api/terrain_create":
                return self._send(200, self._terrain_create(b))
            if u.path == "/api/manifest":
                return self._send(200, self._manifest(b))
            if u.path == "/api/manifest_save":
                return self._send(200, self._manifest_save(b))
            if u.path == "/api/dataset":
                return self._send(200, self._dataset_acquire(b))
            if u.path == "/api/preview":
                return self._send(200, self._preview(b))
            if u.path == "/api/stop":
                job = JOBS.get(b.get("id"))
                if job and job.proc and job.proc.poll() is None:
                    job.proc.terminate()
                subprocess.run(["pkill", "-f", "Binaries/Linux/Holodeck"],
                               capture_output=True)
                return self._send(200, {"ok": True})
            return self._send(404, {"error": "not found"})
        except Exception as e:
            import traceback
            return self._send(500, {"error": str(e), "trace": traceback.format_exc()})


    def _state(self):
        import object_catalog as oc
        import make_terrain as mt
        scene = load_json(SCENE_CFG)
        try:
            catalog = oc.load_catalog().get("objects", [])
        except Exception:
            catalog = []
        job = _active_job()
        return {
            "sensor": load_json(SENSOR_CFG),
            "scene": scene,
            "catalog": catalog,
            "terrains": scene.get("terrains", []),
            "materials": mt.material_db(),
            "active_job_id": job.id if job else None,
            "terrain_presets": mt.PRESETS,
            "disk_free_gib": round(os.statvfs(DATA).f_bavail * os.statvfs(DATA).f_frsize
                                   / (1024 ** 3), 1),
            "manifests": sorted(
                str(p.relative_to(ROOT)) for p in (DATA / "scenes").glob("*.jsonl")
            ) if (DATA / "scenes").is_dir() else [],
        }

    def _terrain_create(self, b):
        import re
        import make_terrain as mt
        name = str(b.get("name", "")).strip()

        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{1,48}", name):
            return {"error": "이름은 영문/숫자/_/- 2~49자여야 합니다: " + name}
        protected = {"mado_report_environment_v1", "mado_district1_environment_v1"}
        if name in protected:
            return {"error": f"'{name}' 은 환경 프로젝트가 준 지형이라 덮어쓸 수 없습니다."}
        try:
            info = mt.install(name, b.get("params", {}), note=b.get("note"), quiet=True)
        except SystemExit as e:
            return {"error": str(e)}
        _terrain_cache.pop(f"{name}_terrain.csv", None)
        return info

    def _manifest(self, b):
        out = DATA / "scenes" / (b.get("out") or "webui_scene.jsonl")
        out.parent.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, str(SCRIPTS / "scene_manifest.py"),
               "--n", str(int(b.get("n", 1))), "--seed", str(int(b.get("seed", 0))),
               "--out", str(out)]
        if b.get("terrain"):
            cmd += ["--terrain", b["terrain"]]
        if b.get("survey_override"):
            cmd += ["--survey-override", json.dumps(b["survey_override"])]
        if b.get("wreck_tilt_deg") is not None:
            cmd += ["--wreck-tilt-deg", str(float(b["wreck_tilt_deg"]))]
        r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
        scenes = []
        if out.exists():
            scenes = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
        return {"rc": r.returncode, "stdout": r.stdout + r.stderr,
                "path": str(out.relative_to(ROOT)), "scenes": scenes}

    def _preview(self, b):
        out = DATA / "preview" / (time.strftime("%Y%m%d-%H%M%S") + ".png")
        cmd = [sys.executable, str(SCRIPTS / "preview_scene.py"),
               "--out", str(out), "--size", str(int(b.get("size", 768)))]
        if b.get("manifest"):
            cmd += ["--manifest", b["manifest"],
                    "--manifest_index", str(int(b.get("manifest_index", 0)))]
        if b.get("pitch_deg") is not None:
            cmd += ["--pitch-deg", str(float(b["pitch_deg"]))]
        for k, v in (b.get("overrides") or {}).items():
            if v is None or v == "":
                continue
            cmd += [f"--{k}", str(v).lower() if isinstance(v, bool) else str(v)]
        job = start_job(cmd)
        job.preview_path = str(out.relative_to(ROOT))
        return {"job": job.id, "cmd": cmd, "preview": job.preview_path}

    def _manifest_save(self, b):
        rel = str(b.get("path", ""))
        p = (ROOT / rel).resolve()
        scenes_dir = (DATA / "scenes").resolve()
        if not str(p).startswith(str(scenes_dir)) or p.suffix != ".jsonl":
            return {"error": "data/scenes 의 .jsonl 만 편집할 수 있습니다: " + rel}
        if not p.is_file():
            return {"error": "파일 없음: " + rel}
        lines = [l for l in p.read_text().splitlines() if l.strip()]
        i = int(b.get("index", 0))
        if not (0 <= i < len(lines)):
            return {"error": f"index {i} 범위 밖 (총 {len(lines)}줄)"}



        def strip(d):
            return {k: v for k, v in d.items() if not k.startswith("_")}
        scene = strip(dict(b.get("scene") or {}))
        if isinstance(scene.get("objects"), list):
            scene["objects"] = [strip(o) if isinstance(o, dict) else o
                                for o in scene["objects"]]
        lines[i] = json.dumps(scene, ensure_ascii=False)
        p.write_text("\n".join(lines) + "\n")
        return {"ok": True, "objects": len(scene.get("objects", []))}

    def _dataset_acquire(self, b):
        import re
        name = str(b.get("name") or "westsea_sss").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{1,48}", name):
            return {"error": "데이터셋 이름은 영문/숫자/_/- 2~49자여야 합니다."}
        try:
            n = int(b.get("n", 100))
            seed = int(b.get("seed", 100000))
            floor_clutter_pct = float(b.get("floor_clutter_pct", 200.0))
            wreck_pct = float(b.get("wreck_pct", 200.0))
            wreck_tilt_deg = float(b.get("wreck_tilt_deg", 90.0))
            depression_deg = float(b.get("depression_deg", 35.0))
            noise_level = float(b.get("noise_level", 1.0))
            speckle_strength = float(b.get("speckle_strength", 1.0))
            texture_cv = float(b.get("texture_cv", 0.0))
        except (TypeError, ValueError):
            return {"error": "씬 수·시드·객체 배치·자세·복각·노이즈 값이 올바르지 않습니다."}
        if not 1 <= n <= 5000:
            return {"error": f"씬 수는 1~5000이어야 합니다 (받은 값 {n})."}
        if not 0.0 <= floor_clutter_pct <= 300.0:
            return {"error": "바닥 클러터 배치량은 0~300%여야 합니다."}
        if not 0.0 <= wreck_pct <= 300.0:
            return {"error": "난파선 배치량은 0~300%여야 합니다."}
        if not 0.0 <= wreck_tilt_deg <= 90.0:
            return {"error": "난파선 기울기 최대값은 0~90°여야 합니다."}
        if not 25.1 <= depression_deg <= 60.0:
            return {"error": "복각은 25.1~60°여야 합니다 (50° 수직빔이 수면 위를 보지 않도록 제한)."}
        if not 0.0 <= noise_level <= 4.0:
            return {"error": "수신기 노이즈 배율은 0~4여야 합니다."}
        if not 0.0 <= speckle_strength <= 1.0:
            return {"error": "스페클 강도는 0~1이어야 합니다."}
        if not 0.0 <= texture_cv <= 1.0:
            return {"error": "저주파 반사 텍스처 CV는 0~1이어야 합니다."}
        platform = str(b.get("platform") or "ideal")
        if platform not in ("ideal", "asv"):
            return {"error": "platform은 ideal 또는 asv여야 합니다."}

        output_keys = {
            "waterfall_png", "true_aspect_png", "raw_numpy", "processed_numpy",
            "mask", "bbox", "bbox_overlay", "scene_preview", "export_zip",
        }
        incoming_outputs = b.get("outputs") or {}
        outputs = {key: bool(incoming_outputs.get(key, False)) for key in output_keys}
        if outputs["bbox_overlay"] and not outputs["waterfall_png"]:
            return {"error": "bbox 검수 오버레이에는 워터폴 PNG가 필요합니다."}
        if not any(outputs[k] for k in output_keys - {"export_zip"}):
            return {"error": "저장할 산출물을 하나 이상 선택하세요."}

        spec = {"name": name, "n": n, "seed": seed, "platform": platform,
                "floor_clutter_pct": floor_clutter_pct,
                "wreck_pct": wreck_pct,
                "wreck_tilt_deg": wreck_tilt_deg,
                "depression_deg": depression_deg, "noise_level": noise_level,
                "speckle_strength": speckle_strength, "texture_cv": texture_cv,
                "outputs": outputs,
                "created_by": "webui-dataset-v2"}
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        sp = RUN_DIR / f"dataset_spec_{time.strftime('%Y%m%d-%H%M%S')}.json"
        sp.write_text(json.dumps(spec, indent=2, ensure_ascii=False))
        cmd = [sys.executable, "-u", str(SCRIPTS / "large_dataset.py"),
               "--spec", str(sp)]
        if b.get("dry_run"):
            cmd.append("--dry-run")
        return {"job": start_job(cmd).id, "cmd": cmd, "spec_path": str(sp)}

    def _static(self, name):
        p = WEB / name
        if not p.exists():
            return self._send(404, "missing " + name, "text/plain")
        ctype = mimetypes.guess_type(str(p))[0] or "text/plain"
        return self._send(200, p.read_bytes(), ctype + "; charset=utf-8")

    def _file(self, rel):
        if not rel:
            return self._send(400, {"error": "path required"})
        p = (ROOT / rel).resolve()
        if not str(p).startswith(str(DATA.resolve())) or not p.is_file():
            return self._send(403, {"error": "forbidden"})
        ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
        return self._send(200, p.read_bytes(), ctype)


def main():
    ap = argparse.ArgumentParser(description="SSS 취득 웹 UI")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"SSS 취득 웹 UI  ->  http://{a.host}:{a.port}")
    print("종료: Ctrl-C")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n종료")


if __name__ == "__main__":
    main()
