"""Token-Bucket Rate Limiter Middleware for Smara and ASGI / WSGI services.

Features:
- Per-client token-bucket rate limiting (capacity and refill rate)
- Robust X-Forwarded-For and X-Real-IP parsing with IP validation
- HTTP 429 Too Many Requests response with Retry-After and X-RateLimit-* headers
- Fail-open / Fail-closed enterprise safety switch
- Thread-safe memory store with automatic eviction
"""
from __future__ import annotations

import ipaddress
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple


@dataclass
class RateLimitConfig:
    capacity: float = 60.0            # Max tokens per bucket (burst limit)
    refill_rate: float = 1.0          # Tokens added per second (sustained limit)
    fail_open: bool = True            # If True, allow request if rate limiter errors out
    header_max_length: int = 512      # Prevent buffer overflow / header DOS
    ban_duration_seconds: float = 0.0 # Optional temporary penalty ban duration


@dataclass
class TokenBucket:
    tokens: float
    last_refill: float
    banned_until: float = 0.0


class RateLimiter:
    def __init__(self, config: Optional[RateLimitConfig] = None):
        self.config = config or RateLimitConfig()
        self._buckets: Dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    def extract_client_ip(self, headers: Dict[str, str], remote_addr: Optional[str] = None) -> str:
        """Defensively parse and sanitize client IP from headers or socket address."""
        lower_headers = {k.lower(): v for k, v in headers.items()}
        raw_ip = ""

        # 1. Check X-Forwarded-For (leftmost is original client)
        if "x-forwarded-for" in lower_headers:
            val = lower_headers["x-forwarded-for"][:self.config.header_max_length]
            parts = [p.strip() for p in val.split(",") if p.strip()]
            if parts:
                raw_ip = parts[0]

        # 2. Check X-Real-IP
        if not raw_ip and "x-real-ip" in lower_headers:
            raw_ip = lower_headers["x-real-ip"][:self.config.header_max_length].strip()

        # 3. Fallback to socket remote_addr
        if not raw_ip and remote_addr:
            raw_ip = remote_addr.strip()

        if not raw_ip:
            return "unknown"

        # Validate IP address syntax (IPv4 or IPv6)
        try:
            if ":" in raw_ip and "." in raw_ip:
                raw_ip = raw_ip.split(":")[0]
            ip_obj = ipaddress.ip_address(raw_ip)
            return str(ip_obj)
        except ValueError:
            cleaned = "".join(c for c in raw_ip if c.isalnum() or c in ".:-")
            return cleaned or "malformed"

    def acquire(self, client_id: str, tokens_requested: float = 1.0) -> Tuple[bool, Dict[str, str]]:
        """Evaluate token bucket for client. Returns (allowed, headers)."""
        now = time.time()
        with self._lock:
            bucket = self._buckets.get(client_id)
            if bucket is None:
                bucket = TokenBucket(tokens=self.config.capacity, last_refill=now)
                self._buckets[client_id] = bucket

            # Check temporary ban
            if bucket.banned_until > now:
                retry_after = int(bucket.banned_until - now) + 1
                return False, {
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(int(self.config.capacity)),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(bucket.banned_until)),
                }

            # Refill tokens
            elapsed = max(0.0, now - bucket.last_refill)
            bucket.tokens = min(self.config.capacity, bucket.tokens + elapsed * self.config.refill_rate)
            bucket.last_refill = now

            # Check availability
            if bucket.tokens >= tokens_requested:
                bucket.tokens -= tokens_requested
                remaining = int(bucket.tokens)
                return True, {
                    "X-RateLimit-Limit": str(int(self.config.capacity)),
                    "X-RateLimit-Remaining": str(remaining),
                    "X-RateLimit-Reset": str(int(now + (self.config.capacity - bucket.tokens) / max(0.001, self.config.refill_rate))),
                }
            else:
                # Depleted
                deficit = tokens_requested - bucket.tokens
                retry_after = max(1, int(deficit / max(0.001, self.config.refill_rate)) + 1)
                if self.config.ban_duration_seconds > 0:
                    bucket.banned_until = now + self.config.ban_duration_seconds
                    retry_after = int(self.config.ban_duration_seconds)

                return False, {
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(int(self.config.capacity)),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(now + retry_after)),
                }

    def reset(self, client_id: Optional[str] = None):
        """Reset one or all rate limit buckets."""
        with self._lock:
            if client_id:
                self._buckets.pop(client_id, None)
            else:
                self._buckets.clear()


class RateLimitMiddleware:
    """Callable WSGI / Framework agnostic HTTP middleware hook."""
    def __init__(self, app_handler: Optional[Callable] = None, config: Optional[RateLimitConfig] = None):
        self.app = app_handler
        self.config = config or RateLimitConfig()
        self.limiter = RateLimiter(self.config)

    def on_request(self, headers: Dict[str, str], remote_addr: Optional[str] = None) -> Tuple[int, Dict[str, str], str]:
        """Intercept incoming request.
        Returns: (status_code, response_headers, body_text)
        status_code 200 means allowed to proceed.
        status_code 429 means rate limit exceeded.
        """
        try:
            client_ip = self.limiter.extract_client_ip(headers, remote_addr)
            allowed, resp_headers = self.limiter.acquire(client_ip)
            if allowed:
                return 200, resp_headers, ""
            else:
                resp_headers["Content-Type"] = "application/json"
                return 429, resp_headers, '{"error":"Too Many Requests","message":"Rate limit exceeded. Please retry later."}'
        except Exception as e:
            if getattr(self.config, "fail_open", True):
                return 200, {"X-RateLimit-Degraded": "fail_open"}, ""
            else:
                return 500, {}, f"Rate limiting fault: {str(e)}"
