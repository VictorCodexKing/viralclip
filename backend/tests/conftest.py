import importlib

import pytest


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """Provide a fresh, isolated jobstore backed by a temp SQLite file."""
    import app.jobstore as jobstore

    importlib.reload(jobstore)
    db_path = tmp_path / "test.db"
    jobstore.init_store(db_path)
    return jobstore
