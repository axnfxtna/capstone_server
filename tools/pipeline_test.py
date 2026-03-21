"""
tools/pipeline_test.py
=======================
Full pipeline smoke test — injects mock PI 5 payloads directly into the
running server and reports what worked, what failed, and what's not yet live.

Run from server/ directory:
  source venv/bin/activate
  python tools/pipeline_test.py

Server must already be running on localhost:8000.
"""

import json
import time
import requests

BASE = "http://localhost:8000"

PASS = "\033[92m✅\033[0m"
FAIL = "\033[91m❌\033[0m"
SKIP = "\033[93m⬜\033[0m"
WARN = "\033[93m⚠️\033[0m"

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
        r = requests.post(f"{BASE}{path}", json=payload, timeout=60)
        return r
    except Exception as e:
        return None


# ─────────────────────────────────────────────────────────────────────
# 0. Server health
# ─────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("0. SERVER HEALTH")
print("=" * 60)

r = requests.get(f"{BASE}/health", timeout=5)
check("GET /health returns 200", r.status_code == 200, r.text[:80])


# ─────────────────────────────────────────────────────────────────────
# 1. /greeting  — registered person
# ─────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("1. /greeting — registered person")
print("=" * 60)

greeting_payload = {
    "timestamp": "2026-03-20T10:00:00",
    "person_id": "Palm (Krittin Sakharin)",
    "is_registered": True,
    "track_id": 1,
    "bbox": [100, 100, 200, 300],
    "vision_confidence": 0.92,
}

r = post("/greeting", greeting_payload)
if r:
    check("POST /greeting returns 200", r.status_code == 200, f"status={r.status_code}")
    data = r.json()
    check("greeting status=ok or cooldown", data.get("status") in ("ok", "cooldown"), str(data))
    if data.get("status") == "ok":
        pt = data.get("phoneme_text", "")
        check("phoneme_text is non-empty", bool(pt), repr(pt[:80]))
        check("reply uses 'ค่ะ' (female particle)", "ค่ะ" in pt, repr(pt[:80]))
else:
    check("POST /greeting reachable", False, "connection error")

# ─────────────────────────────────────────────────────────────────────
# 2. /greeting — cooldown (same person, immediate retry)
# ─────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("2. /greeting — cooldown (same person, immediate retry)")
print("=" * 60)

r2 = post("/greeting", greeting_payload)
if r2:
    check("Second /greeting returns cooldown", r2.json().get("status") == "cooldown", str(r2.json()))

# ─────────────────────────────────────────────────────────────────────
# 3. /greeting — unknown person (should be skipped)
# ─────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("3. /greeting — unknown person (should skip)")
print("=" * 60)

r3 = post("/greeting", {**greeting_payload, "person_id": "Unknown", "is_registered": False})
if r3:
    check("Unknown person returns skipped", r3.json().get("status") == "skipped", str(r3.json()))

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
# Helper: base detection payload
# ─────────────────────────────────────────────────────────────────────

def det(text, confidence=0.9, person="Palm (Krittin Sakharin)"):
    return {
        "timestamp": "2026-03-20T10:01:00",
        "person_id": person,
        "is_registered": True,
        "track_id": 1,
        "bbox": [100, 100, 200, 300],
        "stt": {
            "text": text,
            "confidence": confidence,
            "language": "th",
            "duration": 2.0,
        },
    }


# ─────────────────────────────────────────────────────────────────────
# 5. /detection — low STT confidence (fallback TTS)
# ─────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("5. /detection — low STT confidence (should speak fallback)")
print("=" * 60)

r5 = post("/detection", det("hhh aaa", confidence=0.3))
if r5:
    check("Low-confidence returns 200", r5.status_code == 200, f"status={r5.status_code}")
    check("active=1 even on low-confidence", r5.json().get("active") == 1, str(r5.json()))
    # Check events log for the fallback
    time.sleep(0.5)
    evts = requests.get(f"{BASE}/events").json()
    recent = [e for e in evts if e.get("status") == "low_confidence_fallback"]
    check("low_confidence_fallback event logged", len(recent) > 0,
          f"found {len(recent)} low_confidence_fallback events")

# ─────────────────────────────────────────────────────────────────────
# 6. /detection — timetable query
# ─────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("6. /detection — timetable query")
print("=" * 60)

r6 = post("/detection", det("วิชา Programming เรียนวันไหนครับ", confidence=0.95))
if r6:
    check("Timetable query returns 200", r6.status_code == 200)
    time.sleep(1)
    evts = requests.get(f"{BASE}/events").json()
    timetable_evts = [e for e in evts if e.get("status") == "ok" and "Programming" in (e.get("stt_raw") or "")]
    check("Timetable query event logged as ok", len(timetable_evts) > 0)
    if timetable_evts:
        reply = timetable_evts[0].get("reply_text", "")
        check("Reply mentions a day (วัน)", "วัน" in reply or "จันทร์" in reply or "อังคาร" in reply or "พุธ" in reply,
              repr(reply[:120]))
        check("Reply uses 'ค่ะ'", "ค่ะ" in reply, repr(reply[:80]))
        print(f"       Reply: {reply[:150]}")

