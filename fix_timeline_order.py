# fix_timeline_order.py — show latest events first in timeline
with open('dashboard/app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Find where _timeline_events is iterated and reverse it
old = 'for _ev in _timeline_events:'
new = 'for _ev in reversed(_timeline_events):'

if old in content:
    content = content.replace(old, new, 1)  # only first occurrence
    print('Fixed — latest first')
else:
    print('ERROR: loop not found')

with open('dashboard/app.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('done')
