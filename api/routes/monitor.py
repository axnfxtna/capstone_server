"""
api/routes/monitor.py
======================
Real-time pipeline monitor.

  GET /events          — JSON list of last 50 pipeline events (newest first)
  GET /monitor         — HTML dashboard, auto-refreshes every 3 seconds
"""

import logging
from collections import deque
from datetime import datetime
from typing import Deque, Dict, Any

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()

# ─────────────────────────────────────────────────────────────────────
# In-memory event log (last 50 events, thread-safe enough for asyncio)
# ─────────────────────────────────────────────────────────────────────

_events: Deque[Dict[str, Any]] = deque(maxlen=50)


def log_event(event: Dict[str, Any]) -> None:
    """Call this from receiver.py to record a pipeline event."""
    event.setdefault("received_at", datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))
    _events.appendleft(event)


# ─────────────────────────────────────────────────────────────────────
# JSON API
# ─────────────────────────────────────────────────────────────────────

@router.get("/events")
async def get_events():
    """Return the last 50 pipeline events as JSON (newest first)."""
    return JSONResponse(content=list(_events))


# ─────────────────────────────────────────────────────────────────────
# HTML Dashboard
# ─────────────────────────────────────────────────────────────────────

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>KhanomTan Pipeline Monitor</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: monospace; background: #0d1117; color: #c9d1d9; padding: 20px; }
    h1 { color: #58a6ff; margin-bottom: 6px; font-size: 1.3rem; }
    .subtitle { color: #8b949e; font-size: 0.8rem; margin-bottom: 20px; }
    .event { background: #161b22; border: 1px solid #30363d; border-radius: 6px;
             padding: 12px 16px; margin-bottom: 10px; }
    .event.greeting  { border-left: 3px solid #58a6ff; }
    .event.detection { border-left: 3px solid #3fb950; }
    .event.error     { border-left: 3px solid #f85149; }
    .event.unknown   { border-left: 3px solid #6e7681; }
    .row { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 4px; }
    .label { color: #8b949e; font-size: 0.75rem; min-width: 90px; }
    .value { color: #e6edf3; font-size: 0.85rem; }
    .intent-chat     { color: #58a6ff; }
    .intent-info     { color: #58a6ff; }
    .intent-navigate { color: #d2a679; }
    .intent-farewell { color: #f85149; }
    .tag { display: inline-block; padding: 1px 7px; border-radius: 10px;
           font-size: 0.72rem; font-weight: bold; }
    .tag-registered   { background: #1a3a2a; color: #3fb950; }
    .tag-unregistered { background: #2a1a1a; color: #f85149; }
    .tag-ok    { background: #1a2a3a; color: #58a6ff; }
    .tag-skip  { background: #2a2a1a; color: #e3b341; }
    .tag-err   { background: #2a1a1a; color: #f85149; }
    .tag-noise { background: #1a1a2a; color: #6e7681; }
    .ts { color: #6e7681; font-size: 0.75rem; float: right; }
    .empty { color: #6e7681; text-align: center; padding: 40px; }
    #status { font-size: 0.75rem; color: #6e7681; margin-bottom: 12px; }
  </style>
</head>
<body>
  <h1>KhanomTan Pipeline Monitor</h1>
  <div class="subtitle">Auto-refreshes every 3s &nbsp;|&nbsp; Last 50 events</div>
  <div id="status">Loading...</div>
  <div id="feed"></div>

  <script>
    function intentClass(i) {
      return i ? 'intent-' + i : '';
    }
    function tag(cls, text) {
      return '<span class="tag ' + cls + '">' + text + '</span>';
    }
    function renderEvent(e) {
      const cls = e.endpoint === '/greeting' ? 'greeting'
                : e.endpoint === '/detection' ? 'detection'
                : e.errors && e.errors.length ? 'error' : 'unknown';

      let html = '<div class="event ' + cls + '">';
      html += '<span class="ts">' + (e.received_at || '') + '</span>';
      html += '<div class="row"><span class="label">endpoint</span><span class="value">' + (e.endpoint||'') + '</span></div>';
      html += '<div class="row"><span class="label">person</span><span class="value">' + (e.person_id||'') + ' ' +
              (e.is_registered ? tag('tag-registered','registered') : tag('tag-unregistered','unknown')) + '</span></div>';

      if (e.stt_raw !== undefined) {
        const sttDisplay = e.stt_raw ? e.stt_raw : '<span style="color:#6e7681">(empty)</span>';
        html += '<div class="row"><span class="label">STT raw</span><span class="value">' + sttDisplay + '</span></div>';
      }
      if (e.corrected && e.corrected !== e.stt_raw) {
        html += '<div class="row"><span class="label">corrected</span><span class="value">' + e.corrected + '</span></div>';
      }
      if (e.rag_collection) {
        html += '<div class="row"><span class="label">RAG</span><span class="value" style="color:#8b949e">collection: ' + e.rag_collection + '</span></div>';
      }
      if (e.reply_text) {
        html += '<div class="row"><span class="label">reply</span><span class="value">' + e.reply_text + '</span></div>';
      }
      if (e.intent) {
        let intentStr = '<span class="' + intentClass(e.intent) + '">' + e.intent + '</span>';
        if (e.destination) intentStr += ' → ' + e.destination;
        html += '<div class="row"><span class="label">intent</span><span class="value">' + intentStr + '</span></div>';
      }
      if (e.routed_to && e.routed_to.length) {
        html += '<div class="row"><span class="label">routed to</span><span class="value">' + e.routed_to.join(', ') + '</span></div>';
      }
      if (e.timing_ms) {
        const t = e.timing_ms;
        const bar = (ms, max) => {
          const pct = Math.min(100, ms / max * 100);
          const col = ms > 5000 ? '#f85149' : ms > 2000 ? '#e3b341' : '#3fb950';
          return '<span style="display:inline-block;width:' + pct.toFixed(0) + 'px;height:8px;background:' + col + ';border-radius:2px;vertical-align:middle;margin-right:4px"></span>';
        };
        html += '<div class="row"><span class="label">timing</span><span class="value" style="color:#8b949e">' +
          'grammar ' + bar(t.grammar,2000) + t.grammar + 'ms &nbsp;' +
          'llm ' + bar(t.llm,10000) + t.llm + 'ms &nbsp;' +
          'tts ' + bar(t.tts,5000) + t.tts + 'ms &nbsp;' +
          '<b style="color:#c9d1d9">total ' + t.total + 'ms</b>' +
          '</span></div>';
      }
      if (e.status) {
        const cls2 = e.status === 'ok' ? 'tag-ok' : e.status === 'noise' ? 'tag-noise' : e.status === 'skipped' ? 'tag-skip' : 'tag-err';
        html += '<div class="row"><span class="label">status</span><span class="value">' + tag(cls2, e.status) + '</span></div>';
      }
      if (e.errors && e.errors.length) {
        html += '<div class="row"><span class="label">errors</span><span class="value" style="color:#f85149">' + e.errors.join(', ') + '</span></div>';
      }
      html += '</div>';
      return html;
    }

    async function refresh() {
      try {
        const r = await fetch('/events');
        const events = await r.json();
        document.getElementById('status').textContent =
          events.length + ' events  |  last update: ' + new Date().toLocaleTimeString();
        if (events.length === 0) {
          document.getElementById('feed').innerHTML = '<div class="empty">No events yet — waiting for PI 5...</div>';
        } else {
          document.getElementById('feed').innerHTML = events.map(renderEvent).join('');
        }
      } catch(err) {
        document.getElementById('status').textContent = 'Error fetching events: ' + err;
      }
    }

    refresh();
    setInterval(refresh, 3000);
  </script>
</body>
</html>
"""


@router.get("/monitor", response_class=HTMLResponse)
async def monitor():
    """Live pipeline dashboard."""
    return HTMLResponse(content=_HTML)
