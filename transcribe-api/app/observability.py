"""Thin wrappers around the New Relic Python agent.

Every helper degrades to a no-op when the agent is not installed, not
running (NEW_RELIC_ENABLED=false / no license key), or when there is no
active transaction — so application code can call these unconditionally.
"""

import contextlib

try:
    import newrelic.agent as _nr
except ImportError:  # agent not installed; run un-instrumented
    _nr = None


@contextlib.contextmanager
def trace_segment(name: str):
    """Time a block as a custom segment inside the current transaction."""
    if _nr is not None and _nr.current_transaction() is not None:
        with _nr.FunctionTrace(name=name, group="Custom"):
            yield
    else:
        yield


@contextlib.contextmanager
def background_task(name: str):
    """Trace work outside a web transaction (e.g. model load at startup)."""
    if _nr is not None:
        application = _nr.application()
        with _nr.BackgroundTask(application, name=name, group="Custom"):
            yield
    else:
        yield


def add_transaction_attributes(attrs: dict) -> None:
    if _nr is not None and _nr.current_transaction() is not None:
        _nr.add_custom_attributes(list(attrs.items()))


def notice_error() -> None:
    """Report the exception currently being handled to New Relic."""
    if _nr is not None and _nr.current_transaction() is not None:
        _nr.notice_error()