# ─────────────────────────────────────────────────────────────────────
# 7. /detection — curriculum query
# ─────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("7. /detection — curriculum query")
print("=" * 60)

r7 = post("/detection", det("หลักสูตรปี 1 มีวิชาอะไรบ้างครับ", confidence=0.9))
if r7:
    check("Curriculum query returns 200", r7.status_code == 200)
    time.sleep(1)
    evts = requests.get(f"{BASE}/events").json()
    cur_evts = [e for e in evts if e.get("status") == "ok" and "หลักสูตร" in (e.get("stt_raw") or "")]
    check("Curriculum event logged", len(cur_evts) > 0)
    if cur_evts:
        reply = cur_evts[0].get("reply_text", "")
        check("Reply is non-empty", bool(reply), repr(reply[:80]))
        print(f"       Reply: {reply[:150]}")

# ─────────────────────────────────────────────────────────────────────
# 8. /detection — navigation intent
# ─────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("8. /detection — navigation intent")
print("=" * 60)

r8 = post("/detection", det("พาไปห้องสมุดหน่อยค่ะ", confidence=0.88))
if r8:
    check("Navigation query returns 200", r8.status_code == 200)
    time.sleep(1)
    evts = requests.get(f"{BASE}/events").json()
    nav_evts = [e for e in evts if e.get("status") == "ok" and "ห้องสมุด" in (e.get("stt_raw") or "")]
    check("Navigation event logged", len(nav_evts) > 0)
    if nav_evts:
        intent = nav_evts[0].get("intent", "")
        reply = nav_evts[0].get("reply_text", "")
        check("Intent=navigate", intent == "navigate", f"intent={intent!r}")
        print(f"       Reply: {reply[:150]}")

# ─────────────────────────────────────────────────────────────────────
# 9. /detection — farewell intent
# ─────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("9. /detection — farewell intent")
print("=" * 60)

r9 = post("/detection", det("ขอบคุณค่ะ ลาก่อนนะครับ", confidence=0.9))
if r9:
    check("Farewell query returns 200", r9.status_code == 200)
    time.sleep(1)
    evts = requests.get(f"{BASE}/events").json()
    bye_evts = [e for e in evts if e.get("status") == "ok" and "ลาก่อน" in (e.get("stt_raw") or "")]
    check("Farewell event logged", len(bye_evts) > 0)
    if bye_evts:
        intent = bye_evts[0].get("intent", "")
        reply = bye_evts[0].get("reply_text", "")
        check("Intent=farewell", intent == "farewell", f"intent={intent!r}")
        print(f"       Reply: {reply[:150]}")

# ─────────────────────────────────────────────────────────────────────
# 10. /detection — unregistered person (should skip)
# ─────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("10. /detection — unregistered person")
print("=" * 60)

unregistered = det("สวัสดีครับ", person="Unknown", confidence=0.9)
unregistered["is_registered"] = False
r10 = post("/detection", unregistered)
if r10:
    check("Unregistered returns 200", r10.status_code == 200)
    check("active=0 for unregistered", r10.json().get("active") == 0, str(r10.json()))

# ─────────────────────────────────────────────────────────────────────
# 11. /grammar  — direct grammar correction endpoint
# ─────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("11. /grammar — direct correction endpoint")
print("=" * 60)

rg = requests.post(f"{BASE}/grammar", json={"raw_text": "ผมอยาก ไปเรียน วิชา โปรแกรมมิ่งครับ", "session_id": "test-session"}, timeout=30)
if rg.status_code == 200:
    corrected = rg.json().get("corrected_text", "")
    check("Grammar correction returns text", bool(corrected), repr(corrected[:80]))
else:
    check("Grammar endpoint returns 200", False, f"status={rg.status_code}")

# ─────────────────────────────────────────────────────────────────────
# 12. Not-yet-implemented items
# ─────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("12. NOT YET IMPLEMENTED (informational)")
print("=" * 60)

note("KhanomTan server-side TTS synthesis (2.3) — sending phoneme text to PI 5 only")
note("PI 5 /tts_render confirmed working — depends on Teammate A")
note("PI 5 /navigation confirmed working — depends on Teammate B")
note("Session timeout / expiry (2.6) — sessions are immortal until restart")
note("Grammar corrector skip on high confidence (2.5) — corrects all inputs")
note("Redis session persistence (Phase 3) — in-memory only, lost on restart")
note("/events pagination (Phase 3) — capped at 50 events")

# ─────────────────────────────────────────────────────────────────────
# 13. /events dump
# ─────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("13. PIPELINE EVENTS (last 10)")
print("=" * 60)

evts = requests.get(f"{BASE}/events").json()
for e in evts[-10:]:
    ep = e.get("endpoint", "?")
    pid = e.get("person_id", "?")
    st = e.get("status", "?")
    intent = e.get("intent", "")
    reply = (e.get("reply_text") or "")[:60]
    line = f"  [{ep}] {pid} | status={st}"
    if intent:
        line += f" | intent={intent}"
    if reply:
        line += f" | reply={reply!r}"
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
print(f"  {SKIP} Not yet: {len(skipped)}")

if failed:
    print(f"\n  Failed checks:")
    for label, _, detail in failed:
        print(f"    - {label}")
        if detail:
            print(f"      {detail[:100]}")

print()
