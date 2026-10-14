"""Button-action routing for the main dashboard."""

from __future__ import annotations

from PySide6.QtWidgets import QInputDialog

from src.bci.config import MI_TRUE_LABELS, TRUE_LABELS


class MainActionRouterMixin:
    """Route painted control identifiers to application actions."""
    def _activate(self, action: str, x: float = 0, y: float = 0) -> None:
        """Route every painted control to a stateful application action."""
        if action == "logout":
            self.deactivate()
            self.logout_requested.emit()
        elif action.startswith("research:"):
            self.research_page = action.split(":", 1)[1]
            self.update()
        elif action.startswith("acquisition-tab:"):
            self.acquisition_tab = action.split(":", 1)[1]
            self.update()
        elif action.startswith("engineer:"):
            self.engineer_page = action.split(":", 1)[1]
            self.update()
        elif action.startswith("soldier-root:"):
            self.soldier_root = action.split(":", 1)[1]
            self.update()
        elif action.startswith("soldier-page:"):
            self.soldier_page = action.split(":", 1)[1]
            self.update()
        elif action.startswith("paradigm-frequency:"):
            self.actions.paradigm_frequency = float(action.rsplit(":", 1)[1])
            self.update()
        elif action.startswith("paradigm-type:"):
            self.actions.paradigm_type = action.split(":", 1)[1]
            if self._pipeline_worker is not None:
                self._pipeline_worker.set_paradigm_type(self.actions.paradigm_type)
            if self.actions.paradigm_type != "SSVEP":
                self.actions.bci_control_enabled = False
            self._notify(f"脑控范式已切换：{self.actions.paradigm_type}")
        elif action == "paradigm-size":
            self.actions.stimulus_size = max(0, min(100, round((x - 739) / 299 * 100)))
            self.update()
        elif action == "paradigm-duty":
            self.actions.duty_cycle = max(0, min(100, round((x - 739) / 299 * 100)))
            self.update()
        elif action == "paradigm-apply":
            self._notify(self.actions.apply_paradigm())
        elif action == "paradigm-reset":
            self._notify(self.actions.reset_paradigm())
        elif action == "paradigm-start":
            if self.actions.paradigm_type == "SSVEP":
                self._open_ssvep_stimulus()
            else:
                self._notify("MI 采集模式已启用：请用右上角“添加标签”记录下一段 4 秒运动想象 EEG")
        elif action == "soldier-ssvep-control":
            self._open_ssvep_control()
        elif action == "lsl-refresh":
            self._discover_lsl_streams()
        elif action.startswith("collector-source:"):
            self._select_collector_source(action.split(":", 1)[1])
        elif action == "collector-refresh":
            self._refresh_collector_source()
        elif action == "collector-start":
            self._start_integrated_collection()
        elif action == "collector-stop":
            self._stop_integrated_collection()
        elif action == "collector-pause-waveform":
            self._toggle_collector_waveform()
        elif action == "collector-open-directory":
            self._open_collector_data_directory()
        elif action == "lsl-outlet-toggle":
            self._toggle_lsl_outlet()
        elif action == "lsl-collector-refresh":
            self._discover_lsl_collector_streams()
        elif action == "lsl-collector-start":
            self._start_lsl_collection()
        elif action == "lsl-collector-stop":
            self._stop_lsl_collection()
        elif action == "lsl-collector-pause":
            self._toggle_lsl_collector_waveform()
        elif action == "lsl-collector-open-directory":
            self._open_lsl_collector_directory()
        elif action == "eeg-connect":
            self._prepare_openbci_session()
        elif action == "eeg-disconnect":
            self._disconnect_eeg_pipeline()
            self._notify("EEG 数据源已断开")
        elif action == "openbci-start-session":
            self._prepare_openbci_session()
        elif action == "openbci-data-stream-toggle":
            self._toggle_openbci_data_stream()
        elif action == "openbci-control-panel":
            self._return_openbci_control_panel()
        elif action.startswith("openbci-source:"):
            self._openbci_source = action.split(":", 1)[1]
            self._openbci_transport = "Serial" if self._openbci_source == "CYTON" else ""
            self._openbci_ganglion_transport = "Native BLE"
            self._openbci_channel_count = 4 if self._openbci_source == "GANGLION" else 8
            self._openbci_sample_rate = 200 if self._openbci_source == "GANGLION" else 250
            self._openbci_streamer_mode = (
                "Network" if self._openbci_source == "SYNTHETIC" else "File"
            )
            self._openbci_status_message = f"Data source selected: {self._openbci_source}"
            self.update()
        elif action.startswith("openbci-transport:"):
            self._openbci_transport = action.split(":", 1)[1]
            self._openbci_status_message = f"Transfer protocol selected: {self._openbci_transport}"
            self.update()
        elif action.startswith("openbci-ganglion-transport:"):
            self._openbci_ganglion_transport = action.split(":", 1)[1]
            self._openbci_status_message = (
                f"Transfer protocol selected: {self._openbci_ganglion_transport}"
            )
            self.update()
        elif action.startswith("openbci-wifi-mode:"):
            self._openbci_wifi_mode = action.split(":", 1)[1]
            self.update()
        elif action.startswith("openbci-streamer-mode:"):
            self._openbci_streamer_mode = action.split(":", 1)[1]
            self.update()
        elif action.startswith("openbci-session-format:"):
            self._openbci_session_format = action.split(":", 1)[1]
            self.update()
        elif action.startswith("openbci-board-type:"):
            self._openbci_board_type = action.split(":", 1)[1]
            self.update()
        elif action.startswith("openbci-channel-count:"):
            self._openbci_channel_count = int(action.rsplit(":", 1)[1])
            self.update()
        elif action.startswith("openbci-sample-rate:"):
            self._openbci_sample_rate = int(action.rsplit(":", 1)[1])
            self.update()
        elif action == "openbci-playback-select":
            self._choose_openbci_playback_file()
        elif action == "openbci-generate-session":
            self._generate_openbci_session_name()
        elif action == "openbci-wifi-search":
            self._openbci_status_message = "No Wifi Shield Found"
            self._notify("尚未接入 BrainFlow WiFi Shield 搜索后端；未伪造设备结果")
            self.update()
        elif action == "openbci-ble-search":
            self._openbci_status_message = "No Ganglion BLE device found"
            self._notify("尚未接入 Ganglion BLE 搜索后端；未伪造设备结果")
            self.update()
        elif action == "openbci-serial-autoconnect":
            self._discover_direct_devices()
        elif action == "openbci-guide":
            self._show_openbci_guide()
        elif action == "continuous-record-toggle":
            self._toggle_raw_csv_recording()
        elif action == "csv-import":
            self._choose_csv_replay()
        elif action == "csv-playback-toggle":
            self._toggle_csv_playback()
        elif action == "csv-rewind":
            self._rewind_csv_playback()
        elif action.startswith("csv-speed:"):
            self._set_csv_playback_speed(float(action.rsplit(":", 1)[1]))
        elif action == "csv-seek":
            self._seek_csv_playback((x - 384) / 650)
        elif action.startswith("algorithm-select:"):
            self._notify(self.actions.select_algorithm(action.split(":", 1)[1]))
        elif action.startswith("algorithm-mode:"):
            self.actions.algorithm_mode = action.split(":", 1)[1]
            self._notify(f"算法模式已切换：{self.actions.algorithm_mode}")
        elif action == "algorithm-load":
            self._notify(self._activate_selected_decoder())
        elif action == "algorithm-unload":
            self._notify(self._unload_selected_decoder())
        elif action == "algorithm-default":
            self._notify(self._set_default_selected_decoder())
        elif action == "algorithm-benchmark":
            self._notify(self.actions.benchmark_algorithm())
        elif action.startswith("controller-select:"):
            self.actions.controller = action.split(":", 1)[1]
            self._notify(f"控制器已选择：{self.actions.controller}")
        elif action.startswith("controller-slider:"):
            key = action.rsplit(":", 1)[1]
            self.actions.controller_values[key] = max(0, min(100, round((x - 203) / 707 * 100)))
            self.update()
        elif action == "controller-apply":
            self._notify(self.actions.apply_controller())
        elif action == "controller-save":
            self._notify(self.actions.save_controller_preset())
        elif action == "controller-reset":
            self._notify(self.actions.reset_controller())
        elif action == "mapping-add":
            self._add_mapping_dialog()
        elif action.startswith("mapping-edit:"):
            self._edit_mapping_dialog(int(action.rsplit(":", 1)[1]))
        elif action.startswith("mapping-delete:"):
            self._notify(self.actions.delete_mapping(int(action.rsplit(":", 1)[1])))
        elif action.startswith("data-tab:"):
            self.actions.data_tab = action.split(":", 1)[1]
            if self.actions.data_tab == "数据集生成":
                self._notify(self.actions.generate_dataset())
            else:
                self.update()
        elif action.startswith("data-session:"):
            self.actions.selected_session = int(action.rsplit(":", 1)[1])
            self.update()
        elif action == "marker-add":
            if self._pipeline_worker is None or not self._pipeline_worker.isRunning():
                self._notify("EEG 采集未运行，无法标注试次")
                return
            labels = MI_TRUE_LABELS if self.actions.paradigm_type != "SSVEP" else TRUE_LABELS
            label, ok = QInputDialog.getItem(
                self, "标注下一次 EEG 试次", "真实标签", list(labels), 0, False,
            )
            if ok:
                self._pipeline_worker.capture_next_trial(label)
                self._notify(self.actions.add_marker(label))
        elif action.startswith("log-filter:"):
            self.actions.log_filter = action.split(":", 1)[1]
            self.update()
        elif action == "self-test":
            self._notify(self.actions.run_self_test())
        elif action == "admin-clean-cache":
            self._notify(self.actions.clean_cache())
        elif action == "admin-backup":
            self._notify(self.actions.create_backup())
        elif action == "admin-restore":
            self._notify(self.actions.restore_backup())
        elif action.startswith("admin-device:"):
            self._show_device_dialog(action.split(":", 1)[1])
        elif action.startswith("admin-user:"):
            self._edit_user_dialog(action.split(":", 1)[1])
        elif action.startswith("swarm-command:"):
            self._notify(self.actions.send_swarm_command(action.split(":", 1)[1]))
        elif action == "swarm-configure-uris":
            self._configure_swarm_uris_dialog()
        elif action == "swarm-height-decrease":
            self._notify(self.actions.adjust_swarm_takeoff_height(-0.05))
        elif action == "swarm-height-increase":
            self._notify(self.actions.adjust_swarm_takeoff_height(0.05))
        elif action == "swarm-height-custom":
            self._show_takeoff_height_dialog()
        elif action == "swarm-single-control":
            self._single_uav_control_dialog()
        elif action == "mission-simulate":
            self._run_simulated_structured_mission()
        elif action == "swarm-lighthouse-hold":
            self._notify(self.actions.send_swarm_command("lighthouse_hold"))
        elif action == "swarm-form-line":
            self._notify(self.actions.send_swarm_command(
                "form_line", parameters={"spacing_m": 0.4},
            ))
        elif action == "swarm-form-triangle":
            self._notify(self.actions.send_swarm_command(
                "form_triangle", parameters={"spacing_m": 0.4},
            ))
        elif action == "swarm-connect":
            self._notify(self.actions.connect_swarm())
        elif action == "swarm-disconnect":
            self._center_joysticks(send=True)
            self._notify(self.actions.disconnect_swarm())
        elif action == "swarm-arm":
            self._notify(self.actions.arm_swarm())
        elif action == "swarm-disarm":
            self._center_joysticks(send=True)
            self._notify(self.actions.disarm_swarm())
        elif action == "swarm-stop-motion":
            self._notify(self.actions.stop_swarm_motion())
        elif action == "swarm-motor-stop":
            self._center_joysticks(send=True)
            self._request_motor_stop()
        elif action == "bci-control-toggle":
            self._toggle_soldier_bci_chain()
        elif action == "swarm-emergency-stop":
            self._center_joysticks(send=False)
            self._handle_ssvep_emergency()
        elif action == "swarm-emergency-reset":
            self._notify(self.actions.reset_emergency_after_reboot())
        elif action == "soldier-export":
            path = self.actions.export_soldier_report()
            self._notify(f"报告已导出：{path}")
        elif action == "workspace-export":
            path = self.actions.export_workspace_config()
            self._notify(f"配置已导出：{path}")
        elif action == "workspace-summary":
            self._notify(self.actions.workspace_summary())
        elif action == "workspace-layout":
            self._notify(self.actions.toggle_layout())
        elif action == "workspace-help":
            self._notify("操作提示：选择模块后点击控件；所有结果会在底部提示")
