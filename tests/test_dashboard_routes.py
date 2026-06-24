"""
Flask smoke tests for the health dashboard routes.

Run: python -m pytest tests/test_dashboard_routes.py -v

Isolated from the real db/jobs.db: get_connection is monkeypatched to open a
temp SQLite file built from schema.sql, so these tests never touch live data.
"""
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import db.dedup as dedup
from feedback import server

_SCHEMA = (Path(__file__).parent.parent / "db" / "schema.sql").read_text(encoding="utf-8")


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_jobs.db"
    init = sqlite3.connect(db_path)
    init.executescript(_SCHEMA)
    init.execute("ALTER TABLE run_log ADD COLUMN source_yields TEXT")
    init.commit()
    init.close()

    def _factory():
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        return c

    # _get_db() does `from db.dedup import get_connection` at call time.
    monkeypatch.setattr(dedup, "get_connection", _factory)
    return db_path


@pytest.fixture
def client(temp_db):
    server.app.config["TESTING"] = True
    return server.app.test_client()


def _seed_run(db_path):
    c = sqlite3.connect(db_path)
    now = datetime.now().astimezone().isoformat()
    c.execute(
        """INSERT INTO run_log
           (started_at, finished_at, sites_scraped, new_jobs_found, jobs_scored,
            jobs_filtered, jobs_emailed, api_errors, pre_screen_errors, status, source_yields)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (now, now, 24, 10, 8, 2, 3, 0, 0, "success", '{"euraxess": 5, "tni": 0}'),
    )
    c.commit()
    c.close()


def test_dashboard_empty_db_renders(client):
    # First-run case: no rows at all must NOT 500.
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert b"Last run" in r.data
    assert b"No runs recorded yet" in r.data


def test_api_stats_empty_db(client):
    r = client.get("/api/v1/stats")
    assert r.status_code == 200
    data = r.get_json()
    assert set(data.keys()) == {"last_run", "last_30_days"}
    assert data["last_run"]["state"] == "NO_RUNS_YET"


def test_dashboard_with_data(client, temp_db):
    _seed_run(temp_db)
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert b"Last 30 days" in r.data
    # disabled source labelled, not flagged as a failure
    assert b"disabled" in r.data


def test_api_stats_with_data(client, temp_db):
    _seed_run(temp_db)
    data = client.get("/api/v1/stats").get_json()
    assert data["last_run"]["state"] in ("SENT", "NO_EMAIL", "NOTHING_NEW")
    assert data["last_30_days"]["spend"] == {"instrumented": False}
