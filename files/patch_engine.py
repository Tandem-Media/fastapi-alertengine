# Script to wire write_incident_event into engine.evaluate()
# Run from the repo root: python patch_engine.py

with open('fastapi_alertengine/engine.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add import of write_incident_event and read_incident_events
old_import = 'from .storage import flush_aggregates, read_aggregates, read_metrics, write_batch'
new_import  = 'from .storage import flush_aggregates, read_aggregates, read_metrics, write_batch, write_incident_event, read_incident_events'

if new_import in content:
    print('Import already patched')
else:
    content = content.replace(old_import, new_import)
    print('Import patched')

# 2. Write incident event at end of evaluate() — just before final return
# Find the return statement in evaluate() and insert before it
old_return = '        return {"status": status, "service_name": self.config.service_name, "instance_id": self.config.instance_id'
new_return  = '''        # ── Write to incident timeline (Redis ZSET) if degraded ────────────
        if status in ("warning", "critical") and not self._memory_mode:
            import time as _t
            for alert in alerts:
                write_incident_event(self.redis, self.config, {
                    "timestamp": float(ts),
                    "service":   self.config.service_name,
                    "instance":  self.config.instance_id,
                    "status":    status,
                    "event_type": alert.get("type", "ALERT"),
                    "severity":  alert.get("severity", status),
                    "message":   alert.get("message", ""),
                    "metrics": {
                        "p95_ms":     round(overall_p95, 1),
                        "error_rate": round(error_rate, 4),
                        "samples":    len(events),
                    },
                })

        return {"status": status, "service_name": self.config.service_name, "instance_id": self.config.instance_id'''

if 'Write to incident timeline' in content:
    print('Evaluate already patched')
else:
    if old_return in content:
        content = content.replace(old_return, new_return)
        print('evaluate() patched')
    else:
        print('ERROR: could not find return statement in evaluate()')

# 3. Register /incidents/timeline endpoint in start()
old_endpoint = "        @app.get('/__alertengine/status', include_in_schema=False)"
new_endpoint  = """        @app.get('/incidents/timeline', include_in_schema=False)
        def _it(service: Optional[str] = None, since: float = 0.0, limit: int = 50):
            if engine._memory_mode:
                return {"events": [], "mode": "memory", "note": "Timeline requires Redis"}
            return {"events": read_incident_events(
                engine.redis, engine.config,
                service or engine.config.service_name,
                since=since, limit=limit,
            )}

        @app.get('/__alertengine/status', include_in_schema=False)"""

if '/incidents/timeline' in content:
    print('Endpoint already patched')
else:
    if old_endpoint in content:
        content = content.replace(old_endpoint, new_endpoint)
        print('/incidents/timeline endpoint added')
    else:
        print('ERROR: could not find status endpoint anchor')

with open('fastapi_alertengine/engine.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('engine.py written')
