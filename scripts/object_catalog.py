"""오브젝트 카탈로그 — 3D 모델 폴더를 스캔해 씬 구성이 참조할 목록을 만든다.

디렉터리 규약:

    data/objects/<category>/original/<name>.<ext>   ← 모델링 팀이 준 원본 그대로
    data/objects/<category>/ue_ready/<name>.<ext>   ← UE 임포트 가능한 형태 (fbx | obj)

원본이 이미 UE 가 읽는 포맷(fbx/obj)이면 ue_ready 는 필요 없다. 그 외(stl/glb)는
호스트에서 변환해 ue_ready 에 둔다(scripts/convert_wrecks.py). 과밀 메시의 감쇠본도
여기 들어간다(scripts/ingest_shipwreck_assets.py). 카탈로그는 ue_ready 를 우선
채택하므로, 변환본·감쇠본이 있으면 그쪽이 임포트된다.

  * STL 직접 임포트는 UE 커맨드렛에서 크래시 이력이 있어 반드시 변환해야 한다.
  * FBX 는 네이티브라 변환 없이 original 그대로 쓴다.

아직 도착하지 않은 자산은 data/objects/declared.json 에 미리 선언해두면
카탈로그에 available=false 로 들어간다. 파일이 실제로 들어오면 자동으로 true 가 된다.

크기 정보의 출처 (2026-08-09 전환)
    예전에는 scene_config 의 **카테고리 상수** `target_size_m`(예: shipwreck→45 m)
    하나로 그 카테고리 전체를 같은 크기에 맞췄다. 근거가 없는 대표값이었고, 한
    카테고리 안에 10 m 급 마도선과 60 m 급 대형선이 섞이면 전부 45 m 로 깔렸다.

    지금은 **자산별 실측 치수**를 쓴다. 두 출처를 병합한다.

      data/objects/asset_database.csv   태안 마도해역 시굴조사 보고서 등 문헌 실측.
                                        출처·페이지·신뢰도까지 함께 들어 있다.
      data/objects/local_assets.json    그 CSV 범위 밖(서양 범선 11종) 보충.
                                        우리가 STL 을 실치수로 변환한 것이라 자명하다.

    CSV 는 한 유물을 뷰 1/2/3 여러 행으로 적어 두는데 같은 Base_Object_ID 의 행은
    치수가 동일하다. 미제작 뷰 20종은 파일이 없으므로, **파일명이 아니라
    Base_Object_ID 로 조회**해야 살아남은 뷰에서 항상 답이 나온다.

사용:
    python3 scripts/object_catalog.py                 # 카탈로그 생성/갱신 + 요약 출력
    python3 scripts/object_catalog.py --list          # 항목 전체 출력
    python3 scripts/object_catalog.py --check         # 누락/미배치 자산만 출력 (exit 1 if any)

산출: data/objects/catalog.json
"""
import csv
import json
import pathlib
import sys
import datetime

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG_PATH = pathlib.Path(__file__).resolve().parent / "scene_config.json"

MESH_EXTS = (".fbx", ".glb", ".obj", ".stl")
# UE 커맨드렛이 직접 임포트할 수 있는 포맷. 나머지는 ue_ready/ 변환본이 있어야 한다.
NATIVE_EXTS = (".fbx", ".obj")


def load_config():
    return json.loads(CONFIG_PATH.read_text())


def _ue_mesh_path(cfg, category, name):
    """UE 안에서의 StaticMesh 애셋 경로. import_objects.py 가 이 경로로 임포트한다."""
    return f"{cfg['ue_mesh_root'].rstrip('/')}/{category}/{name}"


def _num(s):
    try:
        v = float(s)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def load_size_db(cfg):
    """자산명 -> 실물 치수 레코드. asset_database.csv + local_assets.json 병합.

    CSV 는 뷰 변형을 여러 행으로 적으므로 **파일명과 Base_Object_ID 양쪽**을 키로
    넣는다. 파일명으로 못 찾으면 Base_Object_ID 로 떨어지게 하기 위해서다.
    """
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
    """파일명 -> 레코드. 없으면 Base_Object_ID 로 떨어진다.

    `mado18-121_1` 처럼 미제작 뷰라 파일명 행이 없는 경우가 아니라, 반대로
    디스크에는 `mado18-121_2` 만 있고 CSV 행도 그 이름으로 있으므로 보통은 1차에
    잡힌다. 파일명이 CSV 와 어긋나는 경우를 위해 뒤 숫자를 떼고 재조회한다.
    """
    if name in db:
        return db[name]
    base = name.rsplit("_", 1)[0]
    return db.get("base:" + base) or db.get("base:" + name)


