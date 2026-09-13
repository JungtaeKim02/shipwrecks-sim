import csv
import json
import pathlib
import sys
import datetime

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG_PATH = pathlib.Path(__file__).resolve().parent / "scene_config.json"

MESH_EXTS = (".fbx", ".glb", ".obj", ".stl")

NATIVE_EXTS = (".fbx", ".obj")


def load_config():
    return json.loads(CONFIG_PATH.read_text())


def _ue_mesh_path(cfg, category, name):
    return f"{cfg['ue_mesh_root'].rstrip('/')}/{category}/{name}"


def _num(s):
    try:
        v = float(s)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def load_size_db(cfg):
    db = {}
    csv_path = ROOT / cfg["object_root"] / "asset_database.csv"
    if csv_path.is_file():
        for r in csv.DictReader(open(csv_path, encoding="utf-8")):
            dims = [_num(r.get(k)) for k in
                    ("Overall_Length_m", "Overall_Width_m", "Overall_Height_m")]
            dims = [d for d in dims if d]
            if not dims:
                continue
            rec = {
                "target_size_m": round(max(dims), 4),
                "bbox_real_m": [round(d, 4) for d in
                                (_num(r.get("Overall_Length_m")) or 0,
                                 _num(r.get("Overall_Width_m")) or 0,
                                 _num(r.get("Overall_Height_m")) or 0)],
                "size_source": (r.get("Source_1") or "").strip() or None,
                "size_source_url": (r.get("Source_1_URL") or "").strip() or None,
                "size_source_page": (r.get("Source_Page_or_Record") or "").strip() or None,
                "size_confidence": (r.get("Confidence") or "").strip() or None,
                "size_status": (r.get("Size_Status") or "").strip() or None,
                "base_object_id": (r.get("Base_Object_ID") or "").strip() or None,
                "name_en": (r.get("English_Name") or "").strip() or None,
                "name_ko": (r.get("Korean_Name") or "").strip() or None,
                "preview_files": [x.strip() for x in (r.get("Preview_File") or "").split(";") if x.strip()],
            }
            stem = pathlib.Path((r.get("Model_File") or "").strip()).stem
            if stem:
                db.setdefault(stem, rec)
            if rec["base_object_id"]:
                db.setdefault("base:" + rec["base_object_id"], rec)

    local_path = ROOT / cfg["object_root"] / "local_assets.json"
    if local_path.is_file():
        for a in json.loads(local_path.read_text()).get("assets", []):
            if not a.get("target_size_m"):
                continue
            db[a["name"]] = {
                "target_size_m": a["target_size_m"],
                "bbox_real_m": a.get("bbox_m"),
                "size_source": a.get("size_source"),
                "size_source_url": None, "size_source_page": None,
                "size_confidence": a.get("size_confidence"),
                "size_status": a.get("size_status"),
                "base_object_id": None,
                "name_en": a["name"], "name_ko": None,
                "material": a.get("material"),
                "tags": a.get("tags"),
            }
    return db


def _size_lookup(db, name):
    if name in db:
        return db[name]
    base = name.rsplit("_", 1)[0]
    return db.get("base:" + base) or db.get("base:" + name)


def _entry(cfg, category, name, source, available, importable=True, size_db=None):
    cat_cfg = cfg["object_categories"].get(category, {})
    tags = list(cat_cfg.get("tags", []))



    overrides = cfg.get("object_material_overrides", {})
    material = overrides.get(f"{category}/{name}") or cat_cfg.get("material")
    if material:

        tags.append(f"sssmat:{material}")

    e = {
        "id": f"{category}/{name}",
        "category": category,
        "name": name,
        "source": str(source) if source else None,
        "available": available,

        "importable": importable,
        "ue_mesh": _ue_mesh_path(cfg, category, name),
        "tags": tags,
        "material": material,
    }


    rec = _size_lookup(size_db or {}, name)
    if rec:
        for k in ("target_size_m", "bbox_real_m", "size_source", "size_source_url",
                  "size_source_page", "size_confidence", "size_status",
                  "base_object_id", "name_en", "name_ko", "preview_files"):
            if rec.get(k) is not None:
                e[k] = rec[k]
        if e.get("preview_files"):
            e["preview_paths"] = [f"data/objects/previews/{name}" for name in e["preview_files"]]



    if cat_cfg.get("enabled") is False:
        e["available"] = False
        e["held_reason"] = cat_cfg.get("_hold_reason", "카테고리 보류")

    held = cfg.get("held_objects", {})
    if e["id"] in held:
        e["available"] = False
        e["held_reason"] = held[e["id"]]

    elif e.get("target_size_m") is None and available:
        e["available"] = False
        e["held_reason"] = ("실물 치수 출처가 없다 — asset_database.csv 에도 "
                            "local_assets.json 에도 항목이 없어 배율을 정할 수 없다.")
    return e






MEASURED_FIELDS = ("measured_bbox_m", "measured_max_m", "triangles",
                   "normalized_source", "measured_from")


