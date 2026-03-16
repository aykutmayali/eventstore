import pytest


@pytest.fixture(autouse=True)
def _enable_db_access_for_all_tests(db):
    """Give all tests access to the database by default."""
