"""导出：CSV / XLSX / Markdown 引语汇编 / 整包备份。

CSV 一律用 UTF-8 BOM，Windows 上双击直接用 Excel 打开不乱码。
引语汇编按原文照录并附完整出处，方便论文写作时核对，不做任何润色或缩写。
"""

from __future__ import annotations

import csv
import zipfile
from datetime import datetime
from pathlib import Path

from . import config, coding
from .db import Database

SPEAKER_CN = {"interviewee": "受访者", "interviewer": "访谈者", "unknown": "未标注"}


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _write_csv(path: Path, headers: list[str], rows: list[list]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(rows)


def _write_xlsx(path: Path, headers: list[str], rows: list[list], title: str = "导出") -> None:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
    except ImportError as e:
        raise RuntimeError("导出 .xlsx 需要 openpyxl：pip install -r requirements.txt") from e
    wb = Workbook()
    ws = wb.active
    ws.title = title[:31]
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="4C6EF5")
        c.alignment = Alignment(vertical="center")
    for r in rows:
        ws.append([v if v is not None else "" for v in r])
    ws.freeze_panes = "A2"
    for col in ws.columns:
        width = max((len(str(c.value or "")) for c in col[:60]), default=8)
        ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, 10), 60)
    wb.save(path)


def _inventory_table(db: Database):
    headers = ["编号", "化名", "身份/岗位", "单位", "访谈日期", "轮次", "阶段",
               "材料标题", "来源文件", "格式", "段落数", "已编码段落数"]
    rows = [[r["code"] or "未归类", r["alias"], r["role"], r["org"],
             r["interview_date"], r["wave"], r["stage"], r["title"],
             r["source_path"], r["source_type"], r["segments"], r["coded_segments"]]
            for r in coding.inventory(db)]
    return headers, rows, "材料清单"


def _segments_table(db: Database):
    headers = ["段落编号", "受访者编号", "化名", "材料标题", "访谈日期", "阶段",
               "轮次", "说话人", "时间戳", "原文", "已打码号"]
    data = db.query(
        "SELECT s.label, i.code, i.alias, d.title, d.interview_date, d.stage, d.wave, "
        "s.speaker, s.timecode, s.text, s.id FROM segments s "
        "JOIN documents d ON d.id=s.document_id "
        "LEFT JOIN interviewees i ON i.id=d.interviewee_id ORDER BY d.id, s.seq"
    )
    cmap: dict = {}
    ids = [r["id"] for r in data]
    for i in range(0, len(ids), 900):
        cmap.update(coding.codes_for_segments(db, ids[i:i + 900]))
    rows = [[r["label"], r["code"] or "未归类", r["alias"], r["title"],
             r["interview_date"], r["stage"], r["wave"],
             SPEAKER_CN.get(r["speaker"], r["speaker"]), r["timecode"], r["text"],
             "；".join(c["name"] for c in cmap.get(r["id"], []))]
            for r in data]
    return headers, rows, "全部段落"


def _codings_table(db: Database):
    headers = ["段落编号", "码号", "码号定义", "编码备注", "受访者编号", "化名",
               "材料标题", "访谈日期", "阶段", "说话人", "原文"]
    data = db.query(
        "SELECT s.label, c.name AS code, c.definition, cg.memo, "
        "i.code AS person_code, i.alias, "
        "d.title, d.interview_date, d.stage, s.speaker, s.text "
        "FROM codings cg JOIN segments s ON s.id=cg.segment_id "
        "JOIN codes c ON c.id=cg.code_id JOIN documents d ON d.id=s.document_id "
        "LEFT JOIN interviewees i ON i.id=d.interviewee_id "
        "ORDER BY c.name, i.code, d.interview_date, s.seq"
    )
    rows = [[r["label"], r["code"], r["definition"], r["memo"],
             r["person_code"] or "未归类", r["alias"], r["title"],
             r["interview_date"], r["stage"],
             SPEAKER_CN.get(r["speaker"], r["speaker"]), r["text"]]
            for r in data]
    return headers, rows, "编码明细"


def _matrix_table(db: Database, rows_dim: str):
    m = coding.matrix(db, rows_dim)
    headers = [m["row_label"]] + [c["name"] for c in m["codes"]] + ["合计"]
    data = [[rk] + [m["cells"][rk][c["id"]] for c in m["codes"]] + [m["totals"][rk]]
            for rk in m["rows"]]
    return headers, data, f"交叉矩阵_{m['row_label']}"


