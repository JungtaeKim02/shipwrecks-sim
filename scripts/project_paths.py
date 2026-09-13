
from __future__ import annotations

import os
import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[1]
HOLOOCEAN_VERSION = "2.4.0"
PACKAGE_NAME = "TestWorlds"


def holoocean_data_root() -> pathlib.Path:
    base = os.environ.get("XDG_DATA_HOME")
    if base:
        return pathlib.Path(base).expanduser() / "holoocean" / HOLOOCEAN_VERSION
    return pathlib.Path.home() / ".local/share/holoocean" / HOLOOCEAN_VERSION


def world_root() -> pathlib.Path:
    override = os.environ.get("HOLOOCEAN_WORLD_DIR")
    if override:
        return pathlib.Path(override).expanduser().resolve()
    return holoocean_data_root() / "worlds" / PACKAGE_NAME


def runtime_config_root() -> pathlib.Path:
    return world_root() / "Linux/Holodeck/Content/Config"


def source_config_root() -> pathlib.Path:
    return ROOT / "holoocean/engine/Content/Config"


def config_roots() -> list[pathlib.Path]:
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
