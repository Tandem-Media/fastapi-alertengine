# fix_ts_labels.py
# Run from repo root: python fix_ts_labels.py

from datetime import datetime

with open('dashboard/app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Fix line 928 (0-indexed: 927)
# Replace raw timestamp with formatted version
old_928 = '                    f\'  <span class="ae-timeline-ts">{_ev.get("timestamp", "—")}</span>\''
new_928 = '''                    f\'  <span class="ae-timeline-ts">{__import__("datetime").datetime.fromtimestamp(float(_ev["timestamp"])).strftime("%H:%M:%S") if _ev.get("timestamp") else _ev.get("ts_str", "—")}</span>\''''

# Fix line 1131 (0-indexed: 1130)
old_1131 = '                        f"  {_ev.get(\'timestamp\',\'—\')}  [{_ev.get(\'event_type\',\'\')}]  "'
new_1131 = '                        f"  {__import__(\'datetime\').datetime.fromtimestamp(float(_ev[\'timestamp\'])).strftime(\'%H:%M:%S\') if _ev.get(\'timestamp\') else \'—\'}  [{_ev.get(\'event_type\',\'\')}]  "'

content = ''.join(lines)

if old_928.strip() in content:
    content = content.replace(old_928, new_928)
    print('Fixed line 928')
else:
    print('ERROR: line 928 pattern not matched')

if old_1131.strip() in content:
    content = content.replace(old_1131, new_1131)
    print('Fixed line 1131')
else:
    print('ERROR: line 1131 pattern not matched')

with open('dashboard/app.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('done')
