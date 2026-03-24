"""
tools/eval_accuracy.py
=======================
Accuracy evaluation for Satu AI Brain — Phase 4.12.

Metrics measured:
  A. Intent Accuracy + F1 per class (chat / info / navigate / farewell)
  B. Slot F1 for navigation destination extraction
  C. Language Compliance (L1–L8 rules), target 100%
  D. Out-of-Scope Rejection Rate — does the robot decline gracefully?
  E. Task Success Rate (TSR) — primary end-to-end metric, target > 0.80

Run from server/ directory:
  source venv/bin/activate
  python tools/eval_accuracy.py

Server must already be running on localhost:8000.
Results are printed to stdout and saved to docs/eval_report.txt.
"""

import re
import sys
import time
from collections import defaultdict
from datetime import datetime
from typing import Optional

import requests

BASE = "http://localhost:8000"
WAIT_AFTER_REQUEST = 25.0  # seconds — 70B model p95 latency ~19s; must exceed that

PASS  = "\033[92m✅\033[0m"
FAIL  = "\033[91m❌\033[0m"
WARN  = "\033[93m⚠️ \033[0m"
INFO  = "\033[94mℹ️ \033[0m"

STUDENT = {
    "person_id": "Palm (Krittin Sakharin)",
    "thai_name": "ปาล์ม",
    "student_id": "65011356",
    "is_registered": True,
}

# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────

# Each fixture: text, expected_intent, expected_rag_collection, optional destination
# TSR success criterion: intent correct + reply non-empty + language compliant
INTENT_FIXTURES = [
    # ── chat intent ──────────────────────────────────────────────
    dict(text="สวัสดีค่ะ",                      intent="chat",     route="chat_history"),
    dict(text="วันนี้เหนื่อยมากเลย",             intent="chat",     route="chat_history"),
    dict(text="น้องสาธุคือใครอ่ะ",               intent="chat",     route="chat_history"),
    dict(text="ทำอะไรได้บ้างคะ",                intent="chat",     route="chat_history"),
    dict(text="ขอบคุณมากนะคะ",                  intent="chat",     route="chat_history"),
    dict(text="เป็นยังไงบ้างช่วงนี้",            intent="chat",     route="chat_history"),

    # ── info intent — uni_info ────────────────────────────────────
    dict(text="ห้องน้ำอยู่ที่ไหนคะ",            intent="info",     route="uni_info"),
    dict(text="ตึก E-12 อยู่โซนไหน",            intent="info",     route="uni_info"),
    dict(text="โรงอาหารอยู่ตึกไหน",             intent="info",     route="uni_info"),
    dict(text="ห้องสมุดอยู่ชั้นไหน",            intent="info",     route="uni_info"),

    # ── info intent — curriculum ──────────────────────────────────
    dict(text="หลักสูตร RAI มีกี่หน่วยกิต",     intent="info",     route="curriculum"),
    dict(text="วิชา Programming เรียนปีไหน",     intent="info",     route="curriculum"),
    dict(text="เรียนกี่ปีจบ",                   intent="info",     route="curriculum"),

    # ── info intent — time_table ──────────────────────────────────
    dict(text="วันจันทร์มีวิชาอะไรบ้าง",         intent="info",     route="time_table"),
    dict(text="วิชา Programming เรียนวันไหน",    intent="info",     route="time_table"),
    dict(text="ตารางเรียนวันศุกร์มีอะไรบ้าง",   intent="info",     route="time_table"),

    # ── navigate intent ───────────────────────────────────────────
    dict(text="พาฉันไปห้องสมุดหน่อย",           intent="navigate", route="uni_info",  destination="ห้องสมุด"),
    dict(text="อยากไปห้องน้ำ",                  intent="navigate", route="uni_info",  destination="ห้องน้ำ"),
    dict(text="พาไปห้อง 1201",                  intent="navigate", route="uni_info",  destination="1201"),
    dict(text="ไปโรงอาหารได้ไหมคะ",             intent="navigate", route="uni_info",  destination="โรงอาหาร"),

    # ── farewell intent ───────────────────────────────────────────
    dict(text="ขอบคุณนะคะ ลาก่อน",              intent="farewell", route="chat_history"),
    dict(text="ไม่ต้องการความช่วยเหลือแล้วค่ะ", intent="farewell", route="chat_history"),
    dict(text="บายค่ะ",                         intent="farewell", route="chat_history"),
]

# Out-of-scope — robot should decline politely (intent=chat, no action, short reply)
OOS_FIXTURES = [
    "ช่วยทำการบ้านให้หน่อย",
    "ขอ wifi password หน่อย",
    "ช่วยโทรหาอาจารย์ได้ไหม",
    "ไปข้างนอกตึกได้ไหม",
    "ช่วยแปลภาษาอังกฤษให้หน่อย",
]

