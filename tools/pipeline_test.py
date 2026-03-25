"""
tools/pipeline_test.py
=======================
Full pipeline smoke test — injects mock PI 5 payloads into the running server
and validates output quality (RAG, prompts, intents, language correctness).

Run from server/ directory:
  source venv/bin/activate
  python tools/pipeline_test.py

Server must already be running on localhost:8000.
"""

import time
import requests

BASE = "http://localhost:8000"

PASS = "\033[92m✅\033[0m"
FAIL = "\033[91m❌\033[0m"
SKIP = "\033[93m⬜\033[0m"

results = []


def check(label: str, ok: bool, detail: str = ""):
    symbol = PASS if ok else FAIL
    results.append((label, ok, detail))
    print(f"  {symbol}  {label}")
    if detail:
        for line in detail.splitlines():
            print(f"       {line}")


def note(label: str, detail: str = ""):
    results.append((label, None, detail))
    print(f"  {SKIP}  {label}")
    if detail:
        for line in detail.splitlines():
            print(f"       {line}")


def post(path, payload):
    try:
        r = requests.post(f"{BASE}{path}", json=payload, timeout=90)
        return r
    except Exception as e:
        print(f"  [connection error] {e}")
        return None


def recent_event(keyword_field: str, keyword_value: str, field: str = "stt_raw"):
    """Fetch /events and find the most recent event where field contains keyword_value."""
    evts = requests.get(f"{BASE}/events", timeout=5).json()
    matches = [e for e in reversed(evts) if keyword_value in (e.get(field) or "")]
    return matches[0] if matches else None


# ─────────────────────────────────────────────────────────────────────
# Payload helpers
# ─────────────────────────────────────────────────────────────────────

STUDENT = {
    "person_id": "Palm (Krittin Sakharin)",
    "thai_name": "ปาล์ม",
    "student_id": "65011356",
    "is_registered": True,
}


def greeting_payload(**overrides):
    p = {
        "timestamp": "2026-03-23T09:00:00",
        "vision_confidence": 0.92,
        **STUDENT,
    }
    p.update(overrides)
    return p


def det(text: str, person: dict = None):
    p = person or STUDENT
    return {
        "timestamp": "2026-03-23T09:01:00",
        "stt": {"text": text, "language": "th", "duration": 2.0},
        **p,
    }


# ─────────────────────────────────────────────────────────────────────
# 0. Server health
# ─────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("0. SERVER HEALTH")
print("=" * 60)

r = requests.get(f"{BASE}/health", timeout=5)
check("GET /health returns 200", r.status_code == 200, r.text[:80])


# ─────────────────────────────────────────────────────────────────────
# 1. /greeting — registered person
# ─────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("1. /greeting — registered person (Palm, ปาล์ม, student_id)")
print("=" * 60)

r1 = post("/greeting", greeting_payload())
if r1:
    check("POST /greeting returns 200", r1.status_code == 200)
    data = r1.json()
    status = data.get("status")
    check("status is ok or cooldown", status in ("ok", "cooldown"), str(data))
    if status == "ok":
        reply = data.get("greeting_text") or data.get("reply_text") or data.get("text") or ""
        check("Greeting text non-empty", bool(reply), repr(reply[:100]))
        check("Uses ค่ะ (female particle)", "ค่ะ" in reply, repr(reply[:120]))
        check("No ครับ in reply", "ครับ" not in reply, repr(reply[:120]))
        check("Does not start with student name", not reply.strip().startswith("ปาล์ม"), repr(reply[:60]))
        print(f"       Reply: {reply[:200]}")
else:
    check("POST /greeting reachable", False, "connection error")


# ─────────────────────────────────────────────────────────────────────
# 2. /greeting — cooldown (same person, immediate retry)
# ─────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("2. /greeting — cooldown (same person, immediate retry)")
print("=" * 60)

r2 = post("/greeting", greeting_payload())
if r2:
    check("Second /greeting returns cooldown", r2.json().get("status") == "cooldown", str(r2.json()))


# ─────────────────────────────────────────────────────────────────────
# 3. /greeting — unknown person (should skip)
# ─────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("3. /greeting — unknown person (should greet as stranger)")
print("=" * 60)

r3 = post("/greeting", greeting_payload(person_id="Unknown", is_registered=False))
if r3:
    data3 = r3.json()
    check("Unknown person returns stranger", data3.get("status") == "stranger", str(data3))
    greeting3 = data3.get("greeting_text", "")
    check("Stranger greeting non-empty", bool(greeting3), repr(greeting3[:100]))
    check("Stranger greeting uses ค่ะ", "ค่ะ" in greeting3, repr(greeting3[:120]))


