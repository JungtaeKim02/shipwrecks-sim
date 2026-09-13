# Shipwrecks Sim

HoloOcean 기반 수중 환경에서 양방향 사이드스캔 소나 영상, 일반 픽셀 마스크와 경계 상자를 저장하는 데이터셋 생성 도구입니다. 센서·환경·장면·저장 항목은 로컬 웹 UI에서 설정합니다.

## 예시 결과

고정 시드 `20260911`, 100 m 주행 구간 5개, 총 2,500개 소나 신호를 취득한 예시입니다. 전체 산출물과 재현 설정은 [`examples/sample_dataset`](examples/sample_dataset)에 있습니다.

| 3D 장면 | 소나 영상과 정답 경계 상자 |
|---|---|
| ![3D scene preview](examples/sample_dataset/samples/scene_000000/preview.png) | ![Side-scan sonar with bounding boxes](examples/sample_dataset/samples/scene_000000/annotations/bbox_overlays/waterfall_sss_ideal_coh3_f520_bw63_vb50_hb0.26_dep27.0_alt16.6_r0-69_res15.0cm_hd16_sp65_slant_beamon_tvgoff_abson_normon_leg04.png) |

![Pixel mask](examples/sample_dataset/samples/scene_000000/annotations/masks/waterfall_sss_ideal_coh3_f520_bw63_vb50_hb0.26_dep27.0_alt16.6_r0-69_res15.0cm_hd16_sp65_slant_beamon_tvgoff_abson_normon_leg04.png)

가운데 검은 띠는 센서 바로 아래 구간이고 양옆은 좌현·우현 해저 반사입니다. 빨간 사각형은 물체 경계 상자, 흰색 픽셀은 물체 영역입니다. 이 예시는 RTX 5060 Ti 16 GB에서 약 94초가 걸렸습니다.

## 기반 버전

- 원본: [BYU HoloOcean](https://github.com/byu-holoocean/HoloOcean)
- 기준: HoloOcean `2.4.0`, `develop`의 [커밋 136b87c1](https://github.com/byu-holoocean/HoloOcean/commit/136b87c1beabef00308d5be7ec7ebe1a80ef0117)
- 엔진: Unreal Engine `5.3.2`

원본에 레이 기반 양방향 사이드스캔 소나, 음향 신호 모델, 실행 중 지형·물체 배치, 픽셀 단위 물체 기록, 시드 기반 장면·항로 생성, 지형에 따른 고도·측정 거리 계산, 웹 UI와 선택적 결과 저장 기능을 추가했습니다. 변경 내용은 `patches/shipwrecks-sim-holoocean-2.4.0.patch` 하나에 들어 있으며 수정된 실행 파일은 배포하지 않습니다.

## 요구 환경

| 항목 | 요구 또는 권장 사항 |
|---|---|
| 운영체제 | 64-bit Linux |
| Python | 3.10 |
| GPU | NVIDIA GPU, VRAM 8 GB 이상 권장 |
| 메모리 | 16 GB 이상 권장 |
| 저장 공간 | 소스·빌드·자산을 위해 40 GB 이상 권장 |
| 도구 | Git, Docker, NVIDIA Container Toolkit, curl, unzip |

Unreal Engine 컨테이너를 받으려면 Epic Games 계정과 연결된 GitHub 계정이 필요합니다. 주행 거리, 장면 수, 물체 수와 소나 광선 수가 늘어나면 데이터 취득 시간과 GPU 부하도 커집니다.

## 설치

먼저 [Epic의 Unreal Engine GitHub 접근 안내](https://www.unrealengine.com/en-US/ue-on-github)에 따라 계정을 연결하고 컨테이너 레지스트리에 로그인합니다.

```bash
docker login ghcr.io

git clone https://github.com/JungtaeKim02/shipwrecks-sim.git
cd shipwrecks-sim
./setup_simulator.sh
```

설치 스크립트는 원본 HoloOcean을 받고 기준 커밋에 패치를 적용한 뒤, Release의 3D 자산을 받아 Unreal 프로젝트에 구성합니다. 이어 TestWorlds를 직접 빌드·설치하고 Python 가상환경까지 준비합니다. 전체 빌드는 시스템에 따라 수십 분 이상 걸릴 수 있습니다.

이미 받은 자산 ZIP을 쓰려면 경로를 지정합니다.

```bash
SHIPWRECKS_ASSET_ARCHIVE=/path/to/holoocean-sss-assets-v0.2.0.zip ./setup_simulator.sh
```

Release에는 실행 ZIP이 없으며 `holoocean-sss-assets-v0.2.0.zip`만 제공합니다. 이 파일에는 3D 메시, 썸네일, 자산 설명 CSV와 런타임 카탈로그가 들어 있습니다.

## 데이터셋 취득

```bash
source .venv/bin/activate
python webui/server.py
```

브라우저에서 <http://127.0.0.1:8765>를 열고 다음 순서로 진행합니다.

1. **센서 파라미터**와 **환경**에서 고정값을 정하고 **설정 저장**을 누릅니다.
2. **데이터셋 취득**에서 장면 수, 시작 시드, 다양화 범위와 저장 결과를 선택합니다.
3. **취득 설명**에서 고정값·무작위값·자동 계산값을 확인합니다.
4. **계획만 생성**으로 예상 시간과 경고를 확인한 뒤 **데이터셋 취득 시작**을 누릅니다.

결과는 기본적으로 `data/datasets/<날짜와 시각>/`에 장면별로 저장됩니다. 워터폴 PNG는 고정된 하나의 데시벨 표시 범위로만 생성됩니다.

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

중지해도 완료된 장면은 유지됩니다. `ZIP 내보내기`를 선택하면 완료 후 전체 세션을 한 파일로 묶습니다.

## 명령행 예시 재현

```bash
python scripts/large_dataset.py \
  --spec examples/sample_dataset/dataset_spec.json \
  --dataset-root data/datasets/example_reproduced
```

동일한 시드는 장면과 설정을 재현합니다. GPU와 드라이버 차이로 모든 부동소수점 값이 비트 단위로 같다고 보장하지는 않습니다.

## 배포 구성

- Git 저장소: 웹 UI, 취득 코드, 단일 HoloOcean 소스 패치, 빌드 자동화, 지형 설정, 예시 데이터
- GitHub Release: 3D 메시와 자산 설명 CSV를 포함한 자산 ZIP, SHA-256 체크섬
- 원본 HoloOcean 및 Unreal Engine: 각 사용자가 공식 경로에서 직접 받아 빌드

HoloOcean과 Unreal Engine에는 각각의 원본 라이선스가 적용됩니다. 3D 자산 출처 정보는 `data/objects/asset_database.csv`에 기록되어 있습니다.
