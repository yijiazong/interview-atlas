"""质性编码层：码本、段落打码、检索、交叉矩阵、时间分期统计、材料清单。

一条编码 = 某个段落 × 某个码号。所有统计都从这条关系实时算，不存冗余计数，
避免改码本后数字对不上（学术论文里的数字必须能复算）。
"""

from __future__ import annotations

from collections import defaultdict

from .db import Database, now

ROW_EXPR = {
    "interviewee": "COALESCE(NULLIF(i.code,''), '未归类')",
    "stage": "COALESCE(NULLIF(d.stage,''), '未分期')",
    "wave": "'第 ' || d.wave || ' 轮'",
    "year": "COALESCE(NULLIF(SUBSTR(d.interview_date,1,4),''), '未注明日期')",
    "speaker": "s.speaker",
}
ROW_LABEL = {
    "interviewee": "受访者", "stage": "阶段", "wave": "访谈轮次",
    "year": "访谈年份", "speaker": "说话人",
}


# ---------- 码本 ----------

def list_codes(db: Database) -> list[dict]:
    return db.query(
        "SELECT c.*, (SELECT COUNT(*) FROM codings x WHERE x.code_id=c.id) AS usage "
        "FROM codes c ORDER BY COALESCE(c.parent_id,0), c.name"
    )


def add_code(db: Database, name: str, definition: str = "", color: str = "#4C6EF5",
             parent_id: int | None = None, memo: str = "") -> int:
    name = name.strip()
    if not name:
        raise ValueError("码号名称不能为空")
    return db.execute(
        "INSERT INTO codes(name,definition,color,parent_id,memo,created_at) VALUES(?,?,?,?,?,?)",
        (name, definition.strip(), color, parent_id, memo.strip(), now()),
    )


def update_code(db: Database, code_id: int, **fields) -> None:
    allowed = ("name", "definition", "color", "parent_id", "memo")
    sets, vals = [], []
    for k in allowed:
        if k in fields:
            sets.append(f"{k}=?")
            vals.append(fields[k])
    if not sets:
        return
    vals.append(code_id)
    db.execute(f"UPDATE codes SET {','.join(sets)} WHERE id=?", vals)


def delete_code(db: Database, code_id: int) -> None:
    db.execute("DELETE FROM codes WHERE id=?", (code_id,))


# ---------- 打码 ----------

def code_segment(db: Database, segment_id: int, code_id: int, memo: str = "") -> None:
    db.execute(
        "INSERT INTO codings(segment_id,code_id,memo,created_at) VALUES(?,?,?,?) "
        "ON CONFLICT(segment_id,code_id) DO UPDATE SET memo=excluded.memo",
        (segment_id, code_id, memo.strip(), now()),
    )


def uncode_segment(db: Database, segment_id: int, code_id: int) -> None:
    db.execute("DELETE FROM codings WHERE segment_id=? AND code_id=?", (segment_id, code_id))


def codes_for_segments(db: Database, segment_ids: list[int]) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = defaultdict(list)
    if not segment_ids:
        return out
    ids = segment_ids[:900]
    ph = ",".join("?" * len(ids))
    for r in db.query(
        f"SELECT cg.segment_id, c.id, c.name, c.color, cg.memo "
        f"FROM codings cg JOIN codes c ON c.id=cg.code_id "
        f"WHERE cg.segment_id IN ({ph}) ORDER BY c.name", ids
    ):
        out[r["segment_id"]].append(
            {"id": r["id"], "name": r["name"], "color": r["color"], "memo": r["memo"]}
        )
    return out


# ---------- 段落浏览与校对 ----------

def document_segments(db: Database, doc_id: int, *, only_uncoded: bool = False,
                      q: str = "", limit: int = 800) -> list[dict]:
    sql = ("SELECT s.* FROM segments s WHERE s.document_id=? ")
    params: list = [doc_id]
    if only_uncoded:
        sql += "AND NOT EXISTS (SELECT 1 FROM codings cg WHERE cg.segment_id=s.id) "
    if q.strip():
        sql += "AND s.text LIKE ? "
        params.append(f"%{q.strip()}%")
    sql += "ORDER BY s.seq LIMIT ?"
    params.append(limit)
    rows = db.query(sql, params)
    cmap = codes_for_segments(db, [r["id"] for r in rows])
    for r in rows:
        r["codes"] = cmap.get(r["id"], [])
    return rows


def update_segment(db: Database, segment_id: int, *, text: str | None = None,
                   speaker: str | None = None) -> None:
    sets, vals = [], []
    if text is not None:
        sets.append("text=?")
        vals.append(text.strip())
    if speaker is not None:
        sets.append("speaker=?")
        vals.append(speaker)
    if sets:
        vals.append(segment_id)
        db.execute(f"UPDATE segments SET {','.join(sets)} WHERE id=?", vals)


def delete_segment(db: Database, segment_id: int) -> None:
    db.execute("DELETE FROM segments WHERE id=?", (segment_id,))


def list_documents(db: Database) -> list[dict]:
    return db.query(
        "SELECT d.*, i.code, i.alias, i.role, "
        "(SELECT COUNT(*) FROM segments s WHERE s.document_id=d.id) AS segments, "
        "(SELECT COUNT(*) FROM codings cg JOIN segments s2 ON s2.id=cg.segment_id "
        " WHERE s2.document_id=d.id) AS codings "
        "FROM documents d LEFT JOIN interviewees i ON i.id=d.interviewee_id "
        "ORDER BY i.code, d.interview_date, d.id"
    )


