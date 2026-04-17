import sqlite3
import pytest
from lute.io._db.common_sqlite import (
    does_table_exist,
    get_tables,
    get_table_cols,
    get_all_rows_for_table,
    compare_cols,
)


@pytest.fixture
def db_conn():
    conn = sqlite3.connect(":memory:")
    yield conn
    conn.close()


def test_does_table_exist(db_conn):
    db_conn.execute("CREATE TABLE test (id INTEGER)")
    assert does_table_exist(db_conn, "test") is True
    assert does_table_exist(db_conn, "non_existent") is False


def test_get_tables(db_conn):
    db_conn.execute("CREATE TABLE test1 (id INTEGER)")
    db_conn.execute("CREATE TABLE test2 (id INTEGER)")
    tables = get_tables(db_conn)
    assert "test1" in tables
    assert "test2" in tables
    assert len(tables) == 2


def test_get_table_cols(db_conn):
    db_conn.execute("CREATE TABLE test (id INTEGER, name TEXT, val REAL)")
    cols = get_table_cols(db_conn, "test")
    assert cols == {"id": "INTEGER", "name": "TEXT", "val": "REAL"}


def test_get_all_rows_for_table(db_conn):
    db_conn.execute("CREATE TABLE test (id INTEGER, name TEXT)")
    db_conn.execute("INSERT INTO test (id, name) VALUES (1, 'foo')")
    db_conn.execute("INSERT INTO test (id, name) VALUES (2, 'bar')")
    rows = get_all_rows_for_table(db_conn, "test")
    assert len(rows) == 2
    assert (1, "foo") in rows
    assert (2, "bar") in rows


def test_compare_cols():
    cols1 = {"id": "INTEGER", "name": "TEXT"}
    cols2 = {"id": "INTEGER", "name": "TEXT", "val": "REAL"}

    # cols2 has 'val' which is not in cols1
    diff = compare_cols(cols1, cols2)
    assert diff == {"val": "REAL"}

    # cols1 has no extra columns compared to cols2
    diff = compare_cols(cols2, cols1)
    assert diff is None

    # Same columns
    diff = compare_cols(cols1, cols1)
    assert diff is None
