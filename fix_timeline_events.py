# fix_timeline_events.py
# Run from repo root: python fix_timeline_events.py

with open('dashboard/app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Find the patched timeline block and add _timeline_events assignment
old = '''        # ── Real incident timeline from backend ───────────────────────────────
        _raw_timeline = fetch_timeline(service)
        # Fallback to synthetic if no real events yet (memory mode / new deploy)
        if _raw_timeline:
            timeline = _raw_timeline
        else:'''

new = '''        # ── Real incident timeline from backend ───────────────────────────────
        _raw_timeline = fetch_timeline(service)
        # Fallback to synthetic if no real events yet (memory mode / new deploy)
        if _raw_timeline:
            timeline = _raw_timeline
        else:'''

# The real fix — find _timeline_events usage and make sure it's assigned
# Add _timeline_events = timeline after the timeline block
if '_timeline_events' in content and 'Real incident timeline from backend' in content:
    # Find the end of the patched block and add assignment
    anchor = '# ── Real incident timeline from backend'
    lines = content.split('\n')
    for i, line in enumerate(lines):
        if anchor in line:
            # Find the end of this block (next non-indented or different block)
            for j in range(i+1, min(i+15, len(lines))):
                if lines[j].strip() and not lines[j].strip().startswith('#') and 'timeline' in lines[j] and '=' in lines[j]:
                    last_timeline_line = j
            # Insert _timeline_events = timeline after the block
            indent = ' ' * (len(lines[i]) - len(lines[i].lstrip()))
            insert_line = f'{indent}_timeline_events = timeline if isinstance(timeline, list) else []'
            # Find where to insert — after the last timeline assignment in the block
            insert_after = last_timeline_line
            lines.insert(insert_after + 1, insert_line)
            content = '\n'.join(lines)
            print(f'Inserted _timeline_events assignment after line {insert_after+1}')
            break

    with open('dashboard/app.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('dashboard/app.py written')
else:
    # Simpler fix: just replace all uses of _timeline_events with timeline
    if '_timeline_events' in content:
        content = content.replace('_timeline_events', 'timeline')
        with open('dashboard/app.py', 'w', encoding='utf-8') as f:
            f.write(content)
        print('Replaced _timeline_events with timeline throughout')
    else:
        print('No fix needed')
