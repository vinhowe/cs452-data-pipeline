"""Minimal ClickHouse client over its HTTP interface (port 8123). No driver needed."""
from __future__ import annotations

import gzip
import json
import os
from pathlib import Path

import requests

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"
# .strip() matters: ClickHouse appends the request body right after the query text, so a
# trailing newline here would make the CSV start with a blank line (read as the header).
INSERT_USGS_SQL = (SQL_DIR / "insert_usgs_csv.sql").read_text().strip()


class ClickHouse:
    def __init__(self, url: str | None = None, user: str | None = None, password: str | None = None):
        self.url = url or os.environ.get("CLICKHOUSE_URL", "http://127.0.0.1:8123")
        self.s = requests.Session()
        # Credentials go in headers, never in the URL (URLs end up in logs).
        self.s.headers["X-ClickHouse-User"] = user or os.environ.get("CLICKHOUSE_USER", "pipeline")
        self.s.headers["X-ClickHouse-Key"] = password or os.environ["CLICKHOUSE_PASSWORD"]

    def _post(self, data, params: dict | None = None, headers: dict | None = None) -> str:
        r = self.s.post(self.url, params=params or {}, data=data, headers=headers or {}, timeout=600)
        if r.status_code != 200:
            raise RuntimeError(f"ClickHouse HTTP {r.status_code}: {r.text[:500]}")
        return r.text

    def execute(self, sql: str) -> str:
        return self._post(sql.encode())

    def rows(self, sql: str) -> list[dict]:
        out = self._post((sql + "\nFORMAT JSONEachRow").encode())
        return [json.loads(line) for line in out.splitlines() if line]

    def apply_schema(self) -> None:
        for stmt in (SQL_DIR / "schema.sql").read_text().split(";"):
            body = "\n".join(l for l in stmt.splitlines() if not l.strip().startswith("--")).strip()
            if body:
                self.execute(body)

    def insert_usgs_csv(self, data: bytes, source: str, gzipped: bool = False) -> None:
        """The one insert path for seed, feed, backfill and revisions alike."""
        headers = {"Content-Encoding": "gzip"} if gzipped else {}
        self._post(data, params={"query": INSERT_USGS_SQL, "param_source": source}, headers=headers)

    def insert_json(self, table: str, rows: list[dict]) -> None:
        body = "\n".join(json.dumps(r, default=str) for r in rows)
        self._post(gzip.compress(body.encode()),
                   params={"query": f"INSERT INTO {table} FORMAT JSONEachRow"},
                   headers={"Content-Encoding": "gzip"})


def sql_str(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"
