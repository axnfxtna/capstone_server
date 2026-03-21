"""
mcp/tts_router.py
==================
Thai phoneme pre-processor (mcp_thaitts).
Converts raw Thai text to syllable-spaced, pronunciation-corrected text
before passing to the TTS engine.

This module reuses the full Thai reading rules pipeline developed in
final_docker_component/src/pipelines/thai_reader_pipeline.py and applies:
  - อักษรควบไม่แท้: ทร→ซ, สร→ส, ศร→ส
  - ฤ vowel: ริ / รึ
  - การันต์ (์): remove silent consonants
  - ตัวสะกด: convert final consonants to their phonetic class
  - ๆ (mai yamok): expand to repeat the preceding word
  - Word-tokenise before syllabifying to prevent cross-word boundary errors
"""

import re
import logging
from typing import FrozenSet, List, Optional

logger = logging.getLogger(__name__)

try:
    from pythainlp.tokenize import syllable_tokenize, word_tokenize
    PYTHAINLP_AVAILABLE = True
except ImportError:
    PYTHAINLP_AVAILABLE = False
    logger.warning("pythainlp not found — Thai syllabification will be skipped")

# ─────────────────────────────────────────────────────────────────────
# Thai character sets
# ─────────────────────────────────────────────────────────────────────

_THAI_RANGE = "\u0e00-\u0e7f"
_THAI_CONSONANTS: FrozenSet[str] = frozenset(
    "กขฃคฅฆงจฉชซฌญฎฏฐฑฒณดตถทธนบปผฝพฟภมยรลวศษสหฬอฮ"
)
_VOWEL_DIACRITICS: FrozenSet[str] = frozenset("าิีึืัุูะๅ็")
_LEADING_VOWELS: FrozenSet[str] = frozenset("เแโใไ")
_THANTHAKHAT = "\u0e4c"
_TOKEN_RE = re.compile(rf"[{_THAI_RANGE}]+|[^{_THAI_RANGE}\s]+")
_PUNCT_STRIP_RE = re.compile(r"[?!,;:\.\u2026]+$")

# ─────────────────────────────────────────────────────────────────────
# ตัวสะกด phonetic map
# ─────────────────────────────────────────────────────────────────────

_FINAL_CONSONANT_PHONETIC = {
    # แม่กด → ด
    "ต": "ด", "ถ": "ด", "ท": "ด", "ธ": "ด",
    "ฎ": "ด", "ฏ": "ด", "ฐ": "ด", "ฑ": "ด", "ฒ": "ด",
    "ช": "ด", "ซ": "ด", "ฉ": "ด", "ส": "ด", "ศ": "ด", "ษ": "ด",
    "จ": "ด", "ฌ": "ด",
    # แม่กก → ก
    "ข": "ก", "ค": "ก", "ฅ": "ก", "ฆ": "ก",
    # แม่กบ → บ
    "ป": "บ", "พ": "บ", "ฟ": "บ", "ภ": "บ",
    # แม่กน → น
    "ณ": "น", "ญ": "น", "ร": "น", "ล": "น", "ฬ": "น",
}
_COMPOUND_VOWEL_CHARS: FrozenSet[str] = frozenset("ยวอ")


# ─────────────────────────────────────────────────────────────────────
# Reading rules
# ─────────────────────────────────────────────────────────────────────

def _rule_a_clusters(s: str) -> str:
    if s.startswith("ทร"): return "ซ" + s[2:]
    if s.startswith("สร"): return "ส" + s[2:]
    if s.startswith("ศร"): return "ส" + s[2:]
    return s


def _rule_b_ru(s: str) -> str:
    if re.fullmatch(r"ฤ[\u0e48\u0e49\u0e4a\u0e4b]?", s):
        return s.replace("ฤ", "รึ")
    return s.replace("ฤ", "ริ")


def _rule_c_thanthakhat(s: str) -> str:
    return re.sub(rf"[{''.join(_THAI_CONSONANTS)}]์", "", s)