def _quotations_markdown(db: Database, code_id: int | None) -> str:
    items = coding.quotations(db, code_id)
    lines = [
        "# 访谈引语汇编",
        "",
        f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "> 以下引语均为访谈原文照录，未作润色、缩写或拼接；",
        "> 每条均附可核查出处（受访者编号、材料、段落号、访谈日期、阶段）。",
        "> 引用时请自行核对原始录音/转录稿，并遵守研究伦理与匿名化约定。",
        "",
    ]
    if not items:
        lines.append("_尚无编码记录。请先在「编码」页给段落打码。_")
        return "\n".join(lines) + "\n"
    current = None
    for r in items:
        if r["code"] != current:
            current = r["code"]
            lines += [f"## 码号：{current}", ""]
            if r["definition"]:
                lines += [f"> 码号定义：{r['definition']}", ""]
        who = " / ".join(x for x in [r["person_code"] or "", r["alias"] or ""] if x) or "未归类"
        meta = "，".join(x for x in [who, r["document_title"],
                                    r["interview_date"] or "日期未注明",
                                    r["stage"] or "未分期",
                                    f"段落 {r['label']}",
                                    SPEAKER_CN.get(r["speaker"], r["speaker"])] if x)
        lines += [f"### {meta}", "", f"> {r['text']}", ""]
        if r["timecode"]:
            lines += [f"时间戳：{r['timecode']}", ""]
    return "\n".join(lines)


def _backup(db: Database, path: Path) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        dbfile = Path(db.path)
        if dbfile.exists():
            z.write(dbfile, "data/atlas.db")
        if config.SOURCES_DIR.exists():
            for p in sorted(config.SOURCES_DIR.rglob("*")):
                if p.is_file():
                    z.write(p, str(Path("data/sources") / p.relative_to(config.SOURCES_DIR)))
        z.writestr("说明.txt",
                   "interview-atlas 项目备份\n"
                   f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                   "data/atlas.db    = 受访者、材料、段落、码本、编码记录（SQLite）\n"
                   "data/sources/    = 导入的原始逐字稿副本\n\n"
                   "恢复方法：把本压缩包解压到项目根目录，覆盖 data/ 后重启应用即可。\n")


def export(db: Database, kind: str, fmt: str = "csv", rows: str = "interviewee",
           code_id: int | None = None) -> Path:
    """执行导出，返回生成的文件路径。"""
    config.EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = _stamp()
    if kind == "backup":
        path = config.EXPORT_DIR / f"atlas_backup_{ts}.zip"
        _backup(db, path)
        return path

    if kind == "quotations":
        if fmt in ("md", "markdown"):
            path = config.EXPORT_DIR / f"引语汇编_{ts}.md"
            path.write_text(_quotations_markdown(db, code_id), encoding="utf-8")
            return path
        items = coding.quotations(db, code_id)
        headers = ["码号", "码号定义", "段落编号", "受访者编号", "化名", "材料标题",
                   "访谈日期", "阶段", "说话人", "时间戳", "原文"]
        data = [[r["code"], r["definition"], r["label"], r["person_code"] or "未归类",
                 r["alias"], r["document_title"], r["interview_date"], r["stage"],
                 SPEAKER_CN.get(r["speaker"], r["speaker"]), r["timecode"], r["text"]]
                for r in items]
        title = "引语汇编"
    elif kind == "inventory":
        headers, data, title = _inventory_table(db)
    elif kind == "segments":
        headers, data, title = _segments_table(db)
    elif kind == "codings":
        headers, data, title = _codings_table(db)
    elif kind == "matrix":
        headers, data, title = _matrix_table(db, rows)
    else:
        raise ValueError(f"未知导出类型：{kind}")

    suffix = "xlsx" if fmt == "xlsx" else "csv"
    path = config.EXPORT_DIR / f"{title}_{ts}.{suffix}"
    if fmt == "xlsx":
        _write_xlsx(path, headers, data, title)
    else:
        _write_csv(path, headers, data)
    return path


def list_exports() -> list[dict]:
    if not config.EXPORT_DIR.exists():
        return []
    out = []
    for p in sorted(config.EXPORT_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.is_file():
            out.append({"name": p.name, "size": p.stat().st_size,
                        "mtime": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")})
    return out
