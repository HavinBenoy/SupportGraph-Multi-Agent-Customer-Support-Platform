"""Re-seeding must never silently delete real cases."""

import pytest

from app.db.seed import CasesExistError, seed
from app.db.session import session_scope
from tests.conftest import AS_OF
from tests.step4.conftest import MakeupRequest, Ticket, rows


def test_reseed_is_refused_when_cases_exist_and_nothing_is_lost(ask):
    ask("Please arrange a make-up class for the session I missed in PYD-301.")
    with pytest.raises(CasesExistError), session_scope() as db:
        seed(db, as_of=AS_OF)
    assert len(rows(MakeupRequest)) == 1 and len(rows(Ticket)) == 1


def test_reseed_with_explicit_reset_clears_cases_only_for_that_choice(ask):
    ask("Please arrange a make-up class for the session I missed in PYD-301.")
    with session_scope() as db:
        seed(db, as_of=AS_OF, reset_cases=True)
    assert rows(MakeupRequest) == [] and rows(Ticket) == []


def test_reseed_is_fine_when_there_are_no_cases():
    with session_scope() as db:
        assert seed(db, as_of=AS_OF)["qspiders_sample"] == 7
