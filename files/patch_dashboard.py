# Patch dashboard/app.py to use real /incidents/timeline endpoint

with open('dashboard/app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add fetch_timeline function after fetch_ingestion
new_fetch = '''
@st.cache_data(ttl=REFRESH_S)
def fetch_timeline(service: str, since: float = 0.0) -> list:
    """Fetch real incident events from the backend append-only timeline."""
    try:
        r = requests.get(
            f"{BASE_URL}/incidents/timeline",
            params={"service": service, "since": since, "limit": 50},
            timeout=5,
        )
        r.raise_for_status()
        return r.json().get("events", [])
    except Exception:
        return []

'''

if 'fetch_timeline' in content:
    print('fetch_timeline already present')
else:
    # Insert after fetch_ingestion function
    anchor = '@st.cache_data(ttl=REFRESH_S)\ndef fetch_ingestion()'
    if anchor in content:
        # Find end of fetch_ingestion function
        idx = content.find(anchor)
        # Find the next @st.cache_data or def after it
        next_section = content.find('\n@', idx + len(anchor))
        if next_section == -1:
            next_section = content.find('\ndef ', idx + len(anchor))
        content = content[:next_section] + '\n' + new_fetch + content[next_section:]
        print('fetch_timeline injected')
    else:
        print('ERROR: could not find fetch_ingestion anchor')

# 2. Replace the synthetic timeline builder call with real data
# Find where build_incident_timeline is called and replace with fetch_timeline
old_timeline_call = 'timeline = build_incident_timeline(ts_df, ep_df, health)'
new_timeline_call = '''# ── Real incident timeline from backend ──────────────────────────────────────
_raw_timeline = fetch_timeline(service)
# Fallback to synthetic timeline if backend has no events yet (memory mode / new deploy)
if _raw_timeline:
    timeline = _raw_timeline
else:
    timeline = build_incident_timeline(ts_df, ep_df, health)'''

if 'Real incident timeline from backend' in content:
    print('Timeline call already patched')
elif old_timeline_call in content:
    content = content.replace(old_timeline_call, new_timeline_call)
    print('Timeline call patched')
else:
    print('ERROR: could not find timeline call')

# 3. Update the timeline rendering to handle both dict formats
# Real events have 'timestamp' (float) + 'message', synthetic have 'ts' (Timestamp) + 'message'
old_render = "                ts_str = ev[\"ts\"].strftime(\"%H:%M\") if hasattr(ev[\"ts\"], \"strftime\") else str(ev[\"ts\"])[:16]"
new_render = """                # Handle both real events (timestamp: float) and synthetic (ts: Timestamp)
                if "timestamp" in ev:
                    from datetime import datetime as _dt
                    ts_str = _dt.fromtimestamp(float(ev["timestamp"])).strftime("%H:%M:%S")
                elif "ts" in ev:
                    ts_str = ev["ts"].strftime("%H:%M") if hasattr(ev["ts"], "strftime") else str(ev["ts"])[:16]
                else:
                    ts_str = "—\""""

if 'Handle both real events' in content:
    print('Render already patched')
elif old_render in content:
    content = content.replace(old_render, new_render)
    print('Timeline render patched')
else:
    print('ERROR: could not find render line')

with open('dashboard/app.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('dashboard/app.py written')
