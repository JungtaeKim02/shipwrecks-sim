# Shipwrecks Sim

Shipwrecks Sim is a local dataset generator for side-scan sonar imagery. It runs on HoloOcean, generates a simulated underwater scene, and saves sonar images together with optional pixel masks, bounding boxes, NumPy arrays, previews, and session archives.

## Example output

The included example uses seed `20260911`, five 100 m survey legs, and 2,500 sonar pings. The complete example and its reproducibility files are in [`examples/sample_dataset`](examples/sample_dataset).

| Scene preview | Side-scan sonar with bounding boxes |
|---|---|
| ![Scene preview](examples/sample_dataset/samples/scene_000000/preview.png) | ![Sonar image with bounding boxes](examples/sample_dataset/samples/scene_000000/annotations/bbox_overlays/waterfall_sss_ideal_coh3_f520_bw63_vb50_hb0.26_dep27.0_alt16.6_r0-69_res15.0cm_hd16_sp65_slant_beamon_tvgoff_abson_normon_leg04.png) |

![Pixel mask](examples/sample_dataset/samples/scene_000000/annotations/masks/waterfall_sss_ideal_coh3_f520_bw63_vb50_hb0.26_dep27.0_alt16.6_r0-69_res15.0cm_hd16_sp65_slant_beamon_tvgoff_abson_normon_leg04.png)

The dark center band is the nadir region beneath the vehicle. The two sides are port and starboard seabed returns. Red rectangles are object bounding boxes; white pixels are object masks.

## Base software and project changes

