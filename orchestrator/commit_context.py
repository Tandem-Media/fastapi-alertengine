# orchestrator/commit_context.py
"""
Diff-in-Pocket: Git commit context for incident diagnosis.

When a latency spike occurs, the AI diagnostic should know what changed
recently in the codebase — not just what the metrics look like.

This module:
1. Stores recent git commits per tenant (via webhook or API polling)
2. Maps incident timestamps to the closest recent commit
3. Injects commit context into Claude prompts:
   "Database pool is starving. This began 3 minutes after commit
    a1b2c3d changed the checkout query isolation level."

Setup options:
  A. GitHub webhook — POST /commits/webhook on every push
  B. Manual push — POST /commits/{tenant_id} with commit data
  C. GitHub API polling — auto-fetch on incident open (requires GITHUB_TOKEN)

Redis key: orchestrator:commits:{tenant_id} (sorted set, scored by timestamp)
TTL: 7 days
"""

import json
import logging
import os
import time
from typing import Optional

import httpx
import redis

logger = logging.getLogger("orchestrator.commit_context")

COMMITS_TTL     = 86400 * 7   # 7 days
MAX_COMMITS     = 50           # per tenant
INCIDENT_WINDOW = 600          # look back 10 minutes before incident


def _redis():
    global _redis_client
    if _redis_client is None:
        url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        _redis_client = redis.Redis.from_url(url, decode_responses=True)
    return _redis_client

_redis_client = None


# ── Commit storage ─────────────────────────────────────────────────────────────

def store_commit(
    tenant_id: str,
    sha: str,
    message: str,
    author: str,
    timestamp: float,
    files_changed: Optional[list] = None,
    additions: int = 0,
    deletions: int = 0,
    repo: str = "",
    branch: str = "main",
) -> None:
    """
    Store a commit for a tenant.
    Called from the webhook endpoint or manual push.
    """
    try:
        key = f"orchestrator:commits:{tenant_id}"
        entry = json.dumps({
            "sha":           sha[:7],
            "sha_full":      sha,
            "message":       message[:200],
            "author":        author,
            "timestamp":     timestamp,
            "files_changed": files_changed or [],
            "additions":     additions,
            "deletions":     deletions,
            "repo":          repo,
            "branch":        branch,
        })
        r = _redis()
        r.zadd(key, {entry: timestamp})
        r.zremrangebyrank(key, 0, -(MAX_COMMITS + 1))  # keep newest N
        r.expire(key, COMMITS_TTL)
        logger.info(
            "Stored commit %s for tenant %s: %s",
            sha[:7], tenant_id, message[:60],
        )
    except Exception as e:
        logger.debug("store_commit failed: %s", e)


def get_recent_commits(tenant_id: str, since: float, limit: int = 5) -> list:
    """
    Return commits within INCIDENT_WINDOW seconds before `since`.
    Sorted newest-first.
    """
    try:
        key       = f"orchestrator:commits:{tenant_id}"
        min_score = since - INCIDENT_WINDOW
        max_score = since + 60  # small buffer after incident start
        raw       = _redis().zrangebyscore(
            key, min_score, max_score,
            withscores=False,
        )
        commits = []
        for r in raw:
            try:
                commits.append(json.loads(r))
            except json.JSONDecodeError:
                continue
        return sorted(commits, key=lambda c: c["timestamp"], reverse=True)[:limit]
    except Exception as e:
        logger.debug("get_recent_commits failed: %s", e)
        return []


def commit_context(tenant_id: str, incident_started_at: float) -> str:
    """
    Returns formatted commit context string for the Claude prompt.
    Returns empty string if no recent commits found.

    Example output:
        Recent deployments before incident:
          3m ago — a1b2c3d: "Fix checkout query isolation level" (John, +12/-3)
          8m ago — d4e5f6g: "Add payment retry logic" (Sarah, +45/-8)
    """
    try:
        commits = get_recent_commits(tenant_id, incident_started_at)
        if not commits:
            return ""

        lines = ["Recent deployments before incident:"]
        for c in commits:
            age_s   = incident_started_at - c["timestamp"]
            age_str = _format_age(age_s)
            files   = len(c.get("files_changed", []))
            adds    = c.get("additions", 0)
            dels    = c.get("deletions", 0)
            change  = f"+{adds}/-{dels}" if adds or dels else f"{files} files"
            lines.append(
                f"  {age_str} ago — {c['sha']}: \"{c['message'][:80]}\" "
                f"({c['author']}, {change})"
            )

        # Highlight if any commit touched DB/query/migration files
        db_keywords = {"migration", "query", "model", "schema", "database", "db", "sql"}
        risky = [
            c for c in commits
            if any(
                kw in " ".join(c.get("files_changed", [])).lower() or
                kw in c["message"].lower()
                for kw in db_keywords
            )
        ]
        if risky:
            lines.append(
                f"⚠️  {len(risky)} recent commit(s) touched database/query files"
            )

        return "\n".join(lines)

    except Exception as e:
        logger.debug("commit_context failed: %s", e)
        return ""


def _format_age(seconds: float) -> str:
    """Format age in human-readable form."""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        return f"{int(seconds // 60)}m"
    else:
        return f"{seconds / 3600:.1f}h"


# ── GitHub API polling (optional) ─────────────────────────────────────────────

async def fetch_github_commits(
    tenant_id: str,
    repo: str,
    branch: str = "main",
    since_minutes: int = 30,
) -> int:
    """
    Fetch recent commits from GitHub API and store them.
    Called when an incident opens if GITHUB_TOKEN is set.

    Args:
        tenant_id: AlertEngine tenant ID
        repo:      GitHub repo in "owner/repo" format
        branch:    Branch to check (default: main)
        since_minutes: How far back to look (default: 30 min)

    Returns:
        Number of commits stored
    """
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        logger.debug("GITHUB_TOKEN not set — skipping GitHub commit fetch")
        return 0

    since = time.time() - (since_minutes * 60)
    since_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(since))

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"https://api.github.com/repos/{repo}/commits",
                params={"sha": branch, "since": since_iso, "per_page": 10},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept":        "application/vnd.github.v3+json",
                },
            )
            if r.status_code != 200:
                logger.warning("GitHub API returned %d for %s", r.status_code, repo)
                return 0

            stored = 0
            for commit in r.json():
                c    = commit.get("commit", {})
                sha  = commit.get("sha", "")
                msg  = c.get("message", "").split("\n")[0]  # first line only
                auth = c.get("author", {}).get("name", "unknown")
                ts   = c.get("author", {}).get("date", "")

                # Parse ISO timestamp
                try:
                    import datetime
                    dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    timestamp = dt.timestamp()
                except Exception:
                    timestamp = time.time()

                store_commit(
                    tenant_id=tenant_id,
                    sha=sha,
                    message=msg,
                    author=auth,
                    timestamp=timestamp,
                    repo=repo,
                    branch=branch,
                )
                stored += 1

            return stored

    except Exception as e:
        logger.warning("GitHub commit fetch failed for %s: %s", repo, e)
        return 0
