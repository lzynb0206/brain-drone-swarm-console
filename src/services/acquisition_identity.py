"""Participant/run metadata shared by capture, markers and automatic storage."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping


COLLECTION_TYPES = ("SSVEP", "MI", "SSVEP+MI混合")


def subject_folder(display_name: str, subject_id: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", display_name).strip(" .")
    return f"{name or '受试者'}__{subject_id}"


def capture_identity(metadata: Mapping, fallback: tuple[str, str]) -> tuple[str, str]:
    """A queued capture keeps its participant even when the UI/login changes."""
    subject_id = str(metadata.get("acquisition_subject_id", "")).strip()
    if subject_id:
        run_id = str(metadata.get("run_id", "")).strip()
        if not run_id:
            raise ValueError("已绑定受试者的采集缺少批次编号")
        return subject_id, run_id
    return fallback


def acquisition_directory(raw_root: Path, metadata: Mapping) -> Path:
    """Legacy/manual captures keep their location; bound runs get a hierarchy."""
    if not metadata.get("acquisition_subject_id"):
        return raw_root
    values = [metadata.get(key, "") for key in
              ("subject_folder", "acquisition_date", "collection_type", "run_id")]
    if metadata.get("collection_type") not in COLLECTION_TYPES:
        raise ValueError("采集子标签无效")
    for value in values:
        if (not isinstance(value, str) or not value.strip() or value in (".", "..")
                or re.search(r'[\\/:*?"<>|\x00-\x1f]', value)):
            raise ValueError("采集目录包含无效路径字段")
    directory = raw_root.joinpath(*values)
    if not directory.resolve().is_relative_to(raw_root.resolve()):
        raise ValueError("采集目录超出原始数据目录")
    return directory
