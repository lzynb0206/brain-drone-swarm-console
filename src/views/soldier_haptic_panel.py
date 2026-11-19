"""Independent monochrome haptic page in the soldier sidebar."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QScrollArea, QSlider, QSpinBox, QToolButton, QVBoxLayout, QWidget,
)


class HandCard(QFrame):
    def __init__(self, hand, controller, parent=None):
        super().__init__(parent)
        self.hand, self.controller = hand, controller
        self._device_rows = None
        self.setObjectName("handCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)
        heading = QHBoxLayout()
        title = QLabel("左手" if hand == "L" else "右手")
        title.setObjectName("cardTitle")
        heading.addWidget(title)
        heading.addStretch()
        self.badge = QLabel("未连接")
        self.badge.setObjectName("stateBadge")
        heading.addWidget(self.badge)
        layout.addLayout(heading)
        device_name = QLabel("Haptic-Left" if hand == "L" else "Haptic-Right")
        device_name.setObjectName("muted")
        layout.addWidget(device_name)

        connection = QGridLayout()
        self.mode = QComboBox()
        self.mode.addItem("USB 串口", "USB")
        self.mode.addItem("蓝牙 BLE", "BLE")
        self.scan = QPushButton("刷新设备")
        self.endpoint = QComboBox()
        self.endpoint.setMinimumWidth(120)
        self.endpoint.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.endpoint.setMinimumContentsLength(14)
        self.connect_button = QPushButton("连接")
        connection.addWidget(self.mode, 0, 0)
        connection.addWidget(self.scan, 0, 1)
        connection.addWidget(self.endpoint, 1, 0)
        connection.addWidget(self.connect_button, 1, 1)
        connection.setColumnStretch(0, 1)
        layout.addLayout(connection)

        self.motor = QLabel("等待设备连接")
        self.motor.setObjectName("motorState")
        layout.addWidget(self.motor)
        self.telemetry = QLabel("实际强度 —  /  驱动 —")
        self.telemetry.setObjectName("muted")
        layout.addWidget(self.telemetry)
        strength_row = QHBoxLayout()
        strength_row.addWidget(QLabel("目标强度"))
        strength_row.addStretch()
        self.strength = QSpinBox()
        self.strength.setRange(0, 127)
        self.strength.setValue(60)
        self.strength.setSuffix(" / 127")
        strength_row.addWidget(self.strength)
        layout.addLayout(strength_row)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 127)
        self.slider.setValue(60)
        self.slider.setAccessibleName("左手目标强度" if hand == "L" else "右手目标强度")
        self.slider.valueChanged.connect(self.strength.setValue)
        self.strength.valueChanged.connect(self.slider.setValue)
        layout.addWidget(self.slider)
        presets = QHBoxLayout()
        for label, value in (("弱  35", 35), ("中  70", 70), ("强  100", 100)):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, v=value: self.strength.setValue(v))
            presets.addWidget(button)
        layout.addLayout(presets)
        controls = QHBoxLayout()
        self.start = QPushButton("开始 / 应用强度")
        self.start.setObjectName("primary")
        self.stop = QPushButton("停止")
        self.start.clicked.connect(lambda: controller.send(hand, f"rtp {self.strength.value()}"))
        self.stop.clicked.connect(lambda: controller.send(hand, "off"))
        controls.addWidget(self.start, 2)
        controls.addWidget(self.stop, 1)
        layout.addLayout(controls)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setMinimumHeight(24)
        layout.addWidget(self.message)

        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setText("效果与诊断")
        self.advanced_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.advanced_toggle.setArrowType(Qt.RightArrow)
        self.advanced_toggle.setCheckable(True)
        layout.addWidget(self.advanced_toggle)
        self.advanced = QWidget()
        form = QGridLayout(self.advanced)
        form.setContentsMargins(0, 0, 0, 0)
        self.effect = QSpinBox()
        self.effect.setRange(0, 123)
        self.effect.setValue(14)
        self.effect.setPrefix("ROM 效果 ")
        self.play_effect = QPushButton("播放效果")
        self.query = QPushButton("查询状态")
        self.i2c = QPushButton("检测 I²C")
        form.addWidget(self.effect, 0, 0)
        form.addWidget(self.play_effect, 0, 1)
        form.addWidget(self.query, 1, 0)
        form.addWidget(self.i2c, 1, 1)
        layout.addWidget(self.advanced)
        self.advanced.hide()
        self.advanced_toggle.toggled.connect(self._toggle_advanced)
        self.play_effect.clicked.connect(lambda: controller.send(hand, f"effect {self.effect.value()}"))
        self.query.clicked.connect(lambda: controller.send(hand, "status"))
        self.i2c.clicked.connect(lambda: controller.send(hand, "i2cscan"))
        self.scan.clicked.connect(lambda: controller.scan(hand, self.mode.currentData()))
        self.connect_button.clicked.connect(self._connect)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.endpoint.currentIndexChanged.connect(self._selection_changed)
        layout.addStretch()
        self.refresh()

    def _toggle_advanced(self, checked):
        self.advanced.setVisible(checked)
        self.advanced_toggle.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)

    def _mode_changed(self):
        self._device_rows = None
        self.refresh()

    def _selection_changed(self):
        state = self.controller.hands[self.hand]
        self.connect_button.setEnabled(not state.pending and (state.state == "ready" or (state.state == "offline" and bool(self.endpoint.currentData()))))

    def _connect(self):
        if self.controller.hands[self.hand].state == "ready":
            self.controller.disconnect_device(self.hand)
        else:
            self.controller.connect_device(self.hand, self.mode.currentData(), self.endpoint.currentData())

    def refresh(self):
        state = self.controller.hands[self.hand]
        ready, idle = state.state == "ready", not state.pending
        rows = state.devices if state.devices_mode == self.mode.currentData() else []
        if rows != self._device_rows:
            current = self.endpoint.currentData()
            self.endpoint.blockSignals(True)
            self.endpoint.clear()
            for row in rows:
                self.endpoint.addItem(row["label"], row["id"])
            if not rows:
                self.endpoint.addItem("请刷新设备列表", None)
            elif current and self.endpoint.findData(current) >= 0:
                self.endpoint.setCurrentIndex(self.endpoint.findData(current))
            self.endpoint.blockSignals(False)
            self._device_rows = list(rows)
        self.mode.setEnabled(state.state == "offline" and idle)
        self.scan.setEnabled(state.state == "offline" and idle)
        self.scan.setText("扫描中…" if state.pending == "scan" else "刷新设备")
        self.endpoint.setEnabled(state.state == "offline" and idle)
        self.endpoint.setToolTip(self.endpoint.currentText())
        self.connect_button.setText("断开" if ready else "连接")
        self._selection_changed()
        self.badge.setText({"offline": "未连接", "connecting": "连接中", "verifying": "校验中", "ready": "已连接", "closing": "断开中"}[state.state])
        fields = state.fields
        driving = ready and fields.get("drv") == "OK"
        self.motor.setText("等待设备连接" if not ready else "驱动异常" if fields.get("drv") == "ERR" else "振动中" if fields.get("motor") == "1" else "已停止" if fields.get("motor") == "0" else "等待设备状态")
        self.telemetry.setText(f"实际强度 {fields.get('rtp', '—')}  /  驱动 {fields.get('drv', '—')}  /  {fields.get('mode', '—')}")
        self.message.setText(state.message)
        self.start.setEnabled(driving and idle)
        self.stop.setEnabled(ready)
        self.play_effect.setEnabled(driving and idle)
        self.query.setEnabled(ready and idle)
        self.i2c.setEnabled(ready and idle)


class SoldierHapticPanel(QScrollArea):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setObjectName("soldierHaptic")
        self.setWidgetResizable(True)
        self.setStyleSheet("""
            #soldierHaptic { background: #f5f5f5; border: 0; }
            #hapticContent { background: #f5f5f5; }
            #hapticContent QLabel { color: #303030; font-size: 13px; background: transparent; }
            #hapticContent QLabel#pageTitle { font-size: 23px; font-weight: 600; }
            #hapticContent QLabel#cardTitle, #hapticContent QLabel#motorState { font-size: 22px; font-weight: 600; }
            #hapticContent QLabel#muted { color: #777777; font-size: 12px; }
            #hapticContent QLabel#stateBadge { background: #f0f0f0; border-radius: 12px; padding: 6px 12px; }
            #hapticContent QFrame#handCard { background: white; border: 1px solid #dedede; border-radius: 8px; }
            #hapticContent QPushButton, #hapticContent QToolButton { color: #303030; background: white;
                border: 1px solid #d3d3d3; border-radius: 5px; padding: 9px 12px; font-size: 13px; }
            #hapticContent QPushButton:hover, #hapticContent QToolButton:hover { background: #ededed; }
            #hapticContent QPushButton#primary { background: #252525; color: white; border-color: #252525; }
            #hapticContent QPushButton#primary:hover { background: #454545; }
            #hapticContent QPushButton:disabled, #hapticContent QPushButton#primary:disabled { background: #f3f3f3; color: #a3a3a3; border-color: #e4e4e4; }
            #hapticContent QComboBox, #hapticContent QSpinBox { background: #fafafa; color: #303030;
                border: 1px solid #d3d3d3; border-radius: 5px; padding: 8px; min-height: 20px; font-size: 13px; }
            #hapticContent QComboBox:disabled { color: #888888; }
            #hapticContent QComboBox QAbstractItemView { color: #303030; background: white; selection-background-color: #e8e8e8; selection-color: #171717; }
            #hapticContent QSlider::groove:horizontal { height: 5px; background: #e5e5e5; border-radius: 2px; }
            #hapticContent QSlider::sub-page:horizontal { background: #353535; border-radius: 2px; }
            #hapticContent QSlider::handle:horizontal { background: #252525; width: 16px; margin: -6px 0; border-radius: 8px; }
            #hapticContent QPlainTextEdit { background: white; color: #555555; border: 1px solid #dedede; border-radius: 5px; font-size: 12px; }
            #soldierHaptic QScrollBar:vertical { background: #f5f5f5; width: 8px; }
            #soldierHaptic QScrollBar::handle:vertical { background: #cccccc; min-height: 24px; border-radius: 4px; }
        """)
        content = QWidget()
        content.setObjectName("hapticContent")
        self.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(6, 6, 6, 12)
        layout.setSpacing(14)
        top = QHBoxLayout()
        title = QLabel("双手触觉控制")
        title.setObjectName("pageTitle")
        top.addWidget(title)
        top.addStretch()
        self.start_both = QPushButton("双手启动")
        self.stop_both = QPushButton("全部停止")
        self.stop_both.setObjectName("primary")
        top.addWidget(self.start_both)
        top.addWidget(self.stop_both)
        layout.addLayout(top)
        hint = QLabel("调整目标强度后点击应用。设备状态以板端回报为准。")
        hint.setObjectName("muted")
        layout.addWidget(hint)
        self.cards_layout = QGridLayout()
        self.cards_layout.setSpacing(20)
        self.cards = {h: HandCard(h, controller) for h in ("L", "R")}
        for i, card in enumerate(self.cards.values()):
            self.cards_layout.addWidget(card, 0, i)
            card.strength.valueChanged.connect(self.refresh)
        layout.addLayout(self.cards_layout)
        self.message = QLabel(controller.message)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.log_toggle = QToolButton()
        self.log_toggle.setText("通信日志")
        self.log_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.log_toggle.setCheckable(True)
        self.log_toggle.setArrowType(Qt.RightArrow)
        layout.addWidget(self.log_toggle, alignment=Qt.AlignLeft)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(200)
        self.log.setFixedHeight(160)
        self.log.setPlainText("\n".join(controller.logs))
        self.log.hide()
        layout.addWidget(self.log)
        layout.addStretch()
        self.log_toggle.toggled.connect(self._toggle_log)
        self.start_both.clicked.connect(lambda: controller.start_both(self.cards["L"].strength.value(), self.cards["R"].strength.value()))
        self.stop_both.clicked.connect(controller.stop_both)
        controller.changed.connect(self.refresh)
        controller.log_received.connect(self.log.appendPlainText)
        self.refresh()

    def _toggle_log(self, checked):
        self.log.setVisible(checked)
        self.log_toggle.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)

    def resizeEvent(self, event):
        # Stack cards on smaller windows while preserving sidebar geometry.
        narrow = self.viewport().width() < 800
        self.cards_layout.addWidget(self.cards["R"], 1 if narrow else 0, 0 if narrow else 1)
        super().resizeEvent(event)

    def refresh(self):
        for card in self.cards.values():
            card.refresh()
        states = list(self.controller.hands.values())
        self.start_both.setEnabled(all(s.state == "ready" and s.fields.get("drv") == "OK" and not s.pending for s in states)
                                   and all(c.strength.value() > 0 for c in self.cards.values()))
        self.stop_both.setEnabled(any(s.state == "ready" for s in states))
        self.message.setText(self.controller.message)
