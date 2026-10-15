"""Button-action routing for the main dashboard."""

from __future__ import annotations

from PySide6.QtCore import QObject, Slot
from PySide6.QtWidgets import QInputDialog

from src.bci.config import MI_TRUE_LABELS, TRUE_LABELS


class MainActionRouter(QObject):
    """Receive role-page signals and route them to focused controllers."""

    def __init__(self, host: QObject) -> None:
        super().__init__(host)
        self.host = host

    @Slot(str, float, float)
    def activate(self, action: str, x: float = 0, y: float = 0) -> None:
        """Route every painted control to a stateful application action."""
        view = self.host
        if action == "logout":
            view.deactivate()
            view.logout_requested.emit()
        elif action.startswith("research:"):
            view.research_page = action.split(":", 1)[1]
            view.update()
        elif action.startswith("acquisition-tab:"):
            view.acquisition_tab = action.split(":", 1)[1]
            view.update()
        elif action.startswith("engineer:"):
            view.engineer_page = action.split(":", 1)[1]
            view.update()
        elif action.startswith("soldier-root:"):
            view.soldier_root = action.split(":", 1)[1]
            view.update()
        elif action.startswith("soldier-page:"):
            view.soldier_page = action.split(":", 1)[1]
            view.update()
        elif action.startswith("paradigm-frequency:"):
            view.actions.paradigm_frequency = float(action.rsplit(":", 1)[1])
            view.update()
        elif action.startswith("paradigm-type:"):
            view.actions.paradigm_type = action.split(":", 1)[1]
            if view._pipeline_worker is not None:
                view._pipeline_worker.set_paradigm_type(view.actions.paradigm_type)
            if view.actions.paradigm_type != "SSVEP":
                view.actions.bci_control_enabled = False
            view._notify(f"脑控范式已切换：{view.actions.paradigm_type}")
        elif action == "paradigm-size":
            view.actions.stimulus_size = max(0, min(100, round((x - 739) / 299 * 100)))
            view.update()
        elif action == "paradigm-duty":
            view.actions.duty_cycle = max(0, min(100, round((x - 739) / 299 * 100)))
            view.update()
        elif action == "paradigm-apply":
            view._notify(view.actions.apply_paradigm())
        elif action == "paradigm-reset":
            view._notify(view.actions.reset_paradigm())
        elif action == "paradigm-start":
            if view.actions.paradigm_type == "SSVEP":
                view._open_ssvep_stimulus()
            else:
                view._notify("MI 采集模式已启用：请用右上角“添加标签”记录下一段 4 秒运动想象 EEG")
        elif action == "ssvep-command-test":
            view._open_ssvep_test()
        elif action == "automatic-experiment-config":
            view._configure_automatic_ssvep_experiment()
        elif action == "automatic-experiment-start":
            view.experiment.start_automatic()
        elif action == "soldier-ssvep-control":
            view._open_ssvep_control()
        elif action.startswith("collector-source:"):
            view.acquisition.select_source(action.split(":", 1)[1])
        elif action == "collector-refresh":
            view.acquisition.refresh_source()
        elif action == "collector-start":
            view.acquisition.start_integrated_collection()
        elif action == "collector-stop":
            view.acquisition.stop_integrated_collection()
        elif action == "collector-pause-waveform":
            view.acquisition.toggle_waveform()
        elif action == "collector-open-directory":
            view.acquisition.open_data_directory()
        elif action == "lsl-outlet-toggle":
            view.acquisition.toggle_outlet()
        elif action == "lsl-collector-refresh":
            view._discover_lsl_collector_streams()
        elif action == "lsl-collector-start":
            view._start_lsl_collection()
        elif action == "lsl-collector-stop":
            view._stop_lsl_collection()
        elif action == "lsl-collector-pause":
            view._toggle_lsl_collector_waveform()
        elif action == "lsl-collector-open-directory":
            view._open_lsl_collector_directory()
        elif action == "openbci-start-session":
            view.acquisition.prepare_session()
        elif action == "openbci-data-stream-toggle":
            view.acquisition.toggle_stream()
        elif action == "openbci-control-panel":
            view.acquisition.return_to_setup()
        elif action == "openbci-serial-autoconnect":
            view.acquisition.discover_direct_devices()
        elif action == "openbci-guide":
            view._show_openbci_guide()
        elif action == "continuous-record-toggle":
            view.acquisition.toggle_raw_recording()
        elif action == "csv-import":
            view.csv_replay.choose_file()
        elif action == "csv-playback-toggle":
            view.csv_replay.toggle()
        elif action == "csv-rewind":
            view.csv_replay.rewind()
        elif action.startswith("csv-speed:"):
            view.csv_replay.set_speed(float(action.rsplit(":", 1)[1]))
        elif action == "csv-seek":
            view.csv_replay.seek((x - 384) / 650)
        elif action.startswith("algorithm-select:"):
            view._notify(view.actions.select_algorithm(action.split(":", 1)[1]))
        elif action.startswith("algorithm-mode:"):
            view.actions.algorithm_mode = action.split(":", 1)[1]
            view._notify(f"算法模式已切换：{view.actions.algorithm_mode}")
        elif action == "algorithm-load":
            view._notify(view._activate_selected_decoder())
        elif action == "algorithm-unload":
            view._notify(view._unload_selected_decoder())
        elif action == "algorithm-default":
            view._notify(view._set_default_selected_decoder())
        elif action == "algorithm-benchmark":
            view._notify(view.actions.benchmark_algorithm())
        elif action.startswith("controller-select:"):
            view.actions.controller = action.split(":", 1)[1]
            view._notify(f"控制器已选择：{view.actions.controller}")
        elif action.startswith("controller-slider:"):
            key = action.rsplit(":", 1)[1]
            view.actions.controller_values[key] = max(0, min(100, round((x - 203) / 707 * 100)))
            view.update()
        elif action == "controller-apply":
            view._notify(view.actions.apply_controller())
        elif action == "controller-save":
            view._notify(view.actions.save_controller_preset())
        elif action == "controller-reset":
            view._notify(view.actions.reset_controller())
        elif action == "mapping-add":
            view._add_mapping_dialog()
        elif action.startswith("mapping-edit:"):
            view._edit_mapping_dialog(int(action.rsplit(":", 1)[1]))
        elif action.startswith("mapping-delete:"):
            view._notify(view.actions.delete_mapping(int(action.rsplit(":", 1)[1])))
        elif action.startswith("data-tab:"):
            view.actions.data_tab = action.split(":", 1)[1]
            if view.actions.data_tab == "数据集生成":
                view._notify(view.actions.generate_dataset())
            else:
                view.update()
        elif action.startswith("data-session:"):
            view.actions.selected_session = int(action.rsplit(":", 1)[1])
            view.update()
        elif action == "marker-add":
            if view._pipeline_worker is None or not view._pipeline_worker.isRunning():
                view._notify("EEG 采集未运行，无法标注试次")
                return
            labels = MI_TRUE_LABELS if view.actions.paradigm_type != "SSVEP" else TRUE_LABELS
            label, ok = QInputDialog.getItem(
                self, "标注下一次 EEG 试次", "真实标签", list(labels), 0, False,
            )
            if ok:
                view._pipeline_worker.capture_next_trial(label)
                view._notify(view.actions.add_marker(label))
        elif action.startswith("log-filter:"):
            view.actions.log_filter = action.split(":", 1)[1]
            view.update()
        elif action == "self-test":
            view._notify(view.actions.run_self_test())
        elif action == "admin-clean-cache":
            view._notify(view.actions.clean_cache())
        elif action == "admin-backup":
            view._notify(view.actions.create_backup())
        elif action == "admin-restore":
            view._notify(view.actions.restore_backup())
        elif action.startswith("admin-device:"):
            view._show_device_dialog(action.split(":", 1)[1])
        elif action.startswith("admin-user:"):
            view._edit_user_dialog(action.split(":", 1)[1])
        elif action.startswith("swarm-command:"):
            view._notify(view.actions.send_swarm_command(action.split(":", 1)[1]))
        elif action == "swarm-configure-uris":
            view._configure_swarm_uris_dialog()
        elif action == "swarm-height-decrease":
            view._notify(view.actions.adjust_swarm_takeoff_height(-0.05))
        elif action == "swarm-height-increase":
            view._notify(view.actions.adjust_swarm_takeoff_height(0.05))
        elif action == "swarm-height-custom":
            view._show_takeoff_height_dialog()
        elif action == "swarm-move-distance-custom":
            view._show_move_distance_dialog()
        elif action == "swarm-single-control":
            view._single_uav_control_dialog()
        elif action == "mission-simulate":
            view._run_simulated_structured_mission()
        elif action == "swarm-lighthouse-hold":
            view._notify(view.actions.send_swarm_command("lighthouse_hold"))
        elif action == "swarm-form-line":
            view._notify(view.actions.send_swarm_command(
                "form_line", parameters={"spacing_m": 0.4},
            ))
        elif action == "swarm-form-triangle":
            view._notify(view.actions.send_swarm_command(
                "form_triangle", parameters={"spacing_m": 0.4},
            ))
        elif action == "swarm-connect":
            view._notify(view.actions.connect_swarm())
        elif action == "swarm-disconnect":
            view._center_joysticks(send=True)
            view._notify(view.actions.disconnect_swarm())
        elif action == "swarm-arm":
            view._notify(view.actions.arm_swarm())
        elif action == "swarm-disarm":
            view._center_joysticks(send=True)
            view._notify(view.actions.disarm_swarm())
        elif action == "swarm-stop-motion":
            view._notify(view.actions.stop_swarm_motion())
        elif action == "swarm-motor-stop":
            view._center_joysticks(send=True)
            view._request_motor_stop()
        elif action == "bci-control-toggle":
            view.soldier_bci.toggle()
        elif action == "swarm-emergency-stop":
            view._center_joysticks(send=False)
            view._handle_ssvep_emergency()
        elif action == "swarm-emergency-reset":
            view._notify(view.actions.reset_emergency_after_reboot())
        elif action == "soldier-export":
            path = view.actions.export_soldier_report()
            view._notify(f"报告已导出：{path}")
        elif action == "workspace-export":
            path = view.actions.export_workspace_config()
            view._notify(f"配置已导出：{path}")
        elif action == "workspace-summary":
            view._notify(view.actions.workspace_summary())
        elif action == "workspace-layout":
            view._notify(view.actions.toggle_layout())
        elif action == "workspace-help":
            view._notify("操作提示：选择模块后点击控件；所有结果会在底部提示")


__all__ = ["MainActionRouter"]
