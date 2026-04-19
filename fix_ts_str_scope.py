# fix_ts_str_scope.py
with open('dashboard/app.py', 'r', encoding='utf-8') as f:
    content = f.read()

lines = content.split('\n')

# Find line 1014 area and show context
for i in range(1005, min(1025, len(lines))):
    print(f'{i+1}: {repr(lines[i])}')
