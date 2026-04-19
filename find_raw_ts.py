# find_raw_ts.py - find where raw timestamp shows in the card
with open('dashboard/app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if 'timestamp' in line.lower() and ('st.' in line or 'html' in line.lower() or 'f"' in line or "f'" in line):
        print(f'{i+1}: {repr(line.rstrip())}')
