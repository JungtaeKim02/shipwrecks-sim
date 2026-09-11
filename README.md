# HoloOcean Side-Scan Sonar Dataset Generator

HoloOcean 기반 수중 환경에서 양쪽 방향의 사이드스캔 소나 영상을 만들고, 물체 마스크와 경계 상자를 함께 저장하는 데이터셋 생성 도구입니다. 센서·환경·장면·저장 항목은 로컬 웹 UI에서 설정합니다.

## 예시 결과

아래 예시는 고정 시드 `20260911`로 1개 장면, 100 m 주행 구간 5개, 2,500개 소나 신호를 취득한 결과입니다. 전체 예시는 [`examples/sample_dataset`](examples/sample_dataset)에 있습니다.

| 장면 미리보기 | 소나 영상 + 정답 경계 상자 |
|---|---|
| ![3D scene preview](examples/sample_dataset/samples/scene_000000/preview.png) | ![Side-scan sonar with bounding boxes](examples/sample_dataset/samples/scene_000000/annotations/bbox_overlays/waterfall_sss_ideal_coh3_f520_bw63_vb50_hb0.26_dep27.0_alt16.6_r0-69_res15.0cm_hd16_sp65_slant_beamon_tvgoff_abson_normon_leg04.png) |

![Pixel mask](examples/sample_dataset/samples/scene_000000/annotations/masks/waterfall_sss_ideal_coh3_f520_bw63_vb50_hb0.26_dep27.0_alt16.6_r0-69_res15.0cm_hd16_sp65_slant_beamon_tvgoff_abson_normon_leg04.png)

- 소나 영상의 가운데 검은 띠는 센서 바로 아래의 수직 구간이며, 양옆은 좌현과 우현에서 얻은 해저 반사 영상입니다.
- 빨간 사각형은 물체의 정답 경계 상자, 흰색 픽셀은 물체가 차지하는 영역의 정답 마스크입니다.
- 이 예시는 `RTX 5060 Ti 16 GB`에서 약 92초가 걸렸습니다. 장면과 설정에 따라 시간은 크게 달라집니다.

## 기반 버전과 변경 사항

