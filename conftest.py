import os
import tempfile
import pytest

from database import db as db_module
from app import app as _app


@pytest.fixture(scope="session")
def _test_db():
    db_fd, db_path = tempfile.mkstemp(suffix="_test.db")
    original = db_module.DATABASE
    db_module.DATABASE = db_path

    yield db_path

    db_module.DATABASE = original
    os.close(db_fd)
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.fixture
def app(_test_db):
    _app.config.update({
        "TESTING": True,
        "SERVER_NAME": "localhost",
    })

    with _app.app_context():
        db_module.init_db()
        db_module.seed_db()

    yield _app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def runner(app):
    return app.test_cli_runner()
