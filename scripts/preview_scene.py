import argparse
import os
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import holoocean
import acquire_sss as A

ROOT = pathlib.Path(__file__).resolve().parents[1]


CAMERA_HFOV_DEG = 92.0
CAM_BODY_OFFSET_M = 3.0


def camera_height(extent, margin=1.12, hfov_deg=CAMERA_HFOV_DEG):
    x0, y0, x1, y1 = extent
    half = max(abs(x1 - x0), abs(y1 - y0)) / 2.0
    return float(half * margin / np.tan(np.radians(hfov_deg / 2.0)))


def build_scenario(size, cam_z, pitch_deg, yaw_deg):
    return {
        "name": "sss_preview", "world": "ExampleLevel", "package_name": "TestWorlds",
        "main_agent": "cam", "ticks_per_sec": 60,
        "agents": [{
            "agent_name": "cam", "agent_type": "HoveringAUV", "control_scheme": 0,
            "location": [0.0, 0.0, cam_z], "rotation": [0, 0, yaw_deg],
            "sensors": [
                {"sensor_type": "RGBCamera", "sensor_name": "top",
                 "location": [0, 0, -CAM_BODY_OFFSET_M],
                 "rotation": [0, float(pitch_deg), 0],
                 "configuration": {"CaptureWidth": size, "CaptureHeight": size}},
                {"sensor_type": "PoseSensor", "sensor_name": "pose"},
            ],
        }],
    }


def capture(cfg, out_path, size=768, height=None, pitch_deg=90.0, yaw_deg=0.0,
            settle=90, underwater=False):
    scene = cfg.get("_scene")
    terrain = (scene or {}).get("terrain", {})
    extent = terrain.get("extent_m", [-150.0, -120.0, 150.0, 120.0])
    seabed = float(cfg.get("seabed_top_m", A.SEABED_TOP_DEFAULT))
    h = float(height) if height else camera_height(extent)

    cam_z = seabed + h
    if underwater:

        cam_z = min(cam_z, -1.0)
        h = cam_z - seabed

    preset = terrain.get("scene_preset")
    use_flat = bool(cfg.get("flat_seabed", True))
    if preset and not use_flat:
        os.environ["HOLOOCEAN_SHIPWRECK_SPAWN"] = "1"
        os.environ["HOLOOCEAN_SHIPWRECK_SCENE_PRESET"] = str(preset)
        print(f"[preview] 필드 지형 프리셋 = {preset}", flush=True)
    else:
        os.environ.pop("HOLOOCEAN_SHIPWRECK_SPAWN", None)
        os.environ.pop("HOLOOCEAN_SHIPWRECK_SCENE_PRESET", None)

    cx = (extent[0] + extent[2]) / 2.0
    cy = (extent[1] + extent[3]) / 2.0
    print(f"[preview] 카메라 ({cx:.0f}, {cy:.0f}, {cam_z:.1f}) m, 해저 위 {h:.0f} m, "
          f"pitch {pitch_deg:.0f}°, {size}x{size} px", flush=True)

    env = holoocean.make(scenario_cfg=build_scenario(size, cam_z, pitch_deg, yaw_deg),
                         show_viewport=False)
    import scene_runtime
    n_obj = 0
    if scene:
        scene_runtime.apply_scene(env, scene)
        n_obj = len(scene.get("objects", []))
    if use_flat:
        scene_runtime.spawn_flat_seabed(env, seabed)

    env.agents["cam"].teleport(np.array([cx, cy, cam_z]),
                               np.array([0.0, 0.0, float(yaw_deg)]))
    frame = None
    for _ in range(max(settle, 5)):
        st = env.tick()
        if "top" in st:
            frame = st["top"]
    if frame is None:
        raise RuntimeError("카메라 프레임을 받지 못했습니다")

    img = np.asarray(frame)[:, :, :3][:, :, ::-1].astype(np.uint8)






    if abs(pitch_deg - 90.0) < 1e-6:
        img = np.ascontiguousarray(np.rot90(img, 3))
    out = pathlib.Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    from PIL import Image
    Image.fromarray(img).save(out)


    span = 2.0 * h * float(np.tan(np.radians(CAMERA_HFOV_DEG / 2.0)))
    info = {
        "path": str(out), "size": size, "height_m": round(h, 1),
        "cam_z_m": round(cam_z, 2), "span_m": round(span, 1),
        "center_m": [cx, cy], "extent_m": extent, "pitch_deg": pitch_deg,
        "terrain": terrain.get("id"), "objects": n_obj,
        "flat_seabed": use_flat, "scene_preset": preset,
        "mean_brightness": round(float(img.mean()), 1),
    }
    print(f"[preview] 저장: {out}  (화면 폭 약 {span:.0f} m, 밝기 {img.mean():.0f})",
          flush=True)


    if img.mean() < 45:
        why = []
        if not preset and not use_flat:
            why.append("지형 프리셋도 없고 평탄 해저도 꺼져 있습니다")
        elif preset and use_flat:
            why.append("평탄 해저가 켜져 있어 지형을 덮었을 수 있습니다")
        print(f"[preview][warn] 화면이 거의 비어 있습니다(밝기 {img.mean():.0f}). "
              + ("; ".join(why) if why else "카메라 높이/지형 위치를 확인하세요."),
              flush=True)
    return out, info


def run(argv=None):
    ap = argparse.ArgumentParser(description="씬 탑뷰 미리보기 캡처")
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--manifest_index", type=int, default=0)
    ap.add_argument("--out", default=str(ROOT / "data/preview/preview.png"))
    ap.add_argument("--size", type=int, default=768)
    ap.add_argument("--height", type=float, default=None,
                    help="해저 위 카메라 높이 [m]. 기본은 지형 전체가 들어오도록 자동")
    ap.add_argument("--pitch-deg", type=float, default=90.0,
                    help="90=바로 아래(탑뷰). 낮추면 비스듬해져 기복이 잘 보인다")
    ap.add_argument("--yaw-deg", type=float, default=0.0)
    ap.add_argument("--settle", type=int, default=90)
    ap.add_argument("--underwater", action="store_true",
                    help="수중에서 촬영(수심에 화각이 묶임). 기본은 수면 위")
    args, extra = ap.parse_known_args(argv)

    argv2 = ["preview", "ideal"] + extra
    if args.manifest:
        argv2 += ["--manifest", args.manifest,
                  "--manifest_index", str(args.manifest_index)]
    cfg = A.derive(A.load_config(argv2))
    capture(cfg, args.out, size=args.size, height=args.height,
            pitch_deg=args.pitch_deg, yaw_deg=args.yaw_deg,
            settle=args.settle, underwater=args.underwater)
    return 0


if __name__ == "__main__":
    sys.exit(run())
