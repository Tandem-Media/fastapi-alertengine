# patch_engine_v2.py — fixed anchors
# Run from repo root: python patch_engine_v2.py

with open('fastapi_alertengine/engine.py', 'r', encoding='utf-8') as f:
    content = f.read()

changed = False

# 1. Add /incidents/timeline endpoint before /__alertengine/status
old_ep = '        @app.get("/__alertengine/status", include_in_schema=False)'
new_ep  = '''        @app.get("/incidents/timeline", include_in_schema=False)
        def _it(service: Optional[str] = None, since: float = 0.0, limit: int = 50):
            if engine._memory_mode:
                return {"events": [], "mode": "memory", "note": "Timeline requires Redis"}
            return {"events": read_incident_events(
                engine.redis, engine.config,
                service or engine.config.service_name,
                since=since, limit=limit,
            )}

        @app.get("/__alertengine/status", include_in_schema=False)'''

if '/incidents/timeline' in content:
    print('Endpoint already present')
elif old_ep in content:
    content = content.replace(old_ep, new_ep)
    print('/incidents/timeline endpoint added')
    changed = True
else:
    print('ERROR: could not find /__alertengine/status anchor')

if changed:
    with open('fastapi_alertengine/engine.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('engine.py written')
else:
    print('No changes made')