- Upstream simulator: [BYU HoloOcean](https://github.com/byu-holoocean/HoloOcean)
- Pinned source: HoloOcean `2.4.0`, `develop` commit [`136b87c1`](https://github.com/byu-holoocean/HoloOcean/commit/136b87c1beabef00308d5be7ec7ebe1a80ef0117)
- Engine: Unreal Engine `5.3.2`

The project adds a ray-based port/starboard side-scan sonar, acoustic signal processing, runtime terrain and object placement, object labels, seed-based scene and survey generation, geometry-aware acquisition settings, a local web UI, selective output saving, and dataset-session ZIP export.

All HoloOcean source modifications are contained in `patches/shipwrecks-sim-holoocean-2.4.0.patch`. A modified HoloOcean executable is not distributed; it is rebuilt locally from the pinned upstream source.

## Asset origin

The project assets were created from photographic reference material for shipwrecks and weathered or eroded objects associated with the western and southern seas of Korea. The reference imagery was converted into 3D assets with Meshy 6 and then prepared for simulation use. Asset metadata, dimensions, and source records are listed in `data/objects/asset_database.csv`.

## Requirements

| Item | Requirement or recommendation |
|---|---|
| Operating system | 64-bit Linux |
| Python | 3.10 |
| GPU | NVIDIA GPU; 8 GB VRAM or more recommended |
| Memory | 16 GB or more recommended |
| Storage | At least 40 GB free for source, build output, and assets |
| Tools | Git, Docker, NVIDIA Container Toolkit, curl, unzip |

Building the Unreal project requires access to the Unreal Engine container image. Link your Epic Games and GitHub accounts following [Epic's GitHub access guide](https://www.unrealengine.com/en-US/ue-on-github), then sign in to the container registry.

## Installation

```bash
docker login ghcr.io

git clone https://github.com/JungtaeKim02/shipwrecks-sim.git
cd shipwrecks-sim
./setup_simulator.sh
```

The setup script clones the pinned HoloOcean `develop` commit, applies the project patch, downloads the Release asset archive, imports the meshes and terrain configuration, builds TestWorlds, installs the world locally, and creates the Python environment. The Unreal build can take several minutes or longer.

To reuse an already downloaded asset archive:

```bash
SHIPWRECKS_ASSET_ARCHIVE=/path/to/holoocean-sss-assets-v0.2.0.zip ./setup_simulator.sh
```

The GitHub Release contains the asset archive and its SHA-256 checksum. It does not contain a simulator executable.

## Launch the web UI

```bash
source .venv/bin/activate
python webui/server.py
```

Open <http://127.0.0.1:8765> in a browser.

## 웹 UI 사용법

일반적인 데이터 취득은 다음 순서로 진행합니다.

1. **센서 파라미터**에서 주파수, 대역폭, 빔폭, 거리 해상도, 신호 처리 등 고정할 소나 값을 설정합니다.
2. **환경**에서 취득에 사용할 해수와 해상 상태를 설정합니다.
3. **필드(지형)**에서 지형을 선택합니다. 새 절차적 지형이 필요할 때만 **지형 만들기**를 사용합니다.
4. **오브젝트**에서 자산 카테고리를 선택하거나 **자산 라이브러리**를 열어 각 메시를 확인하고, 사용 또는 제외한 뒤 적용합니다.
5. **조사 계획 · 씬 생성**에서 플랫폼, 시드, leg 수, 경로를 정한 뒤 **씬 생성**을 누릅니다. 수동으로 설정한 센서값을 유지하려면 **내 센서 값 사용**을 켭니다.
6. **씬 시각화**에서 결과를 확인합니다. **3D**와 **평면도**로 뷰를 전환하고, **시점 맞춤**으로 장면을 프레이밍하며, **센서 빔 보기**로 계산된 탐지 범위를 표시합니다. 3D 화면에서는 오브젝트를 선택해 조정할 수 있습니다.
7. 실행 전 헤더의 **설정 저장**을 누릅니다. **미리보기 촬영**은 간단한 장면 이미지를 저장하고, **중지**는 실행 중인 취득을 안전하게 멈추며 완료된 샘플은 보존합니다.

### 데이터셋 취득 패널

일회성 수동 취득보다 일반적인 데이터셋 생산에 사용하는 패널입니다.

- **취득 설명**: 사용자가 고정하는 값, 장면마다 무작위화되는 값, 지형 기하에 따라 자동 계산되는 값을 설명합니다.
- **데이터셋 이름**, **씬 수**, **시작 시드**: 세션 이름과 재현 가능한 장면 순서를 정합니다.
- 바닥 클러터, 난파선 기울기, 수신기 노이즈, 스페클, 해저 반사 텍스처: 장면별 다양화 범위를 정합니다.
- **저장할 산출물**: 워터폴 PNG, 실제 종횡비 PNG, 원시 NumPy, 처리 dB NumPy, 픽셀 마스크, YOLO 경계 상자, 경계 상자 검수 이미지, 씬 미리보기, 선택적 세션 ZIP을 선택합니다.
- **계획만 생성**: 시뮬레이터를 실행하지 않고 장면 계획과 예상 시간을 만듭니다.
- **데이터셋 취득 시작**: 계획된 세션을 실행합니다.

**실행** 패널은 진행률, 로그, 미리보기, 생성 이미지를 표시합니다. **지난 취득**에서는 이전 세션을 확인할 수 있습니다. 워터폴 PNG는 하나의 고정 표시 동적 범위로만 생성되며, 다른 표시 범위의 augmentation 이미지는 생성하지 않습니다.

## Output layout

```text
data/datasets/<session>/
├── index.json
├── plan.csv
├── manifests/scenes.jsonl
└── samples/scene_000000/
    ├── preview.png
    ├── sss/waterfall/
    ├── sss/raw/
    ├── sss/processed/
    └── annotations/
        ├── masks/
        ├── bboxes/
        └── bbox_overlays/
```

Completed samples remain available if a run is stopped. Selecting ZIP export creates one archive for the completed dataset session.

## Reproduce the included example

```bash
python scripts/large_dataset.py \
  --spec examples/sample_dataset/dataset_spec.json \
  --dataset-root data/datasets/example_reproduced
```

The same seed recreates the planned scene and configuration. Exact floating-point values can differ across GPU and driver versions.

## Distribution layout

- Git repository: web UI, acquisition pipeline, HoloOcean source patch, build automation, terrain configuration, and example dataset
- GitHub Release: 3D asset archive and SHA-256 checksum
- Upstream HoloOcean and Unreal Engine: obtained and built locally by each user

HoloOcean and Unreal Engine remain subject to their respective upstream licenses.
