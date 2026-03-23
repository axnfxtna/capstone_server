"""
tools/benchmark.py
==================
Latency benchmark for KhanomTan AI Brain — Phase 4.11.

Measures TTFR (Time to First Response) and per-stage latency:
  grammar_ms  |  llm_ms  |  tts_ms  |  total_ms

Outputs: mean / p50 / p95 / p99 per stage, broken down by RAG route.
Target: p50 total < 3s,  p95 total < 5s.

Run from server/ directory:
  source venv/bin/activate
  python tools/benchmark.py

Server must already be running on localhost:8000.
Results are saved to docs/benchmark_report.txt.
"""

import statistics
import sys
import time
import re
from collections import defaultdict
from datetime import datetime
from typing import Optional, Tuple

import requests

BASE = "http://localhost:8000"
WAIT_AFTER_REQUEST = 8.0    # generous wait for 70B LLM to finish

PASS  = "\033[92m✅\033[0m"
FAIL  = "\033[91m❌\033[0m"
INFO  = "\033[94mℹ️ \033[0m"

STUDENT = {
    "person_id": "Palm (Krittin Sakharin)",
    "thai_name": "ปาล์ม",
    "student_id": "65011356",
    "is_registered": True,
}

# ─────────────────────────────────────────────────────────────────────
# Fixtures — balanced across all routes and intents
# ─────────────────────────────────────────────────────────────────────

FIXTURES = [
    # chat_history route (no Milvus — fastest path)
    dict(label="chat_history/greeting",    text="สวัสดีค่ะ",                     route="chat_history", intent="chat"),
    dict(label="chat_history/casual",      text="วันนี้เหนื่อยมากเลย",           route="chat_history", intent="chat"),
    dict(label="chat_history/identity",    text="ขนมทานคือใคร",                  route="chat_history", intent="chat"),
    dict(label="chat_history/capabilities",text="ทำอะไรได้บ้าง",                 route="chat_history", intent="chat"),
    dict(label="chat_history/farewell",    text="ขอบคุณนะ ลาก่อนค่ะ",            route="chat_history", intent="farewell"),

    # uni_info route (Milvus search)
    dict(label="uni_info/bathroom",        text="ห้องน้ำอยู่ที่ไหนคะ",           route="uni_info",     intent="info"),
    dict(label="uni_info/zone",            text="ตึก E-12 อยู่โซนไหน",           route="uni_info",     intent="info"),
    dict(label="uni_info/canteen",         text="โรงอาหารอยู่ตึกไหน",            route="uni_info",     intent="info"),
    dict(label="uni_info/navigate",        text="พาฉันไปห้องสมุดหน่อย",          route="uni_info",     intent="navigate"),
    dict(label="uni_info/navigate2",       text="อยากไปห้องน้ำ",                 route="uni_info",     intent="navigate"),

    # curriculum route (Milvus search)
    dict(label="curriculum/credits",       text="หลักสูตร RAI มีกี่หน่วยกิต",   route="curriculum",   intent="info"),
    dict(label="curriculum/year",          text="วิชา Programming เรียนปีไหน",   route="curriculum",   intent="info"),
    dict(label="curriculum/duration",      text="เรียนกี่ปีจบ",                  route="curriculum",   intent="info"),

    # time_table route (Milvus → MySQL)
    dict(label="time_table/monday",        text="วันจันทร์มีวิชาอะไรบ้าง",       route="time_table",   intent="info"),
    dict(label="time_table/subject_day",   text="วิชา Programming เรียนวันไหน",  route="time_table",   intent="info"),
    dict(label="time_table/friday",        text="ตารางเรียนวันศุกร์",             route="time_table",   intent="info"),
]

# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

output_lines = []  # type: ignore

def emit(line: str = ""):
    print(line)
    output_lines.append(re.sub(r"\033\[[0-9;]*m", "", line))


def section(title: str):
    emit()
    emit("=" * 65)
    emit(f"  {title}")
    emit("=" * 65)


def percentile(data, p: int) -> float:
    if not data:
        return 0.0
    sorted_data = sorted(data)
    idx = int(len(sorted_data) * p / 100)
    return sorted_data[min(idx, len(sorted_data) - 1)]


def post_detection(text: str) -> Tuple[bool, float]:
    """Send detection payload, return (success, client_roundtrip_ms)."""
    payload = {
        "timestamp": datetime.utcnow().isoformat(),
        "stt": {"text": text, "language": "th", "duration": 2.0},
        **STUDENT,
    }
    t0 = time.perf_counter()
    try:
        r = requests.post(f"{BASE}/detection", json=payload, timeout=120)
        rt = (time.perf_counter() - t0) * 1000
        return r.status_code == 200, rt
    except Exception as e:
        emit(f"    [connection error] {e}")
        return False, 0.0


def fetch_event(stt_snippet: str, max_wait: float = WAIT_AFTER_REQUEST) -> Optional[dict]:
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            evts = requests.get(f"{BASE}/events", timeout=5).json()
            for evt in reversed(evts):
                if stt_snippet[:15] in (evt.get("stt_raw") or ""):
                    return evt
        except Exception:
            pass
        time.sleep(0.5)
    return None


def fmt_ms(ms: float) -> str:
    return f"{ms:6.0f}ms"


