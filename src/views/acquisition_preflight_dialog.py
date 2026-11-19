"""Operator-visible passive preflight. No stimulation or actuation is started."""
import time
import uuid
import numpy as np
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QCheckBox, QPushButton, QPlainTextEdit
from src.bci.montage import HYBRID
from src.services.acquisition_preflight import AcquisitionProbe, save_probe


class AcquisitionPreflightDialog(QDialog):
    def __init__(self, worker, root, parent=None):
        super().__init__(parent)
        self.worker, self.root = worker, root
        self.probe_result = None
        self.active = False
        self.setWindowTitle("正式采集前：接线、信号与时间标记检查")
        self.resize(780, 560)
        layout = QVBoxLayout(self)
        description = QLabel("按实际接线核对：\n" + "   ".join(f"CH{i+1}={n}" for i, n in enumerate(HYBRID.names))
            + "\n软件不能由 EEG 判断电极的物理位置；下方检查约需 6 秒，请保持睁眼、放松、不动作。")
        description.setWordWrap(True)
        layout.addWidget(description)
        self.mapping = QCheckBox("我已逐路核对电极位置、接线顺序及参考／地线")
        layout.addWidget(self.mapping)
        self.start_button = QPushButton("开始试采检查")
        self.start_button.setEnabled(False)
        self.mapping.toggled.connect(self.start_button.setEnabled)
        self.mapping.toggled.connect(self._mapping_changed)
        self.start_button.clicked.connect(self.start_probe)
        layout.addWidget(self.start_button)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlainText("检查项目：八路信号、逐样本时间戳、实测采样率、间断、与标记时钟的关系、标记落盘。\n"
            "检查不会确认真实阻抗、屏幕物理亮起时刻或远端 LSL 接收。物理延迟需光电／回环实测。")
        layout.addWidget(self.output)
        self.close_button = QPushButton("关闭")
        self.close_button.clicked.connect(self.accept)
        layout.addWidget(self.close_button)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll)

    def _mapping_changed(self, checked):
        if not checked:
            self.probe_result = None

    def start_probe(self):
        self.probe_result = None
        self.probe = AcquisitionProbe()
        self.token = uuid.uuid4().hex[:12]
        self.markers = []
        self.mid_sent = False
        self.active = True
        self.started = time.monotonic()
        self.mapping.setEnabled(False)
        self.start_button.setEnabled(False)
        self.worker.frame_received.connect(self.feed, Qt.QueuedConnection)
        self.mark()
        self.timer.start()

    def mark(self):
        try:
            self.markers.append(self.worker.record_experiment_marker(
                f"research_preflight_{self.token}_{len(self.markers)}",
                metadata={"probe_id": self.token, "research_only": True, "data_kind": "preflight"}))
        except Exception as exc:
            self.probe.errors.append("时间标记写入失败：" + str(exc))

    def feed(self, frame):
        if not self.active:
            return
        try:
            x = np.asarray(frame.samples)
            timestamps = self.worker._frame_timestamps(frame, x.shape[1])
            self.probe.feed(frame, timestamps, self.worker.experiment_clock.now().lsl_seconds)
        except (ValueError, TypeError, IndexError) as exc:
            self.probe.errors.append("试采数据格式错误：" + str(exc))

    def poll(self):
        elapsed = time.monotonic() - self.started
        if elapsed >= 2.5 and not self.mid_sent:
            self.mark()
            self.mid_sent = True
        self.output.setPlainText(f"试采检查中：{elapsed:.1f} / 6.0 秒\n已收到 {sum(len(t) for t in self.probe.timestamps)} 个样本。\n请保持放松。")
        if elapsed >= 6:
            self.stop_probe()
            self.mark()
            try:
                self.probe_result = save_probe(self.root, self.probe, self.markers, self.mapping.isChecked(),
                                         self.worker.experiment_clock.source)
                r = self.probe_result
                if r.get("study_technical_ready") and r.get("signal_quality_warnings"):
                    status = "接线、时间与标记检查通过；信号质量告警已保存，论文方案可继续采集"
                else:
                    status = "软件检查通过" if r["formal_ready"] else "技术检查未通过，请修正后重新检查"
                lines = [status,
                         f"实测采样率：{r.get('measured_rate_hz', 0) or 0:.2f} Hz",
                         f"采集样本：{r['sample_count']}",
                         "本机标记落盘与时间范围：" + ("通过" if r["local_marker_sequence_verified"] else "未通过"),
                         "LSL 发布请求：" + ("成功；未验证远端接收" if r["lsl_publication_requested_successfully"] else "未成功；本机记录另行检查"),
                         *r["errors"], "原始试采和报告已保存到 research_preflight/。",
                         "物理电极由实验员确认；阻抗及屏幕物理延迟未由软件实测。"]
                self.output.setPlainText("\n".join(lines))
            except (OSError, ValueError) as exc:
                self.output.setPlainText("检查结果保存失败：" + str(exc))
            self.mapping.setEnabled(True)
            self.start_button.setEnabled(self.mapping.isChecked())

    def stop_probe(self):
        self.timer.stop()
        if self.active:
            self.active = False
            self.worker.frame_received.disconnect(self.feed)

    def done(self, result):
        self.stop_probe()
        super().done(result)
