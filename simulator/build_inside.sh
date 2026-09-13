#!/usr/bin/env bash
set -euo pipefail

package_tag=${1:-shipwrecks-sim}
engine_dir=/drone/src/engine
unreal_root=/home/ue4/UnrealEngine
editor_cmd="$unreal_root/Engine/Binaries/Linux/UnrealEditor-Cmd"
ispc_source="$engine_dir/Source/Holodeck/FFTWaves/Private/OceanFFTCalculator.ispc"
export PATH="/home/ue4/.local/bin:$PATH"

if [ -f "${ispc_source}.hidden" ]; then
  mv "${ispc_source}.hidden" "$ispc_source"
fi

if ! command -v ue4 >/dev/null 2>&1; then
  pip3 install --user ue4cli
fi

ispc_dir="$unreal_root/Engine/Binaries/ThirdParty/Intel/ISPC/Linux"
if [ ! -x "$ispc_dir/ispc" ]; then
  work_dir=$(mktemp -d /tmp/shipwrecks-ispc.XXXXXX)
  tar -xzf /host_ws/vendor/ispc-v1.18.0-linux.tar.gz -C "$work_dir"
  mkdir -p "$ispc_dir"
  cp "$work_dir/ispc-v1.18.0-linux/bin/ispc" "$ispc_dir/ispc"
  chmod +x "$ispc_dir/ispc"
fi

cd "$engine_dir"
ue4 setroot "$unreal_root"
bash Build/ContinuousIntegration/build_project.sh

run_commandlet() {
  local script_name=$1
  local marker_log=$2
  "$editor_cmd" "$engine_dir/Holodeck.uproject" -run=pythonscript \
    -script="$engine_dir/$script_name" -unattended -nopause -nosplash \
    -RenderOffScreen -stdout >/tmp/shipwrecks-commandlet.log 2>&1 || true
  if ! grep -q DONE "$marker_log"; then
    tail -100 /tmp/shipwrecks-commandlet.log >&2
    printf 'Unreal commandlet failed: %s\n' "$script_name" >&2
    exit 1
  fi
}

run_commandlet import_objects.py "$engine_dir/import_objects.log"
run_commandlet build_scene_seabed.py "$engine_dir/build_scene_seabed.log"
bash Build/ContinuousIntegration/package_ue5_project.sh TestWorlds "$package_tag"

config_source="$engine_dir/Content/Config"
packaged_config="$engine_dir/dist/Linux/Holodeck/Content/Config"
mkdir -p "$packaged_config/mado_scenes" "$packaged_config/mado_terrain"
cp "$config_source/mado_scenes/"*.json "$packaged_config/mado_scenes/"
cp "$config_source/mado_terrain/"*.csv "$packaged_config/mado_terrain/"
(
  cd "$engine_dir/dist"
  zip -qr "$engine_dir/final/$package_tag.zip" \
    Linux/Holodeck/Content/Config/mado_scenes \
    Linux/Holodeck/Content/Config/mado_terrain
)
printf 'Build complete: %s\n' "$engine_dir/final/$package_tag.zip"
