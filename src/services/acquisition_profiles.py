"""Collection modes select their recording path, wiring and fixed decoder."""

from dataclasses import dataclass

from src.bci.decoder_settings import DecoderSettings
from src.bci.montage import HYBRID, LEGACY


@dataclass(frozen=True)
class AcquisitionProfile:
    id: str
    title: str
    description: str
    recording_mode: str
    montage_id: str
    default_paradigm: str
    paradigms: tuple[str, ...]
    algorithm: str
    filter_method: str

    def default_settings(self):
        return DecoderSettings(filter_method=self.filter_method)


ACQUISITION_PROFILES = (
    AcquisitionProfile("ssvep8", "纯 SSVEP · 8 通道", "枕顶 8 通道 · FBCCA", "local_eeg",
                       LEGACY.id, "SSVEP", ("SSVEP",), "FBCCA", "fft"),
    AcquisitionProfile("hybrid8", "SSVEP＋MI · 5+3", "5 路 SSVEP + 3 路 MI", "local_eeg",
                       HYBRID.id, "SSVEP+MI混合", ("SSVEP+MI混合", "MI"), "BN-FBCCA-B", "butter4"),
    AcquisitionProfile("external32", "32 通道 · 外部 BDF", "本机呈现 · BDF 导入分析", "external_bdf",
                       LEGACY.id, "SSVEP+MI混合", ("SSVEP", "SSVEP+MI混合", "MI"), "FBCCA", "butter4"),
)
PROFILES_BY_ID = {profile.id: profile for profile in ACQUISITION_PROFILES}
