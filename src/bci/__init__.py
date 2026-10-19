"""Brain-computer-interface configuration and decoders."""

from src.bci.fbcca_decoder import FBCCADecoder
from src.bci.ocular_fbcca_decoder import OcularArtifactFBCCADecoder
from src.bci.runtime_decoder import RuntimeIntentDecoder
from src.bci.temporal_gate import ConsecutiveWindowGate

__all__ = [
    "ConsecutiveWindowGate", "FBCCADecoder", "OcularArtifactFBCCADecoder",
    "RuntimeIntentDecoder",
]
