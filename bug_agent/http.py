"""Shared JSON HTTP transport for GitHub and model services."""

import json
import urllib.error
import urllib.request


def request_json(url, *, headers=None, payload=None, timeout=120):
    """GET JSON, or POST a JSON payload, and translate common service errors."""
    # urllib uses GET without data and POST when a request body is supplied.
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": "codebase-bug-agent/0.1",
        "Accept": "application/json",
        **({"Content-Type": "application/json"} if data else {}),
        # Caller headers override defaults, such as GitHub's Accept header.
        **(headers or {}),
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        # Do not echo server bodies, which can contain credentials or request text.
        hint = {401: "Check your API key.", 403: "Access denied or rate limit exceeded.",
                404: "Check the repository, issue, endpoint, and model name.",
                429: "Rate limit exceeded; try again later."}.get(exc.code, "Check the service configuration.")
        raise RuntimeError(f"HTTP {exc.code}. {hint}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Cannot reach service: {exc.reason}") from None
    except (ValueError, TimeoutError) as exc:
        raise RuntimeError(f"Invalid service response or timeout: {exc}") from None
