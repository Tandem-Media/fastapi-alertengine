# fastapi_alertengine/actions/incident_replay.py
"""
v1.6 — Incident Replay Mode

Reconstructs request lifecycle from the incident timeline and Redis Stream.

replay_incident(trace_id, rdb, config) returns:
- All incident events matching the trace_id window
- Raw request events from the Redis Stream within the incident timeframe
- A reconstructed timeline showing: first alert → peak → resolution (if any)
- Summary: duration, peak P95, peak error rate, resolution status

Design:
- Read-only — no side effects
- Works on any Redis 5.0+ instance
- Degrades gracefully: returns partial data when some events are missing
"""
from __future__ import annotations

import json
import logging
import math
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_REPLAY_STREAM_WINDOW_S = 300   # ±5 minutes around incident start


def replay_incident(
    trace_id:   str,
    rdb,
    config,
    window_s:   int = _REPLAY_STREAM_WINDOW_S,
) -> dict:
    """
    Reconstruct the request lifecycle for an incident identified by trace_id.

    trace_id can be:
    - A JTI from an action token
    - An incident_id from an ActionSuggestion
    - A trace_id from X-Request-ID headers (stored in stream events)

    Returns a ReplayResult dict with:
        trace_id:        the queried ID
        found:           bool — whether any matching events were found
        incident_events: list of matching incident timeline entries
        stream_events:   list of raw request events from the time window
        timeline:        reconstructed event sequence (oldest first)
        summary:         {duration_s, peak_p95_ms, peak_error_rate, resolved}
    """
    result = {
        "trace_id":        trace_id,
        "found":           False,
        "incident_events": [],
        "stream_events":   [],
        "timeline":        [],
        "summary":         {},
    }

    # ── 1. Find incident events matching this trace_id ─────────────────────
    incident_events = _find_incident_events(trace_id, rdb, config)
    if not incident_events:
        return result

    result["found"]           = True
    result["incident_events"] = incident_events

    # ── 2. Determine time window from first incident event ─────────────────
    first_ts = min(float(e.get("timestamp", 0)) for e in incident_events)
    last_ts  = max(float(e.get("timestamp", 0)) for e in incident_events)
    window_start = first_ts - 30          # 30s before first alert
    window_end   = last_ts  + window_s    # extend forward for resolution

    # ── 3. Pull raw request events from Redis Stream in that window ────────
    stream_events = _fetch_stream_window(rdb, config, window_start, window_end)
    result["stream_events"] = stream_events

    # ── 4. Reconstruct timeline ────────────────────────────────────────────
    timeline = _build_timeline(incident_events, stream_events, window_start, window_end)
    result["timeline"] = timeline

    # ── 5. Compute summary ─────────────────────────────────────────────────
    result["summary"] = _compute_summary(incident_events, stream_events, first_ts, last_ts)

    return result


def _find_incident_events(trace_id: str, rdb, config) -> List[dict]:
    """
    Search incident ZSET for events referencing this trace_id.
    Checks: incident_id field, jti field, and trace_id field.
    """
    try:
        key = f"alertengine:incidents:{config.service_name}"
        raw = rdb.zrangebyscore(key, 0, "+inf", withscores=True)
        matches = []
        for member, score in raw:
            try:
                ev = json.loads(member)
                ev.setdefault("timestamp", score)
                # Match against multiple ID fields
                if (ev.get("incident_id") == trace_id
                        or ev.get("jti")        == trace_id
                        or ev.get("trace_id")   == trace_id
                        # Also match by time proximity if trace_id looks like a timestamp
                        or _ts_match(trace_id, score)):
                    matches.append(ev)
            except Exception:
                continue
        return matches
    except Exception as exc:
        logger.warning("replay _find_incident_events failed: %s", exc)
        return []


def _ts_match(trace_id: str, score: float) -> bool:
    """Check if trace_id looks like a unix timestamp close to score."""
    try:
        ts = float(trace_id)
        return abs(ts - score) < 1.0   # within 1 second
    except (ValueError, TypeError):
        return False


