# patch_dashboard_v2.py — fixed anchors based on actual dashboard structure
# Run from repo root: python patch_dashboard_v2.py

with open('dashboard/app.py', 'r', encoding='utf-8') as f:
    content = f.read()

changed = False

# 1. Add fetch_timeline if not present
if 'def fetch_timeline' not in content:
    fetch_fn = '''
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
    # Insert before the helpers/sidebar section
    anchor = '# ── Helpers'
    if anchor not in content:
        anchor = '# ── Sidebar'
    if anchor not in content:
        anchor = '# ── Fetch data'
    if anchor in content:
        content = content.replace(anchor, fetch_fn + anchor, 1)
        print('fetch_timeline injected')
        changed = True
    else:
        print('ERROR: could not find anchor for fetch_timeline')
else:
    print('fetch_timeline already present')

# 2. Find how the timeline is built in the dashboard and wire in real data
# Look for build_incident_timeline call
if 'Real incident timeline from backend' not in content:
    # Find the line that calls build_incident_timeline
    lines = content.split('\n')
    for i, line in enumerate(lines):
        if 'build_incident_timeline' in line and '=' in line and 'def ' not in line:
            old_line = lines[i]
            indent = len(old_line) - len(old_line.lstrip())
            sp = ' ' * indent
            new_lines = [
                f'{sp}# ── Real incident timeline from backend ───────────────────────────────',
                f'{sp}_raw_timeline = fetch_timeline(service)',
                f'{sp}# Fallback to synthetic if no real events yet (memory mode / new deploy)',
                f'{sp}if _raw_timeline:',
                f'{sp}    timeline = _raw_timeline',
                f'{sp}else:',
                f'{sp}    {old_line.lstrip()}',
            ]
            lines[i] = '\n'.join(new_lines)
            content = '\n'.join(lines)
            print(f'Timeline call patched at line {i+1}')
            changed = True
            break
    else:
        print('ERROR: could not find build_incident_timeline call')

# 3. Fix timestamp rendering to handle real events (float timestamp)
if 'Handle both real events' not in content:
    lines = content.split('\n')
    for i, line in enumerate(lines):
        if 'ts_str' in line and ('strftime' in line or 'ts' in line) and 'timestamp' not in line:
            old_line = lines[i]
            indent = len(old_line) - len(old_line.lstrip())
            sp = ' ' * indent
            new_block = (
                f'{sp}# Handle both real events (timestamp: float) and synthetic (ts: Timestamp)\n'
                f'{sp}if "timestamp" in ev:\n'
                f'{sp}    from datetime import datetime as _dt\n'
                f'{sp}    ts_str = _dt.fromtimestamp(float(ev["timestamp"])).strftime("%H:%M:%S")\n'
                f'{sp}elif "ts" in ev:\n'
                f'{sp}    ts_str = ev["ts"].strftime("%H:%M") if hasattr(ev["ts"], "strftime") else str(ev["ts"])[:16]\n'
                f'{sp}else:\n'
                f'{sp}    ts_str = "—"'
            )
            lines[i] = new_block
            content = '\n'.join(lines)
            print(f'Timestamp render patched at line {i+1}')
            changed = True
            break
    else:
        print('WARNING: could not find ts_str render line — may already be correct')

if changed:
    with open('dashboard/app.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('dashboard/app.py written')
else:
    print('No changes needed')
