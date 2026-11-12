from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app import database


@pytest.fixture
def sqlite_database(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'database.sqlite'}")
    database.apply_migrations()


def test_database_url_and_sql_adapter_follow_backend(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert database.get_db_url() == "sqlite:///./command_hub.db"
    assert database.is_sqlite()
    assert database.sql_for_connection("select * from members where id = ?") == (
        "select * from members where id = ?"
    )

    monkeypatch.setenv("DATABASE_URL", "postgresql://example/database")
    assert not database.is_sqlite()
    assert database.sql_for_connection("select * from members where id = ?") == (
        "select * from members where id = %s"
    )


def test_sqlite_connection_enables_foreign_keys(sqlite_database):
    connection = database.get_connection()
    try:
        assert connection.execute("pragma foreign_keys").fetchone()[0] == 1
    finally:
        connection.close()


def test_database_helpers_persist_events_and_json_audit_payloads(sqlite_database):
    database.execute(
        "insert into members(key, display_name, role) values (?, ?, ?)",
        ("owner", "Administrator", "owner"),
    )
    member = database.fetchone("select * from members where key = ?", ("owner",))
    assert member is not None
    assert member["display_name"] == "Administrator"
    assert database.fetchall("select key from members") == [{"key": "owner"}]

    assert database.record_event("telegram", "event-1")
    assert not database.record_event("telegram", "event-1")
    assert database.is_duplicate_event("telegram", "event-1")
    assert not database.is_duplicate_event("discord", "event-1")

    recorded_at = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
    database.log_action(member["id"], "test", {"recorded_at": recorded_at})
    audit = database.fetchone("select * from audit_log")
    assert audit is not None
    assert json.loads(audit["payload"]) == {
        "recorded_at": "2026-11-12 12:00:00+00:00"
    }


def test_database_helpers_propagate_invalid_roster_configuration(
    sqlite_database, monkeypatch
):
    monkeypatch.setenv("TEAM_ROSTER", '{"not": "an array"}')

    with pytest.raises(TypeError, match="TEAM_ROSTER must be a JSON array"):
        database.ensure_seed_members()


def test_sqlite_migration_errors_are_not_silently_ignored(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'uninitialized.sqlite'}")
    monkeypatch.setattr(database, "MIGRATIONS_DIR", database.MIGRATIONS_DIR.parent / "missing")

    with pytest.raises(FileNotFoundError):
        database.apply_migrations()

    connection = database.get_connection()
    try:
        assert connection.execute(
            "select version from schema_migrations order by version"
        ).fetchall() == []
    finally:
        connection.close()