def _fetch_stream_window(rdb, config, start_ts: float, end_ts: float) -> List[dict]:
    """
    Fetch raw request events from Redis Stream within [start_ts, end_ts].
    Uses XRANGE with millisecond timestamp IDs.
    """
    try:
        start_ms = f"{int(start_ts * 1000)}-0"
        end_ms   = f"{int(end_ts   * 1000)}-0"
        raw = rdb.xrange(config.stream_key, start_ms, end_ms, count=500)
        events = []
        for stream_id, fields in raw:
            try:
                events.append({
                    "stream_id":    stream_id,
                    "timestamp":    int(stream_id.split("-")[0]) / 1000.0,
                    "path":         fields.get("path", ""),
                    "route":        fields.get("route_template") or fields.get("path", ""),
                    "method":       fields.get("method", ""),
                    "status_code":  int(fields.get("status", 0)),
                    "latency_ms":   float(fields.get("latency_ms", 0)),
                    "trace_id":     fields.get("trace_id"),
                    "service_name": fields.get("service_name", ""),
                })
            except Exception:
                continue
        return events
    except Exception as exc:
        logger.warning("replay _fetch_stream_window failed: %s", exc)
        return []


def _build_timeline(
    incident_events: List[dict],
    stream_events:   List[dict],
    window_start:    float,
    window_end:      float,
) -> List[dict]:
    """
    Merge incident events and stream stats into a chronological timeline.
    Buckets stream events by 30-second windows for readability.
    """
    timeline = []

    # Add incident events as-is
    for ev in sorted(incident_events, key=lambda x: float(x.get("timestamp", 0))):
        timeline.append({
            "ts":    float(ev.get("timestamp", 0)),
            "type":  "INCIDENT",
            "event": ev.get("event_type", "ALERT"),
            "sev":   ev.get("severity", ""),
            "msg":   ev.get("message", ""),
            "p95":   ev.get("metrics", {}).get("p95_ms"),
        })

    # Bucket stream events into 30s windows
    bucket_size = 30.0
    buckets: Dict[int, List[dict]] = {}
    for ev in stream_events:
        b = int(ev["timestamp"] // bucket_size)
        buckets.setdefault(b, []).append(ev)

    for bucket_ts, evs in sorted(buckets.items()):
        lats   = [e["latency_ms"] for e in evs if e["latency_ms"] > 0]
        errors = sum(1 for e in evs if e["status_code"] >= 500)
        p95    = _p95(lats) if lats else 0.0
        timeline.append({
            "ts":          bucket_ts * bucket_size,
            "type":        "TRAFFIC",
            "requests":    len(evs),
            "p95_ms":      round(p95, 1),
            "error_count": errors,
            "error_rate":  round(errors / len(evs), 4) if evs else 0.0,
        })

    return sorted(timeline, key=lambda x: x["ts"])


def _p95(values: List[float]) -> float:
    if not values: return 0.0
    s   = sorted(values)
    idx = min(int(math.ceil(len(s) * 0.95)) - 1, len(s) - 1)
    return s[max(idx, 0)]


def _compute_summary(
    incident_events: List[dict],
    stream_events:   List[dict],
    first_ts:        float,
    last_ts:         float,
) -> dict:
    """Compute replay summary statistics."""
    duration_s = last_ts - first_ts if last_ts > first_ts else 0.0

    all_lats = [e["latency_ms"] for e in stream_events if e.get("latency_ms", 0) > 0]
    peak_p95 = _p95(all_lats) if all_lats else 0.0

    errors    = sum(1 for e in stream_events if e.get("status_code", 0) >= 500)
    peak_err  = errors / len(stream_events) if stream_events else 0.0

    # Resolved = last incident event has status "ok" or is followed by no more critical events
    severities  = [e.get("severity", "") for e in incident_events]
    last_sev    = severities[-1] if severities else ""
    resolved    = last_sev not in ("critical", "warning")

    return {
        "duration_s":      round(duration_s, 1),
        "peak_p95_ms":     round(peak_p95, 1),
        "peak_error_rate": round(peak_err, 4),
        "total_requests":  len(stream_events),
        "incident_events": len(incident_events),
        "resolved":        resolved,
        "first_alert_ts":  first_ts,
        "last_event_ts":   last_ts,
    }
