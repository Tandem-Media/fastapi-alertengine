# fix_now_ts.py
with open('dashboard/app.py', 'r', encoding='utf-8') as f:
    content = f.read()

old = '''f'  <span class="ae-timeline-ts">{__import__("datetime").datetime.fromtimestamp(float(_ev["timestamp"])).strftime("%H:%M:%S") if _ev.get("timestamp") else _ev.get("ts_str", "—")}</span>'''

new = '''f'  <span class="ae-timeline-ts">{__import__("datetime").datetime.fromtimestamp(float(_ev["timestamp"])).strftime("%H:%M:%S") if _ev.get("timestamp") and str(_ev.get("timestamp","")).replace(".","").isdigit() else (_ev.get("ts_str") or _ev.get("timestamp") or "—")}</span>'''

if old in content:
    content = content.replace(old, new)
    print('Fixed')
else:
    print('ERROR: pattern not found')

with open('dashboard/app.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('done')
