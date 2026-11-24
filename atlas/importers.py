"""材料导入：把不同格式的逐字稿规范化为「带说话人、带编号的段落」。

所有解析器都返回 ParsedDoc，后续归档、编码、检索只认这套结构。
说话人标签只在行首且匹配已知写法时才识别，避免把正文里的冒号误判成问答。
"""

from __future__ import annotations

import csv
import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import config
from .db import now

# 行首「标签：内容」
_SPEAKER_LINE = re.compile(r"^\s*[\[\(（【]?\s*([^:：\n]{1,14}?)\s*[\]\)）】]?\s*[:：]\s*(.*)$")
_TIMECODE_LINE = re.compile(r"(\d{1,2}:\d{2}(?::\d{2})?[.,]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}(?::\d{2})?[.,]\d{1,3})")
_DATE_IN_NAME = re.compile(r"(20\d{2})[-_.年](\d{1,2})[-_.月](\d{1,2})")
_CODE_IN_NAME = re.compile(r"(?:^|[^A-Za-z])(P\s*0?\d{1,3})(?:$|[^A-Za-z])", re.I)
_WAVE_IN_NAME = re.compile(r"(?:第\s*(\d+)\s*轮|wave\s*(\d+)|[_\-\s]T(\d+)(?:$|[_\-\s]))", re.I)
_SENTENCE_END = re.compile(r"(?<=[。！？!?；;])")


@dataclass
class Segment:
    text: str
    speaker: str = "unknown"       # interviewee / interviewer / unknown
    timecode: str = ""
    start: str = ""
    end: str = ""


@dataclass
class ParsedDoc:
    title: str
    segments: list[Segment] = field(default_factory=list)
    source_type: str = ""
    code: str = ""
    role: str = ""
    alias: str = ""
    interview_date: str = ""
    wave: int = 1
    stage: str = ""
    warnings: list[str] = field(default_factory=list)


class ImportError_(Exception):
    """导入失败（格式不支持、缺依赖、内容为空等）。"""


def _classify(tag: str) -> str | None:
    t = tag.strip().lower().replace(" ", "").replace("\u3000", "")
    if t in config.INTERVIEWER_TAGS:
        return "interviewer"
    if t in config.INTERVIEWEE_TAGS:
        return "interviewee"
    if re.fullmatch(r"(q|i|interviewer)\d*", t):
        return "interviewer"
    if re.fullmatch(r"(a|r|p|respondent|interviewee)\d*", t):
        return "interviewee"
    return None


def _split_oversized(text: str, limit: int = 800) -> list[str]:
    """没有空行/说话人标签的长文本，按句末标点再切，保证段落可编码。"""
    if len(text) <= limit:
        return [text]
    pieces, buf = [], ""
    for sent in _SENTENCE_END.split(text):
        if not sent:
            continue
        if buf and len(buf) + len(sent) > limit:
            pieces.append(buf.strip())
            buf = sent
        else:
            buf += sent
    if buf.strip():
        pieces.append(buf.strip())
    return pieces or [text]


def _lines_to_segments(lines: list[str]) -> list[Segment]:
    segments: list[Segment] = []
    speaker, buf = "unknown", []

    def flush():
        nonlocal buf
        text = " ".join(x.strip() for x in buf if x.strip()).strip()
        for chunk in _split_oversized(text):
            if chunk:
                segments.append(Segment(text=chunk, speaker=speaker))
        buf = []

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            flush()
            continue
        m = _SPEAKER_LINE.match(line)
        role = _classify(m.group(1)) if m else None
        if role:
            flush()
            speaker = role
            rest = m.group(2).strip()
            # 「问：」这类带方括号的时间戳前缀，交给转写解析器处理，这里只取文本
            if rest:
                buf.append(rest)
        else:
            buf.append(line.strip())
    flush()
    return segments


