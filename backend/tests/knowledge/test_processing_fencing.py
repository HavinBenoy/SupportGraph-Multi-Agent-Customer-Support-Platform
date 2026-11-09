"""M6 processing ownership (fencing) on SQLite. The same scenarios run on PostgreSQL in tests/db/test_postgres_m6.py."""

import pytest

from tests.api.conftest import api  # noqa: F401
from tests.calls.apidb import seeded_db  # noqa: F401
from tests.knowledge import fencing
from tests.rag.conftest import embedder, ingested  # noqa: F401


@pytest.mark.parametrize("case", fencing.ALL, ids=lambda f: f.__name__)
def test_processing_ownership(api, case):  # noqa: F811
    case()
