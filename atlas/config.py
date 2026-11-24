"""路径与常量配置。所有运行时数据都放在项目目录内，方便整体备份或删除。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SOURCES_DIR = DATA_DIR / "sources"
EXPORT_DIR = ROOT / "export"
WEB_DIR = ROOT / "web"
EXAMPLES_DIR = ROOT / "examples"
DB_PATH = DATA_DIR / "atlas.db"

# 可导入的材料格式
TEXT_EXT = {".txt", ".md", ".markdown"}
DOC_EXT = {".docx"}
PDF_EXT = {".pdf"}
TABLE_EXT = {".csv", ".xlsx"}
TRANSCRIPT_EXT = {".srt", ".vtt", ".json"}
SUPPORTED_EXT = TEXT_EXT | DOC_EXT | PDF_EXT | TABLE_EXT | TRANSCRIPT_EXT

# 录音/录像（交给可选转写模块）
MEDIA_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg",
             ".mp4", ".mov", ".mkv", ".avi", ".webm"}

# 说话人标签 -> 角色
INTERVIEWER_TAGS = {
    "q", "问", "访谈者", "采访者", "研究者", "主持人", "访问者",
    "i", "interviewer", "问者",
}
INTERVIEWEE_TAGS = {
    "a", "答", "受访者", "被访者", "被访谈者", "受访人", "访谈对象",
    "r", "respondent", "interviewee", "p",
}

# 阶段默认值：纵向研究可按时间分组做对比，允许在设置里改
DEFAULT_STAGES = [
    "2018—2020 扩张期",
    "2021—2022 调整期",
    "2023—2024 收缩/重构期",
]


def ensure_dirs() -> None:
    for d in (DATA_DIR, SOURCES_DIR, EXPORT_DIR):
        d.mkdir(parents=True, exist_ok=True)
