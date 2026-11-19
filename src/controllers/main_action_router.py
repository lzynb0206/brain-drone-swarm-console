"""Button-action routing for the main dashboard."""

from __future__ import annotations

from src.bci.montage import HYBRID_IDS
from src.widgets.scifi_canvas import RESEARCH_NAV_ITEMS

from PySide6.QtCore import QObject, Slot


class MainActionRouter(QObject):
    """Receive role-page signals and route them to focused controllers."""

    def __init__(self, host: QObject) -> None:
        super().__init__(host)
        self.host = host

    @Slot(str, float, float)
    def activate(self, action: str, x: float = 0, y: float = 0) -> None:
        """Route every painted control to a stateful application action."""
        view = self.host
        if (not view.experiment.external_recording and getattr(view.actions, "montage_id", "") in HYBRID_IDS
                and (action == "ssvep-command-test"
                     or action == "paradigm-start" and view.actions.paradigm_type == "SSVEP")):
            view._notify("当前为 5+3 布局，请在混合范式中启动配对采集；八通道 SSVEP 工具需要先恢复原布局")
            return
        if action == "logout":
            view.deactivate()
            view.logout_requested.emit()
        elif action.startswith("research:"):
            requested = action.split(":", 1)[1]
            next_page = {"实验": "CSV回放"}.get(requested, requested)
            if next_page not in {name for name, _x, _w in RESEARCH_NAV_ITEMS if name != "SSVEP测试"}:
                return
            if view.research_page != next_page:
                reset_scroll = getattr(view.research_widget, "reset_research_scroll", None)
                if callable(reset_scroll):
                    reset_scroll()
            view.research_page = next_page
            view.update()
        elif action.startswith("acquisition-profile:"):
            view.acquisition_profile.select(action.split(":", 1)[1])
        elif action == "decoder-settings":
            view._configure_decoder_settings()
        elif action.startswith("research-choice:"):
            view._show_research_choice_menu(action.split(":", 1)[1], x, y)
        elif action.startswith("collection-section:"):
            section = action.split(":", 1)[1]
            page = view.research_widget
            page.collection_section = "" if page.collection_section == section else section
            view.update()
        elif action == "research-stimulus-settings":
            view._configure_research_stimulus()
        elif action.startswith("analysis-kind:"):
            view.trial_analysis.select_kind(action.split(":", 1)[1])
        elif action.startswith("acquisition-tab:"):
            view.acquisition_tab = action.split(":", 1)[1]
            view.update()
        elif action.startswith("soldier-root:"):
            view.soldier_root = action.split(":", 1)[1]
            view.update()
        elif action.startswith("soldier-page:"):
            view.soldier_root = "单兵面板"
            view.soldier_page = action.split(":", 1)[1]
            view.update()
        elif action.startswith("paradigm-frequency:"):
            view.actions.paradigm_frequency = float(action.rsplit(":", 1)[1])
            view.update()
        elif action.startswith("paradigm-type:"):
            if view.experiment.running:
                view._notify("请先结束当前采集，再切换范式")
                return
            paradigm = action.split(":", 1)[1]
            if paradigm not in view.actions.acquisition_profile.paradigms:
                view._notify("请先选择支持此实验类型的采集模式")
                return
            view.actions.paradigm_type = paradigm
            if view._pipeline_worker is not None:
                mode = ("SSVEP+MI混合" if getattr(view.actions, "montage_id", "") in HYBRID_IDS
                        else view.actions.paradigm_type)
                view._pipeline_worker.set_paradigm_type(mode)
            if view.actions.paradigm_type != "SSVEP":
                view.actions.bci_control_enabled = False
            view._notify(f"脑控范式已切换：{view.actions.paradigm_type}")
        elif action.startswith("collection-scheme:"):
            view.experiment.select_collection_scheme(action.split(":", 1)[1])
        elif action.startswith("collection-recording:"):
            view.experiment.set_recording_mode(action.split(":", 1)[1])
        elif action == "collection-config":
            view.experiment.configure_collection()
        elif action == "collection-subject":
            view.experiment.choose_subject()
        elif action == "collection-subject-new":
            view.experiment.choose_subject(create=True)
        elif action == "collection-start":
            view.experiment.start_collection()
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
                view.experiment.start_hybrid()
        elif action.startswith("montage:"):
            view.experiment.configure_montage(action.split(":", 1)[1])
        elif action == "hybrid-experiment-config":
            view.experiment.configure_hybrid()
        elif action == "hybrid-pilot-start":
            view.experiment.start_hybrid(pilot=True)
        elif action == "hybrid-rest-start":
            view.experiment.start_hybrid(mode="rest")
        elif action == "hybrid-experiment-start":
            view.experiment.start_hybrid()
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
        elif action == "analysis-load-project":
            view.trial_analysis.load_project_data()
        elif action == "analysis-import-directory":
            view.trial_analysis.choose_directory()
        elif action == "analysis-import-files":
            view.trial_analysis.choose_files()
        elif action == "analysis-import-bdf":
            view.trial_analysis.choose_bdf()
        elif action.startswith("analysis-subject:"):
            view.trial_analysis.select_subject(action.split(":", 1)[1])
        elif action == "analysis-subject-rename":
            view.trial_analysis.edit_selected_subject_name()
        elif action == "analysis-subject-page-previous":
            view.trial_analysis.previous_subject_page()
        elif action == "analysis-subject-page-next":
            view.trial_analysis.next_subject_page()
        elif action.startswith("analysis-date:"):
            view.trial_analysis.select_date(action.split(":", 1)[1])
        elif action == "analysis-date-page-previous":
            view.trial_analysis.previous_date_page()
        elif action == "analysis-date-page-next":
            view.trial_analysis.next_date_page()
        elif action.startswith("analysis-label:"):
            view.trial_analysis.select_label(action.split(":", 1)[1])
        elif action.startswith("analysis-experiment:"):
            view.trial_analysis.select_experiment(action.split(":", 1)[1])
        elif action == "analysis-experiment-page-previous":
            view.trial_analysis.previous_experiment_page()
        elif action == "analysis-experiment-page-next":
            view.trial_analysis.next_experiment_page()
        elif action.startswith("analysis-trial:"):
            view.trial_analysis.select_record(int(action.rsplit(":", 1)[1]))
        elif action == "analysis-page-previous":
            view.trial_analysis.previous_page()
        elif action == "analysis-page-next":
            view.trial_analysis.next_page()
        elif action == "analysis-window":
            view.trial_analysis.configure_window()
        elif action == "analysis-generate":
            view.trial_analysis.generate_report()
        elif action.startswith("data-tab:"):
            view.actions.data_tab = action.split(":", 1)[1]
            if view.actions.data_tab == "数据集生成":
                view._notify(view.actions.generate_dataset())
            else:
                view.update()
        elif action.startswith("data-session:"):
            view.actions.selected_session = int(action.rsplit(":", 1)[1])
            view.update()
        elif action.startswith("log-filter:"):
            view.actions.log_filter = action.split(":", 1)[1]
            view.update()
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


__all__ = ["MainActionRouter"]
