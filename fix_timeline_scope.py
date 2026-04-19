# fix_timeline_scope.py
with open('dashboard/app.py', 'r', encoding='utf-8') as f:
    content = f.read()

old = '''        # ── Real incident timeline from backend ───────────────────────────────
        _raw_timeline = fetch_timeline(service)
        # Fallback to synthetic if no real events yet (memory mode / new deploy)
        if _raw_timeline:
            timeline = _raw_timeline
        else:
            _timeline_events = build_incident_timeline(ts_df, ep_df, health)
_timeline_events = timeline if isinstance(timeline, list) else []'''

new = '''        # ── Real incident timeline from backend ───────────────────────────────
        _raw_timeline = fetch_timeline(service)
        # Fallback to synthetic if no real events yet (memory mode / new deploy)
        if _raw_timeline:
            _timeline_events = _raw_timeline
        else:
            _timeline_events = build_incident_timeline(ts_df, ep_df, health)'''

if old in content:
    content = content.replace(old, new)
    print('Fixed')
else:
    # Try a more flexible search
    lines = content.split('\n')
    for i, line in enumerate(lines):
        if '_timeline_events = timeline if isinstance(timeline, list) else []' in line:
            lines[i] = ''
            print(f'Removed orphaned line {i+1}')
        if 'timeline = _raw_timeline' in line:
            lines[i] = lines[i].replace('timeline = _raw_timeline', '_timeline_events = _raw_timeline')
            print(f'Fixed assignment at line {i+1}')
        if '_timeline_events = build_incident_timeline' in line and 'else:' not in lines[i-1]:
            print(f'build_incident_timeline line {i+1}: {repr(line)}')
    content = '\n'.join(lines)
    print('Flexible fix applied')

with open('dashboard/app.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('done')