def build(cfg):
    obj_root = ROOT / cfg["object_root"]
    entries = {}
    size_db = load_size_db(cfg)



    prev, stale = {}, []
    prev_path = obj_root / "catalog.json"
    if prev_path.is_file():
        try:
            for o in json.loads(prev_path.read_text()).get("objects", []):
                if any(k in o for k in MEASURED_FIELDS):
                    prev[o["id"]] = o
        except Exception:
            pass


    for cat_dir in sorted(p for p in obj_root.glob("*") if p.is_dir()):
        category = cat_dir.name
        for sub in ("original", "ue_ready"):
            d = cat_dir / sub
            if not d.is_dir():
                continue
            for f in sorted(d.iterdir()):
                if f.suffix.lower() not in MESH_EXTS:
                    continue
                name = f.stem
                entries[f"{category}/{name}"] = _entry(
                    cfg, category, name, f.relative_to(ROOT), True,
                    importable=f.suffix.lower() in NATIVE_EXTS, size_db=size_db,
                )


    declared_path = obj_root / "declared.json"
    if declared_path.is_file():
        declared = json.loads(declared_path.read_text())
        for a in declared.get("assets", []):
            key = f"{a['category']}/{a['name']}"
            if key in entries:
                continue
            expected = f"{cfg['object_root']}/{a['category']}/original/{a['name']}.{a.get('ext', 'fbx')}"
            e = _entry(cfg, a["category"], a["name"], expected, False,
                       importable=a.get("ext", "fbx") in ("fbx", "obj"), size_db=size_db)


            also = e.get("held_reason")
            e["held_reason"] = "3D 파일 미도착"
            if also:
                e["held_reason_secondary"] = also
            entries[key] = e


    for key, e in entries.items():
        old = prev.get(key)
        if not old:
            continue
        for f in MEASURED_FIELDS:
            if f in old:
                e[f] = old[f]
        if old.get("measured_from") and old["measured_from"] != e["source"]:
            stale.append((key, old["measured_from"], e["source"]))
    if stale:
        print(f"[주의] 측정 당시와 소스 파일이 다른 자산 {len(stale)}건 — "
              f"scripts/measure_assets.py 를 다시 돌리는 것이 안전하다:")
        for k, a, b in stale[:5]:
            print(f"    {k}: {a} -> {b}")

    return {
        "version": 2,
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "size_sources": {
            "csv": f"{cfg['object_root']}/asset_database.csv",
            "local": f"{cfg['object_root']}/local_assets.json",
        },
        "_measured_note": ("measured_* 필드는 scripts/measure_assets.py 가 UE 임포트 "
                           "로그에서 재서 병합한다. 카탈로그를 다시 만들어도 "
                           "이어받으므로 사라지지 않는다."),
        "objects": [entries[k] for k in sorted(entries)],
    }


def load_catalog():
    path = ROOT / load_config()["object_root"] / "catalog.json"
    if not path.is_file():
        cat = build(load_config())
        path.write_text(json.dumps(cat, indent=2, ensure_ascii=False))
        return cat
    return json.loads(path.read_text())


def main(argv):
    cfg = cfg_ = load_config()
    catalog = build(cfg_)
    out = ROOT / cfg["object_root"] / "catalog.json"
    out.write_text(json.dumps(catalog, indent=2, ensure_ascii=False))

    objs = catalog["objects"]


    missing = [o for o in objs if not o["available"]
               and o.get("held_reason") == "3D 파일 미도착"]
    held = [o for o in objs if not o["available"] and o not in missing]

    if "--list" in argv:
        for o in objs:
            mark = "OK " if o["available"] else "제외"
            sz = f"{o['target_size_m']:>8.3f} m" if o.get("target_size_m") else "   (치수없음)"
            print(f"  [{mark}] {o['id']:<44}{sz}  {o.get('material') or '-'}")

    if "--check" in argv:
        for o in missing:
            print(f"[미배치] {o['id']}  기대 경로: {o['source']}")
        return 1 if missing else 0

    by_cat = {}
    for o in objs:
        by_cat.setdefault(o["category"], [0, 0])
        by_cat[o["category"]][0] += 1
        if o["available"]:
            by_cat[o["category"]][1] += 1

    print(f"카탈로그 -> {out.relative_to(ROOT)}")
    for c, (total, avail) in sorted(by_cat.items()):
        sizes = [o["target_size_m"] for o in objs
                 if o["category"] == c and o.get("target_size_m")]
        rng = f"{min(sizes):.2f}~{max(sizes):.1f} m" if sizes else "-"
        gt = " [GT]" if any(t == "wreck" for o in objs if o["category"] == c
                            for t in o["tags"]) else ""
        print(f"  {c:<22} {avail:3d}/{total:<3d} 사용 가능   실물 {rng:>16s}{gt}")
    print(f"  {'합계':<22} {sum(v[1] for v in by_cat.values()):3d}/{len(objs):<3d}")

    not_importable = [o for o in objs if o["available"] and not o["importable"]]
    no_size = [o for o in objs if o["available"] and not o.get("target_size_m")]
    if missing:
        print(f"\n  3D 파일 미도착 {len(missing)}건 (declared.json 선언분). --check 로 목록 확인")
    if held:
        print(f"  근거 부족 보류 {len(held)}건 (파일은 있음)")
    if not_importable:
        print(f"  변환 필요 {len(not_importable)}건 (UE 직접 임포트 불가 포맷):")
        for o in not_importable[:5]:
            print(f"    {o['id']}  -> ue_ready/ 에 fbx/obj 변환본 필요")
    if no_size:
        print(f"  [경고] 실물 치수 없는 활성 자산 {len(no_size)}건 — 배율을 정할 수 없다:")
        for o in no_size[:5]:
            print(f"    {o['id']}")


    from collections import Counter
    held = Counter(o["held_reason"].split("—")[0].split(".")[0].strip()[:60]
                   for o in objs if o.get("held_reason"))
    if held:
        print("\n  보류 사유:")
        for reason, n in held.most_common():
            print(f"    {n:3d}건  {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