def parse_plain(text: str, source_type: str = ".txt") -> list[Segment]:
    return _lines_to_segments(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"))


def parse_srt(text: str) -> list[Segment]:
    text = text.replace("\r\n", "\n")
    text = re.sub(r"^WEBVTT[^\n]*\n", "", text.strip())
    out: list[Segment] = []
    for block in re.split(r"\n\s*\n", text):
        lines = [l.strip() for l in block.strip().splitlines() if l.strip()]
        if not lines:
            continue
        tc_idx = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if tc_idx is None:
            continue
        m = _TIMECODE_LINE.search(lines[tc_idx])
        start, end = (m.group(1), m.group(2)) if m else ("", "")
        content = " ".join(lines[tc_idx + 1:]).strip()
        speaker = "unknown"
        sm = _SPEAKER_LINE.match(content)
        role = _classify(sm.group(1)) if sm else None
        if role:
            speaker, content = role, sm.group(2).strip()
        if content:
            out.append(Segment(text=content, speaker=speaker,
                               timecode=f"{start} --> {end}".strip(" ->"),
                               start=start, end=end))
    return out


def parse_json(text: str) -> list[Segment]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ImportError_(f"JSON 解析失败：{e}")
    if isinstance(data, dict):
        for key in ("segments", "utterances", "results", "data", "转写", "段落"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = [data]
    if not isinstance(data, list):
        raise ImportError_("JSON 需要是数组，或含 segments/utterances 数组的对象")
    out: list[Segment] = []
    for item in data:
        if isinstance(item, str):
            out.append(Segment(text=item.strip()))
            continue
        if not isinstance(item, dict):
            continue
        content = ""
        for k in ("text", "content", "transcript", "文本", "内容", "原文"):
            if item.get(k):
                content = str(item[k]).strip()
                break
        if not content:
            continue
        tag = ""
        for k in ("speaker", "role", "label", "说话人", "角色"):
            if item.get(k):
                tag = str(item[k])
                break
        speaker = _classify(tag) or ("unknown" if not tag else "named:" + tag)
        start = str(item.get("start", item.get("开始", "")) or "")
        end = str(item.get("end", item.get("结束", "")) or "")
        out.append(Segment(text=content, speaker=speaker,
                           timecode=str(item.get("timecode", "")) or
                           (f"{start} --> {end}" if start or end else ""),
                           start=start, end=end))
    return out


def parse_table(path: Path) -> list[Segment]:
    """CSV / XLSX：自动找文本列、说话人列、时间列。"""
    rows: list[dict] = []
    if path.suffix.lower() == ".csv":
        for enc in ("utf-8-sig", "utf-8", "gbk"):
            try:
                with path.open("r", encoding=enc, newline="") as f:
                    rows = [dict(r) for r in csv.DictReader(f)]
                break
            except UnicodeDecodeError:
                continue
    else:
        try:
            from openpyxl import load_workbook
        except ImportError as e:
            raise ImportError_("读取 .xlsx 需要 openpyxl：pip install -r requirements.txt") from e
        wb = load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        values = list(ws.values)
        if not values:
            return []
        header = [str(c or "").strip() for c in values[0]]
        for row in values[1:]:
            rows.append({header[i]: row[i] for i in range(min(len(header), len(row)))})
        wb.close()

    def pick(row: dict, keys: tuple[str, ...]):
        for k, v in row.items():
            if str(k).strip().lower() in keys and v not in (None, ""):
                return str(v).strip()
        return ""

    out = []
    for row in rows:
        content = pick(row, ("text", "content", "transcript", "文本", "内容", "原文", "转录", "逐字稿"))
        if not content:
            content = " | ".join(f"{k}：{v}" for k, v in row.items() if v not in (None, ""))
        if not content.strip():
            continue
        tag = pick(row, ("speaker", "role", "label", "说话人", "角色"))
        speaker = _classify(tag) or ("unknown" if not tag else "named:" + tag)
        tc = pick(row, ("timecode", "time", "start", "时间戳", "时间"))
        out.append(Segment(text=content, speaker=speaker, timecode=tc))
    return out


def parse_docx(path: Path) -> list[str]:
    try:
        from docx import Document
    except ImportError as e:
        raise ImportError_("读取 .docx 需要 python-docx：pip install -r requirements.txt") from e
    doc = Document(str(path))
    lines = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            lines.append(" | ".join(c.text.strip() for c in row.cells))
    return lines


def parse_pdf(path: Path) -> list[str]:
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise ImportError_("读取 .pdf 需要 pypdf：pip install -r requirements.txt") from e
    reader = PdfReader(str(path))
    lines: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        lines.extend(text.split("\n"))
    return lines


def guess_metadata(path: Path) -> dict:
    """从文件名推断编号 / 日期 / 轮次 / 身份，推断结果一律可在界面里改。"""
    stem = path.stem
    meta = {"code": "", "interview_date": "", "wave": 1, "role": "", "alias": ""}
    m = _DATE_IN_NAME.search(stem)
    if m:
        meta["interview_date"] = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = _CODE_IN_NAME.search(stem)
    if m:
        meta["code"] = re.sub(r"\s+", "", m.group(1)).upper()
    m = _WAVE_IN_NAME.search(stem)
    if m:
        meta["wave"] = int(next(g for g in m.groups() if g))
    rest = _DATE_IN_NAME.sub("", stem)
    rest = _CODE_IN_NAME.sub("", rest)
    rest = _WAVE_IN_NAME.sub("", rest)
    tokens = [t for t in re.split(r"[_\-\s（）()\[\]]+", rest) if t and not t.isdigit()]
    if tokens:
        meta["role"] = tokens[0][:20]
    if len(tokens) > 1:
        meta["alias"] = tokens[1][:20]
    return meta


def parse_file(path: str | Path) -> ParsedDoc:
    path = Path(path)
    ext = path.suffix.lower()
    if ext not in config.SUPPORTED_EXT:
        raise ImportError_(f"暂不支持 {ext or '无扩展名'}；支持：{', '.join(sorted(config.SUPPORTED_EXT))}")
    meta = guess_metadata(path)
    doc = ParsedDoc(title=path.stem, source_type=ext.lstrip("."), **meta)

    if ext in config.TRANSCRIPT_EXT & {".srt", ".vtt"}:
        doc.segments = parse_srt(path.read_text(encoding="utf-8-sig", errors="replace"))
    elif ext == ".json":
        doc.segments = parse_json(path.read_text(encoding="utf-8-sig", errors="replace"))
    elif ext in config.TABLE_EXT:
        doc.segments = parse_table(path)
    elif ext == ".docx":
        doc.segments = _lines_to_segments(parse_docx(path))
    elif ext == ".pdf":
        doc.segments = _lines_to_segments(parse_pdf(path))
        doc.warnings.append("PDF 提取可能出现断行或错字，请在「材料」里人工核对段落后再编码。")
    else:
        doc.segments = parse_plain(path.read_text(encoding="utf-8-sig", errors="replace"), ext)

    doc.segments = [s for s in doc.segments if s.text.strip()]
    if not doc.segments:
        raise ImportError_("没有解析出可用文本；请检查文件是否为空或是否为扫描版 PDF。")
    if all(s.speaker == "unknown" for s in doc.segments):
        doc.warnings.append("未识别到说话人标签，全部段落记为「未标注」；可在导入后批量指定。")
    return doc


# ---------- 入库 ----------

def label_for(code: str, seq: int, doc_id: int) -> str:
    prefix = code or f"D{doc_id:02d}"
    return f"{prefix}-{seq:03d}"


def create_document(db, parsed: ParsedDoc, *, source_path: str = "",
                    source_type: str = "", interviewee_code: str = "",
                    stage: str = "", interviewee_id: int | None = None) -> dict:
    """把 ParsedDoc 写进数据库。返回导入摘要。"""
    if not parsed.segments:
        raise ImportError_("没有可导入的段落")
    code = (interviewee_code or parsed.code or "").strip()
    if interviewee_id is None and code:
        interviewee_id = db.ensure_interviewee(
            code, alias=parsed.alias, role=parsed.role,
            interview_date=parsed.interview_date, wave=parsed.wave,
            stage=stage or parsed.stage,
        )
    doc_stage = stage or parsed.stage
    doc_id = db.execute(
        "INSERT INTO documents(interviewee_id,title,source_path,source_type,stage,wave,"
        "interview_date,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (interviewee_id, parsed.title, source_path, source_type or parsed.source_type,
         doc_stage, parsed.wave, parsed.interview_date, now()),
    )
    real_code = code
    if not real_code and interviewee_id:
        row = db.scalar("SELECT code FROM interviewees WHERE id=?", (interviewee_id,))
        real_code = row["code"] if row else ""
    db.executemany(
        "INSERT INTO segments(document_id,seq,label,speaker,timecode,text) VALUES(?,?,?,?,?,?)",
        [(doc_id, i, label_for(real_code, i, doc_id), s.speaker, s.timecode, s.text)
         for i, s in enumerate(parsed.segments, 1)],
    )
    return {
        "document_id": doc_id,
        "title": parsed.title,
        "segments": len(parsed.segments),
        "interviewee_id": interviewee_id,
        "code": real_code,
        "warnings": parsed.warnings,
    }


def import_file(db, path: str | Path, *, interviewee_code: str = "", stage: str = "",
                copy_source: bool = True) -> dict:
    """导入磁盘上的一个文件；默认把原件复制进 data/sources/ 以便备份。"""
    path = Path(path)
    parsed = parse_file(path)
    source_rel = ""
    if copy_source:
        config.SOURCES_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        safe = re.sub(r"[^\w.\-\u4e00-\u9fff]+", "_", path.name)
        target = config.SOURCES_DIR / f"{stamp}_{safe}"
        shutil.copy2(path, target)
        source_rel = str(target.relative_to(config.DATA_DIR))
    return create_document(db, parsed, source_path=source_rel, source_type=parsed.source_type,
                           interviewee_code=interviewee_code, stage=stage)


def scan_directory(db, directory: str | Path, *, stage: str = "") -> dict:
    """批量导入一个文件夹里的所有支持格式（不递归进子文件夹，避免误吞备份）。"""
    directory = Path(directory)
    if not directory.is_dir():
        raise ImportError_(f"不是有效文件夹：{directory}")
    # 跳过说明类文件：以 _ / . 开头，或 README*（examples/ 里的说明文档不该被当成访谈稿）
    files = sorted(p for p in directory.iterdir()
                   if p.is_file()
                   and p.suffix.lower() in config.SUPPORTED_EXT
                   and not p.name.startswith((".", "_"))
                   and not p.name.lower().startswith("readme"))
    results, errors = [], []
    for p in files:
        try:
            results.append(import_file(db, p, stage=stage))
        except Exception as e:                       # 单份失败不影响其余
            errors.append({"file": p.name, "error": str(e)})
    return {"imported": results, "errors": errors, "total": len(files)}


def assign_document(db, doc_id: int, interviewee_id: int | None) -> None:
    """改归属后重编段落引用号，保证论文里引用的编号不会指向错人。"""
    row = db.scalar("SELECT code FROM interviewees WHERE id=?", (interviewee_id,)) if interviewee_id else None
    code = row["code"] if row else ""
    db.execute("UPDATE documents SET interviewee_id=? WHERE id=?", (interviewee_id, doc_id))
    segs = db.query("SELECT id, seq FROM segments WHERE document_id=? ORDER BY seq", (doc_id,))
    db.executemany("UPDATE segments SET label=? WHERE id=?",
                   [(label_for(code, s["seq"], doc_id), s["id"]) for s in segs])


def set_segments_speaker(db, doc_id: int, speaker: str) -> int:
    segs = db.query("SELECT id FROM segments WHERE document_id=?", (doc_id,))
    db.executemany("UPDATE segments SET speaker=? WHERE id=?", [(speaker, s["id"]) for s in segs])
    return len(segs)
