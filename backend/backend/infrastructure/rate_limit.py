"""
Distributed rate limiting.

WHY THE IN-PROCESS VERSION WASN'T ENOUGH
----------------------------------------
The original limiter kept its counters in one process's memory. With N API
replicas behind a load balancer, the effective limit became N x the configured
value, and an attacker only had to spread requests across replicas to multiply
their budget. That is fine as an abuse brake; it is not a security control, and
`/auth/login` needs a security control.

THE FIX: a Redis sliding-window counter, shared by every replica.

Implemented with a sorted set per (client, bucket):
    ZREMRANGEBYSCORE  drop entries older than the window
    ZCARD             how many remain
    ZADD              record this request
    PEXPIRE           let the key die on its own

Those four commands MUST be atomic. Run as separate round-trips, two replicas
can both read a count of `limit - 1` and both allow their request, letting the
true rate drift above the limit exactly when the system is under attack -- the
classic check-then-act race. The Lua script below executes server-side in a
single step, so the read and the write cannot be interleaved.

Sliding window, not fixed window: a fixed 60s bucket lets an attacker send
`limit` requests at 00:59 and `limit` again at 01:00 -- 2x the limit in two
seconds, right at the boundary.

FAIL-OPEN, DELIBERATELY. If Redis is unreachable we fall back to the in-process
limiter rather than rejecting traffic. A rate limiter that takes the API down
when its cache dies has converted a minor degradation into an outage. The
fallback still bounds abuse per replica; it just loses the cluster-wide
guarantee until Redis returns.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque

from backend.core.config import settings
from backend.infrastructure import redis_client

logger = logging.getLogger(__name__)

# KEYS[1] = bucket key
# ARGV[1] = now (ms), ARGV[2] = window (ms), ARGV[3] = limit, ARGV[4] = unique member
# Returns {allowed(0|1), current_count, oldest_score_or_0}
_SLIDING_WINDOW_LUA = """
local key    = KEYS[1]
local now    = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit  = tonumber(ARGV[3])
local member = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)

if count >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local oldest_score = 0
  if oldest[2] then oldest_score = tonumber(oldest[2]) end
  return {0, count, oldest_score}
end

redis.call('ZADD', key, now, member)
redis.call('PEXPIRE', key, window)
return {1, count + 1, 0}
"""

_script_sha: str | None = None


class RateLimitResult:
    __slots__ = ("allowed", "limit", "remaining", "retry_after", "backend")

    def __init__(
        self,
        allowed: bool,
        limit: int,
        remaining: int,
        retry_after: int,
        backend: str,
    ) -> None:
        self.allowed = allowed
        self.limit = limit
        self.remaining = remaining
        self.retry_after = retry_after
        self.backend = backend


# ── In-process fallback (used only when Redis is unavailable) ─────────────────
_local_hits: dict[str, deque[float]] = defaultdict(deque)


def _check_local(key: str, limit: int, window_s: float) -> RateLimitResult:
    now = time.monotonic()
    window = _local_hits[key]
    while window and now - window[0] > window_s:
        window.popleft()

    if len(window) >= limit:
        retry_after = int(window_s - (now - window[0])) + 1
        return RateLimitResult(False, limit, 0, retry_after, "local")

    window.append(now)
    return RateLimitResult(True, limit, limit - len(window), 0, "local")


async def check(key: str, limit: int, window_s: float = 60.0) -> RateLimitResult:
    """
    Consume one token for `key`. Returns whether the request may proceed.

    Tries Redis (cluster-wide, atomic); falls back to the in-process limiter if
    Redis is unreachable.
    """
    global _script_sha

    client = await redis_client.get_redis()
    if client is None:
        return _check_local(key, limit, window_s)

    now_ms = int(time.time() * 1000)
    window_ms = int(window_s * 1000)
    # Unique member: two requests in the same millisecond must both be counted.
    # Using the timestamp alone as the member would let ZADD dedupe them into a
    # single entry and undercount a burst -- precisely the traffic we care about.
    member = f"{now_ms}-{time.perf_counter_ns()}"

    try:
        if _script_sha is None:
            _script_sha = await client.script_load(_SLIDING_WINDOW_LUA)

        allowed, count, oldest = await client.evalsha(
            _script_sha, 1, key, now_ms, window_ms, limit, member
        )
    except Exception as e:
        # Covers NOSCRIPT (Redis restarted / script flushed) and any connection
        # error. Reload once, then give up to the local limiter -- we never let
        # a limiter fault become a 500.
        logger.debug("Redis rate-limit failed (%s); falling back locally.", e)
        _script_sha = None
        return _check_local(key, limit, window_s)

    if not allowed:
        retry_after = max(int((oldest + window_ms - now_ms) / 1000) + 1, 1)
        return RateLimitResult(False, limit, 0, retry_after, "redis")

    return RateLimitResult(True, limit, max(limit - int(count), 0), 0, "redis")


def limit_for(path: str) -> tuple[int, str]:
    """
    (limit, bucket) for a path.

    Buckets are separate so hammering /auth cannot exhaust a legitimate user's
    budget for /analysis, and so the expensive endpoints get their own, much
    tighter allowance.
    """
    prefix = settings.API_V1_PREFIX
    if path.startswith(f"{prefix}/auth"):
        return settings.RATE_LIMIT_AUTH_PER_MINUTE, "auth"
    if path.startswith(f"{prefix}/analysis"):
        # ARISE is by far the most expensive call in the system: n_simulations x
        # n_years x 4 strategies, on a worker thread. A handful of concurrent
        # 5,000-path runs will saturate the pool.
        return settings.RATE_LIMIT_ANALYSIS_PER_MINUTE, "analysis"
    if path.startswith(f"{prefix}/reports"):
        return settings.RATE_LIMIT_REPORTS_PER_MINUTE, "reports"
    return settings.RATE_LIMIT_DEFAULT_PER_MINUTE, "default"


def client_id(ip: str, user_id: str | None) -> str:
    """
    Prefer the authenticated user over the IP.

    IP alone is wrong in both directions: an office or mobile carrier NATs
    hundreds of legitimate users behind one address (so they throttle each
    other), while an attacker with a proxy pool trivially rotates IPs. Keying on
    the user id where we have one fixes both; unauthenticated routes (login,
    register) necessarily still key on IP.
    """
    return f"user:{user_id}" if user_id else f"ip:{ip}"
