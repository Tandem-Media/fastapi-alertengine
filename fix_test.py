f = open('tests/test_alertengine.py', 'r', encoding='utf-8')
content = f.read()
f.close()

bad  = '       # Force memory mode by making Redis unavailable\n       with patch("redis.Redis.from_url") as mock_from_url:\n            mock_from_url.return_value.ping.side_effect = ConnectionError("no redis")\n            engine.start(app)'
good = '        # Force memory mode by making Redis unavailable\n        with patch("redis.Redis.from_url") as mock_from_url:\n            mock_from_url.return_value.ping.side_effect = ConnectionError("no redis")\n            engine.start(app)'

fixed = content.replace(bad, good)
print('Changed:', fixed != content)

f = open('tests/test_alertengine.py', 'w', encoding='utf-8')
f.write(fixed)
f.close()
print('Done.')