# ─────────────────────────────────────────────────────────────────────
# 4. /activate poll
# ─────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("4. /activate poll")
print("=" * 60)

r4 = requests.get(f"{BASE}/activate", timeout=5)
check("GET /activate returns 200", r4.status_code == 200)
check("active=1 after greeting", r4.json().get("active") == 1, str(r4.json()))


# ─────────────────────────────────────────────────────────────────────
# 5. /detection — casual chat (chat_history route)
# ─────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("5. /detection — casual chat")
print("=" * 60)

r5 = post("/detection", det("สวัสดีค่ะ วันนี้เป็นยังไงบ้าง"))
if r5:
    check("Casual chat returns 200", r5.status_code == 200)
    time.sleep(1)
    evt = recent_event("stt_raw", "สวัสดีค่ะ วันนี้")
    if evt:
        reply = evt.get("reply_text", "")
        check("Reply non-empty", bool(reply), repr(reply[:80]))
        check("Uses ค่ะ", "ค่ะ" in reply, repr(reply[:120]))
        check("No ครับ", "ครับ" not in reply, repr(reply[:120]))
        check("Does not start with ปาล์ม", not reply.strip().startswith("ปาล์ม"), repr(reply[:60]))
        print(f"       Reply: {reply[:200]}")
    else:
        check("Event logged", False, "event not found in /events")


# ─────────────────────────────────────────────────────────────────────
# 6. /detection — timetable query
# ─────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("6. /detection — timetable query")
print("=" * 60)

r6 = post("/detection", det("วิชา Programming เรียนวันไหน"))
if r6:
    check("Timetable query returns 200", r6.status_code == 200)
    time.sleep(1)
    evt = recent_event("stt_raw", "Programming")
    if evt:
        reply = evt.get("reply_text", "")
        rag = evt.get("rag_collection", "")
        check("RAG collection = time_table", rag == "time_table", f"rag_collection={rag!r}")
        check("Reply mentions a day", any(d in reply for d in ["วัน", "จันทร์", "อังคาร", "พุธ", "พฤหัส", "ศุกร์"]),
              repr(reply[:120]))
        check("Uses ค่ะ", "ค่ะ" in reply, repr(reply[:120]))
        print(f"       Reply: {reply[:200]}")
    else:
        check("Event logged", False, "not found")


# ─────────────────────────────────────────────────────────────────────
# 7. /detection — curriculum query
# ─────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("7. /detection — curriculum query")
print("=" * 60)

r7 = post("/detection", det("หลักสูตร RAI มีวิชาอะไรบ้างในปี 1"))
if r7:
    check("Curriculum query returns 200", r7.status_code == 200)
    time.sleep(1)
    evt = recent_event("stt_raw", "หลักสูตร RAI")
    if evt:
        reply = evt.get("reply_text", "")
        rag = evt.get("rag_collection", "")
        check("RAG collection = curriculum", rag == "curriculum", f"rag_collection={rag!r}")
        check("Reply non-empty", bool(reply), repr(reply[:80]))
        check("Uses ค่ะ", "ค่ะ" in reply, repr(reply[:120]))
        print(f"       Reply: {reply[:200]}")
    else:
        check("Event logged", False, "not found")


# ─────────────────────────────────────────────────────────────────────
# 8. /detection — uni_info query (building location)
# ─────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("8. /detection — uni_info query (building location)")
print("=" * 60)

r8 = post("/detection", det("ตึก E-12 อยู่ที่ไหน"))
if r8:
    check("Uni info query returns 200", r8.status_code == 200)
    time.sleep(1)
    evt = recent_event("stt_raw", "E-12")
    if evt:
        reply = evt.get("reply_text", "")
        rag = evt.get("rag_collection", "")
        check("RAG collection = uni_info", rag == "uni_info", f"rag_collection={rag!r}")
        check("Reply mentions E-12 or Zone D or วิศวกรรม",
              any(k in reply for k in ["E-12", "Zone D", "วิศวกรรม", "โซน"]),
              repr(reply[:120]))
        check("Uses ค่ะ", "ค่ะ" in reply, repr(reply[:120]))
        print(f"       Reply: {reply[:200]}")
    else:
        check("Event logged", False, "not found")


# ─────────────────────────────────────────────────────────────────────
# 9. /detection — navigation intent
# ─────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("9. /detection — navigation intent")
print("=" * 60)

