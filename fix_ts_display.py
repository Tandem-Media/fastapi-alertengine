# fix_ts_display.py
# Run from repo root: python fix_ts_display.py

with open('dashboard/app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# The timeline card shows the timestamp field directly in the label
# Find the block that renders timeline events and fix the ts_str logic

# Replace the entire ts_str assignment block with correct version
old = '''                # Handle both real events (timestamp: float) and synthetic (ts: Timestamp)
                if "timestamp" in ev:
                    from datetime import datetime as _dt
                    ts_str = _dt.fromtimestamp(float(ev["timestamp"])).strftime("%H:%M:%S")
                elif "ts" in ev:
                    ts_str = ev["ts"].strftime("%H:%M") if hasattr(ev["ts"], "strftime") else str(ev["ts"])[:16]
                else:
                    ts_str = "—"'''

if old in content:
    print('Render block already correct — issue is elsewhere')
else:
    # Try to find whatever ts_str assignment exists and replace it
    import re
    # Find lines with ts_str =
    lines = content.split('\n')
    for i, line in enumerate(lines):
        if 'ts_str' in line and '=' in line and 'def ' not in line:
            print(f'Line {i+1}: {repr(line)}')
