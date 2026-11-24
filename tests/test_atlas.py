"""核心逻辑回归测试：python -m unittest discover -s tests -v

全部使用临时目录，不碰项目里的真实数据。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from atlas import anonymize, coding, config, exporters, importers, transcribe
from atlas.db import Database

ROOT = Path(__file__).resolve().parent.parent


class AtlasTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="atlas_unittest_"))
        self._orig = (config.DATA_DIR, config.SOURCES_DIR, config.EXPORT_DIR, config.DB_PATH)
        config.DATA_DIR = self.tmp / "data"
        config.SOURCES_DIR = self.tmp / "data" / "sources"
        config.EXPORT_DIR = self.tmp / "export"
        config.DB_PATH = self.tmp / "data" / "atlas.db"
        config.ensure_dirs()
        self.db = Database(config.DB_PATH)

    def tearDown(self):
        (config.DATA_DIR, config.SOURCES_DIR,
         config.EXPORT_DIR, config.DB_PATH) = self._orig

    def _write(self, name: str, text: str) -> Path:
        p = self.tmp / name
        p.write_text(text, encoding="utf-8")
        return p

    # ---------- 解析 ----------

    def test_plain_speaker_split(self):
        segs = importers.parse_plain(
            "问：你刚来时谁带你？\n答：师父手把手带，每天写复盘。\n\n"
            "访谈者：后来呢？\n受访者：后来靠考核表了。\n")
        self.assertEqual(len(segs), 4)
        self.assertEqual([s.speaker for s in segs],
                         ["interviewer", "interviewee", "interviewer", "interviewee"])
        self.assertIn("师父", segs[1].text)

    def test_no_speaker_tags_keeps_paragraphs(self):
        segs = importers.parse_plain("第一段内容。\n\n第二段内容，没有冒号。\n")
        self.assertEqual(len(segs), 2)
        self.assertTrue(all(s.speaker == "unknown" for s in segs))

    def test_srt_timecode_and_speaker(self):
        text = ("1\n00:00:01,000 --> 00:00:04,000\n受访者：那时候一个师父带两个人。\n\n"
                "2\n00:00:05,000 --> 00:00:09,000\n问：后来呢？\n")
        segs = importers.parse_srt(text)
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0].speaker, "interviewee")
        self.assertEqual(segs[1].speaker, "interviewer")
        self.assertIn("00:00:01,000", segs[0].timecode)

    def test_json_transcript(self):
        data = {"segments": [
            {"speaker": "受访者", "text": "2023 年换了店长。", "start": "00:01:02", "end": "00:01:10"},
            {"speaker": "访谈者", "text": "能具体说说吗？"},
        ]}
        segs = importers.parse_json(json.dumps(data, ensure_ascii=False))
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0].speaker, "interviewee")
        self.assertIn("00:01:02", segs[0].timecode)

    def test_guess_metadata(self):
        m = importers.guess_metadata(Path("P07_区域经理_2023-11-02_第2轮.docx"))
        self.assertEqual(m["code"], "P07")
        self.assertEqual(m["interview_date"], "2023-11-02")
        self.assertEqual(m["wave"], 2)
        self.assertEqual(m["role"], "区域经理")

    def test_unsupported_extension(self):
        with self.assertRaises(importers.ImportError_):
            importers.parse_file(self._write("a.exe", "x"))

    # ---------- 入库 / 归档 ----------

    def test_import_labels_and_relabel(self):
        p = self._write("P01_店长_2019-03-12.txt", "问：你好。\n答：你好。\n")
        r = importers.import_file(self.db, p, stage="2018—2020 扩张期")
        self.assertEqual(r["segments"], 2)
        self.assertEqual(r["code"], "P01")
        labels = [s["label"] for s in coding.document_segments(self.db, r["document_id"])]
        self.assertEqual(labels, ["P01-001", "P01-002"])
        new_id = self.db.ensure_interviewee("P02")
        importers.assign_document(self.db, r["document_id"], new_id)
        labels = [s["label"] for s in coding.document_segments(self.db, r["document_id"])]
        self.assertEqual(labels, ["P02-001", "P02-002"])

    def test_inventory_and_list(self):
        p = self._write("P01_店长_2019-03-12.txt", "答：内容一。\n\n答：内容二。\n")
        importers.import_file(self.db, p)
        inv = coding.inventory(self.db)
        self.assertEqual(len(inv), 1)
        self.assertEqual(inv[0]["code"], "P01")
        self.assertEqual(inv[0]["segments"], 2)
        self.assertEqual(len(coding.list_interviewees(self.db)), 1)

    def test_scan_directory_skips_readme(self):
        d = self.tmp / "batch"
        d.mkdir()
        (d / "P01_A_2020-01-01.txt").write_text("答：一段。", encoding="utf-8")
        (d / "README.md").write_text("说明，不该被导入", encoding="utf-8")
        (d / "_说明.md").write_text("也不该被导入", encoding="utf-8")
        r = importers.scan_directory(self.db, d)
        self.assertEqual(r["total"], 1)
        self.assertEqual(len(r["imported"]), 1)

    # ---------- 编码 / 统计 ----------

    def test_coding_search_matrix(self):
        p = self._write("P01_店长_2019-03-12.txt",
                        "问：早期怎么带人？\n答：师父手把手带。\n\n问：后来呢？\n答：靠考核表了。\n")
        r = importers.import_file(self.db, p, stage="扩张期")
        segs = coding.document_segments(self.db, r["document_id"])
        c1 = coding.add_code(self.db, "情感连带", "师徒私人照顾")
        c2 = coding.add_code(self.db, "考核量化")
        coding.code_segment(self.db, segs[1]["id"], c1)
        coding.code_segment(self.db, segs[3]["id"], c2)
        self.assertEqual(len(coding.search(self.db, codes=[c1])), 1)
        self.assertEqual(len(coding.search(self.db, q="师父")), 1)
        m = coding.matrix(self.db, "interviewee")
        self.assertEqual(m["rows"], ["P01"])
        self.assertEqual(m["cells"]["P01"][c1], 1)
        self.assertEqual(m["totals"]["P01"], 2)
        coding.uncode_segment(self.db, segs[1]["id"], c1)
        self.assertEqual(coding.matrix(self.db, "interviewee")["cells"]["P01"][c1], 0)
        q = coding.quotations(self.db, c2)
        self.assertEqual(q[0]["code"], "考核量化")
        self.assertIsNotNone(q[0]["person_code"])

    def test_quotations_have_full_source(self):
        p = self._write("P01_店长_2019-03-12.txt", "答：一句可引用的原话。\n")
        r = importers.import_file(self.db, p, stage="2018—2020 扩张期")
        s = coding.document_segments(self.db, r["document_id"])[0]
        c = coding.add_code(self.db, "测试码")
        coding.code_segment(self.db, s["id"], c)
        q = coding.quotations(self.db, c)[0]
        for key in ("label", "document_title", "interview_date", "stage", "text"):
            self.assertIn(key, q)
        self.assertEqual(q["text"], "一句可引用的原话。")

    # ---------- 脱敏 ----------

    def test_anonymize_preview_and_apply(self):
        p = self._write("P01_A.txt", "答：师父带我，师父也盯指标。\n")
        r = importers.import_file(self.db, p)
        preview, hits = anonymize.preview("师父带我", {"师父": "带教者"})
        self.assertEqual((preview, hits), ("带教者带我", 1))
        anonymize.add_alias(self.db, "师父", "带教者")
        out = anonymize.apply_to_all(self.db)
        self.assertEqual(out["replacements"], 2)
        self.assertIn("带教者", coding.document_segments(self.db, r["document_id"])[0]["text"])

    # ---------- 导出 ----------

    def test_exports_all_formats(self):
        p = self._write("P01_A_2020-01-01.txt", "答：一段原话。\n")
        r = importers.import_file(self.db, p, stage="扩张期")
        s = coding.document_segments(self.db, r["document_id"])[0]
        c = coding.add_code(self.db, "情感连带")
        coding.code_segment(self.db, s["id"], c)
        for kind, fmt, ext in [
            ("inventory", "csv", ".csv"), ("inventory", "xlsx", ".xlsx"),
            ("segments", "csv", ".csv"), ("codings", "csv", ".csv"),
            ("matrix", "xlsx", ".xlsx"), ("quotations", "md", ".md"),
            ("quotations", "csv", ".csv"), ("backup", "zip", ".zip"),
        ]:
            path = exporters.export(self.db, kind, fmt, "interviewee")
            self.assertTrue(path.exists(), f"{kind}/{fmt} 未生成")
            self.assertEqual(path.suffix, ext)
            self.assertGreater(path.stat().st_size, 0)
        md = next(config.EXPORT_DIR.glob("引语汇编_*.md")).read_text(encoding="utf-8")
        self.assertIn("一段原话", md)
        self.assertIn("P01-001", md)
        self.assertIn("原文照录", md)

    def test_export_unknown_kind(self):
        with self.assertRaises(ValueError):
            exporters.export(self.db, "nope")

    # ---------- 可选转写 ----------

    @unittest.skipIf(transcribe.is_available(), "本机已安装转写依赖")
    def test_transcribe_unavailable_is_explicit(self):
        self.assertFalse(transcribe.is_available())
        self.assertIn("pip install", transcribe.status()["hint"])
        with self.assertRaises(transcribe.TranscribeUnavailable):
            transcribe.transcribe_file(self.tmp / "a.mp3")


if __name__ == "__main__":
    unittest.main(verbosity=2)
