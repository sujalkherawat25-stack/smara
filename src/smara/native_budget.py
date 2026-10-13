"""One output/reservation policy for chat, goals and scheduled native turns."""
import json

DEFAULT_OUTPUT_TOKENS = 4096
DEFAULT_CYCLE_SECONDS = 900
DEFAULT_TOKEN_ESTIMATE = 500_000


def output_payload(payload, ceiling=None):
    result = dict(payload)
    requested = result.get("max_tokens", DEFAULT_OUTPUT_TOKENS)
    if type(requested) is not int or not 1 <= requested <= 1_000_000:
        raise ValueError("Output token allowance must be a positive bounded integer")
    if ceiling is not None:
        if type(ceiling) is not int or not 1 <= ceiling <= 1_000_000:
            raise ValueError("Invalid explicit output token ceiling")
        requested = min(requested, ceiling)
    result["max_tokens"] = requested
    return result


def reservation_estimate(payload):
    """Conservative UTF-8 bytes plus reserved output, NOT billed tokens."""
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) + payload["max_tokens"]
