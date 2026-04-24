# load.py
"""
Load generator for the AlertEngine demo.

Sends steady burst traffic to /api/payments/process so that
AlertEngine has enough data to compute P95 latency, error rate,
and health scores.

Run:
    python load.py

Options (edit at top of file):
    BASE_URL     — target server
    CONCURRENCY  — requests per burst
    INTERVAL_S   — seconds between bursts

Pre-warm: run for 15–20 seconds before triggering failure so that
AlertEngine has a baseline and the drop looks dramatic.
"""

import asyncio
import sys
import time

import httpx

BASE_URL    = "http://localhost:8000"
CONCURRENCY = 15       # requests per burst
INTERVAL_S  = 0.3      # seconds between bursts
ENDPOINT    = "/api/payments/process"

stats = {"ok": 0, "err": 0, "total": 0, "start": time.time()}


async def hit(client: httpx.AsyncClient) -> None:
    try:
        r = await client.get(f"{BASE_URL}{ENDPOINT}", timeout=5)
        body = r.json()
        stats["total"] += 1
        if body.get("status") == "success":
            stats["ok"] += 1
        else:
            stats["err"] += 1
    except Exception:
        stats["total"] += 1
        stats["err"] += 1


def _print_stats() -> None:
    elapsed  = time.time() - stats["start"]
    rate     = stats["total"] / elapsed if elapsed > 0 else 0
    err_rate = stats["err"] / stats["total"] if stats["total"] > 0 else 0
    sys.stdout.write(
        f"\r  Requests: {stats['total']:>5}  |  "
        f"OK: {stats['ok']:>5}  |  "
        f"Errors: {stats['err']:>4}  |  "
        f"Error rate: {err_rate:.1%}  |  "
        f"RPS: {rate:.1f}   "
    )
    sys.stdout.flush()


async def main() -> None:
    print(f"⚡ Load generator → {BASE_URL}{ENDPOINT}")
    print(f"   Concurrency: {CONCURRENCY} req/burst  |  Interval: {INTERVAL_S}s")
    print(f"   Pre-warm for 15–20s before triggering failure.\n")

    async with httpx.AsyncClient() as client:
        burst_n = 0
        while True:
            burst_n += 1
            tasks = [hit(client) for _ in range(CONCURRENCY)]
            await asyncio.gather(*tasks)
            _print_stats()
            await asyncio.sleep(INTERVAL_S)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        elapsed = time.time() - stats["start"]
        print(f"\n\nStopped. Total: {stats['total']} requests in {elapsed:.0f}s")