def list_interviewees(db: Database) -> list[dict]:
    return db.query(
        "SELECT i.*, "
        "(SELECT COUNT(*) FROM documents d WHERE d.interviewee_id=i.id) AS documents, "
        "(SELECT COUNT(*) FROM segments s JOIN documents d2 ON d2.id=s.document_id "
        " WHERE d2.interviewee_id=i.id) AS segments "
        "FROM interviewees i ORDER BY i.code"
    )


# ---------- 检索 ----------

def search(db: Database, *, codes: list[int] | None = None, q: str = "",
           interviewee_id: int | None = None, stage: str = "", speaker: str = "",
           limit: int = 500) -> list[dict]:
    """按码号（任一命中）、关键词、受访者、阶段、说话人筛选段落。"""
    sql = (
        "SELECT s.id, s.seq, s.label, s.speaker, s.timecode, s.text, "
        "d.id AS document_id, d.title AS document_title, d.interview_date, d.stage, d.wave, "
        "i.code, i.alias, i.role "
        "FROM segments s JOIN documents d ON d.id=s.document_id "
        "LEFT JOIN interviewees i ON i.id=d.interviewee_id WHERE 1=1 "
    )
    params: list = []
    if codes:
        sql += f"AND EXISTS (SELECT 1 FROM codings cg WHERE cg.segment_id=s.id " \
               f"AND cg.code_id IN ({','.join('?' * len(codes))})) "
        params.extend(codes)
    if q.strip():
        sql += "AND s.text LIKE ? "
        params.append(f"%{q.strip()}%")
    if interviewee_id:
        sql += "AND d.interviewee_id=? "
        params.append(interviewee_id)
    if stage.strip():
        sql += "AND (d.stage=? OR i.stage=?) "
        params.extend([stage.strip(), stage.strip()])
    if speaker.strip():
        sql += "AND s.speaker=? "
        params.append(speaker.strip())
    sql += "ORDER BY i.code, d.interview_date, s.seq LIMIT ?"
    params.append(limit)
    rows = db.query(sql, params)
    cmap = codes_for_segments(db, [r["id"] for r in rows])
    for r in rows:
        r["codes"] = cmap.get(r["id"], [])
    return rows


# ---------- 统计 ----------

def frequency(db: Database, group_by: str = "stage",
              code_ids: list[int] | None = None) -> list[dict]:
    expr = ROW_EXPR.get(group_by, ROW_EXPR["stage"])
    sql = (
        f"SELECT {expr} AS grp, c.id AS code_id, c.name AS code, "
        "COUNT(DISTINCT s.id) AS n "
        "FROM codings cg JOIN codes c ON c.id=cg.code_id "
        "JOIN segments s ON s.id=cg.segment_id "
        "JOIN documents d ON d.id=s.document_id "
        "LEFT JOIN interviewees i ON i.id=d.interviewee_id WHERE 1=1 "
    )
    params: list = []
    if code_ids:
        sql += f"AND c.id IN ({','.join('?' * len(code_ids))}) "
        params.extend(code_ids)
    sql += "GROUP BY grp, c.id ORDER BY grp, c.name"
    return db.query(sql, params)


def matrix(db: Database, rows: str = "interviewee",
           code_ids: list[int] | None = None) -> dict:
    """交叉表：行=受访者/阶段/轮次/年份，列=码号，值=段落数。"""
    if rows not in ROW_EXPR:
        raise ValueError(f"不支持的行维度：{rows}")
    long = frequency(db, rows, code_ids)
    row_keys = sorted({r["grp"] for r in long})
    code_map: dict[int, str] = {}
    for r in long:
        code_map[r["code_id"]] = r["code"]
    # 没有编码记录时，仍把码本列出来，方便看清「哪些码还没用」
    for c in list_codes(db):
        code_map.setdefault(c["id"], c["name"])
    codes = [{"id": k, "name": v} for k, v in sorted(code_map.items(), key=lambda x: x[1])]
    cells = {rk: {c["id"]: 0 for c in codes} for rk in row_keys}
    for r in long:
        cells[r["grp"]][r["code_id"]] = r["n"]
    return {"row_label": ROW_LABEL[rows], "rows": row_keys, "codes": codes,
            "cells": cells,
            "totals": {rk: sum(cells[rk].values()) for rk in row_keys}}


def inventory(db: Database) -> list[dict]:
    """材料清单：一份访谈一行，含脱敏编号、阶段与编码进度。"""
    return db.query(
        "SELECT d.id, d.title, d.source_path, d.source_type, d.interview_date, "
        "d.wave, d.stage, i.code, i.alias, i.role, i.org, "
        "(SELECT COUNT(*) FROM segments s WHERE s.document_id=d.id) AS segments, "
        "(SELECT COUNT(DISTINCT s2.id) FROM segments s2 JOIN codings cg ON cg.segment_id=s2.id "
        " WHERE s2.document_id=d.id) AS coded_segments "
        "FROM documents d LEFT JOIN interviewees i ON i.id=d.interviewee_id "
        "ORDER BY i.code, d.interview_date, d.id"
    )


def quotations(db: Database, code_id: int | None = None) -> list[dict]:
    """引语汇编：写论文时按码号找原话，每条都带可核查出处。"""
    return db.query(
        "SELECT c.id AS code_id, c.name AS code, c.definition, "
        "s.id AS segment_id, s.label, s.seq, s.text, s.speaker, s.timecode, "
        "d.title AS document_title, d.interview_date, d.stage, "
        "i.code AS person_code, i.alias "
        "FROM codings cg JOIN codes c ON c.id=cg.code_id "
        "JOIN segments s ON s.id=cg.segment_id "
        "JOIN documents d ON d.id=s.document_id "
        "LEFT JOIN interviewees i ON i.id=d.interviewee_id "
        "WHERE (c.id=? OR ? IS NULL) "
        "ORDER BY c.name, i.code, d.interview_date, s.seq",
        (code_id, code_id),
    )
