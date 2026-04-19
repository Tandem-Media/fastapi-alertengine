# fix_ts_str_final.py
with open('dashboard/app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace ts_str at line 1014 with fmt_ts(h_ts) which is already defined
old = "            f'    {ts_str} &nbsp;·&nbsp; {h_n} samples &nbsp;·&nbsp; anomaly {h_anomaly:.2f}'"
new = "            f'    {fmt_ts(h_ts)} &nbsp;·&nbsp; {h_n} samples &nbsp;·&nbsp; anomaly {h_anomaly:.2f}'"

if old in content:
    content = content.replace(old, new)
    print('Fixed ts_str -> fmt_ts(h_ts)')
else:
    print('ERROR: pattern not found')

with open('dashboard/app.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('done')