# ─────────────────────────────────────────────────────────────────────
# Language compliance rules (L1–L8)
# ─────────────────────────────────────────────────────────────────────

CJK_RE      = re.compile(r"[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")
ENG_SENT_RE = re.compile(r"[A-Za-z][A-Za-z0-9 ,''\-]{8,}[.!?]")
MULTI_SENT_RE = re.compile(r"[ค่ะคะ]\s+[^\s]")   # particle followed by more text → likely 2nd sentence


def language_checks(reply: str, student_name: str = "ปาล์ม") -> dict:
    """Return {rule_id: (passed: bool, detail: str)} for L1–L8."""
    return {
        "L1_no_khrap":      ("ครับ" not in reply,               f"found ครับ in: {reply[:80]}"),
        "L2_no_cjk":        (not CJK_RE.search(reply),          f"CJK chars found in: {reply[:80]}"),
        "L3_no_eng_sent":   (not ENG_SENT_RE.search(reply),     f"English sentence found: {reply[:80]}"),
        "L4_no_name_start": (not reply.strip().startswith(student_name),
                                                                  f"starts with name: {reply[:40]}"),
        "L5_max_2_sent":    (reply.count("ค่ะ") + reply.count("คะ") + reply.count("นะคะ") <= 3,
                                                                  f"may exceed 2 sentences: {reply[:80]}"),
        "L6_no_male_pron":  (not any(p in reply for p in ["ผม", "ดิฉัน", "ข้าพเจ้า"]),
                                                                  f"male/formal pronoun found: {reply[:80]}"),
        "L7_no_male_self":   (not any(p in reply for p in ["ผมคือ", "ผมชื่อ"]),
                                                                  f"male self-ID found: {reply[:80]}"),
        "L8_no_old_fallback": ("ขออภัยค่ะ ไม่เข้าใจ" not in reply and "ไม่ทราบค่ะ" not in reply,
                                                                   f"old fallback phrase found: {reply[:80]}"),
    }


# ─────────────────────────────────────────────────────────────────────
# HTTP helpers
# ─────────────────────────────────────────────────────────────────────

def get_event_count() -> int:
    """Return current number of events in /events (baseline snapshot)."""
    try:
        return len(requests.get(f"{BASE}/events", timeout=5).json())
    except Exception:
        return 0


def post_detection(text: str) -> bool:
    payload = {
        "timestamp": datetime.utcnow().isoformat(),
        "stt": {"text": text, "language": "th", "duration": 2.0},
        **STUDENT,
    }
    try:
        r = requests.post(f"{BASE}/detection", json=payload, timeout=90)
        return r.status_code == 200
    except Exception as e:
        print(f"    [connection error] {e}")
        return False


def fetch_event(stt_snippet: str, after_index: int = 0, max_wait: float = WAIT_AFTER_REQUEST) -> Optional[dict]:
    """Poll /events for an event added at or after after_index matching stt_snippet.

    after_index prevents stale events from a previous run being returned before
    the current LLM call completes (critical when model latency > poll window).
    """
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            evts = requests.get(f"{BASE}/events", timeout=5).json()
            new_evts = evts[after_index:]          # only events added after snapshot
            for evt in reversed(new_evts):
                if stt_snippet[:15] in (evt.get("stt_raw") or ""):
                    return evt
        except Exception:
            pass
        time.sleep(0.5)
    return None


# ─────────────────────────────────────────────────────────────────────
# F1 helpers
# ─────────────────────────────────────────────────────────────────────

def compute_f1(tp: int, fp: int, fn: int) -> float:
    p = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    r = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    return 2 * p * r / (p + r) if (p + r) > 0 else 0.0


def destination_match(predicted: Optional[str], expected: str) -> bool:
    """Fuzzy destination match — checks if expected keyword appears in predicted."""
    if not predicted:
        return False
    return expected.lower() in predicted.lower() or predicted.lower() in expected.lower()


# ─────────────────────────────────────────────────────────────────────
# Main evaluation
# ─────────────────────────────────────────────────────────────────────

output_lines = []  # type: ignore

def emit(line: str = ""):
    print(line)
    output_lines.append(re.sub(r"\033\[[0-9;]*m", "", line))   # strip ANSI for file


def section(title: str):
    emit()
    emit("=" * 65)
    emit(f"  {title}")
    emit("=" * 65)


