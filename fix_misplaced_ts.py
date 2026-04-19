# fix_misplaced_ts.py
with open('dashboard/app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Remove lines 975-982 (0-indexed: 974-981) — the misplaced ev block
# These are inside the Active Incident section where ev is not defined
remove_block = '''        # Handle both real events (timestamp: float) and synthetic (ts: Timestamp)
        if "timestamp" in ev:
            from datetime import datetime as _dt
            ts_str = _dt.fromtimestamp(float(ev["timestamp"])).strftime("%H:%M:%S")
        elif "ts" in ev:
            ts_str = ev["ts"].strftime("%H:%M") if hasattr(ev["ts"], "strftime") else str(ev["ts"])[:16]
        else:
            ts_str = "—"
'''

content = ''.join(lines)

if remove_block in content:
    content = content.replace(remove_block, '')
    print('Removed misplaced ev block')
else:
    # Try without trailing newline
    remove_block2 = remove_block.rstrip('\n')
    if remove_block2 in content:
        content = content.replace(remove_block2, '')
        print('Removed misplaced ev block (v2)')
    else:
        print('ERROR: block not found — showing lines 974-983:')
        for i in range(973, 983):
            print(f'{i+1}: {repr(lines[i].rstrip())}')

with open('dashboard/app.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('done')
