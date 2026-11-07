import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"  # anyio's pytest plugin ships with FastAPI's dependencies; no extra test dependency


try:  # the voice tests need numpy (installed with `make voice-setup` or `pip install numpy`); without it the rest of the suite still runs
    import numpy  # noqa: F401
except ImportError:
    collect_ignore_glob = ["test_*.py"]


@pytest.fixture(autouse=True)
def roomy_caller_budget(monkeypatch):
    """The caller-audio abuse guard (real-time budget) would trip on tests that push many utterances back to back."""
    from app.voice import session as sess

    monkeypatch.setattr(sess, "BURST_MS", 120_000)


@pytest.fixture(autouse=True)
def hang_guard():
    """A socket test that waits forever would hang the whole suite: fail it after 90 s instead (main thread, POSIX)."""
    import signal

    def boom(*_):
        raise TimeoutError("test hung for 90 s (a socket never delivered a message)")

    old = signal.signal(signal.SIGALRM, boom)
    signal.alarm(90)
    yield
    signal.alarm(0)
    signal.signal(signal.SIGALRM, old)
