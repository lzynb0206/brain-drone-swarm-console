"""Native Qt camera/gesture panel embedded in the soldier dashboard."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from src.controllers.gesture_controller import DEFAULT_WEIGHTS, GestureController


class CameraPreview(QWidget):
    def __init__(self, placeholder, parent=None):
        super().__init__(parent)
        self.image = QImage()
        self.placeholder = placeholder
        self.setMinimumSize(160, 120)

    def set_image(self, image):
        self.image = image
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#f3f3f3") if self.image.isNull() else Qt.GlobalColor.black)
        if self.image.isNull():
            painter.setPen(Qt.GlobalColor.gray)
            painter.drawText(self.rect(), Qt.AlignCenter, self.placeholder)
        else:
            size = self.image.size().scaled(self.size(), Qt.KeepAspectRatio)
            rect = self.rect()
            rect.setSize(size)
            rect.moveCenter(self.rect().center())
            painter.drawImage(rect, self.image)
        painter.end()


class SoldierVisionPanel(QScrollArea):
    def __init__(self, controller: GestureController, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setObjectName("soldierVision")
        self.setWidgetResizable(True)
        self.setStyleSheet("""
            QScrollArea#soldierVision { background: #ffffff; border: 1px solid #dedede; }
            #visionContent { background: #ffffff; }
            #visionContent QLabel { color: #383838; font-size: 12px; }
            #visionContent QLabel#visionTitle { color: #202020; font-size: 20px; font-weight: bold; }
            #visionContent QLabel#gestureVerdict { color: #202020; font-size: 24px; font-weight: bold; }
            #visionContent QGroupBox { color: #666666; border: 1px solid #dedede;
                border-radius: 5px; margin-top: 12px; padding-top: 12px; }
            #visionContent QGroupBox::title { subcontrol-origin: margin; left: 12px; }
            #visionContent QLineEdit, #visionContent QSpinBox, #visionContent QComboBox {
                color: #292929; background: #fafafa; border: 1px solid #d3d3d3;
                border-radius: 3px; padding: 5px; min-height: 20px; }
            #visionContent QPushButton { color: #ffffff; background: #292929;
                border: 1px solid #292929; border-radius: 4px; padding: 7px 14px; }
            #visionContent QPushButton:disabled { color: #a0a0a0; border-color: #e1e1e1; background: #f1f1f1; }
            #visionContent QPushButton:hover { background: #454545; }
            #visionContent QLineEdit:focus, #visionContent QSpinBox:focus,
            #visionContent QComboBox:focus { border: 1px solid #555555; }
            #visionContent QComboBox QAbstractItemView { color: #292929; background: #ffffff;
                selection-background-color: #e8e8e8; selection-color: #171717; }
            QScrollArea#soldierVision QScrollBar:vertical { background: #f5f5f5; width: 8px; }
            QScrollArea#soldierVision QScrollBar::handle:vertical { background: #cccccc; border-radius: 4px; min-height: 24px; }
        """)
        content = QWidget()
        content.setObjectName("visionContent")
        self.setWidget(content)
        outer = QVBoxLayout(content)
        outer.setContentsMargins(16, 12, 16, 12)
        title_row = QHBoxLayout()
        title = QLabel("摄像头 · 手势识别")
        title.setObjectName("visionTitle")
        title_row.addWidget(title)
        title_row.addStretch()
        self.start_button = QPushButton("连接并识别")
        self.stop_button = QPushButton("停止采集")
        title_row.addWidget(self.start_button)
        title_row.addWidget(self.stop_button)
        outer.addLayout(title_row)

        self.config = QWidget()
        form = QGridLayout(self.config)
        form.setContentsMargins(0, 0, 0, 0)
        self.source_kind = QComboBox()
        for label, value in (("本机 / USB 摄像头", "camera"), ("机载 MJPEG 视频流", "mjpeg"),
                             ("视频文件回放", "video")):
            self.source_kind.addItem(label, value)
        self.camera_id = QSpinBox()
        self.camera_id.setRange(0, 99)
        self.camera_id.setPrefix("摄像头 #")
        self.source = QLineEdit()
        self.browse_video = QPushButton("选择视频")
        self.weights = QLineEdit(str(DEFAULT_WEIGHTS))
        self.browse_weights = QPushButton("选择权重")
        self.threshold = QSpinBox()
        self.threshold.setRange(2, 60)
        self.threshold.setValue(12)
        self.threshold.setPrefix("帧差阈值 ")
        self.rotation = QComboBox()
        for angle in (0, 90, 180, 270):
            self.rotation.addItem(f"旋转 {angle}°", angle)
        form.addWidget(self.source_kind, 0, 0)
        source_row = QHBoxLayout()
        source_row.addWidget(self.camera_id)
        source_row.addWidget(self.source, 1)
        source_row.addWidget(self.browse_video)
        form.addLayout(source_row, 0, 1, 1, 2)
        form.addWidget(self.threshold, 0, 3)
        form.addWidget(self.rotation, 0, 4)
        form.addWidget(QLabel("SNN 模型权重"), 1, 0)
        form.addWidget(self.weights, 1, 1, 1, 3)
        form.addWidget(self.browse_weights, 1, 4)
        form.setColumnStretch(1, 1)
        outer.addWidget(self.config)
        self.status = QLabel()
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        body = QHBoxLayout()
        camera_box = QGroupBox("实时画面")
        camera_layout = QVBoxLayout(camera_box)
        self.preview = CameraPreview("连接摄像头后显示实时画面")
        self.preview.setMinimumHeight(280)
        camera_layout.addWidget(self.preview, 1)
        camera_layout.addWidget(QLabel("右手挥手 → 前进提示    /    左手挥手 → 后退提示    /    其他 → 拒识"))
        body.addWidget(camera_box, 3)
        right = QVBoxLayout()
        result_box = QGroupBox("识别结果 · 11 类 SNN")
        result_layout = QVBoxLayout(result_box)
        self.verdict = QLabel("等待识别")
        self.verdict.setObjectName("gestureVerdict")
        self.suggestion = QLabel("指令提示：—")
        self.metrics = QLabel("发放率 —    推理 — ms")
        self.top3 = QLabel("Top 3：—")
        self.top3.setWordWrap(True)
        for widget in (self.verdict, self.suggestion, self.metrics, self.top3):
            result_layout.addWidget(widget)
        right.addWidget(result_box)
        event_box = QGroupBox("帧差事件 · 16 时间片 · 红 ON / 蓝 OFF")
        event_layout = QVBoxLayout(event_box)
        self.events = CameraPreview("等待首个完整识别窗口")
        self.events.setMinimumHeight(155)
        event_layout.addWidget(self.events)
        right.addWidget(event_box, 1)
        body.addLayout(right, 2)
        outer.addLayout(body, 1)
        note = QLabel("手势仅提供识别与指令提示，不自动执行飞行。发放率为模型原始分数；普通摄像头的帧差输入与 DVS 训练数据不同。")
        note.setWordWrap(True)
        outer.addWidget(note)

        self.source_kind.currentIndexChanged.connect(self._source_changed)
        self.browse_video.clicked.connect(self._choose_video)
        self.browse_weights.clicked.connect(self._choose_weights)
        self.start_button.clicked.connect(self._start)
        self.stop_button.clicked.connect(controller.stop)
        controller.frame_received.connect(self.preview.set_image)
        controller.events_received.connect(self.events.set_image)
        controller.changed.connect(self.refresh)
        self._source_changed()
        self.refresh()

    def _source_changed(self):
        kind = self.source_kind.currentData()
        self.camera_id.setVisible(kind == "camera")
        self.source.setVisible(kind != "camera")
        self.browse_video.setVisible(kind == "video")
        self.source.clear()
        self.source.setPlaceholderText("http://设备IP/stream" if kind == "mjpeg" else "选择本机视频文件")

    def _choose_video(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择手势回放视频", "", "视频 (*.mp4 *.avi *.mov *.mkv);;所有文件 (*)")
        if path:
            self.source.setText(path)

    def _choose_weights(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 SNN 权重", str(DEFAULT_WEIGHTS.parent), "PyTorch 权重 (*.pth *.pt)")
        if path:
            self.weights.setText(path)

    def _start(self):
        self.preview.set_image(QImage())
        self.events.set_image(QImage())
        kind = self.source_kind.currentData()
        source = str(self.camera_id.value()) if kind == "camera" else self.source.text().strip()
        self.controller.start(kind, source, self.weights.text().strip(),
                              self.threshold.value(), self.rotation.currentData())

    def refresh(self):
        controller = self.controller
        self.status.setText(controller.message)
        self.start_button.setEnabled(not controller.running)
        self.stop_button.setEnabled(controller.running)
        self.config.setEnabled(not controller.running)
        result = controller.fresh_result()
        if result:
            self.verdict.setText(result["top_name"])
            self.suggestion.setText("指令提示：" + {1: "前进", 3: "后退"}.get(result["top"], "拒识 / 悬停"))
            self.metrics.setText(f"发放率 {result['score']:.3f}    推理 {result['infer_ms']:.0f} ms")
            self.top3.setText("   /   ".join(f"{r['name']} {r['score']:.3f}" for r in result["top3"]))
        else:
            self.verdict.setText("等待新结果" if controller.running else "识别已停止")
            self.suggestion.setText("指令提示：—")
            self.metrics.setText("发放率 —    推理 — ms")
            self.top3.setText("Top 3：—")
            self.events.set_image(QImage())
        if not controller.running or controller._failed:
            self.preview.set_image(QImage())
