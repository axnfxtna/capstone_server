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

# ─────────────────────────────────────────────────────────────────────
# English → Thai expansion (for TTS readability)
# ─────────────────────────────────────────────────────────────────────

_ENG_LETTER = {
    'A': 'เอ',  'B': 'บี',   'C': 'ซี',        'D': 'ดี',
    'E': 'อี',  'F': 'เอฟ',  'G': 'จี',         'H': 'เอช',
    'I': 'ไอ',  'J': 'เจ',   'K': 'เค',         'L': 'แอล',
    'M': 'เอ็ม', 'N': 'เอ็น', 'O': 'โอ',        'P': 'พี',
    'Q': 'คิว', 'R': 'อาร์', 'S': 'เอส',        'T': 'ที',
    'U': 'ยู',  'V': 'วี',   'W': 'ดับเบิ้ลยู', 'X': 'เอ็กซ์',
    'Y': 'วาย', 'Z': 'แซด',
}

# Common English words/abbreviations that appear in robot replies
_ENG_WORD = {
    'email':        'อีเมล',
    'wifi':         'ไวไฟ',
    'zone':         'โซน',
    'credit':       'เครดิต',
    'credits':      'เครดิต',
    'lab':          'แลป',
    'ai':           'เอไอ',
    'rai':          'อาร์เอไอ',
    'kmitl':        'เค เอ็ม ไอ ที แอล',
    'building':     'อาคาร',
    'floor':        'ชั้น',
    'robotics':     'โรโบติกส์',
    'engineering':  'วิศวกรรม',
}

# Regex to split a token into letter-runs and digit-runs
_ALNUM_SPLIT = re.compile(r'[A-Za-z]+|\d+')


_DIGIT_THAI = {'0': 'ศูนย์', '1': 'หนึ่ง', '2': 'สอง', '3': 'สาม',
               '4': 'สี่', '5': 'ห้า', '6': 'หก', '7': 'เจ็ด',
               '8': 'แปด', '9': 'เก้า'}


def _num_to_thai(n: int) -> str:
    # 4-digit numbers are room/building codes — read digit by digit
    s = str(n)
    if len(s) == 4:
        return ' '.join(_DIGIT_THAI[d] for d in s)
    try:
        from pythainlp.util import num_to_thaiword
        return num_to_thaiword(n)
    except Exception:
        return s


def _expand_token(token: str) -> str:
    """Convert one non-Thai, non-space token to Thai-readable text."""
    # 1. Pure integer
    if re.fullmatch(r'\d+', token):
        return _num_to_thai(int(token))

    # 2. Known English word (case-insensitive)
    if token.lower() in _ENG_WORD:
        return _ENG_WORD[token.lower()]

    # 3. Hyphenated compound: E-12, Zone-D, 12-01 → process each part
    if '-' in token:
        parts = [_expand_token(p) for p in token.split('-') if p]
        return ' '.join(parts)

    # 4. Mixed alphanumeric without hyphen: E12 → อี สิบสอง
    sub = _ALNUM_SPLIT.findall(token)
    if len(sub) > 1:
        return ' '.join(_expand_token(p) for p in sub)

    # 5. Pure letter string → spell out letter by letter
    if re.fullmatch(r'[A-Za-z]+', token):
        return ' '.join(_ENG_LETTER.get(c.upper(), c) for c in token)

    # 6. Fallback
    return token


def expand_for_tts(text: str) -> str:
    """
    Translate English letters, acronyms, and numbers in Thai text into
    Thai-readable words without changing meaning.

    Examples:
      E-12   → อี สิบสอง
      KMITL  → เค เอ็ม ไอ ที แอล
      RAI    → อาร์ เอ ไอ
      Zone D → โซน ดี
      3      → สาม
      email  → อีเมล

    Thai text is passed through unchanged.
    """
    if not text or not text.strip():
        return text

    _chunk_re = re.compile(rf"[{_THAI_RANGE}]+|[^\s{_THAI_RANGE}]+|\s+")
    parts: List[str] = []
    for m in _chunk_re.finditer(text):
        token = m.group()
        if token.isspace():
            parts.append(token)
        elif re.search(rf"[{_THAI_RANGE}]", token):
            parts.append(token)          # pure Thai — untouched
        else:
            parts.append(_expand_token(token))

    return re.sub(r' {2,}', ' ', ''.join(parts)).strip()

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