# ─────────────────────────────────────────────────────────────────────
# Main benchmark
# ─────────────────────────────────────────────────────────────────────

def run():
    section("0. SERVER HEALTH")
    try:
        r = requests.get(f"{BASE}/health", timeout=5)
        emit(f"  {PASS if r.status_code == 200 else FAIL}  {r.json()}")
        if r.status_code != 200:
            sys.exit(1)
    except Exception as e:
        emit(f"  {FAIL}  {e}")
        sys.exit(1)

    section(f"1. RUNNING {len(FIXTURES)} FIXTURES")
    emit(f"  Wait per request: {WAIT_AFTER_REQUEST}s   Student: {STUDENT['thai_name']}")
    emit()

    # {route: {stage: [ms values]}}
    by_route = defaultdict(lambda: defaultdict(list))
    all_stages = defaultdict(list)
    missing = 0

    for i, fx in enumerate(FIXTURES, 1):
        label = fx["label"]
        text  = fx["text"]
        route = fx["route"]

        emit(f"  [{i:02d}/{len(FIXTURES)}] {label}")

        ok, client_rt = post_detection(text)
        if not ok:
            emit(f"    {FAIL}  POST failed")
            missing += 1
            continue

        evt = fetch_event(text)
        if not evt:
            emit(f"    {FAIL}  event not found within {WAIT_AFTER_REQUEST}s")
            missing += 1
            continue

        timing = evt.get("timing_ms", {})
        if not timing:
            emit(f"    {INFO}  no timing_ms in event (old event format?)")
            missing += 1
            continue

        g  = timing.get("grammar", 0)
        l  = timing.get("llm", 0)
        ts = timing.get("tts", 0)
        total = timing.get("total", 0)
        intent = evt.get("intent", "?")
        reply  = (evt.get("reply_text") or "")[:60]

        emit(f"    grammar={fmt_ms(g)}  llm={fmt_ms(l)}  tts={fmt_ms(ts)}  total={fmt_ms(total)}  intent={intent!r}")
        emit(f"    reply: {reply!r}")

        for stage, val in [("grammar", g), ("llm", l), ("tts", ts), ("total", total)]:
            if val > 0:
                by_route[route][stage].append(val)
                all_stages[stage].append(val)

    # ── Results by route ──────────────────────────────────────────
    section("2. LATENCY BY RAG ROUTE  (ms)")

    header = f"  {'Route':<30}  {'n':>3}  {'mean':>7}  {'p50':>7}  {'p95':>7}  {'p99':>7}"
    emit(header)
    emit("  " + "-" * 63)

    route_order = ["chat_history", "uni_info", "curriculum", "time_table"]
    for route in route_order:
        if route not in by_route:
            continue
        data = by_route[route]["total"]
        if not data:
            continue
        n    = len(data)
        mean = statistics.mean(data)
        p50  = percentile(data, 50)
        p95  = percentile(data, 95)
        p99  = percentile(data, 99)
        tag  = PASS if p50 < 3000 else FAIL
        emit(f"  {tag} {route:<28}  {n:>3}  {mean:>6.0f}ms  {p50:>6.0f}ms  {p95:>6.0f}ms  {p99:>6.0f}ms")

    # ── Results by stage (all fixtures) ──────────────────────────
    section("3. LATENCY BY STAGE  (all fixtures, ms)")
    emit(f"  {'Stage':<12}  {'n':>3}  {'mean':>7}  {'p50':>7}  {'p95':>7}  {'p99':>7}")
    emit("  " + "-" * 50)

    STAGE_TARGETS = {"grammar": 200, "llm": 3000, "tts": 800, "total": 3000}
    for stage in ["grammar", "llm", "tts", "total"]:
        data = all_stages[stage]
        if not data:
            continue
        n    = len(data)
        mean = statistics.mean(data)
        p50  = percentile(data, 50)
        p95  = percentile(data, 95)
        p99  = percentile(data, 99)
        target = STAGE_TARGETS.get(stage, 9999)
        tag  = PASS if p50 <= target else FAIL
        emit(f"  {tag} {stage:<10}  {n:>3}  {mean:>6.0f}ms  {p50:>6.0f}ms  {p95:>6.0f}ms  {p99:>6.0f}ms   target p50≤{target}ms")

    # ── Summary ───────────────────────────────────────────────────
    section("4. SUMMARY")
    total_data = all_stages["total"]
    if total_data:
        p50_total = percentile(total_data, 50)
        p95_total = percentile(total_data, 95)
        emit(f"  Fixtures run  : {len(FIXTURES) - missing}/{len(FIXTURES)}")
        emit(f"  TTFR p50      : {p50_total:.0f}ms   {'✅ target < 3000ms' if p50_total < 3000 else '❌ ABOVE TARGET'}")
        emit(f"  TTFR p95      : {p95_total:.0f}ms   {'✅ target < 5000ms' if p95_total < 5000 else '❌ ABOVE TARGET'}")
    if missing:
        emit(f"  {FAIL}  {missing} fixture(s) missing timing data — check server logs")
    emit()

    # Save report
    report_path = "docs/benchmark_report.txt"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"KhanomTan Latency Benchmark — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
        f.write("\n".join(output_lines))
    emit(f"  Report saved → {report_path}")
    emit()


if __name__ == "__main__":
    run()
