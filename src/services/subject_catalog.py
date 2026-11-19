"""Protected subject names and acquisition-run ownership metadata.

Raw NPZ trials remain immutable.  This sidecar catalog gives every participant
a stable ID, lets operators edit only the display name, and records the exact
run-to-subject/date relationship without guessing from nearby timestamps.
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping

from src.services.acquisition_identity import subject_folder


CATALOG_SCHEMA_VERSION = 1
CATALOG_RELATIVE_PATH = Path("sessions") / "subject_catalog.json"
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class SubjectAssignment:
    """Resolved participant metadata for one acquisition run."""

    subject_id: str
    display_name: str
    acquisition_date: str


class SubjectCatalog:
    """Atomically persist protected participant and run metadata."""

    def __init__(self, data_root: Path | str) -> None:
        self.data_root = Path(data_root).expanduser().resolve()
        self.path = self.data_root / CATALOG_RELATIVE_PATH
        self._lock = threading.RLock()
        self._payload = self._read()

    @property
    def subjects(self) -> dict[str, dict[str, str]]:
        values = self._payload.get("subjects", {})
        return dict(values) if isinstance(values, dict) else {}

    @property
    def runs(self) -> dict[str, dict[str, str]]:
        values = self._payload.get("runs", {})
        return dict(values) if isinstance(values, dict) else {}

    def next_default_name(self) -> str:
        used = {
            str(values.get("display_name", "")).strip()
            for values in self.subjects.values()
            if isinstance(values, dict)
        }
        index = 1
        while f"受试者{index}号" in used:
            index += 1
        return f"受试者{index}号"

    def create_subject(self, display_name: str) -> str:
        """Create/select a participant before any EEG has been recorded."""
        name = self._normalized_name(display_name)
        with self._lock:
            self._payload = self._read()
            for key, value in self.subjects.items():
                if value["display_name"].casefold() == name.casefold():
                    return key
            key = self._new_subject_id()
            self._payload["subjects"][key] = {
                "display_name": name, "created_at": self._now(), "updated_at": self._now(),
                "storage_folder": subject_folder(name, key),
            }
            self._write()
            return key

    def bind_acquisition(self, subject_id: str, run_id: str, acquisition_date: str,
                         collection_type: str, collection_scheme: str) -> dict[str, str]:
        """Fix ownership and destination before the first stage starts."""
        date = self._normalized_date(acquisition_date)
        if not run_id.strip():
            raise ValueError("采集批次编号不能为空")
        with self._lock:
            self._payload = self._read()
            subject = self._payload["subjects"].get(subject_id)
            if not subject or subject.get("display_name") == "其他":
                raise ValueError("请先选择具体受试者")
            if run_id in self.runs:
                raise ValueError("本批次已有归属，不能重复开始")
            folder = subject.setdefault("storage_folder", subject_folder(subject["display_name"], subject_id))
            self._payload["runs"][run_id] = {
                "subject_id": subject_id, "acquisition_date": date,
                "collection_type": collection_type, "collection_scheme": collection_scheme,
                "assignment_source": "acquisition-selection", "updated_at": self._now(),
            }
            self._write()
            return {"acquisition_subject_id": subject_id, "subject_name": subject["display_name"],
                    "subject_folder": folder, "acquisition_date": date, "run_id": run_id,
                    "collection_type": collection_type, "collection_scheme": collection_scheme}

    def restore_acquisition(self, run_id: str, date: str, subject_id: str, name: str) -> None:
        """Recover explicitly embedded ownership when importing a copied run."""
        date, name = self._normalized_date(date), self._normalized_name(name)
        if not re.fullmatch(r"subject-\d+", subject_id):
            raise ValueError("采集受试者编号无效")
        with self._lock:
            self._payload = self._read()
            if self.assignment_for_run(run_id):
                return
            subject = self._payload["subjects"].get(subject_id)
            if subject and subject["display_name"] != name:
                raise ValueError(f"导入受试者 {subject_id} 与已有名称不一致，请核对数据目录")
            self._payload["subjects"].setdefault(subject_id, {
                "display_name": name, "created_at": self._now(), "updated_at": self._now(),
            })
            self._payload["runs"][run_id] = {
                "subject_id": subject_id, "acquisition_date": date,
                "assignment_source": "embedded-acquisition-identity", "updated_at": self._now(),
            }
            self._write()

    def assignment_for_run(self, run_id: str) -> SubjectAssignment | None:
        run = self.runs.get(str(run_id), {})
        if not isinstance(run, dict):
            return None
        subject_id = str(run.get("subject_id", "")).strip()
        subject = self.subjects.get(subject_id, {})
        if not subject_id or not isinstance(subject, dict):
            return None
        name = str(subject.get("display_name", "")).strip()
        date = self._normalized_date(run.get("acquisition_date", ""))
        if not name or not date:
            return None
        return SubjectAssignment(subject_id, name, date)

    @property
    def subject_order(self) -> tuple[str, ...]:
        saved = self._payload.get("subject_order", [])
        if not isinstance(saved, list):
            return ()
        return tuple(dict.fromkeys(value for value in saved if isinstance(value, str) and value in self.subjects))

    def set_subject_order(self, subject_ids: Iterable[str]) -> None:
        """Persist display order only, preserving run ownership and EEG files."""
        requested = list(subject_ids)
        with self._lock:
            previous = self._payload
            self._payload = self._read()
            try:
                if len(set(requested)) != len(requested) or any(key not in self.subjects for key in requested):
                    raise ValueError("受试者排序包含重复或未知编号")
                remaining = list(self.subject_order)
                remaining.extend(key for key in self.subjects if key not in remaining)
                self._payload["subject_order"] = requested + [key for key in remaining if key not in requested]
                self._write()
            except (OSError, ValueError):
                self._payload = previous
                raise

    def ensure_runs(self, run_dates: Mapping[str, str]) -> bool:
        """Place legacy runs without an explicit owner in the ``其他`` bucket."""

        changed = False
        with self._lock:
            self._payload = self._read()
            for run_id, acquisition_date in sorted(run_dates.items()):
                normalized_run = str(run_id).strip()
                if not normalized_run or self.assignment_for_run(normalized_run):
                    continue
                self._assign_run_unlocked(
                    normalized_run,
                    self._normalized_date(acquisition_date),
                    "其他",
                    assignment_source="automatic-other-bucket",
                )
                changed = True
            if changed:
                self._write()
        return changed

    def assign_run(
        self,
        run_id: str,
        acquisition_date: str,
        display_name: str,
        *,
        assignment_source: str = "operator",
    ) -> SubjectAssignment:
        normalized_run = str(run_id).strip()
        if not normalized_run:
            raise ValueError("采集批次编号不能为空")
        normalized_date = self._normalized_date(acquisition_date)
        normalized_name = self._normalized_name(display_name)
        with self._lock:
            self._payload = self._read()
            assignment = self._assign_run_unlocked(
                normalized_run,
                normalized_date,
                normalized_name,
                assignment_source=assignment_source,
            )
            self._write()
        return assignment

    def assign_runs(
        self,
        run_ids: Iterable[str],
        acquisition_dates: Mapping[str, str],
        display_name: str,
        *,
        assignment_source: str = "operator",
    ) -> SubjectAssignment:
        normalized_name = self._normalized_name(display_name)
        final: SubjectAssignment | None = None
        with self._lock:
            self._payload = self._read()
            for run_id in run_ids:
                normalized_run = str(run_id).strip()
                if not normalized_run:
                    continue
                final = self._assign_run_unlocked(
                    normalized_run,
                    self._normalized_date(acquisition_dates.get(normalized_run, "")),
                    normalized_name,
                    assignment_source=assignment_source,
                )
            if final is None:
                raise ValueError("至少需要一个有效采集批次")
            self._write()
        return final

    def rename_subject(self, subject_id: str, display_name: str) -> str:
        """Rename metadata only; raw EEG files and run ownership are untouched."""

        normalized_id = str(subject_id).strip()
        normalized_name = self._normalized_name(display_name)
        with self._lock:
            self._payload = self._read()
            subjects = self._payload["subjects"]
            if normalized_id not in subjects:
                raise ValueError("所选受试者不存在")
            duplicate = next(
                (
                    key for key, values in subjects.items()
                    if key != normalized_id
                    and str(values.get("display_name", "")).strip().casefold()
                    == normalized_name.casefold()
                ),
                "",
            )
            if duplicate:
                raise ValueError("该名称已属于另一位受试者，请使用不同名称")
            subjects[normalized_id]["display_name"] = normalized_name
            subjects[normalized_id]["updated_at"] = self._now()
            self._write()
        return normalized_name

    def _assign_run_unlocked(
        self,
        run_id: str,
        acquisition_date: str,
        display_name: str,
        *,
        assignment_source: str,
    ) -> SubjectAssignment:
        subjects = self._payload["subjects"]
        subject_id = next(
            (
                key for key, values in subjects.items()
                if str(values.get("display_name", "")).strip().casefold()
                == display_name.casefold()
            ),
            "",
        )
        now = self._now()
        if not subject_id:
            subject_id = self._new_subject_id()
            subjects[subject_id] = {
                "display_name": display_name,
                "created_at": now,
                "updated_at": now,
            }
        self._payload["runs"][run_id] = {
            "subject_id": subject_id,
            "acquisition_date": acquisition_date,
            "assignment_source": str(assignment_source),
            "updated_at": now,
        }
        return SubjectAssignment(subject_id, display_name, acquisition_date)

    def _read(self) -> dict[str, object]:
        if not self.path.exists():
            return {
                "schema_version": CATALOG_SCHEMA_VERSION,
                "subjects": {},
                "runs": {},
            }
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"受试者目录损坏，已停止写入保护原数据：{exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("受试者目录格式无效，已停止写入保护原数据")
        if int(payload.get("schema_version", 0)) != CATALOG_SCHEMA_VERSION:
            raise ValueError("受试者目录版本不兼容，已停止写入保护原数据")
        subjects = payload.get("subjects")
        runs = payload.get("runs")
        if not isinstance(subjects, dict) or not isinstance(runs, dict):
            raise ValueError("受试者目录缺少 subjects/runs，已停止写入保护原数据")
        return payload

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        encoded = json.dumps(
            self._payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(self.path)

    def _new_subject_id(self) -> str:
        subjects = self._payload["subjects"]
        index = 1
        while f"subject-{index:04d}" in subjects:
            index += 1
        return f"subject-{index:04d}"

    @staticmethod
    def _normalized_name(value: object) -> str:
        normalized = " ".join(str(value).strip().split())
        if not normalized:
            raise ValueError("受试者姓名不能为空")
        if len(normalized) > 64:
            raise ValueError("受试者姓名最多64个字符")
        return normalized

    @staticmethod
    def _normalized_date(value: object) -> str:
        normalized = str(value).strip()
        if not _DATE_PATTERN.fullmatch(normalized):
            raise ValueError(f"采集日期格式无效：{normalized or '空'}")
        try:
            datetime.strptime(normalized, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(f"采集日期无效：{normalized}") from exc
        return normalized

    @staticmethod
    def _now() -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")


__all__ = ["SubjectAssignment", "SubjectCatalog"]
