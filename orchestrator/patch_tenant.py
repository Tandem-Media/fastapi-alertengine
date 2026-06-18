"""
One-off patch for tenants created before secret/shadow_mode existed.
Run from inside orchestrator/ with REDIS_URL pointed at the public
Railway Redis connection string.
"""
import os
os.environ['REDIS_URL'] = redis://default:eOctWyUYOtYWucQJdgbyeFMTlvGJOPbs@thomas.proxy.rlwy.net:43403

import secrets
import time
from tenants import get_tenant, save_tenant

t = get_tenant('ec4ea152')
if not t:
    print("Tenant not found — check the ID and REDIS_URL.")
else:
    t['secret'] = secrets.token_hex(32)
    t['shadow_mode'] = True
    t['shadow_enabled_at'] = time.time()
    t['shadow_disabled_at'] = None
    save_tenant(t)
    print("Patched. Secret (copy now, shown once):", t['secret'])