def run():
    # Health check
    section("0. SERVER HEALTH")
    try:
        r = requests.get(f"{BASE}/health", timeout=5)
        ok = r.status_code == 200
        emit(f"  {PASS if ok else FAIL}  GET /health → {r.status_code}")
        if not ok:
            emit("  Server not reachable. Exiting.")
            sys.exit(1)
    except Exception as e:
        emit(f"  {FAIL}  Connection failed: {e}")
        sys.exit(1)

    # ── A. Intent Accuracy + F1 ────────────────────────────────────
    section("A. INTENT ACCURACY + F1 PER CLASS")
    emit(f"  Fixtures: {len(INTENT_FIXTURES)}   Wait per request: {WAIT_AFTER_REQUEST}s")
    emit()

    intent_classes = ["chat", "info", "navigate", "farewell"]
    # {class: {tp, fp, fn}}
    counts = {c: {"tp": 0, "fp": 0, "fn": 0} for c in intent_classes}
    route_correct = 0
    lang_totals   = defaultdict(lambda: {"pass": 0, "fail": 0})
    tsr_scores    = []
    slot_results  = []   # (expected_dest, predicted_dest, matched)

    for i, fx in enumerate(INTENT_FIXTURES, 1):
        text     = fx["text"]
        exp_int  = fx["intent"]
        exp_rag  = fx["route"]
        exp_dest = fx.get("destination")

        emit(f"  [{i:02d}/{len(INTENT_FIXTURES)}] {text[:50]}")
        emit(f"        expected: intent={exp_int}  route={exp_rag}"
             + (f"  dest≈{exp_dest}" if exp_dest else ""))

        pre_count = get_event_count()
        ok = post_detection(text)
        if not ok:
            emit(f"        {FAIL}  POST failed — skipping")
            tsr_scores.append(0)
            continue

        evt = fetch_event(text, after_index=pre_count)
        if not evt:
            emit(f"        {WARN}  event not found in /events within {WAIT_AFTER_REQUEST}s")
            tsr_scores.append(0)
            continue

        got_int  = evt.get("intent", "")
        got_rag  = evt.get("rag_collection", "")
        got_dest = evt.get("destination")
        reply    = evt.get("reply_text", "")

        # Intent confusion matrix
        for cls in intent_classes:
            if cls == exp_int and cls == got_int:
                counts[cls]["tp"] += 1
            elif cls != exp_int and cls == got_int:
                counts[cls]["fp"] += 1
            elif cls == exp_int and cls != got_int:
                counts[cls]["fn"] += 1

        int_ok  = got_int == exp_int
        rag_ok  = got_rag == exp_rag
        route_correct += int(rag_ok)

        # Slot F1 (navigate only)
        if exp_dest is not None:
            matched = destination_match(got_dest, exp_dest)
            slot_results.append((exp_dest, got_dest, matched))
            slot_tag = f"  dest={got_dest!r} {'✅' if matched else '❌'}"
        else:
            slot_tag = ""

        # Language compliance
        lc = language_checks(reply)
        lc_pass = all(v for v, _ in lc.values())
        for rule, (passed, _) in lc.items():
            lang_totals[rule]["pass" if passed else "fail"] += 1

        # TSR: intent correct + reply non-empty + all language rules pass
        tsr = 1.0 if (int_ok and bool(reply) and lc_pass) else \
              0.5 if (int_ok and bool(reply)) else 0.0
        tsr_scores.append(tsr)

        status = PASS if int_ok else FAIL
        emit(f"        {status}  intent={got_int!r}  rag={got_rag!r}{slot_tag}  TSR={tsr}")
        emit(f"        reply: {reply[:90]!r}")

    # ── A results ──────────────────────────────────────────────────
    emit()
    emit("  ── Intent F1 per class ──")
    all_f1 = []
    for cls in intent_classes:
        tp = counts[cls]["tp"]
        fp = counts[cls]["fp"]
        fn = counts[cls]["fn"]
        f1 = compute_f1(tp, fp, fn)
        all_f1.append(f1)
        total_expected = tp + fn
        tag = PASS if f1 >= 0.85 else FAIL
        emit(f"    {tag}  {cls:<10}  F1={f1:.2f}  (TP={tp} FP={fp} FN={fn})  n={total_expected}")

    macro_f1 = sum(all_f1) / len(all_f1)
    total_correct = sum(counts[c]["tp"] for c in intent_classes)
    intent_acc = total_correct / len(INTENT_FIXTURES) if INTENT_FIXTURES else 0
    tag = PASS if intent_acc >= 0.90 else FAIL
    emit()
    emit(f"    {tag}  Intent Accuracy = {intent_acc:.1%}  (target ≥ 90%)")
    emit(f"    {INFO}  Macro F1        = {macro_f1:.2f}")
    emit(f"    {INFO}  RAG Route Acc   = {route_correct}/{len(INTENT_FIXTURES)} = {route_correct/len(INTENT_FIXTURES):.1%}")

    # ── B. Slot F1 ─────────────────────────────────────────────────
    section("B. SLOT F1 — NAVIGATION DESTINATION")
    if slot_results:
        tp_s = sum(1 for _, _, m in slot_results if m)
        fp_s = sum(1 for _, p, m in slot_results if p and not m)
        fn_s = sum(1 for _, p, m in slot_results if not m)
        slot_f1 = compute_f1(tp_s, fp_s, fn_s)
        tag = PASS if slot_f1 >= 0.85 else FAIL
        for exp, got, matched in slot_results:
            sym = PASS if matched else FAIL
            emit(f"  {sym}  expected={exp!r}  got={got!r}")
        emit()
        emit(f"  {tag}  Slot F1 = {slot_f1:.2f}  (TP={tp_s} FP={fp_s} FN={fn_s})  target ≥ 0.85")
    else:
        emit(f"  {WARN}  No navigate fixtures ran — slot F1 not computed")

    # ── C. Language Compliance ─────────────────────────────────────
    section("C. LANGUAGE COMPLIANCE (L1–L8)")
    total_responses = len([s for s in tsr_scores if s is not None])
    all_lang_pass = True
    for rule, counts_lc in sorted(lang_totals.items()):
        p = counts_lc["pass"]
        f = counts_lc["fail"]
        rate = p / (p + f) if (p + f) > 0 else 1.0
        tag = PASS if rate == 1.0 else FAIL
        if rate < 1.0:
            all_lang_pass = False
        emit(f"  {tag}  {rule:<25}  {p}/{p+f} pass  ({rate:.0%})")
    overall_tag = PASS if all_lang_pass else FAIL
    emit()
    emit(f"  {overall_tag}  Overall language compliance  target = 100%")

    # ── D. Out-of-Scope Rejection Rate ─────────────────────────────
    section("D. OUT-OF-SCOPE REJECTION RATE")
    emit(f"  Fixtures: {len(OOS_FIXTURES)}")
    emit()
    oos_declined = 0
    for text in OOS_FIXTURES:
        emit(f"  Input: {text}")
        ok = post_detection(text)
        if not ok:
            emit(f"    {FAIL}  POST failed")
            continue
        evt = fetch_event(text)
        if not evt:
            emit(f"    {WARN}  event not found")
            continue
        reply  = evt.get("reply_text", "")
        intent = evt.get("intent", "")
        dest   = evt.get("destination")
        # OOS success: no navigate intent, no destination extracted, reply non-empty
        declined = intent != "navigate" and not dest and bool(reply)
        oos_declined += int(declined)
        tag = PASS if declined else FAIL
        emit(f"    {tag}  intent={intent!r}  dest={dest!r}")
        emit(f"         reply: {reply[:90]!r}")
    rate = oos_declined / len(OOS_FIXTURES) if OOS_FIXTURES else 1.0
    tag = PASS if rate >= 0.80 else FAIL
    emit()
    emit(f"  {tag}  OOS Rejection Rate = {oos_declined}/{len(OOS_FIXTURES)} = {rate:.0%}  (target ≥ 80%)")

    # ── E. Task Success Rate ───────────────────────────────────────
    section("E. TASK SUCCESS RATE (TSR)")
    if tsr_scores:
        tsr_mean = sum(tsr_scores) / len(tsr_scores)
        full  = sum(1 for s in tsr_scores if s == 1.0)
        half  = sum(1 for s in tsr_scores if s == 0.5)
        zero  = sum(1 for s in tsr_scores if s == 0.0)
        tag = PASS if tsr_mean >= 0.80 else FAIL
        emit(f"  Score distribution:  full=1.0 × {full}  partial=0.5 × {half}  fail=0 × {zero}")
        emit(f"  {tag}  TSR = {tsr_mean:.2f}  (target ≥ 0.80 for pilot deployment)")

    # ── Final summary ──────────────────────────────────────────────
    section("SUMMARY")
    emit(f"  Intent Accuracy : {intent_acc:.1%}   (target ≥ 90%)")
    emit(f"  Macro F1        : {macro_f1:.2f}")
    if slot_results:
        emit(f"  Slot F1 (dest)  : {slot_f1:.2f}    (target ≥ 0.85)")
    emit(f"  Lang Compliance : {'100%' if all_lang_pass else 'FAILED — see C above'}")
    emit(f"  OOS Rejection   : {rate:.0%}       (target ≥ 80%)")
    if tsr_scores:
        emit(f"  TSR             : {tsr_mean:.2f}     (target ≥ 0.80)")
    emit()

    # Save report
    report_path = "docs/eval_report.txt"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"Satu Accuracy Evaluation — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
        f.write("\n".join(output_lines))
    emit(f"  Report saved → {report_path}")
    emit()


if __name__ == "__main__":
    run()
