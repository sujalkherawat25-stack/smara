"""Comprehensive unit test suite for Rate Limiter Middleware."""
import time
import pytest
from rate_limiter import RateLimiter, RateLimitConfig, RateLimitMiddleware


def test_normal_traffic_allowed():
    limiter = RateLimiter(RateLimitConfig(capacity=10, refill_rate=2))
    allowed, headers = limiter.acquire("192.168.1.1")
    assert allowed is True
    assert headers["X-RateLimit-Limit"] == "10"
    assert headers["X-RateLimit-Remaining"] == "9"


def test_burst_tolerance_and_exhaustion():
    limiter = RateLimiter(RateLimitConfig(capacity=3, refill_rate=1))
    client = "10.0.0.1"
    assert limiter.acquire(client)[0] is True
    assert limiter.acquire(client)[0] is True
    assert limiter.acquire(client)[0] is True
    
    # 4th request in burst should be rejected
    rejected, headers = limiter.acquire(client)
    assert rejected is False
    assert headers["X-RateLimit-Remaining"] == "0"
    assert int(headers["Retry-After"]) >= 1


def test_token_bucket_refill():
    limiter = RateLimiter(RateLimitConfig(capacity=2, refill_rate=10))
    client = "10.0.0.2"
    assert limiter.acquire(client)[0] is True
    assert limiter.acquire(client)[0] is True
    assert limiter.acquire(client)[0] is False
    
    # Sleep 0.25s to replenish ~2 tokens
    time.sleep(0.25)
    assert limiter.acquire(client)[0] is True


def test_x_forwarded_for_extraction():
    limiter = RateLimiter()
    headers = {"X-Forwarded-For": "203.0.113.195, 70.41.3.18, 150.172.238.178"}
    ip = limiter.extract_client_ip(headers)
    assert ip == "203.0.113.195"


def test_x_real_ip_extraction():
    limiter = RateLimiter()
    headers = {"X-Real-IP": "198.51.100.42"}
    ip = limiter.extract_client_ip(headers)
    assert ip == "198.51.100.42"


def test_socket_remote_addr_fallback():
    limiter = RateLimiter()
    ip = limiter.extract_client_ip({}, remote_addr="172.16.0.5")
    assert ip == "172.16.0.5"


def test_ipv6_handling():
    limiter = RateLimiter()
    headers = {"X-Real-IP": "2001:0db8:85a3:0000:0000:8a2e:0370:7334"}
    ip = limiter.extract_client_ip(headers)
    assert "2001:db8:85a3::8a2e:370:7334" in ip or "2001:0db8" in ip


def test_header_max_length_bounds():
    limiter = RateLimiter(RateLimitConfig(header_max_length=20))
    huge_header = {"X-Forwarded-For": "A" * 5000}
    ip = limiter.extract_client_ip(huge_header)
    assert len(ip) <= 20


def test_middleware_on_request_allow():
    mw = RateLimitMiddleware(config=RateLimitConfig(capacity=5))
    status, headers, body = mw.on_request({"X-Real-IP": "1.1.1.1"})
    assert status == 200
    assert headers["X-RateLimit-Remaining"] == "4"


def test_middleware_on_request_429():
    mw = RateLimitMiddleware(config=RateLimitConfig(capacity=1))
    status1, _, _ = mw.on_request({"X-Real-IP": "2.2.2.2"})
    assert status1 == 200
    
    status2, headers2, body2 = mw.on_request({"X-Real-IP": "2.2.2.2"})
    assert status2 == 429
    assert "Retry-After" in headers2
    assert "Too Many Requests" in body2


def test_fail_open_safety_switch():
    mw = RateLimitMiddleware(config=RateLimitConfig(fail_open=True))
    mw.limiter = None  # Force internal error
    status, headers, _ = mw.on_request({})
    assert status == 200
    assert headers.get("X-RateLimit-Degraded") == "fail_open"