- 원본: [BYU HoloOcean](https://github.com/byu-holoocean/HoloOcean)
- 기준: HoloOcean `2.4.0`, `develop`의 [커밋 136b87c1](https://github.com/byu-holoocean/HoloOcean/commit/136b87c1beabef00308d5be7ec7ebe1a80ef0117)
- 엔진: Unreal Engine `5.3`

원본에 다음 기능을 추가하거나 변경했습니다.

- 선체 아래 좌우에 배치한 두 개의 레이 기반 사이드스캔 소나
- 거리 감쇠, 빔 방향성, 간섭 무늬, 공간 상관 잡음, 수신기 잡음 및 영상 보정
- 실행 중 해저 지형과 3D 물체를 배치하고 물체 종류를 픽셀 단위로 기록하는 기능
- 시드 기반 장면·주행 경로 생성과 지형에 따른 고도·측정 거리 자동 계산
- 웹 UI, 선택적 결과 저장, 장면별 폴더 구성 및 ZIP 내보내기
- 일반 픽셀 마스크와 경계 상자 생성

Python 클라이언트 변경은 [`patches/holoocean-client-2.4.0.diff`](patches/holoocean-client-2.4.0.diff)에 기록되어 있습니다. 수정된 엔진 실행 파일은 별도의 Release 파일로 제공합니다.

## 요구 환경

| 항목 | 요구 또는 권장 사항 |
|---|---|
| 운영체제 | 64-bit Linux. 제공 실행 파일은 Linux용입니다. |
| Python | 3.10 권장 |
| GPU | 전용 NVIDIA GPU 권장, 실사용은 VRAM 8 GB 이상 권장 |
| 메모리 | HoloOcean 공식 최소 8 GB, 이 프로젝트는 16 GB 이상 권장 |
| CPU | 공식 권장 기준인 2.5 GHz 이상 4코어 수준 또는 그 이상 |
| 저장 공간 | 설치와 임시 결과를 고려해 25 GB 이상 여유 공간 권장 |

화면을 숨겨 실행해도 시뮬레이터의 3D 계산에는 GPU가 사용됩니다. 주행 거리, 장면 수, 물체 수, 소나 광선 수를 늘리면 GPU 부하와 실행 시간이 함께 증가합니다.

## 설치

### 1. 저장소와 Python 환경

```bash
git clone https://github.com/JungtaeKim02/shipwrecks-sim.git
cd shipwrecks-sim

git clone https://github.com/byu-holoocean/HoloOcean.git holoocean
git -C holoocean checkout 136b87c1beabef00308d5be7ec7ebe1a80ef0117
git -C holoocean apply ../patches/holoocean-client-2.4.0.diff

python3.10 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 2. 수정된 시뮬레이터와 3D 자산

[Releases](https://github.com/JungtaeKim02/shipwrecks-sim/releases/tag/v0.1.0)에서 다음 두 파일을 받습니다.

- `holoocean-sss-linux-v0.1.0.zip`: 수정된 Linux 실행 패키지
- `holoocean-sss-assets-v0.1.0.zip`: 3D 메시, 썸네일, 자산 설명 CSV

```bash
mkdir -p downloads
# 위 Release에서 두 ZIP을 downloads/에 저장한 뒤 실행합니다.

WORLD_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/holoocean/2.4.0/worlds/TestWorlds"
mkdir -p "$WORLD_DIR"
unzip downloads/holoocean-sss-linux-v0.1.0.zip -d "$WORLD_DIR"
chmod +x "$WORLD_DIR/Linux/Holodeck/Binaries/Linux/Holodeck"

unzip downloads/holoocean-sss-assets-v0.1.0.zip -d .
```

다른 위치에 실행 패키지를 설치했다면 `HOLOOCEAN_WORLD_DIR`에 `TestWorlds` 폴더 경로를 지정합니다.

## 데이터셋 만들기

```bash
source .venv/bin/activate
python webui/server.py
```

브라우저에서 <http://127.0.0.1:8765>를 열고 다음 순서로 진행합니다.

1. **센서 파라미터**와 **환경**에서 고정할 값을 정하고 **설정 저장**을 누릅니다.
2. **데이터셋 취득**에서 장면 수, 시작 시드, 다양화 범위와 저장 결과를 선택합니다.
3. **취득 설명**에서 고정값·무작위값·자동 계산값을 확인합니다.
4. **계획만 생성**으로 예상 시간과 경고를 확인한 뒤 **데이터셋 취득 시작**을 누릅니다.
5. 결과는 기본적으로 `data/datasets/<날짜와 시각>/`에 장면별로 저장됩니다.

중지해도 이미 끝난 장면은 지워지지 않습니다. `ZIP 내보내기`를 선택하면 실행이 끝난 뒤 전체 세션을 한 파일로 묶습니다.

### 주요 결과 구조

```text
data/datasets/<session>/
├── index.json                     # 성공, 실패, 실제 출력 수
├── plan.csv                       # 장면별 무작위화 결과
├── manifests/scenes.jsonl         # 지형, 물체, 주행 계획
└── samples/scene_000000/
    ├── preview.png                # 장면 미리보기 (선택)
    ├── sss/waterfall/             # 소나 PNG (선택)
    ├── sss/raw/                   # 수신 처리 전 NumPy (선택)
    ├── sss/processed/             # 처리된 NumPy (선택)
    └── annotations/
        ├── masks/                 # 일반 픽셀 마스크 (선택)
        ├── bboxes/                # YOLO TXT와 픽셀 좌표 JSON (선택)
        └── bbox_overlays/         # 검수용 경계 상자 영상 (선택)
```

## 명령행으로 예시 재현

```bash
python scripts/large_dataset.py \
  --spec examples/sample_dataset/dataset_spec.json \
  --dataset-root data/datasets/example_reproduced
```

같은 시드는 장면과 설정을 재현하기 위한 값입니다. GPU와 드라이버의 병렬 계산 차이 때문에 모든 부동소수점 값이 비트 단위로 같다고 보장하지는 않습니다.

## 자산과 배포 주의사항

- `data/objects/asset_database.csv`에는 3D 자산의 실제 크기, 설명, 출처가 기록되어 있습니다. 이 CSV는 출처 기록이며 자산 사용권을 대신하지 않습니다.
- 현재 자산 폴더에는 모든 3D 모델에 공통으로 적용되는 라이선스 파일이 없습니다. 공개 Release 전에 각 모델의 재배포 권한을 확인해야 합니다.
- HoloOcean 원본 코드와 수정된 Unreal 실행 패키지에는 [HoloOcean 라이선스](https://github.com/byu-holoocean/HoloOcean/blob/develop/LICENSE)와 [Unreal Engine EULA](https://www.unrealengine.com/eula/unreal)가 적용됩니다. 공개 배포 권한을 확인하지 못했다면 실행 패키지는 비공개 Release로 제공하고, 사용자가 직접 빌드하도록 해야 합니다.
- GitHub는 일반 Git 저장소의 100 MiB 초과 파일을 차단하므로 실행 패키지와 메시 묶음은 저장소가 아닌 Release에 둡니다.
