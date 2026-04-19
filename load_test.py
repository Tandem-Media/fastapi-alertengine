import requests
import random
import time
import threading

endpoints = [
    "/pay",
    "/pay",
    "/pay",
    "/pay/slow",
    "/pay/critical",
]

def worker(worker_id):
    for i in range(300):
        endpoint = random.choice(endpoints)
        url = f"http://localhost:8000{endpoint}"
        try:
            requests.get(url, timeout=15)
        except:
            pass
        time.sleep(random.uniform(0.01, 0.1))
    print(f"Worker {worker_id} done")

threads = []
for i in range(5):  # 5 concurrent users
    t = threading.Thread(target=worker, args=(i,))
    t.start()
    threads.append(t)

for t in threads:
    t.join()

print("Load test complete.")