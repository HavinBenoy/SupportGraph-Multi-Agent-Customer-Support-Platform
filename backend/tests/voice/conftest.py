import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"  # anyio's pytest plugin ships with FastAPI's dependencies; no extra test dependency


try:  # the voice tests need numpy (installed with `make voice-setup` or `pip install numpy`); without it the rest of the suite still runs
    import numpy  # noqa: F401
except ImportError:
    collect_ignore_glob = ["test_*.py"]
