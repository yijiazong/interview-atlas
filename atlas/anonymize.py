"""脱敏处理：真实姓名 -> 化名/编号的映射，一键应用到全部原文与标题。

刻意不做「自动猜姓名」：中文姓名与专有名词边界模糊，自动替换容易误伤正文，
也会让访谈原文失真。这里只按你确认过的映射表做精确字符串替换，可预览、可核对。
"""

from __future__ import annotations

from .db import Database


def list_aliases(db: Database) -> list[dict]:
    return db.query("SELECT id, real_name, replacement FROM aliases ORDER BY id")


def add_alias(db: Database, real_name: str, replacement: str) -> int:
    real_name, replacement = real_name.strip(), replacement.strip()
    if not real_name or not replacement:
        raise ValueError("真实姓名与替换文本都不能为空")
    return db.execute(
        "INSERT INTO aliases(real_name,replacement) VALUES(?,?) "
        "ON CONFLICT(real_name) DO UPDATE SET replacement=excluded.replacement",
        (real_name, replacement),
    )


def remove_alias(db: Database, alias_id: int) -> None:
    db.execute("DELETE FROM aliases WHERE id=?", (alias_id,))


def mapping(db: Database) -> dict[str, str]:
    return {r["real_name"]: r["replacement"] for r in list_aliases(db)}


def preview(text: str, pairs: dict[str, str]) -> tuple[str, int]:
    """返回 (替换后文本, 替换次数)，供界面预览。"""
    hits = 0
    # 长词优先，避免「张」先于「张三」被替换
    for src in sorted(pairs, key=len, reverse=True):
        hits += text.count(src)
        text = text.replace(src, pairs[src])
    return text, hits


def apply_to_all(db: Database) -> dict:
    """把映射应用到 segments.text 与 documents.title。返回替换统计。"""
    pairs = mapping(db)
    if not pairs:
        return {"segments_changed": 0, "documents_changed": 0, "replacements": 0}
    total, seg_changed, doc_changed = 0, 0, 0
    seg_updates, doc_updates = [], []
    for row in db.query("SELECT id, text FROM segments"):
        new, hits = preview(row["text"], pairs)
        if hits:
            seg_updates.append((new, row["id"]))
            total += hits
            seg_changed += 1
    for row in db.query("SELECT id, title FROM documents"):
        new, hits = preview(row["title"], pairs)
        if hits:
            doc_updates.append((new, row["id"]))
            total += hits
            doc_changed += 1
    if seg_updates:
        db.executemany("UPDATE segments SET text=? WHERE id=?", seg_updates)
    if doc_updates:
        db.executemany("UPDATE documents SET title=? WHERE id=?", doc_updates)
    return {"segments_changed": seg_changed, "documents_changed": doc_changed,
            "replacements": total}


def auto_alias_interviewees(db: Database) -> int:
    """给还没化名的受访者补一个中性代称（受访者A/B…），方便导出匿名清单。"""
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    rows = db.query("SELECT id, code FROM interviewees WHERE alias IS NULL OR alias='' ORDER BY id")
    for i, r in enumerate(rows):
        db.execute("UPDATE interviewees SET alias=? WHERE id=?", (f"受访者{letters[i % 26]}", r["id"]))
    return len(rows)
