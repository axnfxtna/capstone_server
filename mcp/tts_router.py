"""
mcp/tts_router.py
==================
Thai text pre-processor for Satu TTS.

Splits Thai text into space-separated syllables so Satu can read
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
    'wi-fi':        'ไวไฟ',
    'zone':         'โซน',
    'credit':       'เครดิต',
    'credits':      'เครดิต',
    'lab':          'แลป',
    'ai':           'เอไอ',
    'rai':          'อาร์เอไอ',
    'matbot':       'แมทบอท',
    'kmitl':        'เค เอ็ม ไอ ที แอล',
    'building':     'อาคาร',
    'floor':        'ชั้น',
    'robotics':     'โรโบติกส์',
    'engineering':  'วิศวกรรม',
    'programming':  'โปรแกรมมิ่ง',
    'drawing':      'ดรออิ้ง',
    'introduction': 'อินโทรดักชั่น',
    'intro':        'อินโทร',
    'physics':      'ฟิสิกส์',
    'to':           'ทู',
    'password':     'พาสเวิร์ด',
    'microprocessor': 'ไมโครโปรเซสเซอร์',
    'microcontroller': 'ไมโครคอนโทรลเลอร์',
    'interface': 'อินเทอร์เฟซ',
    'and': 'แอนด์',
}

# Regex to split a token into letter-runs and digit-runs
_ALNUM_SPLIT = re.compile(r'[A-Za-z]+|\d+')

# Thai word substitutions for known mispronunciations and abbreviations.
# Applied before syllabification — longer matches first.
_THAI_SUBSTITUTION = {
    'น้องสาธุ': 'น้อง สา ทุ',
    'สาธุ':     'สา ทุ',
    'พ.ศ.':     'ปี',     # "พ.ศ." dots break TTS prosody — replace with spoken form
    'ค.ศ.':     'ปี',
}


_ONES = ['ศูนย์', 'หนึ่ง', 'สอง', 'สาม', 'สี่', 'ห้า', 'หก', 'เจ็ด', 'แปด', 'เก้า']
_PLACES = ['', 'สิบ', 'ร้อย', 'พัน', 'หมื่น', 'แสน']

# Thai words — used for digit-by-digit (room/building codes)
_DIGIT_THAI = ['ศูนย์', 'หนึ่ง', 'สอง', 'สาม', 'สี่', 'ห้า', 'หก', 'เจ็ด', 'แปด', 'เก้า']


def _int_to_thai_words(n: int) -> str:
    """
    Pure-Python Thai number → words. No external dependencies.

    Rules:
      - tens digit = 1  → สิบ   (not หนึ่งสิบ)
      - tens digit = 2  → ยี่สิบ (not สองสิบ)
      - units digit = 1, and tens digit ≠ 0 → เอ็ด (not หนึ่ง)
      - millions handled recursively
    """
    if n == 0:
        return 'ศูนย์'
    if n < 0:
        return 'ลบ' + _int_to_thai_words(-n)
    if n >= 1_000_000:
        hi = n // 1_000_000
        lo = n % 1_000_000
        return _int_to_thai_words(hi) + 'ล้าน' + (_int_to_thai_words(lo) if lo else '')

    digits = []
    tmp = n
    while tmp:
        digits.append(tmp % 10)
        tmp //= 10
    digits.reverse()           # most-significant first
    length = len(digits)

    result = ''
    for i, d in enumerate(digits):
        place = length - 1 - i   # 0=units, 1=tens, 2=hundreds, …
        if d == 0:
            continue
        if place == 1:
            if d == 1:
                result += 'สิบ'    # 10, 11…19 → just สิบ, no หนึ่ง
            elif d == 2:
                result += 'ยี่สิบ'  # 20…29
            else:
                result += _ONES[d] + 'สิบ'
        elif place == 0 and d == 1 and length > 1 and digits[i - 1] != 0:
            # units=1, there IS a non-zero tens digit → เอ็ด
            result += 'เอ็ด'
        else:
            result += _ONES[d] + (_PLACES[place] if place > 0 else '')
    return result


def thai_time_str(hour: int, minute: int) -> str:
    """Convert hour/minute to colloquial spoken Thai time.

    Uses the 6-period informal system (ตี / โมงเช้า / เที่ยง / บ่าย / เย็น / ทุ่ม)
    rather than the formal 24-hour นาฬิกา system, matching natural Thai speech.

    Period mapping:
      00:00          → เที่ยงคืน
      01:00 - 05:59  → ตี{1-5}
      06:00 - 11:59  → {6-11}โมงเช้า
      12:00 - 12:59  → เที่ยง
      13:00          → บ่ายโมง
      14:00 - 15:59  → บ่าย{2-3}โมง
      16:00 - 18:59  → {4-6}โมงเย็น
      19:00 - 23:59  → {1-5}ทุ่ม
    """
    mn_str = f"{_int_to_thai_words(minute)}นาที" if minute > 0 else ""

    if hour == 0:
        base = "เที่ยงคืน"
    elif 1 <= hour <= 5:
        base = f"ตี{_int_to_thai_words(hour)}"
    elif 6 <= hour <= 11:
        base = f"{_int_to_thai_words(hour)}โมงเช้า"
    elif hour == 12:
        base = "เที่ยง"
    elif hour == 13:
        base = "บ่ายโมง"
    elif 14 <= hour <= 15:
        base = f"บ่าย{_int_to_thai_words(hour - 12)}โมง"
    elif 16 <= hour <= 18:
        base = f"{_int_to_thai_words(hour - 12)}โมงเย็น"
    else:  # 19-23
        base = f"{_int_to_thai_words(hour - 18)}ทุ่ม"

    return f"{base} {mn_str}".strip()


def _normalize_time(text: str) -> str:
    """Replace HH:MM / H:MM patterns with spoken Thai time words."""
    def _replace(m: re.Match) -> str:
        h, mn = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mn <= 59:
            return thai_time_str(h, mn)
        return m.group()   # out-of-range — leave untouched
    return re.sub(r'\b(\d{1,2}):(\d{2})\b', _replace, text)


def _num_to_thai(n: int) -> str:
    """Convert an integer to Thai words for TTS.

    4-digit non-year numbers are read digit-by-digit (room/building codes).
    All others are converted to full Thai words.
    """
    s = str(n)
    if len(s) == 4 and not (2400 <= n <= 2600):
        return ' '.join(_DIGIT_THAI[int(d)] for d in s)
    return _int_to_thai_words(n)


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

    # Apply substitutions (abbreviations, known mispronunciations) before chunking
    for word, replacement in _THAI_SUBSTITUTION.items():
        text = text.replace(word, replacement)

    # Normalize HH:MM time patterns before digit expansion
    text = _normalize_time(text)

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
    Prepare Thai text for Satu TTS.

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

    # Apply Thai word substitutions (known mispronunciations)
    for word, replacement in _THAI_SUBSTITUTION.items():
        text = text.replace(word, replacement)

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
