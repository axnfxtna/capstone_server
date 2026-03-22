"""
mcp/tts_router.py
==================
Thai text pre-processor for KhanomTan TTS v1.0.

Splits Thai text into space-separated syllables so KhanomTan can read
each unit individually (e.g. อะไร → อะ ไร, ต้องการ → ต้อง การ).

Crucially does NOT apply phoneme substitution rules — characters are
preserved as-is so the monitor and logs remain readable (คุณ stays คุณ,
อะไร stays อะ ไร not อะไน).
"""

import re
import logging
from typing import List

logger = logging.getLogger(__name__)

_THAI_RANGE = "\u0e00-\u0e7f"
_TOKEN_RE = re.compile(rf"[{_THAI_RANGE}]+|[^{_THAI_RANGE}\s]+")

try:
    from pythainlp.tokenize import thai_syllables, word_tokenize
    from pythainlp.util import normalize as _normalize
    _PYTHAINLP = True
except ImportError:
    _PYTHAINLP = False
    logger.warning("pythainlp not found — Thai syllabification will be skipped")


def _syllabify_thai(run: str) -> str:
    """
    Tokenize a Thai run into space-separated syllables.
    Does NOT change any characters — only inserts spaces.
    """
    if not _PYTHAINLP:
        return run
    try:
        words = word_tokenize(run, engine="newmm")
    except Exception:
        words = [run]

    syllables: List[str] = []
    for word in words:
        w = word.strip()
        if not w:
            continue
        try:
            parts = [s for s in thai_syllables(w) if s.strip()]
            syllables.extend(parts if parts else [w])
        except Exception:
            syllables.append(w)
    return " ".join(syllables)


def to_tts_ready(text: str) -> str:
    """
    Prepare Thai text for KhanomTan TTS v1.0.

    Steps:
      1. pythainlp normalize (unicode fixes, no character substitution)
      2. Expand ๆ (mai yamok) — repeat preceding word
      3. Syllabify Thai runs — insert spaces between syllables
         (อะไร → อะ ไร  |  คุณ → คุณ  |  ต้องการ → ต้อง การ)
      4. Collapse whitespace
    """
    if not text or not text.strip():
        return text

    if _PYTHAINLP:
        try:
            text = _normalize(text)
        except Exception:
            pass

    # Expand ๆ (mai yamok)
    text = re.sub(
        r"([\u0e00-\u0e45\u0e47-\u0e7f]+)\s*ๆ",
        r"\1 \1",
        text,
    )

    # Syllabify Thai runs; pass non-Thai tokens through unchanged
    parts: List[str] = []
    pos = 0
    for m in _TOKEN_RE.finditer(text):
        start, end = m.span()
        if pos < start:
            parts.append(text[pos:start])
        token = m.group()
        if re.search(rf"[{_THAI_RANGE}]", token):
            parts.append(_syllabify_thai(token))
        else:
            parts.append(token)
        pos = end
    if pos < len(text):
        parts.append(text[pos:])

    return re.sub(r" {2,}", " ", "".join(parts)).strip()
