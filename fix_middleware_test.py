# fix_middleware_test.py
# Run from repo root: python fix_middleware_test.py

with open('tests/test_alertengine.py', 'r', encoding='utf-8') as f:
    content = f.read()

old = '        assert engine._queue.qsize() >= 1, "middleware should have enqueued a metric"'
new = '        assert engine._stats["enqueued"] >= 1, "middleware should have enqueued a metric"'

if new in content:
    print('Already fixed')
elif old in content:
    content = content.replace(old, new)
    with open('tests/test_alertengine.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('Fixed')
else:
    print('ERROR: pattern not found')
