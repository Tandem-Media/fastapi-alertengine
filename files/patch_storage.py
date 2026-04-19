# Script to append incident event functions to storage.py
# Run from the repo root: python patch_storage.py

addition = '''

# ─────────────────────────────────────────────────────────────────────────────
# Incident event store — real append-only timeline backed by Redis ZSET
# ─────────────────────────────────────────────────────────────────────────────
import time as _time

_INCIDENT_TTL_SECONDS = 86_400   # 24 hours
_INCIDENT_MAX_EVENTS  = 500      # cap per service


def write_incident_event(rdb, config, event: dict) -> None:
    """
    Append a real incident event to the Redis ZSET for this service.

    Key  : alertengine:incidents:{service}
    Score: unix timestamp (float)
    Value: JSON-encoded event dict

    Never raises — a Redis outage must not break the eval loop.
    """
    try:
        key   = f"alertengine:incidents:{event.get(\'service\', config.service_name)}"
        score = float(event.get("timestamp", _time.time()))
        value = _json.dumps(event)
        pipe  = rdb.pipeline(transaction=False)
        pipe.zadd(key, {value: score})
        pipe.zremrangebyrank(key, 0, -(_INCIDENT_MAX_EVENTS + 1))
        pipe.expire(key, _INCIDENT_TTL_SECONDS)
        pipe.execute()
    except Exception as exc:
        logger.warning("write_incident_event failed: %s", exc)


def read_incident_events(
    rdb,
    config,
    service: str,
    since: float = 0.0,
    limit: int = 50,
) -> list:
    """
    Read incident events for *service* recorded after *since* (unix ts).
    Returns a list of dicts sorted oldest-first. Returns [] on any error.
    """
    try:
        key  = f"alertengine:incidents:{service}"
        raw  = rdb.zrangebyscore(key, since, "+inf", start=0, num=limit, withscores=True)
        events = []
        for member, score in raw:
            try:
                ev = _json.loads(member)
                ev.setdefault("timestamp", score)
                events.append(ev)
            except Exception:
                continue
        return events
    except Exception as exc:
        logger.warning("read_incident_events failed: %s", exc)
        return []
'''

with open('fastapi_alertengine/storage.py', 'r', encoding='utf-8') as f:
    content = f.read()

if 'write_incident_event' in content:
    print('Already patched — skipping')
else:
    with open('fastapi_alertengine/storage.py', 'a', encoding='utf-8') as f:
        f.write(addition)
    print('storage.py patched OK')
