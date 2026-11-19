"""Explicit manual alignment and named-channel selection for external BDF."""
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QLabel, QLineEdit,
    QDoubleSpinBox, QPushButton, QFileDialog, QPlainTextEdit, QCheckBox, QDialogButtonBox)

from src.services.bdf_import import BDFRecording, ManualAlignment, plan_bdf_segments, import_bdf


class BDFImportWorker(QThread):
    result = Signal(object)
    failed = Signal(str)

    def __init__(self, arguments, parent=None):
        super().__init__(parent)
        self.arguments = arguments

    def run(self):
        try:
            self.result.emit(import_bdf(*self.arguments))
        except Exception as exc:
            self.failed.emit(str(exc))


class BDFImportDialog(QDialog):
    def __init__(self, recording: BDFRecording, output_root, log_root, parent=None):
        super().__init__(parent)
        self.recording = recording
        self.output_root = Path(output_root)
        self.log_root = Path(log_root)
        self.paths = []
        self.worker = None
        self.setWindowTitle('导入外部 BDF · 对齐、分段与分析通道')
        self.resize(860, 720)
        layout = QVBoxLayout(self)
        heading = QLabel(f'{recording.path.name} · {len(recording.eeg_names)} 路 EEG · {recording.fs:g} Hz · {recording.duration:g} 秒\n'
                         + ' / '.join(recording.eeg_names))
        heading.setWordWrap(True); layout.addWidget(heading)
        note = QLabel('连续 BDF 没有任务标签。请选择本机同一轮实验的 events.jsonl。\n'
                      'BDF 时间 = 日志经过时间 × (1 + 漂移 ppm / 1,000,000) + 偏移。\n'
                      '两台电脑的开始按钮或系统时间不能保证同步；没有可核验锚点时，分段仅供探索，不能当作已验证配对。')
        note.setWordWrap(True); layout.addWidget(note)
        form = QFormLayout(); layout.addLayout(form)
        self.log = QLineEdit(); form.addRow('同一轮实验日志', self.log)
        browse = QPushButton('选择实验日志…'); browse.clicked.connect(self.choose_log); form.addRow(browse)
        self.offset = QDoubleSpinBox(); self.offset.setRange(-86400,86400); self.offset.setDecimals(4)
        self.offset.setSuffix(' 秒'); form.addRow('日志 0 秒对应的 BDF 时间', self.offset)
        self.drift = QDoubleSpinBox(); self.drift.setRange(-10000,10000); self.drift.setDecimals(3)
        self.drift.setSuffix(' ppm'); form.addRow('时钟漂移（未知时 0，仍属未验证）', self.drift)
        self.evidence = QLineEdit(); self.evidence.setPlaceholderText('记录锚点和测量方法，或填写：人工估计，未验证')
        form.addRow('对齐依据', self.evidence)
        names = recording.eeg_names
        self.ssvep = QLineEdit(', '.join(n for n in ('O1','Oz','O2','POz','P3','Pz','P4') if n in names))
        self.mi = QLineEdit(', '.join(n for n in ('FC3','C3','CP3','Cz','CPz','FC4','C4','CP4') if n in names))
        form.addRow('SSVEP 分析通道（英文逗号分隔）', self.ssvep)
        form.addRow('MI 分析通道（英文逗号分隔）', self.mi)
        channel_note = QLabel('始终保存全部脑电通道；上述选择只用于分析。默认通道是起点，不代表已经找到了最优组合。')
        channel_note.setWordWrap(True); layout.addWidget(channel_note)
        preview = QPushButton('检查截取范围'); preview.clicked.connect(self.preview); layout.addWidget(preview)
        self.preview_text = QPlainTextEdit(); self.preview_text.setReadOnly(True); layout.addWidget(self.preview_text)
        self.ack = QCheckBox('我已核对日志与 BDF 属于同一轮；接受当前对齐标记为“人工对齐，未验证”')
        layout.addWidget(self.ack)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText('按日志分段导入')
        self.buttons.accepted.connect(self.start_import); self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def choose_log(self):
        path, _ = QFileDialog.getOpenFileName(self,'选择同一轮 events.jsonl',str(self.log_root),'实验日志 (*.jsonl)')
        if path:
            self.log.setText(path)

    def alignment(self):
        return ManualAlignment(self.offset.value(),self.drift.value(),self.evidence.text())

    def preview(self):
        try:
            planned = plan_bdf_segments(self.recording,self.log.text(),self.alignment())
            lines = [f'可截取 {len(planned)} 个完整阶段；原 BDF 不修改。时间对齐仍未验证。']
            for c, a, b in planned[:20]:
                lines.append(f'{c["true_label"]} · {c.get("stage","ssvep")} · BDF {a/self.recording.fs:.3f}–{b/self.recording.fs:.3f} 秒'
                             f' · 日志起点 {c["start_seconds"]:.3f} 秒')
            if len(planned)>20:
                lines.append(f'…另有 {len(planned)-20} 个阶段')
            self.preview_text.setPlainText('\n'.join(lines))
            return True
        except (OSError,ValueError,KeyError,TypeError) as exc:
            self.preview_text.setPlainText(f'不能导入：{exc}')
            return False

    def start_import(self):
        if self.worker and self.worker.isRunning():
            return
        if not self.ack.isChecked():
            self.preview_text.setPlainText('请核对同一轮实验及时间对齐说明后勾选确认。')
            return
        if not self.preview():
            return
        split = lambda text: tuple(n.strip() for n in text.replace('，',',').split(',') if n.strip())
        arguments = (self.recording,self.log.text(),self.output_root,self.alignment(),
                     split(self.ssvep.text()),split(self.mi.text()))
        self.worker = BDFImportWorker(arguments,self)
        self.worker.result.connect(self._result)
        self.worker.failed.connect(lambda message:self.preview_text.setPlainText('导入失败：'+message))
        self.worker.finished.connect(self._finished)
        self.buttons.setEnabled(False)
        self.preview_text.setPlainText('正在读取并保存全部脑电通道；导入结果写入独立目录，请稍候…')
        self.worker.start()

    def _result(self, paths):
        self.paths = paths

    def _finished(self):
        self.buttons.setEnabled(True)
        if self.paths:
            self.accept()

    def reject(self):
        if self.worker and self.worker.isRunning():
            return
        super().reject()