def _entry(cfg, category, name, source, available, importable=True, size_db=None):
    cat_cfg = cfg["object_categories"].get(category, {})
    tags = list(cat_cfg.get("tags", []))

    # 재질: 자산별 오버라이드 > 카테고리 기본값. (혼합 재질 카테고리 때문에 필요하다 —
    # anthropogenic_debris 는 앵커=철, 목재판=목재, 건설잔해=콘크리트가 섞여 있다.)
    overrides = cfg.get("object_material_overrides", {})
    material = overrides.get(f"{category}/{name}") or cat_cfg.get("material")
    if material:
        # 재질은 태그로 선언한다. 엔진이 이 이름을 materials.csv 에서 조회한다.
        tags.append(f"sssmat:{material}")

    e = {
        "id": f"{category}/{name}",
        "category": category,
        "name": name,
        "source": str(source) if source else None,
        "available": available,
        # UE 가 직접 읽는 포맷인가. false 면 ue_ready/ 변환본이 필요하다.
        "importable": importable,
        "ue_mesh": _ue_mesh_path(cfg, category, name),
        "tags": tags,
        "material": material,
    }

    # ---- 실물 치수 병합 -------------------------------------------------
    rec = _size_lookup(size_db or {}, name)
    if rec:
        for k in ("target_size_m", "bbox_real_m", "size_source", "size_source_url",
                  "size_source_page", "size_confidence", "size_status",
                  "base_object_id", "name_en", "name_ko", "preview_files"):
            if rec.get(k) is not None:
                e[k] = rec[k]
        if e.get("preview_files"):
            e["preview_paths"] = [f"data/objects/previews/{name}" for name in e["preview_files"]]

    # ---- 보류 판정 ------------------------------------------------------
    # (1) 카테고리 전체 보류 (재질 근거 미확보 등)
    if cat_cfg.get("enabled") is False:
        e["available"] = False
        e["held_reason"] = cat_cfg.get("_hold_reason", "카테고리 보류")
    # (2) 개별 자산 보류
    held = cfg.get("held_objects", {})
    if e["id"] in held:
        e["available"] = False
        e["held_reason"] = held[e["id"]]
    # (3) 실물 치수를 모르면 배율을 계산할 수 없다
    elif e.get("target_size_m") is None and available:
        e["available"] = False
        e["held_reason"] = ("실물 치수 출처가 없다 — asset_database.csv 에도 "
                            "local_assets.json 에도 항목이 없어 배율을 정할 수 없다.")
    return e


# measure_assets.py 가 UE 임포트 로그에서 재서 카탈로그에 써 넣는 필드들.
# 카탈로그를 다시 만들 때 이걸 안 물려주면 **조용히 사라지고**, 그 뒤 뽑는 씬은
# 배율이 개체 변동만 적용된 틀린 크기가 된다. 실제로 재빌드 한 번에 46종의 실측이
# 날아간 적이 있어 명시적으로 보존한다.
MEASURED_FIELDS = ("measured_bbox_m", "measured_max_m", "triangles",
                   "normalized_source", "measured_from")


def build(cfg):
    obj_root = ROOT / cfg["object_root"]
    entries = {}
    size_db = load_size_db(cfg)

    # 이전 카탈로그의 UE 실측값을 이어받는다. 자산 파일이 바뀌었으면(감쇠본으로 교체
    # 등) 측정이 낡았을 수 있으므로 출처 경로도 같이 비교해 알린다.
    prev, stale = {}, []
    prev_path = obj_root / "catalog.json"
    if prev_path.is_file():
        try:
            for o in json.loads(prev_path.read_text()).get("objects", []):
                if any(k in o for k in MEASURED_FIELDS):
                    prev[o["id"]] = o
        except Exception:
            pass

    # 1) 실제로 디스크에 있는 파일. ue_ready/ 를 original/ 보다 우선한다.
    for cat_dir in sorted(p for p in obj_root.glob("*") if p.is_dir()):
        category = cat_dir.name
        for sub in ("original", "ue_ready"):     # 뒤가 앞을 덮어씀 = ue_ready 우선
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

    # 2) 아직 안 온 자산 (declared.json). 이미 있으면 덮어쓰지 않는다.
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
            # 파일이 없다는 사실이 다른 어떤 보류 사유보다 우선한다 — 재질 근거가
            # 생겨도 파일이 없으면 못 쓴다. 카테고리 보류가 겹치면 뒤에 덧붙인다.
            also = e.get("held_reason")
            e["held_reason"] = "3D 파일 미도착"
            if also:
                e["held_reason_secondary"] = also
            entries[key] = e

    # 3) 이전 UE 실측값 이어받기
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
    """씬 샘플러가 쓰는 진입점. 카탈로그가 없으면 즉석에서 만든다."""
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
    # 파일이 실제로 없는 것과, 파일은 있는데 근거 부족으로 보류한 것을 구분한다.
    # 둘을 뭉뚱그리면 "왜 안 쓰이나" 를 알 수 없다.
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

    # 보류 사유별 집계. "왜 이 자산이 안 쓰이나" 를 한눈에 보게 한다.
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
