"""
tts/vits_engine.py — Option A: Server-Side TTS (KhanomTan)
===========================================================
Stub — TTS integration pending.
Will use KhanomTan VITS model from RobotAI/vachanatts when wired up.

Model:   MMS-TTS-THAI-MALEV1  (VitsModel + VitsTokenizer, 22050 Hz)
Source:  ../RobotAI/vachanatts/models/MMS-TTS-THAI-MALEV1
"""

import logging

logger = logging.getLogger(__name__)


class VITSEngine:
    """Stub — returns empty bytes until KhanomTan TTS is wired up."""

    def __init__(self, model_dir: str = "", model_name: str = "MMS-TTS-THAI-MALEV1",
                 speaking_rate: float = 1.0):
        self.model_dir = model_dir
        self.model_name = model_name
        self.speaking_rate = speaking_rate
        logger.warning(
            "VITSEngine: TTS not yet wired up — synthesise() will return empty bytes"
        )

    def synthesise(self, text: str) -> bytes:
        """Placeholder — returns empty bytes."""
        logger.warning("VITSEngine.synthesise: TTS stub called for %r", text[:40])
        return b""
