"""Portable, user-actionable provider error classification extracted from Memento.

This module deliberately has no dependency on a particular SDK or provider.
The stable ``kind`` is safe for CLI/Web rendering; raw provider errors are not
sent to clients.
"""
from __future__ import annotations

KIND_NO_CREDITS = "no_credits"
KIND_INVALID_KEY = "invalid_key"
KIND_RATE_LIMIT = "rate_limit"
KIND_CONTEXT_TOO_LONG = "context_too_long"
KIND_MODEL_UNAVAILABLE = "model_unavailable"
KIND_PROVIDER_DOWN = "provider_down"
KIND_TIMEOUT = "timeout"
KIND_NETWORK = "network"
KIND_NOT_CONFIGURED = "not_configured"
KIND_UNKNOWN = "unknown"


def classify(exc: Exception | str) -> str:
    message = str(exc).lower()
    status = getattr(exc, "status_code", None)
    response_text = ""
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    else:
        response = getattr(exc, "response", None)
    if response is not None:
        # Provider bodies are used only for coarse classification. They are
        # never returned to the client, which keeps keys and request details
        # out of the stable error contract.
        try:
            response_text = response.text.lower()[:2_000]
        except Exception:
            response_text = ""
    diagnostic = f"{message} {response_text}"
    if "no smara chat provider is configured" in diagnostic or "smara agent model provider is not configured" in diagnostic:
        return KIND_NOT_CONFIGURED
    if status == 402 or any(value in diagnostic for value in ("insufficient_quota", "insufficient credits", "credit balance", "payment required")):
        return KIND_NO_CREDITS
    if status in {401, 403} or any(value in diagnostic for value in ("invalid api key", "invalid_api_key", "api key", "permission-denied", "unauthorized", "authenticationerror")):
        return KIND_INVALID_KEY
    if status == 429 or "rate limit" in diagnostic or "rate_limit" in diagnostic:
        return KIND_RATE_LIMIT
    if status == 413 or "context length" in diagnostic or "maximum context" in diagnostic:
        return KIND_CONTEXT_TOO_LONG
    if "model_not_found" in diagnostic or "model not found" in diagnostic or "decommissioned" in diagnostic or (status == 400 and any(value in diagnostic for value in ("not available", "beta", "unsupported model"))):
        return KIND_MODEL_UNAVAILABLE
    if (isinstance(status, int) and status >= 500) or any(value in diagnostic for value in ("service unavailable", "bad gateway", "overloaded")):
        return KIND_PROVIDER_DOWN
    if "timeout" in diagnostic or "timed out" in diagnostic:
        return KIND_TIMEOUT
    if any(value in diagnostic for value in ("connection", "econnref", "name or service not known")):
        return KIND_NETWORK
    return KIND_UNKNOWN


def user_message(kind: str, *, provider: str = "the configured model provider") -> str:
    messages = {
        KIND_NO_CREDITS: f"{provider} is out of available quota. Try again later or choose another configured provider.",
        KIND_INVALID_KEY: f"{provider} rejected its API credentials. Update the server-side provider configuration.",
        KIND_RATE_LIMIT: f"{provider} is temporarily rate-limiting requests. Try again shortly.",
        KIND_CONTEXT_TOO_LONG: "That request is too large for the selected model. Try a shorter message or start a new conversation.",
        KIND_MODEL_UNAVAILABLE: "The selected model is unavailable. Choose another configured model.",
        KIND_PROVIDER_DOWN: f"{provider} is temporarily unavailable. Try again shortly.",
        KIND_TIMEOUT: "The model response timed out. Try again.",
        KIND_NETWORK: f"Smara could not reach {provider}. Try again shortly.",
        KIND_NOT_CONFIGURED: "Smara chat is not configured yet. Add a server-side model provider before using direct chat.",
    }
    return messages.get(kind, "Smara could not draft a reply. Try again.")


def describe(exc: Exception | str, *, provider: str = "the configured model provider") -> tuple[str, str]:
    kind = classify(exc)
    return kind, user_message(kind, provider=provider)
