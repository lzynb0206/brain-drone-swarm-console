"""Named wiring contracts. Never reinterpret historical recordings by position."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Montage:
    id: str
    names: tuple[str, ...]
    ssvep: tuple[str, ...]
    mi: tuple[str, ...] = ()

    def indices(self, names, modality):
        actual = tuple(names)
        requested = self.ssvep if modality == "SSVEP" else self.mi if modality == "MI" else ()
        if not requested or len(set(actual)) != len(actual) or any(n not in actual for n in requested):
            raise ValueError(f"{modality} channel names missing or ambiguous: {actual}")
        return tuple(actual.index(n) for n in requested)


LEGACY = Montage("posterior8_v1", ("O1", "Oz", "O2", "PO3", "POz", "PO4", "P3", "P4"),
                 ("O1", "Oz", "O2", "PO3", "POz", "PO4", "P3", "P4"))
HYBRID_V1 = Montage("hybrid5_3_v1", ("O1", "Oz", "O2", "PO3", "PO4", "C3", "Cz", "C4"),
                 ("O1", "Oz", "O2", "PO3", "PO4"), ("C3", "Cz", "C4"))
HYBRID = Montage("hybrid5_3_p3p4_v2", ("O1", "Oz", "O2", "P3", "P4", "C3", "Cz", "C4"),
                 ("O1", "Oz", "O2", "P3", "P4"), ("C3", "Cz", "C4"))
MONTAGES = {m.id: m for m in (LEGACY, HYBRID_V1, HYBRID)}
HYBRID_IDS = frozenset((HYBRID_V1.id, HYBRID.id))


def research_decoder(montage_id, settings=None):
    """Versioned, non-actuating decoder used only when saving research trials."""
    from src.bci.bn_fbcca import BNFBCCADecoder
    from src.bci.decoder_settings import DecoderSettings
    if montage_id not in HYBRID_IDS:
        raise ValueError("Unknown hybrid montage")
    settings = settings or DecoderSettings(filter_method="butter4" if montage_id == HYBRID.id else "fft")
    return BNFBCCADecoder("b", channels_1based=(1, 2, 3, 4, 5), **settings.to_dict())
