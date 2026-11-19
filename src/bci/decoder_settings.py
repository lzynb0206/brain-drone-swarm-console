"""Validated FBCCA parameters shared by collection and later analysis."""

from dataclasses import asdict, dataclass
import math

from src.bci.config import FBCCA_MIN_MARGIN, FBCCA_MIN_SCORE


@dataclass(frozen=True)
class DecoderSettings:
    harmonics: int = 4
    filter_method: str = "fft"
    min_score: float = FBCCA_MIN_SCORE
    min_margin: float = FBCCA_MIN_MARGIN

    def validate(self):
        if isinstance(self.harmonics, bool) or not isinstance(self.harmonics, int) or not 1 <= self.harmonics <= 6:
            raise ValueError("谐波数量应为 1–6 的整数")
        if self.filter_method not in {"fft", "butter4"}:
            raise ValueError("不支持的 FBCCA 滤波方式")
        if any(not math.isfinite(v) or not 0 <= v <= 3 for v in (self.min_score, self.min_margin)):
            raise ValueError("分数和差值阈值应在 0–3 之间")
        return self

    def to_dict(self):
        return asdict(self.validate())

    @classmethod
    def from_dict(cls, values):
        try:
            return cls(**dict(values)).validate()
        except (TypeError, OverflowError) as exc:
            raise ValueError("实验记录中的解码参数无效") from exc

    def create_decoder(self, channels_1based=tuple(range(1, 9))):
        from src.bci.fbcca_decoder import FBCCADecoder
        return FBCCADecoder(channels_1based=channels_1based, **self.to_dict())
