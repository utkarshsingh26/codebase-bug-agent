"""Optional LangSmith spans, shared by hosted and local model workflows."""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import inspect
import os
import sys

from .config import CONFIG_DIR, load_config


_active = ContextVar("agent_tracing", default=False)


def _safe(value):
    """Snapshot trace data, masking configured credentials without serializing clients."""
    if isinstance(value, dict):
        return {str(k): "[REDACTED]" if any(s in str(k).lower() for s in
                ("api_key", "authorization", "password", "token")) else _safe(v)
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    if isinstance(value, str):
        for name, secret in os.environ.items():
            if secret and len(secret) >= 8 and any(s in name.upper() for s in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
                value = value.replace(secret, "[REDACTED]")
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return f"<{type(value).__name__}>"


def traced(name, run_type="chain"):
    """Record nested inputs, outputs, timing, and failures only when enabled."""
    def decorate(function):
        signature = inspect.signature(function)

        @wraps(function)
        def wrapped(*args, **kwargs):
            if not _active.get():
                return function(*args, **kwargs)
            from langsmith import trace

            arguments = signature.bind(*args, **kwargs).arguments
            inputs = {k: _safe(v) for k, v in arguments.items() if k not in {"self", "model", "repo", "github"}}
            owner = arguments.get("self")
            metadata = {}
            if hasattr(owner, "session_id"):
                metadata["session_id"] = metadata["thread_id"] = owner.session_id
            if run_type == "llm":
                metadata["ls_model_name"] = owner.model
                metadata["endpoint"] = owner.url
            with trace(name, run_type=run_type, inputs=inputs, metadata=_safe(metadata)) as run:
                result = function(*args, **kwargs)
                run.end(outputs={"result": _safe(result)})
                return result
        return wrapped
    return decorate


@contextmanager
def tracing_session():
    """Configure SDK context for the CLI and flush queued traces before exit."""
    settings = load_config(CONFIG_DIR / "tracing.toml", {
        "enabled": bool, "project": str, "endpoint": str, "api_key_env": str})
    override = os.getenv("LANGSMITH_TRACING")
    enabled = override.lower() in {"true", "1", "yes"} if override is not None else settings.get("enabled", False)
    if not enabled:
        yield
        return
    try:
        from langsmith import Client, tracing_context
    except ImportError:
        raise RuntimeError('LangSmith tracing requires: python -m pip install -e ".[tracing]"') from None
    key = os.getenv(settings.get("api_key_env", "LANGSMITH_API_KEY"))
    if not key:
        raise RuntimeError("Tracing is enabled but its API key environment variable is not set. See config/tracing.toml.")
    client = Client(api_key=key, api_url=os.getenv("LANGSMITH_ENDPOINT") or
                    settings.get("endpoint", "https://api.smith.langchain.com"))
    token = _active.set(True)
    try:
        project = os.getenv("LANGSMITH_PROJECT") or settings.get("project", "codebase-bug-agent")
        print(f"LangSmith tracing enabled: {project}", file=sys.stderr)
        with tracing_context(enabled=True, client=client, project_name=project,
                             tags=["codebase-bug-agent"], metadata={"application": "codebase-bug-agent"}):
            yield
    finally:
        _active.reset(token)
        client.flush()
