"""Apply collection mode and decoder parameters before a session starts."""

from PySide6.QtCore import QObject

from src.bci.montage import MONTAGES
from src.services.acquisition_profiles import PROFILES_BY_ID


class AcquisitionProfileController(QObject):
    def __init__(self, host):
        super().__init__(host)
        self.host = host

    @property
    def locked(self):
        view = self.host
        worker = view._pipeline_worker
        stimulus = view._stimulus_window
        return bool(view.experiment.running or (worker is not None and worker.isRunning())
                    or (stimulus is not None and (stimulus.running or stimulus.stimuli_active))
                    or view.actions.bci.session_active)

    def _can_edit(self):
        if self.locked:
            self.host._notify("请先停止数据流和当前实验，再修改采集模式或解码参数")
            return False
        return True

    def select(self, profile_id):
        if not self._can_edit():
            return False
        profile = PROFILES_BY_ID[profile_id]
        view = self.host
        montage = MONTAGES[profile.montage_id]
        configure = getattr(view.acquisition.stream, "configure_montage", None)
        if profile.recording_mode == "local_eeg" and callable(configure):
            try:
                configure(montage.names)
            except (RuntimeError, ValueError) as exc:
                view._notify(str(exc))
                return False
        previous = view.actions.acquisition_profile
        view.actions.recording_mode = profile.recording_mode
        view.actions.montage_id = montage.id
        if previous.id != profile.id or view.actions.paradigm_type not in profile.paradigms:
            view.actions.paradigm_type = profile.default_paradigm
        view.actions.bci.disable()
        view._eeg_channel_names = montage.names
        view._eeg_original_channel_names = ()
        view.acquisition_stage = "setup"
        self._apply_decoder()
        view._notify(f"已选择 {profile.title} · 解码流程已自动配置")
        return True

    def configure_decoder(self, settings):
        if not self._can_edit():
            return False
        settings.validate()
        view = self.host
        view.actions.decoder_settings_by_profile[view.actions.acquisition_profile.id] = settings
        self._apply_decoder()
        view._notify("FBCCA 参数已应用")
        return True

    def _apply_decoder(self):
        view = self.host
        view.intent_decoder = view.actions.decoder_settings.create_decoder()
        view._latest_recognition = None
        # Rebuild only while stopped; workers and trial recorders must share
        # the new instance rather than retaining stale parameters.
        view._build_eeg_worker(replace=True)
        view.update()
