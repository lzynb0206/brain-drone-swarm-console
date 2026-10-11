"""Capture deterministic UI previews for quick visual QA."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from src.app import CommandConsoleWindow
from src.domain import Role, User
from src.main import _load_stylesheet, _set_ui_font


def main() -> int:
    output_dir = Path("artifacts")
    output_dir.mkdir(exist_ok=True)

    app = QApplication(sys.argv)
    _set_ui_font(app)
    _load_stylesheet(app)

    window = CommandConsoleWindow()
    window.resize(1280, 720)
    window.show()
    app.processEvents()
    window.grab().save(str(output_dir / "login-preview.png"))

    window._show_main_view(User("researcher", "科研演示账号", Role.RESEARCHER))
    app.processEvents()
    window.grab().save(str(output_dir / "research-acquisition-setup.png"))
    setup_variants = (
        ("cyton-wifi", "CYTON", "WiFi", "Native BLE"),
        ("ganglion-native", "GANGLION", "", "Native BLE"),
        ("ganglion-wifi", "GANGLION", "", "WiFi"),
        ("playback", "PLAYBACK", "", "Native BLE"),
        ("synthetic", "SYNTHETIC", "", "Native BLE"),
        ("streaming", "STREAMING", "", "Native BLE"),
    )
    for name, source, cyton_transport, ganglion_transport in setup_variants:
        window.main_view._openbci_source = source
        window.main_view._openbci_transport = cyton_transport
        window.main_view._openbci_ganglion_transport = ganglion_transport
        window.main_view._openbci_channel_count = 4 if source == "GANGLION" else 8
        window.main_view._openbci_sample_rate = 200 if source == "GANGLION" else 250
        window.main_view._openbci_streamer_mode = "Network" if source == "SYNTHETIC" else "File"
        window.main_view.update()
        app.processEvents()
        window.grab().save(str(output_dir / f"research-acquisition-setup-{name}.png"))
    window.main_view._openbci_source = "CYTON"
    window.main_view._openbci_transport = "Serial"
    window.main_view._openbci_channel_count = 8
    window.main_view._openbci_sample_rate = 250
    window.main_view._openbci_streamer_mode = "File"
    window.main_view.acquisition_stage = "runtime"
    window.main_view.acquisition_tab = "Dashboard"
    window.main_view.update()
    app.processEvents()
    window.grab().save(str(output_dir / "research-acquisition.png"))
    for acquisition_tab in ("LSL", "Packet Loss", "Hardware"):
        window.main_view.acquisition_tab = acquisition_tab
        window.main_view.update()
        app.processEvents()
        window.grab().save(str(output_dir / f"research-acquisition-{acquisition_tab.replace(' ', '-').lower()}.png"))
    window.main_view.research_page = "采集器"
    window.main_view.update()
    app.processEvents()
    window.grab().save(str(output_dir / "research-integrated-collector.png"))
    for page in ("实验", "范式", "算法", "控制", "映射", "数据"):
        window.main_view.research_page = page
        window.main_view.update()
        app.processEvents()
        window.grab().save(str(output_dir / f"research-{page}.png"))

    for username, role in (("admin", Role.ADMIN), ("engineer", Role.ENGINEER), ("soldier", Role.SOLDIER)):
        window._show_main_view(User(username, username, role))
        app.processEvents()
        window.grab().save(str(output_dir / f"{username}-main.png"))
    window.main_view.engineer_page = "深度诊断"
    window._show_main_view(User("engineer", "engineer", Role.ENGINEER))
    if window.main_view.engineer_dashboard is not None:
        window.main_view.engineer_dashboard._set_mode(1)
    app.processEvents()
    window.grab().save(str(output_dir / "engineer-diagnostics.png"))
    window._show_main_view(User("soldier", "soldier", Role.SOLDIER))
    window.main_view.soldier_root = "单兵面板"
    window.main_view.update()
    app.processEvents()
    window.grab().save(str(output_dir / "soldier-brain.png"))
    window.main_view.soldier_page = "状态监控"
    window.main_view.update()
    app.processEvents()
    window.grab().save(str(output_dir / "soldier-status.png"))
    window.main_view.deactivate()
    app.processEvents()
    window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
