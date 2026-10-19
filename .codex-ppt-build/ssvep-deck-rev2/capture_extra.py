"""Render current project pages and SSVEP windows for presentation evidence."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from src.app import CommandConsoleWindow
from src.domain import Role, User
from src.main import _load_stylesheet, _set_ui_font
from src.views.single_target_calibration_window import SingleTargetCalibrationWindow
from src.views.ssvep_stimulus_window import SSVEPStimulusWindow


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".codex-ppt-build" / "ssvep-deck-rev2" / "screens"


def grab(widget, name: str, app: QApplication) -> None:
    widget.resize(1280, 720)
    widget.show()
    app.processEvents()
    widget.grab().save(str(OUT / name))


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv)
    _set_ui_font(app)
    _load_stylesheet(app)

    main_window = CommandConsoleWindow()
    main_window.resize(1280, 720)
    main_window._show_main_view(User("researcher", "科研演示账号", Role.RESEARCHER))
    main_window.main_view.research_page = "单目标校准"
    main_window.main_view.update()
    grab(main_window, "research-single-target.png", app)

    for page, name in (
        ("实验", "research-experiment.png"),
        ("范式", "research-paradigm.png"),
        ("算法", "research-algorithm.png"),
        ("映射", "research-mapping.png"),
    ):
        main_window.main_view.research_page = page
        main_window.main_view.update()
        app.processEvents()
        main_window.grab().save(str(OUT / name))

    test = SSVEPStimulusWindow(mode="test")
    test.set_idle_calibration_seconds(0.0)
    test.set_control_profile(move_distance_m=0.05, command_cooldown_seconds=2.5)
    test.begin_trial()
    grab(test, "ssvep-command-test.png", app)

    drive = SSVEPStimulusWindow(mode="control")
    drive.set_idle_calibration_seconds(0.0)
    drive.set_control_profile(move_distance_m=0.05, command_cooldown_seconds=2.5)
    drive.begin_trial()
    grab(drive, "ssvep-online-drive.png", app)

    isolated = SingleTargetCalibrationWindow(mode="isolated")
    isolated.current_index = 0
    isolated._enter("stimulus")
    grab(isolated, "single-target-isolated.png", app)

    guided = SingleTargetCalibrationWindow(mode="guided")
    guided.current_index = 0
    guided._enter("stimulus")
    grab(guided, "single-target-guided.png", app)

    for widget in (guided, isolated, drive, test, main_window):
        widget.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
