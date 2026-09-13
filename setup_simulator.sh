#!/usr/bin/env bash
set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source_dir=${HOLOOCEAN_SOURCE_DIR:-"$project_root/holoocean"}
upstream_url=https://github.com/byu-holoocean/HoloOcean.git
upstream_commit=136b87c1beabef00308d5be7ec7ebe1a80ef0117
release_tag=v0.2.0
asset_name=holoocean-sss-assets-v0.2.0.zip
asset_archive=${SHIPWRECKS_ASSET_ARCHIVE:-"$project_root/downloads/$asset_name"}
engine_image=${HOLOOCEAN_ENGINE_IMAGE:-ghcr.io/epicgames/unreal-engine:dev-slim-5.3.2}
package_tag=shipwrecks-sim-v0.2.0
python_bin=${PYTHON_BIN:-python3.10}
prepare_only=false
if [ "${1:-}" = "--prepare-only" ]; then
  prepare_only=true
elif [ "$#" -gt 0 ]; then
  printf '사용법: %s [--prepare-only]\n' "$0" >&2
  exit 2
fi

for command_name in git curl unzip docker "$python_bin"; do
  command -v "$command_name" >/dev/null 2>&1 || {
    printf '필수 명령을 찾을 수 없습니다: %s\n' "$command_name" >&2
    exit 1
  }
done

if [ ! -d "$source_dir/.git" ]; then
  git clone "$upstream_url" "$source_dir"
fi

patch_file="$project_root/patches/shipwrecks-sim-holoocean-2.4.0.patch"
if git -C "$source_dir" apply --unidiff-zero --reverse --check "$patch_file" >/dev/null 2>&1; then
  printf '[1/6] HoloOcean 소스 패치가 이미 적용되어 있습니다.\n'
else
  if [ -n "$(git -C "$source_dir" status --porcelain)" ]; then
    printf 'HoloOcean 소스에 다른 변경이 있습니다. 별도 보관한 뒤 다시 실행하세요: %s\n' "$source_dir" >&2
    exit 1
  fi
  git -C "$source_dir" fetch origin "$upstream_commit"
  git -C "$source_dir" checkout --detach "$upstream_commit"
  git -C "$source_dir" apply --unidiff-zero --check "$patch_file"
  git -C "$source_dir" apply --unidiff-zero "$patch_file"
  printf '[1/6] HoloOcean 2.4.0 기준 소스 패치를 적용했습니다.\n'
fi

mkdir -p "$project_root/downloads"
if ! find "$project_root/data/objects" -type f \( -iname '*.fbx' -o -iname '*.obj' \) -print -quit | grep -q .; then
  if [ ! -f "$asset_archive" ]; then
    printf '[2/6] 3D 자산을 GitHub Release에서 받습니다.\n'
    curl -fL --retry 3 \
      "https://github.com/JungtaeKim02/shipwrecks-sim/releases/download/$release_tag/$asset_name" \
      -o "$asset_archive"
  fi
  unzip -q -o "$asset_archive" -d "$project_root"
fi
find "$project_root/data/objects" -type f \( -iname '*.fbx' -o -iname '*.obj' \) -print -quit | grep -q . || {
  printf 'Release 자산 압축을 풀었지만 3D 메시를 찾지 못했습니다.\n' >&2
  exit 1
}
printf '[2/6] 3D 메시와 자산 CSV를 준비했습니다.\n'

cp "$project_root/simulator/import_objects.py" "$source_dir/engine/import_objects.py"
cp "$project_root/simulator/build_scene_seabed.py" "$source_dir/engine/build_scene_seabed.py"
cp -a "$project_root/simulator/config/." "$source_dir/engine/Content/Config/"
printf '[3/6] 메시 임포트 및 지형 설정을 엔진 소스에 배치했습니다.\n'

if [ "$prepare_only" = true ]; then
  printf '소스 준비 검증을 완료했습니다(--prepare-only). 빌드와 설치는 수행하지 않았습니다.\n'
  exit 0
fi

ispc_archive="$project_root/vendor/ispc-v1.18.0-linux.tar.gz"
if [ ! -f "$ispc_archive" ]; then
  mkdir -p "$project_root/vendor"
  curl -fL --retry 3 \
    https://github.com/ispc/ispc/releases/download/v1.18.0/ispc-v1.18.0-linux.tar.gz \
    -o "$ispc_archive"
fi

printf '[4/6] Unreal Engine 컨테이너로 시뮬레이터를 빌드합니다.\n'
docker run --rm --gpus all \
  -v "$source_dir/engine:/drone/src/engine" \
  -v "$project_root:/host_ws" \
  "$engine_image" \
  bash /host_ws/simulator/build_inside.sh "$package_tag"

package_zip="$source_dir/engine/final/$package_tag.zip"
if [ ! -f "$package_zip" ]; then
  printf '빌드 결과를 찾지 못했습니다: %s\n' "$package_zip" >&2
  exit 1
fi

world_dir=${HOLOOCEAN_WORLD_DIR:-"${XDG_DATA_HOME:-$HOME/.local/share}/holoocean/2.4.0/worlds/TestWorlds"}
if [ -e "$world_dir" ]; then
  backup_dir="${world_dir}.backup.$(date +%Y%m%d-%H%M%S)"
  mv "$world_dir" "$backup_dir"
  printf '기존 월드는 다음 위치에 보관했습니다: %s\n' "$backup_dir"
fi
mkdir -p "$world_dir"
unzip -q "$package_zip" -d "$world_dir"
chmod +x "$world_dir/Linux/Holodeck/Binaries/Linux/Holodeck"
printf '[5/6] 빌드한 TestWorlds를 설치했습니다: %s\n' "$world_dir"

if [ ! -d "$project_root/.venv" ]; then
  "$python_bin" -m venv "$project_root/.venv"
fi
"$project_root/.venv/bin/python" -m pip install --upgrade pip
"$project_root/.venv/bin/python" -m pip install -e "$source_dir/client"
"$project_root/.venv/bin/python" -m pip install -r "$project_root/requirements.txt"
printf '[6/6] Python 환경까지 준비했습니다. 실행: source .venv/bin/activate && python webui/server.py\n'
