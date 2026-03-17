"""Token-bucket rate limiter backed by Redis.

Implements a sliding token bucket per key (typically customer-id).
Uses a Lua script executed atomically in Redis to guarantee correctness
under concurrent access.

Integration with DRF:
    - ``TokenBucketThrottle`` is a DRF throttle class that can be applied
      to any ViewSet or action via ``throttle_classes``.
"""

from __future__ import annotations

import logging
import time

from django.conf import settings
from rest_framework.throttling import BaseThrottle

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lua script for atomic token consumption
# ---------------------------------------------------------------------------
# KEYS[1] = bucket key
# ARGV[1] = capacity (max tokens)
# ARGV[2] = refill_rate (tokens per second)
# ARGV[3] = current timestamp (float seconds)
# ARGV[4] = tokens to consume (usually 1)
#
# Returns: {allowed (0/1), tokens_remaining, retry_after_seconds}
_BUCKET_LUA_SCRIPT = """
local key          = KEYS[1]
local capacity     = tonumber(ARGV[1])
local refill_rate  = tonumber(ARGV[2])
local now          = tonumber(ARGV[3])
local requested    = tonumber(ARGV[4])

local bucket = redis.call('HMGET', key, 'tokens', 'last_refill')
local tokens      = tonumber(bucket[1])
local last_refill  = tonumber(bucket[2])

if tokens == nil then
    -- First request: initialise bucket at full capacity
    tokens = capacity
    last_refill = now
end

-- Refill tokens based on elapsed time
local elapsed = math.max(0, now - last_refill)
tokens = math.min(capacity, tokens + elapsed * refill_rate)

local allowed = 0
local retry_after = 0

if tokens >= requested then
    tokens = tokens - requested
    allowed = 1
else
    retry_after = (requested - tokens) / refill_rate
end

-- Persist state with TTL = capacity / refill_rate * 2 (auto-cleanup)
local ttl = math.ceil(capacity / refill_rate * 2)
redis.call('HSET', key, 'tokens', tostring(tokens), 'last_refill', tostring(now))
redis.call('EXPIRE', key, ttl)

return {allowed, tostring(tokens), tostring(retry_after)}
"""

# Cache the loaded Lua script SHA per Redis connection
_script_sha: str | None = None


def _get_redis():
    """Return the default Redis connection from django-redis."""
    from django_redis import get_redis_connection

    return get_redis_connection("default")


def _ensure_script(conn) -> str:
    """Load the Lua script and cache its SHA."""
    global _script_sha  # noqa: PLW0603
    if _script_sha is None:
        _script_sha = conn.script_load(_BUCKET_LUA_SCRIPT)
    return _script_sha


def consume_token(
    key: str,
    capacity: int | None = None,
    refill_rate: float | None = None,
    tokens: int = 1,
) -> tuple[bool, float, float]:
    """Try to consume *tokens* from the bucket identified by *key*.

    Returns
    -------
    (allowed, tokens_remaining, retry_after)
    """
    capacity = capacity or getattr(settings, "RATE_LIMIT_CAPACITY", 10)
    refill_rate = refill_rate or getattr(settings, "RATE_LIMIT_REFILL_RATE", 2.0)
    now = time.time()

    conn = _get_redis()
    sha = _ensure_script(conn)

    try:
        result = conn.evalsha(
            sha,
            1,
            key,
            str(capacity),
            str(refill_rate),
            str(now),
            str(tokens),
        )
    except Exception:
        # If NOSCRIPT error, reload and retry once
        _script_sha_reset()
        sha = _ensure_script(conn)
        result = conn.evalsha(
            sha,
            1,
            key,
            str(capacity),
            str(refill_rate),
            str(now),
            str(tokens),
        )

    allowed = int(result[0]) == 1
    tokens_remaining = float(result[1])
    retry_after = float(result[2])

    return allowed, tokens_remaining, retry_after


def _script_sha_reset() -> None:
    global _script_sha  # noqa: PLW0603
    _script_sha = None


# ---------------------------------------------------------------------------
# DRF Throttle class
# ---------------------------------------------------------------------------
class TokenBucketThrottle(BaseThrottle):
    """DRF throttle using Redis token-bucket per customer.

    Settings (in Django settings):
        RATE_LIMIT_CAPACITY      -- max tokens in bucket (default 10)
        RATE_LIMIT_REFILL_RATE   -- tokens added per second (default 2.0)
    """

    def allow_request(self, request, view) -> bool:
        self.retry_after_secs = 0.0

        # Determine the rate-limit key
        ident = self.get_ident(request)
        key = f"rate_limit:order_place:{ident}"

        try:
            allowed, _remaining, retry_after = consume_token(key)
        except Exception:
            logger.exception("Token bucket Redis error -- allowing request")
            return True  # fail-open: don't block on Redis outage

        if not allowed:
            self.retry_after_secs = retry_after
            return False
        return True

    def wait(self) -> float | None:
        """Return seconds to wait before next allowed request."""
        return self.retry_after_secs or None

    def get_ident(self, request) -> str:
        """Build a unique identifier for the rate-limit bucket.

        Priority:
        1. Authenticated user id
        2. Customer id from request data (for /place/ on an order)
        3. Client IP as fallback
        """
        if request.user and request.user.is_authenticated:
            return f"user:{request.user.pk}"

        # Fallback to IP
        xff = request.META.get("HTTP_X_FORWARDED_FOR")
        if xff:
            return f"ip:{xff.split(',')[0].strip()}"
        return f"ip:{request.META.get('REMOTE_ADDR', 'unknown')}"