def _rule_d_final_consonant(s: str) -> str:
    chars = list(s)
    n = len(chars)
    last_c_idx = next(
        (i for i in range(n - 1, -1, -1) if chars[i] in _THAI_CONSONANTS), -1
    )
    if last_c_idx == -1:
        return s
    last_dv_idx = next(
        (i for i in range(n - 1, -1, -1) if chars[i] in _VOWEL_DIACRITICS), -1
    )
    is_final = last_dv_idx != -1 and last_c_idx > last_dv_idx
    if not is_final and chars and chars[0] in _LEADING_VOWELS:
        if len([i for i in range(1, n) if chars[i] in _THAI_CONSONANTS]) >= 2:
            is_final = True
    if not is_final:
        return s
    silent_set = set()
    if last_dv_idx != -1:
        silent_set = {
            i for i in range(last_dv_idx + 1, last_c_idx)
            if chars[i] in _THAI_CONSONANTS and chars[i] not in _COMPOUND_VOWEL_CHARS
        }
    result = []
    for i, ch in enumerate(chars):
        if i in silent_set:
            continue
        if i == last_c_idx and ch in _FINAL_CONSONANT_PHONETIC:
            result.append(_FINAL_CONSONANT_PHONETIC[ch])
        else:
            result.append(ch)
    return "".join(result)


def _apply_reading_rules(syllable: str) -> str:
    s = syllable.strip()
    if not s:
        return s
    s = _rule_a_clusters(s)
    s = _rule_b_ru(s)
    s = _rule_c_thanthakhat(s)
    s = _rule_d_final_consonant(s)
    return s or syllable


# ─────────────────────────────────────────────────────────────────────
# Syllabification
# ─────────────────────────────────────────────────────────────────────

def _syllabify_word(word: str) -> List[str]:
    if not PYTHAINLP_AVAILABLE:
        return [word]
    for engine in ("han_solo", None):
        try:
            kw = {} if engine is None else {"engine": engine}
            result = [s for s in syllable_tokenize(word, **kw) if s.strip()]
            if result:
                return result
        except Exception:
            pass
    try:
        result = [w for w in word_tokenize(word, engine="newmm") if w.strip()]
        if result:
            return result
    except Exception:
        pass
    return [word]


def _process_thai_run(run: str) -> str:
    if not PYTHAINLP_AVAILABLE:
        return run
    try:
        words = word_tokenize(run, engine="newmm")
    except Exception:
        words = [run]
    all_syllables: List[str] = []
    for word in words:
        w = word.strip()
        if not w:
            continue
        syllables = _syllabify_word(w)
        syllables = [_apply_reading_rules(s) for s in syllables]
        all_syllables.extend(syllables)
    return " ".join(all_syllables)


def _is_thai(text: str) -> bool:
    return bool(re.search(rf"[{_THAI_RANGE}]", text))


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────

def to_tts_ready(text: str) -> str:
    """
    Convert raw Thai LLM output to syllable-spaced, pronunciation-corrected
    text suitable for TTS (KhanomTan / MMS-TTS-THAI).

    This is the main entry point used by the FastAPI /thai_tts route.
    """
    if not text or not text.strip():
        return text

    # Normalise whitespace
    text = re.sub(r"\s+", " ", text).strip()

    # Expand ๆ (mai yamok) — repeat preceding word
    text = re.sub(
        r"([\u0e00-\u0e45\u0e47-\u0e7f]+)\s*ๆ",
        r"\1 \1",
        text,
    )

    output_parts: List[str] = []
    pos = 0
    for m in _TOKEN_RE.finditer(text):
        start, end = m.span()
        if pos < start:
            output_parts.append(text[pos:start])
        token = m.group()
        if _is_thai(token):
            output_parts.append(_process_thai_run(token))
        else:
            cleaned = _PUNCT_STRIP_RE.sub("", token)
            output_parts.append(cleaned if cleaned else token)
        pos = end
    if pos < len(text):
        output_parts.append(text[pos:])

    result = "".join(output_parts)
    result = re.sub(r" {2,}", " ", result).strip()
    return result