r9 = post("/detection", det("พาไปห้อง A หน่อย"))
if r9:
    check("Navigation query returns 200", r9.status_code == 200)
    time.sleep(1)
    evt = recent_event("stt_raw", "ห้อง A")
    if evt:
        intent = evt.get("intent", "")
        reply = evt.get("reply_text", "")
        check("Intent = navigate", intent == "navigate", f"intent={intent!r}")
        check("Uses ค่ะ", "ค่ะ" in reply, repr(reply[:120]))
        print(f"       Reply: {reply[:200]}")
    else:
        check("Event logged", False, "not found")


# ─────────────────────────────────────────────────────────────────────
# 10. /detection — farewell intent
# ─────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("10. /detection — farewell intent")
print("=" * 60)

r10 = post("/detection", det("ขอบคุณนะ ลาก่อน"))
if r10:
    check("Farewell query returns 200", r10.status_code == 200)
    time.sleep(1)
    evt = recent_event("stt_raw", "ลาก่อน")
    if evt:
        intent = evt.get("intent", "")
        reply = evt.get("reply_text", "")
        check("Intent = farewell", intent == "farewell", f"intent={intent!r}")
        check("Uses ค่ะ", "ค่ะ" in reply, repr(reply[:120]))
        print(f"       Reply: {reply[:200]}")
    else:
        check("Event logged", False, "not found")


# ─────────────────────────────────────────────────────────────────────
# 11. /detection — unregistered person (should skip)
# ─────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("11. /detection — unregistered person")
print("=" * 60)

r11 = post("/detection", det("สวัสดีครับ", person={
    "person_id": "Unknown",
    "thai_name": None,
    "student_id": None,
    "is_registered": False,
}))
if r11:
    check("Unregistered returns 200", r11.status_code == 200)
    check("Guest pipeline runs (active=1)", r11.json().get("active") == 1, str(r11.json()))


# ─────────────────────────────────────────────────────────────────────
# 12. /grammar — direct endpoint
# ─────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("12. /grammar — STT normalizer")
print("=" * 60)

# Short input (<15 chars) should pass through unchanged
rg1 = requests.post(f"{BASE}/grammar", json={"raw_text": "ไปไหนมา", "session_id": "test"}, timeout=30)
if rg1.status_code == 200:
    out = rg1.json().get("corrected_text", "")
    check("Short input passes through (<15 chars)", out == "ไปไหนมา", f"got: {out!r}")
else:
    check("Grammar endpoint 200", False, f"status={rg1.status_code}")

# Normal input — should not balloon in length (hallucination guard)
rg2 = requests.post(f"{BASE}/grammar", json={"raw_text": "อยากรู้วิชาที่เรียนวัน พุธ ครับ", "session_id": "test"}, timeout=30)
if rg2.status_code == 200:
    inp = "อยากรู้วิชาที่เรียนวัน พุธ ครับ"
    out = rg2.json().get("corrected_text", "")
    check("Output not hallucinated (≤1.5× input length)", len(out) <= len(inp) * 1.5,
          f"in={len(inp)} chars  out={len(out)} chars: {out!r}")
    check("Output non-empty", bool(out), repr(out[:80]))
    print(f"       In:  {inp!r}")
    print(f"       Out: {out!r}")
else:
    check("Grammar endpoint 200 (long input)", False, f"status={rg2.status_code}")


# ─────────────────────────────────────────────────────────────────────
# 13. Pipeline events dump (last 10)
# ─────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("13. PIPELINE EVENTS (last 10)")
print("=" * 60)

evts = requests.get(f"{BASE}/events", timeout=5).json()
for e in evts[-10:]:
    ep = e.get("endpoint", "?")
    pid = e.get("person_id", "?")
    st = e.get("status", "?")
    rag = e.get("rag_collection", "")
    intent = e.get("intent", "")
    reply = (e.get("reply_text") or "")[:70]
    timing = e.get("timing_ms", {})
    line = f"  [{ep}] {pid} | status={st}"
    if rag:
        line += f" | rag={rag}"
    if intent:
        line += f" | intent={intent}"
    if timing:
        line += f" | {timing.get('total', '?')}ms"
    if reply:
        line += f"\n       reply={reply!r}"
    print(line)


# ─────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)

passed = [r for r in results if r[1] is True]
failed = [r for r in results if r[1] is False]
skipped = [r for r in results if r[1] is None]

print(f"\n  {PASS} Passed:  {len(passed)}")
print(f"  {FAIL} Failed:  {len(failed)}")
print(f"  {SKIP} Skipped: {len(skipped)}")

if failed:
    print(f"\n  Failed checks:")
    for label, _, detail in failed:
        print(f"    - {label}")
        if detail:
            print(f"      {detail[:120]}")

print()
