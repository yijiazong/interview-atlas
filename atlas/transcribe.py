"""可选：本地录音转写（faster-whisper，离线免费）。

能力边界写在这里，免得被误用：
- 只做「语音 -> 带时间戳的文字」，**不自动区分说话人**。
  自动说话人分离（diarization）需要另接 pyannote 模型 + HuggingFace token，
  本模块不假装具备该能力。转写后可在「材料」页按文档批量标注说话人，
  或导入外部工具（含支持说话人分离的转写服务）产出的 .srt/.vtt/.json。
- 首次使用会下载模型（small ≈ 460MB / medium ≈ 1.5GB），需要联网一次。
- 未安装依赖时明确报错并给出安装命令，绝不静默跳过。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from . import config
from .importers import ParsedDoc, Segment, create_document, guess_metadata

INSTALL_HINT = "pip install -r requirements-transcribe.txt"


class TranscribeUnavailable(RuntimeError):
    pass


def is_available() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


def status() -> dict:
    return {
        "available": is_available(),
        "engine": "faster-whisper (本地离线)" if is_available() else "未安装",
        "hint": "" if is_available() else INSTALL_HINT,
        "note": "转写不自动区分说话人；首次运行需联网下载模型。",
        "model_sizes": ["tiny", "base", "small", "medium", "large-v3"],
    }


def _fmt(seconds: float | None) -> str:
    if seconds is None:
        return ""
    ms = int(round(float(seconds) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def transcribe_file(path: str | Path, *, model_size: str = "small",
                    language: str = "zh") -> ParsedDoc:
    """把音视频转写成 ParsedDoc；speaker 一律记为 unknown，时间戳保留。"""
    if not is_available():
        raise TranscribeUnavailable(
            f"未安装本地转写依赖。请先在终端执行：{INSTALL_HINT}\n"
            "（或改用外部转写工具导出 .srt/.vtt/.json 后，直接在「材料」页导入。）"
        )
    path = Path(path)
    if path.suffix.lower() not in config.MEDIA_EXT:
        raise ValueError(f"不是受支持的音视频格式：{path.suffix}")

    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(str(path), language=language,
                                       vad_filter=True, beam_size=5)
    out: list[Segment] = []
    for seg in segments:
        text = (seg.text or "").strip()
        if not text:
            continue
        start, end = _fmt(seg.start), _fmt(seg.end)
        out.append(Segment(text=text, speaker="unknown",
                           timecode=f"{start} --> {end}", start=start, end=end))

    meta = guess_metadata(path)
    doc = ParsedDoc(title=path.stem, segments=out, source_type="transcript", **meta)
    doc.warnings.append("转写稿由本地语音模型生成，未自动区分说话人，请人工标注并核对错字后再编码。")
    if not out:
        doc.warnings.append("没有识别到语音内容；请确认音频有效或换用更大的模型。")
    return doc


def transcribe_and_import(db, path: str | Path, *, model_size: str = "small",
                          language: str = "zh", stage: str = "") -> dict:
    """转写 -> 存一份 JSON 副本到 data/sources -> 入库。"""
    import json

    parsed = transcribe_file(path, model_size=model_size, language=language)
    if not parsed.segments:
        raise TranscribeUnavailable("没有识别到语音内容，未创建材料。")
    config.SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    out = config.SOURCES_DIR / f"{Path(path).stem}_转写.json"
    payload = {
        "source": Path(path).name,
        "segments": [{"start": s.start, "end": s.end, "speaker": s.speaker,
                      "text": s.text} for s in parsed.segments],
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    result = create_document(db, parsed,
                             source_path=str(out.relative_to(config.DATA_DIR)),
                             source_type="json", stage=stage)
    return result
