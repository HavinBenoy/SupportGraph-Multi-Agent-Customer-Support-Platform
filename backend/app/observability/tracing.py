"""Langfuse tracing for graph runs.

Rule: tracing must NEVER break or slow a ticket. It is off by default, needs both keys, and any
failure (bad config, server down, SDK error) degrades to an untraced run plus one warning.
Keys are never logged.

Usage:
    config = instrument_config({"recursion_limit": 10, "configurable": {...}},
                               tenant_id="acme_store", customer_id="cust_alice", ticket_id="T-1")
    graph.invoke(state, config)
    flush_tracing()          # short-lived scripts; a long-running API server does not need it
"""

import logging
from typing import Any

from app.config import Settings, get_settings

log = logging.getLogger(__name__)

_client: Any = None  # the Langfuse client we created (kept so we can flush it)


def tracing_enabled(settings: Settings | None = None) -> bool:
    s = settings or get_settings()
    if not s.tracing_enabled:
        return False
    if not (s.langfuse_public_key and s.langfuse_secret_key):
        log.warning("SG_TRACING_ENABLED is true but LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY are not both set; "
                    "continuing without tracing (run `make langfuse-init`).")
        return False
    return True


def _make_handler(s: Settings) -> Any:
    global _client
    from langfuse import Langfuse
    from langfuse.langchain import CallbackHandler

    # Creating the client registers it under its public key; the handler then finds it by that key.
    _client = Langfuse(
        public_key=s.langfuse_public_key,
        secret_key=s.langfuse_secret_key.get_secret_value(),  # type: ignore[union-attr]
        base_url=s.langfuse_base_url,
    )
    return CallbackHandler(public_key=s.langfuse_public_key)


def instrument_config(config: dict, *, tenant_id: str, customer_id: str, ticket_id: str,
                      settings: Settings | None = None) -> dict:
    """Return `config` plus a Langfuse callback and trace attributes. Returns it unchanged if tracing is off.

    Trace attributes (so you can filter in the UI):
      session  = "<tenant>:<ticket>"  (same shape as the planned LangGraph thread_id)
      user     = customer_id          (a pseudonymous id, not a name or email)
      tags     = ["tenant:<tenant>"]
    """
    s = settings or get_settings()
    if not tracing_enabled(s):
        return config
    try:
        handler = _make_handler(s)
    except Exception as e:  # noqa: BLE001 - tracing must not take the app down
        log.warning("Tracing disabled for this run: could not initialise Langfuse (%s: %s)", type(e).__name__, e)
        return config

    return {
        **config,
        "run_name": "supportgraph.ticket",
        "callbacks": [*config.get("callbacks", []), handler],
        "metadata": {
            **config.get("metadata", {}),
            "langfuse_session_id": f"{tenant_id}:{ticket_id}",
            "langfuse_user_id": customer_id,
            "langfuse_tags": [f"tenant:{tenant_id}"],
        },
    }


def flush_tracing() -> None:
    """Send any buffered spans now. Call before a short-lived process exits."""
    if _client is None:
        return
    try:
        _client.flush()
    except Exception as e:  # noqa: BLE001
        log.warning("Could not flush traces (%s: %s)", type(e).__name__, e)


def shutdown_tracing() -> None:
    """Flush and stop the exporter threads."""
    global _client
    if _client is None:
        return
    try:
        _client.shutdown()
    except Exception as e:  # noqa: BLE001
        log.warning("Could not shut down tracing cleanly (%s: %s)", type(e).__name__, e)
    finally:
        _client = None
