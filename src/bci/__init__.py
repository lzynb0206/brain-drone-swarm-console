"""Brain-computer-interface configuration and decoders."""

from src.bci.fbcca_decoder import FBCCADecoder
from src.bci.runtime_decoder import RuntimeIntentDecoder

__all__ = [
    "FBCCADecoder",
    "RuntimeIntentDecoder",
]
