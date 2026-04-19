with open('dashboard/app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Show lines 965-990
for i in range(964, min(990, len(lines))):
    print(f'{i+1}: {repr(lines[i].rstrip())}')
