"""배포본과 개발 소스에서 공통으로 쓰는 HoloOcean 경로."""

from __future__ import annotations

import os
import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[1]
HOLOOCEAN_VERSION = "2.4.0"
PACKAGE_NAME = "TestWorlds"


def holoocean_data_root() -> pathlib.Path:
    """HoloOcean 사용자 데이터 경로를 반환한다."""
    base = os.environ.get("XDG_DATA_HOME")
    if base:
        return pathlib.Path(base).expanduser() / "holoocean" / HOLOOCEAN_VERSION
    return pathlib.Path.home() / ".local/share/holoocean" / HOLOOCEAN_VERSION


def world_root() -> pathlib.Path:
    """커스텀 실행 패키지의 루트(TestWorlds)를 반환한다.

    기본 설치 위치가 아닌 곳에 압축을 풀었으면 ``HOLOOCEAN_WORLD_DIR``로
    TestWorlds 폴더를 직접 지정할 수 있다.
    """
    override = os.environ.get("HOLOOCEAN_WORLD_DIR")
    if override:
        return pathlib.Path(override).expanduser().resolve()
    return holoocean_data_root() / "worlds" / PACKAGE_NAME


def runtime_config_root() -> pathlib.Path:
    return world_root() / "Linux/Holodeck/Content/Config"


def source_config_root() -> pathlib.Path:
    return ROOT / "holoocean/engine/Content/Config"


def config_roots() -> list[pathlib.Path]:
    """읽기 우선순위: 명시 경로, 설치된 배포본, 개발 소스."""
    roots: list[pathlib.Path] = []
    override = os.environ.get("HOLOOCEAN_CONFIG_ROOT")
    if override:
        roots.append(pathlib.Path(override).expanduser().resolve())
    roots.extend((runtime_config_root(), source_config_root()))
    return roots


def find_config_file(relative_path: str | pathlib.Path) -> pathlib.Path:
    relative = pathlib.Path(relative_path)
    for root in config_roots():
        candidate = root / relative
        if candidate.is_file():
            return candidate
    return config_roots()[0] / relative


def find_config_dir(relative_path: str | pathlib.Path) -> pathlib.Path:
    relative = pathlib.Path(relative_path)
    for root in config_roots():
        candidate = root / relative
        if candidate.is_dir():
            return candidate
    return config_roots()[0] / relative
