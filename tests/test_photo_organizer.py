# -*- coding: utf-8 -*-
"""photo-organizer 核心函数单元测试（对应一审评审 #16）。

覆盖：get_name_dt / _safe_transfer / restore_from_log / write_organize_log。
运行：python -m unittest discover -s tests -v
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core
import photo_organizer as po


class TestGetNameDt(unittest.TestCase):
    def test_ymd_variants(self):
        for name, expect in [
            ("IMG_20260101_123456.jpg", (2026, 1, 1)),
            ("2026-01-02_photo.png", (2026, 1, 2)),
            ("wx_camera_20260304.jpg", (2026, 3, 4)),
        ]:
            dt = po.get_name_dt(name)
            self.assertIsNotNone(dt, name)
            self.assertEqual((dt.year, dt.month, dt.day), expect, name)

    def test_invalid_year(self):
        self.assertIsNone(po.get_name_dt("IMG_19691231.jpg"))
        self.assertIsNone(po.get_name_dt("IMG_21000101.jpg"))

    def test_unix_ts(self):
        dt = po.get_name_dt("mmexport1785419041663.jpg")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.year, 2026)

    def test_no_date(self):
        self.assertIsNone(po.get_name_dt("random_name.png"))

    def test_digit_boundary_hits(self):
        # 二审 S1：正则增加 (?<!\d)/(?!\d) 数字边界后，数字两侧紧贴字母仍应命中
        for name, expect in [
            ("IMG_20260806_144736.jpg", (2026, 8, 6)),
            ("Screenshot_2026-08-09-05-03-57.png", (2026, 8, 9)),
            ("abc20260806def.png", (2026, 8, 6)),
        ]:
            dt = po.get_name_dt(name)
            self.assertIsNotNone(dt, name)
            self.assertEqual((dt.year, dt.month, dt.day), expect, name)

    def test_digit_boundary_miss(self):
        # 二审 S1：前后紧贴数字的日期串不应被当作文件名日期（20260806 后紧贴数字 2）
        self.assertIsNone(po.get_name_dt("IMG_202608062.jpg"))

    def test_ts_range_2000_now(self):
        # 二审 S2：时间戳校验收紧为 2000-01-01 至当前，10 位假时间戳应被拒绝
        self.assertIsNone(po.get_name_dt("file_1234567890.jpg"))
        self.assertIsNone(po.get_name_dt("file_0000000000.jpg"))


class TestSafeTransfer(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="po_test_"))
        self.src_dir = self.tmp / "src"
        self.dst_dir = self.tmp / "dst"
        self.src_dir.mkdir()
        self.dst_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_file(self, p: Path, content: bytes = b"hello"):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        return p

    def test_copy_mode(self):
        src = self._make_file(self.src_dir / "a.jpg")
        dst = self.dst_dir / "2026-01-01" / "a.jpg"
        dst.parent.mkdir(parents=True, exist_ok=True)  # 目标父目录由 organize 流程预先创建
        po._safe_transfer(src, dst, copy_mode=True)
        self.assertTrue(dst.exists())
        self.assertTrue(src.exists())  # 复制模式源保留
        self.assertFalse(dst.with_name(dst.name + ".photo-org-tmp").exists())

    def test_move_same_drive(self):
        src = self._make_file(self.src_dir / "b.jpg", b"data")
        dst = self.dst_dir / "b.jpg"
        po._safe_transfer(src, dst, copy_mode=False)
        self.assertTrue(dst.exists())
        self.assertFalse(src.exists())  # 移动模式源消失
        self.assertEqual(dst.read_bytes(), b"data")

    def test_failed_copy_cleans_tmp(self):
        src = self._make_file(self.src_dir / "c.jpg")
        dst = self.dst_dir / "c.jpg"
        # 目标已存在同名 .photo-org-tmp 且不可覆盖时，os.replace 前应清理失败路径
        tmp = dst.with_name(dst.name + ".photo-org-tmp")
        tmp.write_bytes(b"x")
        os.chmod(tmp, 0o400)
        try:
            with self.assertRaises(Exception):
                po._safe_transfer(src, dst, copy_mode=True)
        finally:
            os.chmod(tmp, 0o600)
        # 残留半成品应被清理（尽力而为），目标未被写入
        self.assertFalse(dst.exists())


class TestRestoreFromLog(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="po_test_"))
        self.log_dir = self.tmp / "logs"
        self.log_dir.mkdir()
        self.orig_get_logs_dir = core.get_logs_dir
        core.get_logs_dir = lambda: self.log_dir
        self.src_dir = self.tmp / "src"
        self.dst_dir = self.tmp / "dst"
        self.src_dir.mkdir()
        self.dst_dir.mkdir()

    def tearDown(self):
        core.get_logs_dir = self.orig_get_logs_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_log(self, stem, recs):
        p = self.log_dir / (stem + ".jsonl")
        p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n",
                     encoding="utf-8")
        return p

    def test_restore_move(self):
        src = self.src_dir / "a.jpg"
        dst = self.dst_dir / "2026-01-01" / "a.jpg"
        src.write_bytes(b"data")
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"data")
        src.unlink()  # 模拟移动完成：源已不存在
        self._write_log("t1", [
            {"type": "meta", "ok": False, "src": str(self.src_dir), "dest": str(self.dst_dir)},
            {"type": "move", "src": str(src), "dst": str(dst), "ok": True},
        ])
        stats = po.restore_from_log("t1")
        self.assertEqual(stats["moved"], 1)
        self.assertTrue(src.exists())
        self.assertFalse(dst.exists())

    def test_restore_skip_when_src_exists(self):
        src = self.src_dir / "b.jpg"
        dst = self.dst_dir / "b.jpg"
        src.write_bytes(b"data")
        dst.write_bytes(b"data")
        self._write_log("t2", [
            {"type": "meta", "ok": False, "src": str(self.src_dir), "dest": str(self.dst_dir)},
            {"type": "move", "src": str(src), "dst": str(dst), "ok": True},
        ])
        stats = po.restore_from_log("t2")
        self.assertEqual(stats["skipped"], 1)
        self.assertTrue(dst.exists())  # 源仍存在则不覆盖

    def test_restore_remove_copy(self):
        src = self.src_dir / "c.jpg"
        dst = self.dst_dir / "c.jpg"
        src.write_bytes(b"data")
        dst.write_bytes(b"data")
        self._write_log("t3", [
            {"type": "meta", "ok": False, "src": str(self.src_dir), "dest": str(self.dst_dir)},
            {"type": "copy", "src": str(src), "dst": str(dst), "ok": True},
        ])
        stats = po.restore_from_log("t3")
        self.assertEqual(stats["removed"], 1)
        self.assertFalse(dst.exists())

    def test_missing_log(self):
        stats = po.restore_from_log("no_such")
        self.assertIn("error", stats)

    def test_restore_scope_missing_meta(self):
        # 二审 SEC1：日志首行 meta 缺 src/dest 时拒绝还原
        self._write_log("t4", [
            {"type": "meta", "ok": False},
            {"type": "move", "src": str(self.src_dir / "a.jpg"),
             "dst": str(self.dst_dir / "a.jpg"), "ok": True},
        ])
        stats = po.restore_from_log("t4")
        self.assertIn("error", stats)

    def test_restore_scope_out_of_range(self):
        # 二审 SEC1：动作路径超出 meta 记录的源/目标目录时跳过并告警
        other = self.tmp / "other"
        other.mkdir(exist_ok=True)
        self._write_log("t5", [
            {"type": "meta", "ok": False, "src": str(other), "dest": str(other)},
            {"type": "move", "src": str(self.src_dir / "a.jpg"),
             "dst": str(self.dst_dir / "a.jpg"), "ok": True},
        ])
        stats = po.restore_from_log("t5")
        self.assertEqual(stats.get("skipped"), 1)
        self.assertEqual(stats.get("moved"), 0)


class TestWriteOrganizeLog(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="po_test_"))
        self.log_dir = self.tmp / "logs"
        self.log_dir.mkdir()
        self.orig_get_logs_dir = core.get_logs_dir
        core.get_logs_dir = lambda: self.log_dir

    def tearDown(self):
        core.get_logs_dir = self.orig_get_logs_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_write_ok(self):
        actions = [
            {"type": "move", "src": "C:/a.jpg", "dst": "C:/b.jpg", "ok": True},
        ]
        meta = {"mode": "move", "source": "C:/", "target": "C:/"}
        p = po.write_organize_log(meta=meta, lines=[], actions=actions, keep=5)
        self.assertTrue(p.exists())
        lines = p.read_text(encoding="utf-8").strip().splitlines()
        meta_rec = json.loads(lines[0])
        self.assertEqual(meta_rec["type"], "meta")
        self.assertEqual(meta_rec["action_count"], 1)  # #17：首行写入动作条数
        self.assertEqual(meta_rec["mode"], "move")
        self.assertEqual(len(lines), 2)

    def test_io_error_raises(self):
        # 使写入路径不可写（目录变文件），验证抛 OSError 而非返回假路径
        blocker = self.log_dir / "blocked"
        blocker.write_text("x")
        core.get_logs_dir = lambda: blocker
        with self.assertRaises(OSError):
            po.write_organize_log(meta={}, lines=[], actions=[], keep=5)

    def test_keep_cleans_old(self):
        # 直接构造 4 份旧清单，模拟超出保留份数；keep=2 应只保留 2 份
        for i in range(4):
            (self.log_dir / f"organize_2026010{i}.jsonl").write_text("x", encoding="utf-8")
        po.write_organize_log(meta={}, lines=[], actions=[], keep=2)
        remaining = [p for p in self.log_dir.glob("*.jsonl")]
        self.assertLessEqual(len(remaining), 3)  # 新写 1 份 + 保留 2 份旧


class TestFileDt(unittest.TestCase):
    """二审 PERF1：--no-exif 路径 get_file_dt_noexif 跳过 EXIF 后仍能正确取日期"""

    def test_noexif_uses_name_date(self):
        # 无 EXIF 的真实图片（写字节即可，get_exif_dt 打不开会返回 None）：
        # get_file_dt_noexif 应回落文件名日期，与 get_file_dt 结果一致
        p = self._root() / "IMG_20260806_144736.jpg"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"not-a-real-image")
        dt = po.get_file_dt_noexif(str(p))
        self.assertIsNotNone(dt)
        self.assertEqual((dt.year, dt.month, dt.day), (2026, 8, 6))
        self.assertEqual(po.get_file_dt(str(p)), dt)

    def _root(self):
        return Path(tempfile.mkdtemp(prefix="po_noexif_"))


if __name__ == "__main__":
    unittest.main()
