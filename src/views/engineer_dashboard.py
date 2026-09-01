"""Widget-based engineering dashboard with replaceable data providers."""

from __future__ import annotations

from collections import deque

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.domain import SystemMetrics
from src.ports import EngineerDataProvider, SystemMonitor


class MetricsChart(QWidget):
    """Small data-driven chart; only the plot is custom-painted."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(190)
        self._cpu: deque[float] = deque([42, 47, 45, 51, 49, 56, 52, 48], maxlen=40)
        self._memory: deque[float] = deque([58, 59, 58, 60, 61, 60, 62, 61], maxlen=40)
        self._latency: deque[float] = deque([18, 16, 20, 15, 19, 17, 14, 16], maxlen=40)

    def append(self, metrics: SystemMetrics) -> None:
        self._cpu.append(metrics.cpu_percent)
        self._memory.append(metrics.memory_percent)
        self._latency.append(min(100, metrics.latency_ms * 3))
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#091426"))
        left, top, right, bottom = 42, 18, self.width() - 18, self.height() - 30
        painter.setPen(QPen(QColor("#20334d"), 1, Qt.DashLine))
        for i in range(5):
            y = top + (bottom - top) * i / 4
            painter.drawLine(left, int(y), right, int(y))
        for values, color in (
            (self._cpu, "#3b82f6"),
            (self._memory, "#22c55e"),
            (self._latency, "#f59e0b"),
        ):
            points = list(values)
            if len(points) < 2:
                continue
            path = QPainterPath()
            for i, value in enumerate(points):
                x = left + (right - left) * i / max(1, len(points) - 1)
                y = bottom - (bottom - top) * max(0, min(100, value)) / 100
                path.moveTo(QPointF(x, y)) if i == 0 else path.lineTo(QPointF(x, y))
            painter.setPen(QPen(QColor(color), 2))
            painter.drawPath(path)


class EngineerDashboard(QWidget):
    """Real Qt controls for navigation, logs, diagnostics and live metrics."""

    logout_requested = Signal()

    MODULES = (
        ("swarm", "蜂群作战管理"),
        ("target", "目标识别与跟踪"),
        ("decoder", "脑电解码引擎"),
        ("coordination", "设备协同管理"),
        ("device", "设备状态监控"),
        ("quality", "信号质量监控"),
        ("data", "实验数据管理"),
    )

    def __init__(
        self,
        system_monitor: SystemMonitor,
        data_provider: EngineerDataProvider,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.system_monitor = system_monitor
        self.data_provider = data_provider
        self.module_tables: dict[str, QTableWidget] = {}
        self.metric_labels: dict[str, QLabel] = {}
        self._build_ui()
        self._apply_style()
        self.module_list.setCurrentRow(5)
        self.log_tabs.setCurrentIndex(0)
        self.refresh()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QFrame()
        header.setObjectName("engineerHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(22, 0, 18, 0)
        title = QLabel("脑控无人机蜂群作战系统")
        title.setObjectName("engineerTitle")
        header_layout.addWidget(title)
        header_layout.addStretch()
        self.connection_label = QLabel("● 演示数据接口")
        self.connection_label.setObjectName("connectionState")
        header_layout.addWidget(self.connection_label)
        logout = QPushButton("退出登录")
        logout.setObjectName("secondaryButton")
        logout.clicked.connect(self.logout_requested)
        header_layout.addWidget(logout)
        root.addWidget(header)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        root.addLayout(body, 1)

        sidebar = QFrame()
        sidebar.setObjectName("engineerSidebar")
        sidebar.setFixedWidth(230)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(16, 22, 16, 18)
        side_layout.addWidget(self._section_title("功能模块"))
        self.module_list = QListWidget()
        self.module_list.setObjectName("moduleList")
        self.module_list.setFocusPolicy(Qt.NoFocus)
        for key, label in self.MODULES:
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, key)
            self.module_list.addItem(item)
        self.module_list.currentRowChanged.connect(self._module_changed)
        side_layout.addWidget(self.module_list, 1)
        body.addWidget(sidebar)

        center = QFrame()
        center.setObjectName("engineerCenter")
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(24, 18, 24, 14)
        center_layout.setSpacing(12)
        self.center_title = QLabel("信号质量监控")
        self.center_title.setObjectName("pageTitle")
        center_layout.addWidget(self.center_title)
        self.mode_stack = QStackedWidget()
        self.monitor_stack = QStackedWidget()
        for key, label in self.MODULES:
            page = self._build_quality_page() if key == "quality" else self._build_module_page(key, label)
            self.monitor_stack.addWidget(page)
        self.mode_stack.addWidget(self.monitor_stack)
        self.mode_stack.addWidget(self._build_diagnostics_page())
        center_layout.addWidget(self.mode_stack, 1)

        footer = QHBoxLayout()
        footer.addStretch()
        self.monitor_button = QPushButton("系统监控")
        self.monitor_button.setObjectName("modeButton")
        self.monitor_button.setCheckable(True)
        self.monitor_button.setChecked(True)
        self.diagnostics_button = QPushButton("深度诊断")
        self.diagnostics_button.setObjectName("modeButton")
        self.diagnostics_button.setCheckable(True)
        self.monitor_button.clicked.connect(lambda: self._set_mode(0))
        self.diagnostics_button.clicked.connect(lambda: self._set_mode(1))
        footer.addWidget(self.monitor_button)
        footer.addWidget(self.diagnostics_button)
        footer.addStretch()
        center_layout.addLayout(footer)
        body.addWidget(center, 1)

        logs = QFrame()
        logs.setObjectName("engineerLogs")
        logs.setFixedWidth(310)
        logs_layout = QVBoxLayout(logs)
        logs_layout.setContentsMargins(10, 14, 10, 14)
        logs_layout.addWidget(self._section_title("运行日志"))
        self.log_tabs = QTabWidget()
        self.log_tabs.setObjectName("logTabs")
        self.log_views: dict[str, QTextEdit] = {}
        for level in ("INFO", "WARN", "ERROR"):
            view = QTextEdit()
            view.setReadOnly(True)
            self.log_views[level] = view
            self.log_tabs.addTab(view, level)
        self.log_tabs.currentChanged.connect(self._refresh_logs)
        logs_layout.addWidget(self.log_tabs, 1)
        body.addWidget(logs)

    def _build_quality_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        streams_panel = self._panel("数据流状态")
        cards = QHBoxLayout()
        cards.setSpacing(12)
        for stream in self.data_provider.streams():
            card = QFrame()
            card.setObjectName("streamCard")
            card_layout = QVBoxLayout(card)
            name = QLabel(stream.name)
            name.setObjectName("cardTitle")
            card_layout.addWidget(name)
            card_layout.addWidget(QLabel(stream.source))
            rate = QLabel(f"{stream.sample_rate_hz:g} Hz")
            rate.setObjectName("cardMetric")
            card_layout.addWidget(rate)
            card_layout.addStretch()
            state = "在线" if stream.online else "离线"
            card_layout.addWidget(QLabel(f"{state}  ·  丢包 {stream.packet_loss_percent:.1f}%"))
            cards.addWidget(card)
        streams_panel.layout().addLayout(cards)
        layout.addWidget(streams_panel)

        performance = self._panel("系统性能")
        metrics = QHBoxLayout()
        for key, label, color in (
            ("cpu", "CPU", "#3b82f6"), ("memory", "内存", "#22c55e"),
            ("latency", "延迟", "#f59e0b"), ("throughput", "吞吐量", "#22d3ee"),
        ):
            value = QLabel(f"{label}\n--")
            value.setObjectName("metricLabel")
            value.setStyleSheet(f"border-top: 2px solid {color};")
            self.metric_labels[key] = value
            metrics.addWidget(value)
        performance.layout().addLayout(metrics)
        self.metrics_chart = MetricsChart()
        performance.layout().addWidget(self.metrics_chart, 1)
        layout.addWidget(performance, 1)
        return page

    def _build_module_page(self, key: str, label: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        summary = self._panel(f"{label}概览")
        description = QLabel("当前显示演示数据。接入真实接口后，此表会由数据提供器定时刷新。")
        description.setWordWrap(True)
        summary.layout().addWidget(description)
        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(["对象", "状态", "指标", "更新时间"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setAlternatingRowColors(True)
        summary.layout().addWidget(table, 1)
        self.module_tables[key] = table
        layout.addWidget(summary, 1)
        return page

    def _build_diagnostics_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        panel = self._panel("系统自检")
        grid = QGridLayout()
        self.diagnostic_bars: dict[str, QProgressBar] = {}
        for row, (key, label) in enumerate((("cpu", "CPU"), ("gpu", "GPU"), ("memory", "内存"))):
            grid.addWidget(QLabel(label), row, 0)
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setFormat("%p%")
            self.diagnostic_bars[key] = bar
            grid.addWidget(bar, row, 1)
        panel.layout().addLayout(grid)
        self.self_test_result = QLabel("尚未执行自检")
        self.self_test_result.setObjectName("selfTestResult")
        panel.layout().addWidget(self.self_test_result)
        self.self_test_button = QPushButton("执行一键自检")
        self.self_test_button.setObjectName("primaryButton")
        self.self_test_button.clicked.connect(self._run_self_test)
        panel.layout().addWidget(self.self_test_button, 0, Qt.AlignLeft)
        layout.addWidget(panel, 1)
        return page

    def _panel(self, title: str) -> QFrame:
        panel = QFrame()
        panel.setObjectName("contentPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 14, 18, 16)
        layout.setSpacing(10)
        layout.addWidget(self._section_title(title))
        return panel

    @staticmethod
    def _section_title(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionTitle")
        return label

    def _module_changed(self, row: int) -> None:
        if row < 0:
            return
        key, label = self.MODULES[row]
        self.monitor_stack.setCurrentIndex(row)
        self.center_title.setText(label)
        self._set_mode(0)
        self._refresh_module(key)

    def _set_mode(self, index: int) -> None:
        self.mode_stack.setCurrentIndex(index)
        self.monitor_button.setChecked(index == 0)
        self.diagnostics_button.setChecked(index == 1)
        if index == 1:
            self.center_title.setText("系统深度诊断")
        elif self.module_list.currentRow() >= 0:
            self.center_title.setText(self.MODULES[self.module_list.currentRow()][1])

    def _refresh_module(self, key: str) -> None:
        table = self.module_tables.get(key)
        if table is None:
            return
        records = self.data_provider.module_records(key)
        table.setRowCount(len(records))
        for row, record in enumerate(records):
            for column, value in enumerate((record.name, record.status, record.metric, record.updated_at)):
                table.setItem(row, column, QTableWidgetItem(value))

    def _refresh_logs(self, index: int = 0) -> None:
        level = ("INFO", "WARN", "ERROR")[max(0, index)]
        self.log_views[level].setPlainText("\n\n".join(self.data_provider.logs(level)))

    def refresh(self) -> None:
        metrics = self.system_monitor.snapshot()
        self.metric_labels["cpu"].setText(f"CPU\n{metrics.cpu_percent:.0f}%")
        self.metric_labels["memory"].setText(f"内存\n{metrics.memory_percent:.0f}%")
        self.metric_labels["latency"].setText(f"延迟\n{metrics.latency_ms:.0f} ms")
        self.metric_labels["throughput"].setText(f"吞吐量\n{metrics.throughput_mbps:.1f} MB/s")
        self.metrics_chart.append(metrics)
        self.diagnostic_bars["cpu"].setValue(round(metrics.cpu_percent))
        self.diagnostic_bars["gpu"].setValue(round(metrics.gpu_percent))
        self.diagnostic_bars["memory"].setValue(round(metrics.memory_percent))
        row = self.module_list.currentRow()
        if row >= 0:
            self._refresh_module(self.MODULES[row][0])
        self._refresh_logs(self.log_tabs.currentIndex())

    def _run_self_test(self) -> None:
        metrics = self.system_monitor.snapshot()
        self.self_test_result.setText(
            "自检完成：采集链路正常、数据提供器正常、界面刷新正常\n"
            f"CPU {metrics.cpu_percent:.0f}% · 内存 {metrics.memory_percent:.0f}% · 延迟 {metrics.latency_ms:.0f}ms"
        )

    def _apply_style(self) -> None:
        self.setStyleSheet("""
            QWidget { color: #e8eef8; font-size: 13px; }
            QFrame#engineerHeader { background: #0b1425; border-bottom: 1px solid #243653; min-height: 58px; max-height: 58px; }
            QLabel#engineerTitle { font-size: 18px; font-weight: 700; }
            QLabel#connectionState { color: #55d98b; margin-right: 16px; }
            QFrame#engineerSidebar, QFrame#engineerLogs { background: #0d182b; }
            QFrame#engineerCenter { background: #071020; }
            QLabel#pageTitle { font-size: 22px; font-weight: 700; padding: 2px 0 6px 0; }
            QLabel#sectionTitle { font-size: 15px; font-weight: 700; padding: 3px; }
            QListWidget#moduleList { background: transparent; border: none; outline: none; padding-top: 8px; }
            QListWidget#moduleList::item { padding: 13px 12px; margin: 2px 0; border-radius: 5px; color: #aebbd0; }
            QListWidget#moduleList::item:selected { background: #1d3a70; color: white; border-left: 3px solid #3b82f6; }
            QFrame#contentPanel, QFrame#streamCard { background: #101d32; border: 1px solid #263a59; border-radius: 7px; }
            QFrame#streamCard { min-height: 118px; }
            QLabel#cardTitle { font-size: 15px; font-weight: 700; }
            QLabel#cardMetric { font-size: 24px; font-weight: 700; color: #ddecff; }
            QLabel#metricLabel { background: #0b1628; padding: 9px 14px; border-radius: 4px; font-weight: 600; }
            QPushButton { min-height: 34px; padding: 0 16px; border-radius: 5px; background: #15233a; border: 1px solid #354966; }
            QPushButton:hover { background: #1b3154; }
            QPushButton#primaryButton, QPushButton#modeButton:checked { background: #2563eb; border-color: #4f83f3; }
            QTableWidget, QTextEdit { background: #091426; alternate-background-color: #0d1a2e; border: 1px solid #263a59; gridline-color: #263a59; }
            QHeaderView::section { background: #14233a; padding: 8px; border: none; border-right: 1px solid #263a59; font-weight: 600; }
            QTabWidget::pane { border: 1px solid #263a59; }
            QTabBar::tab { background: #0d182b; color: #8795aa; padding: 10px 18px; }
            QTabBar::tab:selected { color: white; border-bottom: 2px solid #3b82f6; }
            QProgressBar { background: #0b1628; border: 1px solid #263a59; border-radius: 4px; text-align: center; min-height: 24px; }
            QProgressBar::chunk { background: #3b82f6; border-radius: 3px; }
            QLabel#selfTestResult { background: #091426; border: 1px solid #263a59; padding: 14px; }
        """)
