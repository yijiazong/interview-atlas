"""SQLite 元数据层。原文存在 segments.text，编码存在 codings，全部本地可查。"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS interviewees (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    code           TEXT NOT NULL UNIQUE,       -- 脱敏编号 P01、P02 ...
    alias          TEXT DEFAULT '',           -- 化名
    role           TEXT DEFAULT '',           -- 岗位 / 身份
    org            TEXT DEFAULT '',           -- 所属门店 / 部门
    stage          TEXT DEFAULT '',           -- 时间阶段（纵向研究用）
    interview_date TEXT DEFAULT '',           -- YYYY-MM-DD
    wave           INTEGER NOT NULL DEFAULT 1,-- 访谈轮次
    note           TEXT DEFAULT '',
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    interviewee_id  INTEGER REFERENCES interviewees(id) ON DELETE SET NULL,
    title           TEXT NOT NULL,
    source_path     TEXT DEFAULT '',          -- data/sources 下的相对路径
    source_type     TEXT DEFAULT '',
    stage           TEXT DEFAULT '',
    wave            INTEGER NOT NULL DEFAULT 1,
    interview_date  TEXT DEFAULT '',
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS segments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,              -- 文档内顺序，从 1 开始
    label       TEXT DEFAULT '',              -- P01-001 一类可引用编号
    speaker     TEXT NOT NULL DEFAULT 'unknown',  -- interviewee / interviewer / unknown
    timecode    TEXT DEFAULT '',              -- 转写稿时间戳
    text        TEXT NOT NULL,
    UNIQUE(document_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_segments_doc ON segments(document_id);

CREATE TABLE IF NOT EXISTS codes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    definition TEXT DEFAULT '',
    color      TEXT DEFAULT '#4C6EF5',
    parent_id  INTEGER REFERENCES codes(id) ON DELETE SET NULL,
    memo       TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS codings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    segment_id INTEGER NOT NULL REFERENCES segments(id) ON DELETE CASCADE,
    code_id    INTEGER NOT NULL REFERENCES codes(id) ON DELETE CASCADE,
    memo       TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(segment_id, code_id)
);
CREATE INDEX IF NOT EXISTS idx_codings_seg ON codings(segment_id);
CREATE INDEX IF NOT EXISTS idx_codings_code ON codings(code_id);

CREATE TABLE IF NOT EXISTS aliases (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    real_name   TEXT NOT NULL UNIQUE,
    replacement TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Database:
    """轻量封装：每次操作独立连接，单用户本地工具足够，且不会留下锁。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 注意：sqlite3 的 `with conn` 只管理事务，不会关闭连接，必须显式 close。
        conn = self.connect()
        try:
            conn.executescript(SCHEMA)
        finally:
            conn.close()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def query(self, sql: str, params=()) -> list[dict]:
        conn = self.connect()
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def scalar(self, sql: str, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params=()) -> int:
        conn = self.connect()
        try:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def executemany(self, sql: str, seq) -> None:
        conn = self.connect()
        try:
            conn.executemany(sql, seq)
            conn.commit()
        finally:
            conn.close()

    # ---------- 受访者 ----------

    def next_interviewee_code(self) -> str:
        row = self.scalar("SELECT MAX(CAST(SUBSTR(code,2) AS INTEGER)) AS n "
                          "FROM interviewees WHERE code LIKE 'P%'")
        return f"P{(row['n'] or 0) + 1:02d}"

    def ensure_interviewee(self, code: str = "", **fields) -> int:
        """按编号获取或创建受访者；code 为空时自动分配 P0x。"""
        code = (code or "").strip() or self.next_interviewee_code()
        row = self.scalar("SELECT id FROM interviewees WHERE code = ?", (code,))
        if row:
            return row["id"]
        allowed = ("alias", "role", "org", "stage", "interview_date", "wave", "note")
        cols = ["code", "created_at"]
        vals = [code, now()]
        for k in allowed:
            if k in fields and fields[k] not in (None, ""):
                cols.append(k)
                vals.append(fields[k])
        sql = f"INSERT INTO interviewees ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
        return self.execute(sql, vals)

    # ---------- 设置 ----------

    def get_setting(self, key: str, default: str = "") -> str:
        row = self.scalar("SELECT value FROM settings WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    # ---------- 概览 ----------

    def stats(self) -> dict:
        def n(sql):
            return self.scalar(sql)["c"]
        return {
            "interviewees": n("SELECT COUNT(*) c FROM interviewees"),
            "documents": n("SELECT COUNT(*) c FROM documents"),
            "segments": n("SELECT COUNT(*) c FROM segments"),
            "codes": n("SELECT COUNT(*) c FROM codes"),
            "codings": n("SELECT COUNT(*) c FROM codings"),
            "coded_segments": n("SELECT COUNT(DISTINCT segment_id) c FROM codings"),
            "uncoded_segments": (n("SELECT COUNT(*) c FROM segments")
                                 - n("SELECT COUNT(DISTINCT segment_id) c FROM codings")),
            "unassigned_documents": n("SELECT COUNT(*) c FROM documents WHERE interviewee_id IS NULL"),
        }
