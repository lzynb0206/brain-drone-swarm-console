"""UI workflow for importing, selecting and reporting labeled EEG trials."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Protocol

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QInputDialog,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from src.bci.config import TRUE_LABELS
from src.services.trial_analysis_service import (
    TrialAnalysisRecord,
    TrialAnalysisReport,
    TrialAnalysisService,
)
from src.services.subject_catalog import SubjectCatalog


class TrialAnalysisHost(Protocol):
    experiment_recorder: object | None

    def _notify(self, message: str) -> None: ...
    def update(self, *args) -> None: ...


@dataclass(frozen=True)
class TrialExperimentGroup:
    """One acquisition run shown between the action and trial selectors."""

    experiment_id: str
    started_at: str
    display_time: str
    record_count: int


@dataclass(frozen=True)
class TrialSubjectGroup:
    """One protected participant shown as the first analysis level."""

    subject_id: str
    display_name: str
    record_count: int
    date_count: int


@dataclass(frozen=True)
class TrialDateGroup:
    """One acquisition day within the selected participant."""

    acquisition_date: str
    display_date: str
    record_count: int


class TrialAnalysisController(QObject):
    """Own the data-page selection state and native import/report dialogs."""

    PAGE_SIZE = 6
    EXPERIMENT_PAGE_SIZE = 4
    SUBJECT_PAGE_SIZE = 4
    DATE_PAGE_SIZE = 5
    ALL_DATES = "__all_dates__"

    def __init__(
        self,
        host: TrialAnalysisHost,
        output_root: Path | str,
    ) -> None:
        parent = host if isinstance(host, QObject) else None
        super().__init__(parent)
        self.host = host
        self.service = TrialAnalysisService(output_root)
        self.records: list[TrialAnalysisRecord] = []
        self.source_path: Path | None = None
        self.catalog: SubjectCatalog | None = None
        self._subject_order: tuple[str, ...] = ()
        self.selected_subject_id = ""
        self.selected_date = ""
        self.subject_page = 0
        self.date_page = 0
        self.selected_kind = ""
        self.selected_label = ""
        self.selected_experiment_id = ""
        self.experiment_page = 0
        self.selected_record_index = -1
        self.page = 0
        self.window_start_seconds = 0.0
        self.window_end_seconds = 0.0
        self.last_report: TrialAnalysisReport | None = None
        self.report_pixmap: QPixmap | None = None
        self.paired_report_pixmap: QPixmap | None = None
        self.report_pair_id = ""
        self.report_note = ""

    @staticmethod
    def record_kind(record) -> str:
        if getattr(record, "analysis_kind", "") == "rest":
            return "rest"
        paradigm = str(getattr(record, "paradigm_type", ""))
        if getattr(record, "is_serial_pair", False) or ("MI" in paradigm and "SSVEP" in paradigm):
            return "SSVEP+MI混合"
        if paradigm == "MI" or getattr(record, "analysis_kind", "") == "mi":
            return "MI"
        return "SSVEP"

    @staticmethod
    def kind_title(kind: str) -> str:
        return {"SSVEP": "SSVEP", "MI": "MI 运动想象", "SSVEP+MI混合": "SSVEP＋MI 联合", "rest": "静息"}.get(kind, kind)

    @property
    def kinds(self) -> tuple[str, ...]:
        available = {self.record_kind(record) for record in self.records
                     if self._record_in_selected_subject_date(record, filter_kind=False)}
        return tuple(kind for kind in ("SSVEP", "MI", "SSVEP+MI混合", "rest") if kind in available)

    def select_kind(self, kind: str) -> None:
        if kind not in self.kinds:
            return
        self.selected_kind = kind
        self._clear_report()
        self._select_first_label()
        self.host.update()

    @property
    def labels(self) -> tuple[str, ...]:
        available = {
            record.true_label
            for record in self.records
            if self._record_in_selected_subject_date(record)
        }
        ordered = [label for label in TRUE_LABELS if label in available]
        ordered.extend(sorted(available.difference(ordered)))
        return tuple(ordered)

    @property
    def subjects(self) -> tuple[TrialSubjectGroup, ...]:
        grouped: dict[str, list[TrialAnalysisRecord]] = {}
        for record in self.records:
            subject_id = str(getattr(record, "subject_id", "")).strip()
            if subject_id:
                grouped.setdefault(subject_id, []).append(record)
        subjects = [
            TrialSubjectGroup(
                subject_id=subject_id,
                display_name=str(records[0].subject_name),
                record_count=len(records),
                date_count=len({record.acquisition_date for record in records}),
            )
            for subject_id, records in grouped.items()
        ]
        order = self.catalog.subject_order if self.catalog is not None else self._subject_order
        ranks = {subject_id: index for index, subject_id in enumerate(order)}
        subjects.sort(key=lambda group: (ranks.get(group.subject_id, len(ranks)), group.display_name, group.subject_id))
        return tuple(subjects)

    @property
    def subject_page_count(self) -> int:
        return max(1, (len(self.subjects) + self.SUBJECT_PAGE_SIZE - 1) // self.SUBJECT_PAGE_SIZE)

    @property
    def visible_subjects(self) -> tuple[TrialSubjectGroup, ...]:
        start = self.subject_page * self.SUBJECT_PAGE_SIZE
        return self.subjects[start:start + self.SUBJECT_PAGE_SIZE]

    @property
    def selected_subject(self) -> TrialSubjectGroup | None:
        return next(
            (group for group in self.subjects if group.subject_id == self.selected_subject_id),
            None,
        )

    @property
    def dates(self) -> tuple[TrialDateGroup, ...]:
        grouped: dict[str, list[TrialAnalysisRecord]] = {}
        for record in self.records:
            if str(getattr(record, "subject_id", "")) != self.selected_subject_id:
                continue
            date = str(getattr(record, "acquisition_date", "")).strip()
            if date:
                grouped.setdefault(date, []).append(record)
        dates = [
            TrialDateGroup(
                acquisition_date=date,
                display_date=self._display_acquisition_date(date),
                record_count=len(records),
            )
            for date, records in grouped.items()
        ]
        dates.sort(key=lambda group: group.acquisition_date, reverse=True)
        if not dates:
            return ()
        all_dates = TrialDateGroup(
            acquisition_date=self.ALL_DATES,
            display_date="全部日期",
            record_count=sum(group.record_count for group in dates),
        )
        return (all_dates, *dates)

    @property
    def date_page_count(self) -> int:
        return max(1, (len(self.dates) + self.DATE_PAGE_SIZE - 1) // self.DATE_PAGE_SIZE)

    @property
    def visible_dates(self) -> tuple[TrialDateGroup, ...]:
        start = self.date_page * self.DATE_PAGE_SIZE
        return self.dates[start:start + self.DATE_PAGE_SIZE]

    @property
    def selected_record(self) -> TrialAnalysisRecord | None:
        if 0 <= self.selected_record_index < len(self.records):
            return self.records[self.selected_record_index]
        return None

    def paired_record(self, record: TrialAnalysisRecord) -> TrialAnalysisRecord | None:
        if not record.is_serial_pair:
            return None
        other_stage = "mi" if record.stage == "ssvep" else "ssvep"
        candidates = [row for row in self.records
                      if row.stage == other_stage and row.pair_id == record.pair_id
                      and row.experiment_id == record.experiment_id and row.subject_id == record.subject_id]
        return candidates[0] if len(candidates) == 1 else None

    @property
    def selected_label_experiments(self) -> tuple[TrialExperimentGroup, ...]:
        grouped: dict[str, list[TrialAnalysisRecord]] = {}
        for record in self.records:
            if (
                record.true_label != self.selected_label
                or not self._record_in_selected_subject_date(record)
            ):
                continue
            experiment_id = self._record_experiment_id(record)
            grouped.setdefault(experiment_id, []).append(record)
        experiments = []
        for experiment_id, records in grouped.items():
            start_candidates = [
                value
                for record in records
                for value in [
                    str(getattr(record, "experiment_start_time", ""))
                    or str(getattr(record, "start_time", ""))
                ]
                if value
            ]
            started_at = min(start_candidates, default="")
            experiments.append(
                TrialExperimentGroup(
                    experiment_id=experiment_id,
                    started_at=started_at,
                    display_time=self._display_experiment_time(started_at),
                    record_count=len(records),
                )
            )
        experiments.sort(
            key=lambda group: (group.started_at, group.experiment_id),
            reverse=True,
        )
        return tuple(experiments)

    @property
    def experiment_page_count(self) -> int:
        count = len(self.selected_label_experiments)
        return max(1, (count + self.EXPERIMENT_PAGE_SIZE - 1) // self.EXPERIMENT_PAGE_SIZE)

    @property
    def visible_experiments(self) -> tuple[TrialExperimentGroup, ...]:
        start = self.experiment_page * self.EXPERIMENT_PAGE_SIZE
        return self.selected_label_experiments[start:start + self.EXPERIMENT_PAGE_SIZE]

    @property
    def selected_label_records(self) -> list[tuple[int, TrialAnalysisRecord]]:
        return [
            (index, record)
            for index, record in enumerate(self.records)
            if record.true_label == self.selected_label
            and self._record_in_selected_subject_date(record)
        ]

    @property
    def page_count(self) -> int:
        count = len(self.selected_label_records)
        return max(1, (count + self.PAGE_SIZE - 1) // self.PAGE_SIZE)

    @property
    def visible_records(self) -> list[tuple[int, TrialAnalysisRecord]]:
        records = self.selected_label_records
        start = self.page * self.PAGE_SIZE
        return records[start:start + self.PAGE_SIZE]

    @property
    def source_summary(self) -> str:
        if not self.records:
            return "尚未导入带标签试次"
        date_count = len(
            {
                (
                    str(getattr(record, "subject_id", "")),
                    str(getattr(record, "acquisition_date", "")),
                )
                for record in self.records
            }
        )
        source_name = (
            self.source_path.name or str(self.source_path)
            if self.source_path is not None
            else "多文件"
        )
        return (
            f"{source_name} · {len(self.subjects)} 位受试者 · {date_count} 个采集日 · "
            f"{len(self.records)} 个试次"
        )

    def load_project_data(self) -> None:
        recorder = self.host.experiment_recorder
        root = getattr(recorder, "root", None)
        if root is None:
            root = Path.cwd() / "data"
        self.load_source(Path(root))

    def choose_directory(self) -> None:
        self._release_mouse()
        path = QFileDialog.getExistingDirectory(
            self._parent_widget(),
            "选择带标签 EEG 数据目录",
            str(self.source_path or Path.cwd() / "data"),
        )
        if path:
            self.load_source(Path(path))
        else:
            self.host._notify("已取消导入数据目录")

    def choose_files(self) -> None:
        self._release_mouse()
        paths, _selected_filter = QFileDialog.getOpenFileNames(
            self._parent_widget(),
            "选择一个或多个带标签 EEG 试次",
            str(self.source_path or Path.cwd() / "data"),
            "带标签 EEG 试次 (*.npz);;所有文件 (*)",
        )
        if not paths:
            self.host._notify("已取消导入试次")
            return
        records: list[TrialAnalysisRecord] = []
        warnings: list[str] = []
        for path in paths:
            try:
                record = self.service.read_metadata(path)
                if not record.true_label:
                    raise ValueError("缺少真实标签")
                records.append(record)
            except (OSError, ValueError, KeyError) as exc:
                warnings.append(f"{Path(path).name}: {exc}")
        if not records:
            self.host._notify("导入失败：所选文件都不是有效的带标签 EEG 试次")
            return
        self._set_records(records, None, warnings)

    def choose_bdf(self) -> None:
        from src.services.bdf_import import BDFRecording
        from src.views.bdf_import_dialog import BDFImportDialog

        self._release_mouse()
        path, _ = QFileDialog.getOpenFileName(self._parent_widget(), "选择外部连续脑电 BDF",
                                             str(Path.home() / "Desktop"), "脑电 BDF (*.bdf *.BDF)")
        if not path:
            return
        try:
            recording = BDFRecording(path)
            root = Path(getattr(self.host.experiment_recorder, "root", self.service.output_root))
            dialog = BDFImportDialog(recording, root / "raw_trials" / "bdf_imports",
                                     root / "external_experiments", self._parent_widget())
            if dialog.exec() == QDialog.Accepted and dialog.paths:
                records = [self.service.read_metadata(p) for p in dialog.paths]
                self._set_records(records, None, ["外部 BDF：人工对齐，未验证；SSVEP 的 P 尚未计算"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.host._notify(f"BDF 导入失败：{exc}")

    def load_source(self, source: Path | str) -> None:
        resolved_source = self._resolved_analysis_source(
            Path(source).expanduser().resolve()
        )
        try:
            records, warnings = self.service.discover(resolved_source)
        except (OSError, ValueError) as exc:
            self.host._notify(f"导入失败：{exc}")
            return
        self._set_records(records, resolved_source, warnings)

    def _set_records(
        self,
        records: list[TrialAnalysisRecord],
        source: Path | None,
        warnings: list[str],
    ) -> None:
        unique = {record.path: record for record in records}
        catalog_root = self._catalog_root(source, tuple(unique.values()))
        try:
            self.catalog = SubjectCatalog(catalog_root)
            for record in unique.values():
                if record.acquisition_subject_id and not self.catalog.assignment_for_run(record.experiment_id):
                    self.catalog.restore_acquisition(record.experiment_id, record.acquisition_date,
                                                     record.acquisition_subject_id, record.subject_name)
            run_dates = {
                self._record_experiment_id(record): record.acquisition_date
                for record in unique.values()
            }
            self.catalog.ensure_runs(run_dates)
            resolved = []
            for record in unique.values():
                assignment = self.catalog.assignment_for_run(
                    self._record_experiment_id(record)
                )
                if assignment is None:
                    raise ValueError(f"批次 {record.experiment_id} 缺少受试者归属")
                resolved.append(
                    replace(
                        record,
                        subject_id=assignment.subject_id,
                        subject_name=assignment.display_name,
                        acquisition_date=assignment.acquisition_date,
                    )
                )
        except (OSError, ValueError) as exc:
            self.host._notify(f"受试者目录加载失败：{exc}")
            return
        self.records = sorted(
            resolved,
            key=lambda row: (
                row.subject_name,
                row.acquisition_date,
                row.experiment_start_time,
                row.true_label,
                row.start_time,
                row.trial_id,
            ),
        )
        self.source_path = source
        self._clear_report()
        self.subject_page = 0
        self.date_page = 0
        self.experiment_page = 0
        self.page = 0
        self._select_first_subject()
        warning_text = f"，跳过 {len(warnings)} 个无效文件" if warnings else ""
        self.host._notify(
            f"已导入 {len(self.records)} 个带标签试次，共 {len(self.subjects)} 位受试者、"
            f"{sum(group.date_count for group in self.subjects)} 个采集日"
            f"{warning_text}"
        )
        self.host.update()

    def select_subject(self, subject_id: str) -> None:
        subject_ids = [group.subject_id for group in self.subjects]
        if subject_id not in subject_ids:
            self.host._notify("所选受试者不存在")
            return
        self.selected_subject_id = subject_id
        self.subject_page = subject_ids.index(subject_id) // self.SUBJECT_PAGE_SIZE
        self.date_page = 0
        self.page = 0
        self._clear_report()
        self._select_first_date()
        self.host.update()

    def edit_selected_subject_name(self) -> None:
        subject = self.selected_subject
        if subject is None or self.catalog is None:
            self.host._notify("请先载入并选择一位受试者")
            return
        self._release_mouse()
        value, accepted = QInputDialog.getText(
            self._parent_widget(),
            "编辑受试者名称",
            "仅修改受试者显示名称；原始EEG与批次归属不会被改动：",
            text=subject.display_name,
        )
        if not accepted:
            return
        try:
            new_name = self.catalog.rename_subject(subject.subject_id, value)
        except ValueError as exc:
            self.host._notify(f"名称未修改：{exc}")
            return
        self.records = [
            replace(record, subject_name=new_name)
            if record.subject_id == subject.subject_id else record
            for record in self.records
        ]
        self.host._notify(
            f"受试者名称已更新为“{new_name}”；原始数据和稳定编号均已保护"
        )
        self.host.update()

    def move_subject(self, subject_id: str, target_id: str, after: bool = False) -> None:
        """Reorder cards without changing the selected trial or its report."""
        order = [group.subject_id for group in self.subjects]
        if subject_id == target_id or subject_id not in order or target_id not in order:
            return
        updated = list(order)
        updated.remove(subject_id)
        updated.insert(updated.index(target_id) + int(after), subject_id)
        if updated == order:
            return
        try:
            if self.catalog is not None:
                self.catalog.set_subject_order(updated)
            else:
                self._subject_order = tuple(updated)
        except (OSError, ValueError) as exc:
            self.host._notify(f"受试者顺序未保存：{exc}")
            return
        self.subject_page = updated.index(subject_id) // self.SUBJECT_PAGE_SIZE
        self.host.update()

    def previous_subject_page(self) -> None:
        self.subject_page = max(0, self.subject_page - 1)
        self._select_first_subject(visible_only=True)
        self._clear_report()
        self.host.update()

    def next_subject_page(self) -> None:
        self.subject_page = min(self.subject_page_count - 1, self.subject_page + 1)
        self._select_first_subject(visible_only=True)
        self._clear_report()
        self.host.update()

    def select_date(self, acquisition_date: str) -> None:
        date_values = [group.acquisition_date for group in self.dates]
        if acquisition_date not in date_values:
            self.host._notify("所选采集日期不存在")
            return
        self.selected_date = acquisition_date
        self.date_page = date_values.index(acquisition_date) // self.DATE_PAGE_SIZE
        self.page = 0
        self._clear_report()
        self._select_first_label()
        self.host.update()

    def previous_date_page(self) -> None:
        self.date_page = max(0, self.date_page - 1)
        self._select_first_date(visible_only=True)
        self._clear_report()
        self.host.update()

    def next_date_page(self) -> None:
        self.date_page = min(self.date_page_count - 1, self.date_page + 1)
        self._select_first_date(visible_only=True)
        self._clear_report()
        self.host.update()

    def select_label(self, label: str) -> None:
        if label not in self.labels:
            self.host._notify(f"当前数据中没有“{label}”试次")
            return
        self.selected_label = label
        self.page = 0
        self._clear_report()
        self._select_first_visible()
        self.host.update()

    def select_experiment(self, experiment_id: str) -> None:
        """Compatibility selector; the UI now exposes subject then date."""

        experiments = self.selected_label_experiments
        experiment_ids = [group.experiment_id for group in experiments]
        if experiment_id not in experiment_ids:
            self.host._notify("所选实验批次不存在")
            return
        self.selected_experiment_id = experiment_id
        matching = next(
            (
                record for record in self.records
                if self._record_experiment_id(record) == experiment_id
            ),
            None,
        )
        if matching is not None:
            self.selected_subject_id = matching.subject_id
            self.selected_date = matching.acquisition_date
        self.experiment_page = experiment_ids.index(experiment_id) // self.EXPERIMENT_PAGE_SIZE
        self.page = 0
        self._clear_report()
        self._select_first_visible()
        self.host.update()

    def select_record(self, record_index: int) -> None:
        if not 0 <= record_index < len(self.records):
            self.host._notify("所选试次不存在")
            return
        record = self.records[record_index]
        self.selected_subject_id = str(getattr(record, "subject_id", ""))
        self.selected_date = str(getattr(record, "acquisition_date", ""))
        self.selected_kind = self.record_kind(record)
        self.selected_label = record.true_label
        self.selected_experiment_id = self._record_experiment_id(record)
        self.selected_record_index = record_index
        label_indices = [index for index, _record in self.selected_label_records]
        self.page = label_indices.index(record_index) // self.PAGE_SIZE
        self._reset_window(record)
        self._clear_report()
        self.host.update()

    def previous_experiment_page(self) -> None:
        self.experiment_page = max(0, self.experiment_page - 1)
        self._clear_report()
        self._select_first_experiment(visible_only=True)
        self.host.update()

    def next_experiment_page(self) -> None:
        self.experiment_page = min(
            self.experiment_page_count - 1,
            self.experiment_page + 1,
        )
        self._clear_report()
        self._select_first_experiment(visible_only=True)
        self.host.update()

    def previous_page(self) -> None:
        self.page = max(0, self.page - 1)
        self._clear_report()
        self._select_first_visible()
        self.host.update()

    def next_page(self) -> None:
        self.page = min(self.page_count - 1, self.page + 1)
        self._clear_report()
        self._select_first_visible()
        self.host.update()

    def _select_first_subject(self, visible_only: bool = False) -> None:
        subjects = self.visible_subjects if visible_only else self.subjects
        if not subjects:
            self.selected_subject_id = ""
            self.selected_date = ""
            self.selected_label = ""
            self.selected_record_index = -1
            return
        self.selected_subject_id = subjects[0].subject_id
        self.date_page = 0
        self._select_first_date()

    def _select_first_date(self, visible_only: bool = False) -> None:
        dates = self.visible_dates if visible_only else self.dates
        if not dates:
            self.selected_date = ""
            self.selected_label = ""
            self.selected_record_index = -1
            return
        self.selected_date = dates[0].acquisition_date
        self.page = 0
        self._select_first_label()

    def _select_first_label(self) -> None:
        if self.selected_kind not in self.kinds:
            self.selected_kind = next(iter(self.kinds), "")
        labels = self.labels
        self.selected_label = labels[0] if labels else ""
        self.selected_experiment_id = ""
        self.page = 0
        self._select_first_visible()

    def _select_first_visible(self) -> None:
        visible = self.visible_records
        if not visible:
            self.selected_record_index = -1
            self.window_start_seconds = 0.0
            self.window_end_seconds = 0.0
            return
        self.selected_record_index = visible[0][0]
        self._reset_window(visible[0][1])

    def _select_first_experiment(self, visible_only: bool = False) -> None:
        experiments = (
            self.visible_experiments if visible_only else self.selected_label_experiments
        )
        if not experiments:
            self.selected_experiment_id = ""
            self.selected_record_index = -1
            self.window_start_seconds = 0.0
            self.window_end_seconds = 0.0
            return
        self.selected_experiment_id = experiments[0].experiment_id
        self.page = 0
        self._select_first_visible()

    def _record_in_selected_subject_date(self, record: TrialAnalysisRecord, *, filter_kind: bool = True) -> bool:
        return (
            str(getattr(record, "subject_id", "")) == self.selected_subject_id
            and (not filter_kind or not self.selected_kind or self.record_kind(record) == self.selected_kind)
            and (
                self.selected_date == self.ALL_DATES
                or str(getattr(record, "acquisition_date", "")) == self.selected_date
            )
        )

    def _catalog_root(
        self,
        source: Path | None,
        records: tuple[TrialAnalysisRecord, ...],
    ) -> Path:
        candidate = source if source is not None else (records[0].path if records else None)
        if candidate is not None:
            for ancestor in (candidate, *candidate.parents):
                if ancestor.name == "raw_trials":
                    return ancestor.parent
        if source is not None:
            if source.is_file():
                return source.parent
            if source.name == "raw_trials":
                return source.parent
            if (source / "raw_trials").is_dir():
                return source
            return source
        recorder = self.host.experiment_recorder
        root = getattr(recorder, "root", None)
        if root is not None:
            return Path(root).expanduser().resolve()
        if records:
            return records[0].path.parent
        return Path.cwd() / "data"

    @staticmethod
    def _resolved_analysis_source(source: Path) -> Path:
        """Route a selected top-level ``data`` folder to research experiments."""

        research_root = source / "research_experiments"
        if (research_root / "raw_trials").is_dir():
            return research_root
        return source

    @staticmethod
    def _record_experiment_id(record: TrialAnalysisRecord) -> str:
        return (
            str(getattr(record, "experiment_id", "")).strip()
            or str(getattr(record, "session_id", "")).strip()
            or "未分组实验"
        )

    @staticmethod
    def _display_experiment_time(value: str) -> str:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return "实验时间未知"
        return f"{parsed:%Y年%m月%d日 %H:%M}"

    @staticmethod
    def _display_acquisition_date(value: str) -> str:
        try:
            parsed = datetime.strptime(str(value), "%Y-%m-%d")
        except (TypeError, ValueError):
            return str(value) or "日期未知"
        return f"{parsed:%Y年%m月%d日}"

    def _reset_window(self, record: TrialAnalysisRecord) -> None:
        # Analyse the complete saved trial by default.  Cropping remains an
        # explicit operator choice in the time-window dialog; it must not be
        # silently introduced merely because a trial happens to be 5 s long.
        self.window_start_seconds = 0.0
        self.window_end_seconds = record.duration_seconds

    def configure_window(self) -> None:
        record = self.selected_record
        if record is None:
            self.host._notify("请先导入并选择一个试次")
            return
        self._release_mouse()
        dialog = QDialog(self._parent_widget())
        dialog.setWindowTitle("选择试次分析时间段")
        dialog.setWindowModality(Qt.WindowModal)
        layout = QVBoxLayout(dialog)
        description = QLabel(
            f"试次：{record.trial_id}\n真实标签：{record.true_label}\n"
            f"完整时长：{record.duration_seconds:.3f} 秒",
            dialog,
        )
        layout.addWidget(description)
        form = QFormLayout()
        start = self._seconds_box(dialog, 0.0, record.duration_seconds, self.window_start_seconds)
        end = self._seconds_box(dialog, 0.0, record.duration_seconds, self.window_end_seconds)
        form.addRow("开始时间", start)
        form.addRow("结束时间", end)
        layout.addLayout(form)
        hint = QLabel("时间段只作用于当前选中的阶段，配对的另一阶段保留完整时长；至少需要 100 个采样点。", dialog)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=dialog,
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if end.value() <= start.value():
            self.host._notify("分析结束时间必须晚于开始时间")
            return
        if (end.value() - start.value()) * record.sample_rate_hz < 100:
            self.host._notify("所选片段太短，至少需要 100 个采样点")
            return
        self.window_start_seconds = start.value()
        self.window_end_seconds = end.value()
        self._clear_report()
        self.host._notify(
            f"分析时间段已设为 {start.value():.2f}–{end.value():.2f} 秒"
        )
        self.host.update()

    def generate_report(self) -> None:
        record = self.selected_record
        if record is None:
            self.host._notify("请先导入数据并选择动作和具体试次")
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        note = ""
        reports = []
        try:
            self.service.prepare_renderer()
            report = self.service.generate_report(
                record,
                self.window_start_seconds,
                self.window_end_seconds,
            )
            reports = [report]
            partner = self.paired_record(record)
            if record.is_serial_pair and partner is None:
                note = "未载入唯一匹配的另一阶段，仅展示当前阶段；请导入同一批次的完整配对数据。"
            if partner is not None:
                ssvep, mi = (record, partner) if record.stage == "ssvep" else (partner, record)
                try:
                    self.service.validate_pair(ssvep, mi)
                    paired_report = self.service.generate_report(partner, 0., partner.duration_seconds)
                    reports = [report, paired_report] if record.stage == "ssvep" else [paired_report, report]
                except (OSError, ValueError) as exc:
                    note = f"仅展示当前阶段，另一阶段未合并：{exc}"
        except (OSError, ValueError) as exc:
            self.host._notify(f"分析报告生成失败：{exc}")
            return
        except Exception as exc:
            self.host._notify(f"分析组件运行失败：{exc}")
            return
        finally:
            QApplication.restoreOverrideCursor()
        pixmaps = []
        for item in reports:
            pixmap = QPixmap()
            if not pixmap.loadFromData(item.figure_png, "PNG"):
                self.host._notify("分析完成，但界面图表渲染失败")
                return
            pixmaps.append(pixmap)
        self.last_report = report
        self.report_pixmap = pixmaps[0]
        self.paired_report_pixmap = pixmaps[1] if len(pixmaps) == 2 else None
        self.report_pair_id = record.pair_id if len(pixmaps) == 2 else ""
        self.report_note = (("外部 BDF：人工时间对齐尚未验证；此图不能证明跨环节 P 有效。 "
                             if record.alignment_status else "") + note)
        self.host._notify(
            ("同组图表已按 SSVEP → MI 顺序显示；" if len(pixmaps) == 2 else "分析结果已显示在本页下方，可向下滚动查看；")
            + f"可用通道 {report.usable_channel_count}/{report.total_channel_count}"
            + (f"；{note}" if note else "")
        )
        self.host.update()

    def _clear_report(self) -> None:
        """Invalidate stale results and put painting/hit testing back at top."""

        self.last_report = None
        self.report_pixmap = None
        self.paired_report_pixmap = None
        self.report_pair_id = ""
        self.report_note = ""
        research_widget = getattr(self.host, "research_widget", None)
        reset_scroll = getattr(research_widget, "reset_research_scroll", None)
        if callable(reset_scroll):
            reset_scroll(repaint=False)

    def report_display_height(self, width: float) -> float:
        pixmap = self.report_pixmap
        if pixmap is None or pixmap.isNull() or pixmap.width() <= 0:
            return 0.0
        height = float(width) * pixmap.height() / pixmap.width()
        paired = self.paired_report_pixmap
        if paired is not None and not paired.isNull():
            height += 42. + float(width) * paired.height() / paired.width()
        return height

    @staticmethod
    def _seconds_box(
        parent: QWidget,
        minimum: float,
        maximum: float,
        value: float,
    ) -> QDoubleSpinBox:
        box = QDoubleSpinBox(parent)
        box.setRange(minimum, maximum)
        box.setDecimals(3)
        box.setSingleStep(0.1)
        box.setSuffix(" s")
        box.setValue(value)
        return box

    def _parent_widget(self) -> QWidget | None:
        return self.host if isinstance(self.host, QWidget) else None

    def _release_mouse(self) -> None:
        release = getattr(self.host, "_release_mouse_capture_for_dialog", None)
        if callable(release):
            release()


__all__ = ["TrialAnalysisController"]
