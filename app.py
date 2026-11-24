"""interview-atlas 本地 Web 服务。

只监听 127.0.0.1：访谈材料属于敏感数据，这个工具不该暴露到局域网或公网。
启动：python app.py   （可选环境变量 ATLAS_PORT / ATLAS_NO_BROWSER）
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import traceback
import webbrowser
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from atlas import anonymize, coding, config, exporters, importers, transcribe
from atlas.db import Database
from atlas.importers import ImportError_
from atlas.transcribe import TranscribeUnavailable

config.ensure_dirs()
db = Database(config.DB_PATH)
app = Flask(__name__, static_folder=str(config.WEB_DIR), static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024   # 单份材料上限 200MB
app.json.ensure_ascii = False


def ok(data):
    return jsonify({"ok": True, "data": data})


def api(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return ok(fn(*args, **kwargs))
        except (ValueError, ImportError_, TranscribeUnavailable, RuntimeError, OSError, KeyError) as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        except Exception as e:                                  # noqa: BLE001
            traceback.print_exc()
            return jsonify({"ok": False, "error": f"服务器错误：{e}"}), 500
    return wrapper


def body() -> dict:
    return request.get_json(silent=True) or {}


def _safe_name(name: str) -> str:
    name = Path(name or "upload.txt").name
    cleaned = re.sub(r"[^\w.\-\u4e00-\u9fff]+", "_", name)
    return cleaned[:120] or "upload.txt"


def ingest_path(path: Path, *, stage: str = "", code: str = "", saved: bool = False) -> dict:
    """解析并入库；原件统一留一份在 data/sources/ 便于备份与核查。"""
    parsed = importers.parse_file(path)
    if saved:
        rel = str(Path(path).relative_to(config.DATA_DIR))
    else:
        config.SOURCES_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
        target = config.SOURCES_DIR / f"{stamp}_{_safe_name(path.name)}"
        shutil.copy2(path, target)
        rel = str(target.relative_to(config.DATA_DIR))
    return importers.create_document(
        db, parsed, source_path=rel, source_type=parsed.source_type,
        interviewee_code=code, stage=stage,
    )


def stages() -> list[str]:
    raw = db.get_setting("stages", "")
    if raw:
        try:
            v = json.loads(raw)
            if isinstance(v, list) and v:
                return v
        except json.JSONDecodeError:
            pass
    return list(config.DEFAULT_STAGES)


# ---------- 页面 ----------

@app.get("/")
def index():
    return send_from_directory(str(config.WEB_DIR), "index.html")


# ---------- 概览 / 设置 ----------

@app.get("/api/state")
@api
def state():
    return {
        "stats": db.stats(),
        "stages": stages(),
        "transcribe": transcribe.status(),
        "paths": {"data": str(config.DATA_DIR), "export": str(config.EXPORT_DIR),
                  "examples": str(config.EXAMPLES_DIR)},
        "version": "0.1.0",
    }


@app.post("/api/settings")
@api
def save_settings():
    data = body()
    if "stages" in data:
        v = data["stages"]
        if isinstance(v, str):
            v = [x.strip() for x in v.splitlines() if x.strip()]
        if not isinstance(v, list):
            raise ValueError("stages 需要是数组或换行分隔的文本")
        db.set_setting("stages", json.dumps([str(x) for x in v], ensure_ascii=False))
    return {"stages": stages()}


# ---------- 材料导入 ----------

@app.post("/api/import")
@api
def upload_files():
    files = request.files.getlist("files")
    if not files:
        raise ValueError("没有收到文件")
    stage = request.form.get("stage", "")
    code = request.form.get("code", "")
    results, errors = [], []
    config.SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    for f in files:
        if not f.filename:
            continue
        # 用时间戳子目录隔离同名文件，保留原文件名：编号/日期/身份都靠文件名推断，
        # 若给文件名加前缀，会污染材料标题与元数据。
        folder = config.SOURCES_DIR / datetime.now().strftime("%Y%m%d%H%M%S%f")
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / _safe_name(f.filename)
        f.save(str(target))
        try:
            results.append(ingest_path(target, stage=stage, code=code, saved=True))
        except Exception as e:                                # noqa: BLE001
            errors.append({"file": f.filename, "error": str(e)})
    if not results and errors:
        raise ValueError("；".join(f"{e['file']}：{e['error']}" for e in errors))
    return {"imported": results, "errors": errors, "stats": db.stats()}


@app.post("/api/import/folder")
@api
def import_folder():
    data = body()
    path = Path(data.get("path", "")).expanduser()
    if not path.is_dir():
        raise ValueError(f"不是有效文件夹：{path}")
    result = importers.scan_directory(db, path, stage=data.get("stage", ""))
    result["stats"] = db.stats()
    return result


@app.get("/api/documents")
@api
def documents():
    return coding.list_documents(db)


@app.get("/api/documents/<int:doc_id>")
@api
def document_detail(doc_id: int):
    d = db.scalar("SELECT * FROM documents WHERE id=?", (doc_id,))
    if not d:
        raise ValueError("材料不存在")
    i = db.scalar("SELECT * FROM interviewees WHERE id=?", (d["interviewee_id"],)) \
        if d["interviewee_id"] else None
    return {"document": d, "interviewee": i,
            "segments": coding.document_segments(db, doc_id)}


@app.delete("/api/documents/<int:doc_id>")
@api
def delete_document(doc_id: int):
    db.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    return {"stats": db.stats()}


@app.post("/api/documents/<int:doc_id>/assign")
@api
def assign_document(doc_id: int):
    data = body()
    iid = data.get("interviewee_id") or None
    if iid and not db.scalar("SELECT id FROM interviewees WHERE id=?", (iid,)):
        raise ValueError("受访者不存在")
    importers.assign_document(db, doc_id, iid)
    if data.get("stage") is not None:
        db.execute("UPDATE documents SET stage=? WHERE id=?", (data["stage"], doc_id))
    return {"stats": db.stats()}


@app.post("/api/documents/<int:doc_id>/speaker")
@api
def set_speaker(doc_id: int):
    speaker = body().get("speaker", "")
    if speaker not in ("interviewee", "interviewer", "unknown"):
        raise ValueError("说话人取值不合法")
    n = importers.set_segments_speaker(db, doc_id, speaker)
    return {"updated": n}


@app.patch("/api/segments/<int:seg_id>")
@api
def patch_segment(seg_id: int):
    data = body()
    coding.update_segment(db, seg_id, text=data.get("text"), speaker=data.get("speaker"))
    return {"id": seg_id}


@app.delete("/api/segments/<int:seg_id>")
@api
def remove_segment(seg_id: int):
    coding.delete_segment(db, seg_id)
    return {"id": seg_id}


# ---------- 受访者 / 脱敏 ----------

@app.get("/api/interviewees")
@api
def interviewees():
    return coding.list_interviewees(db)


@app.post("/api/interviewees")
@api
def add_interviewee():
    d = body()
    iid = db.ensure_interviewee(
        d.get("code", ""), alias=d.get("alias", ""), role=d.get("role", ""),
        org=d.get("org", ""), stage=d.get("stage", ""),
        interview_date=d.get("interview_date", ""), wave=int(d.get("wave", 1) or 1),
        note=d.get("note", ""),
    )
    return {"id": iid, "items": coding.list_interviewees(db)}


@app.patch("/api/interviewees/<int:iid>")
@api
def patch_interviewee(iid: int):
    d = body()
    allowed = ("code", "alias", "role", "org", "stage", "interview_date", "wave", "note")
    sets, vals = [], []
    for k in allowed:
        if k in d:
            sets.append(f"{k}=?")
            vals.append(d[k])
    if sets:
        vals.append(iid)
        db.execute(f"UPDATE interviewees SET {','.join(sets)} WHERE id=?", vals)
    # 编号改了，同步刷新引用号
    if "code" in d:
        for doc in db.query("SELECT id FROM documents WHERE interviewee_id=?", (iid,)):
            importers.assign_document(db, doc["id"], iid)
    return {"items": coding.list_interviewees(db)}


@app.delete("/api/interviewees/<int:iid>")
@api
def remove_interviewee(iid: int):
    db.execute("DELETE FROM interviewees WHERE id=?", (iid,))
    return {"items": coding.list_interviewees(db)}


@app.get("/api/aliases")
@api
def aliases():
    return anonymize.list_aliases(db)


@app.post("/api/aliases")
@api
def add_alias():
    d = body()
    anonymize.add_alias(db, d.get("real_name", ""), d.get("replacement", ""))
    return {"items": anonymize.list_aliases(db)}


@app.delete("/api/aliases/<int:aid>")
@api
def remove_alias(aid: int):
    anonymize.remove_alias(db, aid)
    return {"items": anonymize.list_aliases(db)}


@app.post("/api/aliases/apply")
@api
def apply_aliases():
    return anonymize.apply_to_all(db)


# ---------- 码本 / 编码 ----------

@app.get("/api/codes")
@api
def codes():
    return coding.list_codes(db)


@app.post("/api/codes")
@api
def add_code():
    d = body()
    cid = coding.add_code(db, d.get("name", ""), d.get("definition", ""),
                          d.get("color", "#4C6EF5"), d.get("parent_id") or None)
    return {"id": cid, "items": coding.list_codes(db)}


@app.patch("/api/codes/<int:cid>")
@api
def patch_code(cid: int):
    d = body()
    allowed = ("name", "definition", "color", "parent_id", "memo")
    coding.update_code(db, cid, **{k: v for k, v in d.items() if k in allowed})
    return {"items": coding.list_codes(db)}


@app.delete("/api/codes/<int:cid>")
@api
def remove_code(cid: int):
    coding.delete_code(db, cid)
    return {"items": coding.list_codes(db)}


@app.post("/api/codings")
@api
def add_coding():
    d = body()
    coding.code_segment(db, int(d["segment_id"]), int(d["code_id"]), d.get("memo", ""))
    return {"stats": db.stats()}


@app.delete("/api/codings")
@api
def remove_coding():
    d = body()
    coding.uncode_segment(db, int(d["segment_id"]), int(d["code_id"]))
    return {"stats": db.stats()}


# ---------- 检索 / 统计 ----------

@app.get("/api/search")
@api
def search():
    raw = request.args.get("codes", "")
    code_ids = [int(x) for x in raw.split(",") if x.strip().isdigit()]
    return coding.search(
        db, codes=code_ids or None, q=request.args.get("q", ""),
        interviewee_id=request.args.get("interviewee_id", type=int),
        stage=request.args.get("stage", ""), speaker=request.args.get("speaker", ""),
    )


@app.get("/api/matrix")
@api
def matrix():
    return coding.matrix(db, request.args.get("rows", "interviewee"))


@app.get("/api/frequency")
@api
def frequency():
    return coding.frequency(db, request.args.get("group", "stage"))


@app.get("/api/inventory")
@api
def inventory():
    return coding.inventory(db)


# ---------- 导出 / 下载 ----------

@app.get("/api/export")
@api
def export():
    kind = request.args.get("kind", "inventory")
    fmt = request.args.get("fmt", "csv")
    rows = request.args.get("rows", "interviewee")
    code_id = request.args.get("code_id", type=int)
    path = exporters.export(db, kind, fmt, rows, code_id)
    return {"name": path.name, "size": path.stat().st_size, "path": str(path)}


@app.get("/api/exports")
@api
def exports():
    return exporters.list_exports()


@app.get("/api/download/<path:name>")
def download(name: str):
    # 防止路径穿越
    if Path(name).name != name or ".." in name:
        return jsonify({"ok": False, "error": "非法文件名"}), 400
    return send_from_directory(str(config.EXPORT_DIR), name, as_attachment=True)


# ---------- 可选：本地录音转写 ----------

@app.get("/api/transcribe/status")
@api
def transcribe_status():
    return transcribe.status()


@app.post("/api/transcribe")
@api
def transcribe_upload():
    if not transcribe.is_available():
        raise TranscribeUnavailable(
            f"未安装本地转写依赖。请在项目目录执行：{transcribe.INSTALL_HINT}\n"
            "或者用外部转写工具导出 .srt/.vtt/.json 后在「材料」页直接导入。"
        )
    f = request.files.get("file")
    if not f or not f.filename:
        raise ValueError("没有收到音视频文件")
    folder = config.SOURCES_DIR / datetime.now().strftime("%Y%m%d%H%M%S%f")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / _safe_name(f.filename)
    f.save(str(target))
    result = transcribe.transcribe_and_import(
        db, target, model_size=request.form.get("model_size", "small"),
        language=request.form.get("language", "zh"),
        stage=request.form.get("stage", ""),
    )
    result["stats"] = db.stats()
    return result


if __name__ == "__main__":
    port = int(os.environ.get("ATLAS_PORT", "5000"))
    url = f"http://127.0.0.1:{port}/"
    print("=" * 60)
    print("  interview-atlas  访谈材料整理与编码工作台")
    print(f"  打开：{url}")
    print(f"  数据：{config.DATA_DIR}")
    print("  仅监听本机 127.0.0.1；按 Ctrl+C 停止。")
    print("=" * 60)
    if os.environ.get("ATLAS_NO_BROWSER") != "1":
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
