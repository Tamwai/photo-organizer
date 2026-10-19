"""
照片按拍摄日期归类脚本（命令行版 + 图形界面版二合一）

用法：
    python photo_organizer.py                        # 无参数：打开图形界面（双击 EXE 同此）
    python photo_organizer.py <目标目录>              # 命令行：整理指定目录
    python photo_organizer.py <目标目录> --dry-run    # 命令行：模拟运行，只预览不移动
    python photo_organizer.py --dry-run              # 命令行：模拟整理配置区 TARGET_DIR
    python photo_organizer.py --gui                  # 强制启动图形界面
    python photo_organizer.py -g                     # 同上
"""
import sys

__version__ = "0.1.0"

import os
import re
import shutil
from datetime import datetime
from pathlib import Path

try:
    from PIL import Image
    from PIL.ExifTags import TAGS
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# ========== 配置区 ==========
# 默认目标目录：取程序（源码或 EXE）所在目录，避免写死个人路径
if getattr(sys, "frozen", False):  # PyInstaller 打包成 EXE 后
    BASE_DIR = Path(sys.executable).resolve().parent
else:  # 源码运行
    BASE_DIR = Path(__file__).resolve().parent
TARGET_DIR = str(BASE_DIR)  # 默认整理的目标目录，可按需修改
DATE_FMT = "%Y-%m-%d"  # 日期文件夹格式（如 2026-07-12）
PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp", ".heic", ".heif"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm", ".m4v", ".3gp", ".ts", ".mts"}
INCLUDE_VIDEO = True  # 是否同时整理视频
SUPPORTED_EXTS = set(PHOTO_EXTS) | (VIDEO_EXTS if INCLUDE_VIDEO else set())
# 文件名中的日期模式，支持多种写法：
#   IMG_20260806_144736.jpg / Screenshot_2026-08-09-05-03-57.jpg
#   2026.08.06.jpg / 2026 08 06.jpg / 20260806.jpg / VID_2026-08-06.mp4
# 分隔符：- _ . 空格（可混用），也可无分隔符（8 位连续数字）
NAME_DATE_PATTERNS = [
    re.compile(r"(\d{4})\s*[-_.]?\s*(\d{2})\s*[-_.]?\s*(\d{2})"),
]
# Unix 时间戳模式：13 位毫秒（mmexport1785419041663 等） / 10 位秒（1785419041 等）
TS_PATTERNS = [
    re.compile(r"(?<!\d)(\d{13})(?!\d)"),
    re.compile(r"(?<!\d)(\d{10})(?!\d)"),
]
# 合理年份范围（照片/视频拍摄时间不会超出）
MIN_YEAR, MAX_YEAR = 1970, 2099
# ============================


def get_exif_date(filepath: str) -> str | None:
    """读取 EXIF 原始拍摄日期，失败或无 PIL 返回 None"""
    if not HAS_PIL:
        return None
    try:
        img = Image.open(filepath)
        exif = img.getexif()
        if exif:
            for tag_id, value in exif.items():
                tag_name = TAGS.get(tag_id, "")
                if tag_name == "DateTimeOriginal":
                    return datetime.strptime(value, "%Y:%m:%d %H:%M:%S").strftime(DATE_FMT)
    except Exception:
        pass
    return None


def get_name_date(filename: str) -> str | None:
    """从文件名中解析日期，失败返回 None。
    判定顺序：1) 明确的日期格式（含年月日，年份校验合理范围）2) Unix 时间戳
    """
    # 1) 明确日期格式：找文件名中第一个合法（年 1970-2099）的 YYYYMMDD
    for pat in NAME_DATE_PATTERNS:
        for m in pat.finditer(filename):
            try:
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if not (MIN_YEAR <= y <= MAX_YEAR):
                    continue
                return datetime(y, mo, d).strftime(DATE_FMT)
            except ValueError:
                continue
    # 2) Unix 时间戳（mmexport1785419041663 / wx_camera_ 等）
    for pat in TS_PATTERNS:
        m = pat.search(filename)
        if m:
            try:
                ts = int(m.group(1))
                if ts >= 10**12:  # 13 位毫秒
                    ts //= 1000
                dt = datetime.fromtimestamp(ts)
                if MIN_YEAR <= dt.year <= MAX_YEAR:
                    return dt.strftime(DATE_FMT)
            except (ValueError, OSError, OverflowError):
                continue
    return None


def get_file_date(filepath: str) -> str:
    """获取文件日期：优先 EXIF，其次文件名日期，最后取修改时间和创建时间中较早的"""
    exif_date = get_exif_date(filepath)
    if exif_date:
        return exif_date

    name_date = get_name_date(Path(filepath).name)
    if name_date:
        return name_date

    stat = os.stat(filepath)
    mtime = stat.st_mtime
    ctime = stat.st_ctime
    earliest = min(mtime, ctime)
    return datetime.fromtimestamp(earliest).strftime(DATE_FMT)


def organize(target_dir: str, dry_run: bool = False,
             include_video: bool = INCLUDE_VIDEO, log_func=print):
    """整理照片/视频到日期文件夹。
    - dry_run: 模拟运行，只预览不移动
    - include_video: 是否同时整理视频
    - log_func: 日志回调（默认 print；GUI 可传入文本队列）
    """
    target = Path(target_dir)
    if not target.exists():
        log_func(f"[错误] 目录不存在: {target_dir}")
        return

    supported = set(PHOTO_EXTS) | (set(VIDEO_EXTS) if include_video else set())
    photos = [f for f in target.iterdir() if f.is_file() and f.suffix.lower() in supported]
    if not photos:
        log_func("[提示] 目录下没有照片文件")
        return

    stats = {"moved": 0, "skipped": 0, "failed": 0, "new_dirs": set(), "exif": 0, "fallback": 0}

    for p in photos:
        try:
            date_str = get_exif_date(str(p))
            if date_str:
                stats["exif"] += 1
            else:
                date_str = get_file_date(str(p))
                stats["fallback"] += 1

            dest_dir = target / date_str
            is_new_dir = not dest_dir.exists()
            if not dry_run:
                dest_dir.mkdir(exist_ok=True)
            if is_new_dir:
                stats["new_dirs"].add(date_str)

            dest_path = dest_dir / p.name
            if dest_path.exists():
                stats["skipped"] += 1
                log_func(f"[跳过] 已存在: {p.name} -> {date_str}/")
            elif dry_run:
                stats["moved"] += 1
                log_func(f"[模拟] 将移动: {p.name} -> {date_str}/")
            else:
                shutil.move(str(p), str(dest_path))
                stats["moved"] += 1
                log_func(f"[移动] {p.name} -> {date_str}/")
        except Exception as e:
            stats["failed"] += 1
            log_func(f"[错误] 处理失败: {p.name} ({e})")

    mode = "模拟运行" if dry_run else "整理完成"
    log_func(f"\n===== {mode} =====")
    log_func(f"移动: {stats['moved']} 张 | 跳过: {stats['skipped']} 张 | 失败: {stats['failed']} 张")
    log_func(f"新建日期文件夹: {len(stats['new_dirs'])} 个")
    log_func(f"EXIF 判定: {stats['exif']} 张 | 兜底判定: {stats['fallback']} 张")


# ========== 图形界面（--gui / -g 启动） ==========
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk


class PhotoOrganizerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("照片按拍摄日期归类")
        self.root.geometry("680x520")
        self.root.minsize(560, 420)

        self.log_q = queue.Queue()
        self.running = False

        # 目标目录行
        top = ttk.Frame(root, padding=(10, 10, 10, 4))
        top.pack(fill=tk.X)
        ttk.Label(top, text="目标目录:").pack(side=tk.LEFT)
        self.dir_var = tk.StringVar(value=TARGET_DIR)
        self.dir_entry = ttk.Entry(top, textvariable=self.dir_var)
        self.dir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        ttk.Button(top, text="浏览...", command=self.browse_dir).pack(side=tk.LEFT)

        # 选项行
        opts = ttk.Frame(root, padding=(10, 4, 10, 4))
        opts.pack(fill=tk.X)
        self.video_var = tk.BooleanVar(value=INCLUDE_VIDEO)
        self.dry_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="同时整理视频", variable=self.video_var).pack(side=tk.LEFT, padx=(0, 16))
        ttk.Checkbutton(opts, text="模拟运行（只预览不移动）", variable=self.dry_var).pack(side=tk.LEFT)

        # 按钮行
        btns = ttk.Frame(root, padding=(10, 4, 10, 4))
        btns.pack(fill=tk.X)
        self.start_btn = ttk.Button(btns, text="开始整理", command=self.start)
        self.start_btn.pack(side=tk.LEFT)
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(btns, textvariable=self.status_var).pack(side=tk.LEFT, padx=12)

        # 日志区
        log_frame = ttk.Frame(root, padding=(10, 4, 10, 10))
        log_frame.pack(fill=tk.BOTH, expand=True)
        self.log_text = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def browse_dir(self):
        selected = filedialog.askdirectory(title="选择要整理的照片目录")
        if selected:
            self.dir_var.set(selected)

    def log(self, msg: str):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def start(self):
        if self.running:
            return
        target_dir = self.dir_var.get().strip()
        if not target_dir:
            messagebox.showwarning("提示", "请先选择目标目录")
            return

        self.running = True
        self.start_btn.configure(state=tk.DISABLED)
        self.status_var.set("正在整理...")
        self.log("----------------------------------------")
        self.log(f"目标目录: {target_dir}")
        self.log(f"整理视频: {'是' if self.video_var.get() else '否'} | 模式: {'模拟运行' if self.dry_var.get() else '正式整理'}")
        self.log("----------------------------------------")

        t = threading.Thread(target=self.worker, args=(target_dir,), daemon=True)
        t.start()
        self.root.after(100, self.poll)

    def worker(self, target_dir: str):
        organize(target_dir, dry_run=self.dry_var.get(),
                 include_video=self.video_var.get(), log_func=self.log_q.put)
        self.log_q.put(None)

    def poll(self):
        try:
            while True:
                item = self.log_q.get_nowait()
                if item is None:
                    self.running = False
                    self.start_btn.configure(state=tk.NORMAL)
                    self.status_var.set("完成")
                    self.root.bell()
                    return
                self.log(item)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="照片按拍摄日期归类脚本（命令行 + 图形界面）")
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}",
                        help="显示版本号")
    parser.add_argument("target_dir", nargs="?", default=None,
                        help="要整理的目标目录；不提供且无其它参数时自动打开图形界面")
    parser.add_argument("--dry-run", action="store_true",
                        help="模拟运行，只预览归类结果，不移动任何文件（命令行模式）")
    parser.add_argument("-g", "--gui", action="store_true",
                        help="强制启动图形界面")
    args = parser.parse_args()

    # 双击无参数 → 图形界面；命令行带参数 → 命令行模式
    if args.gui or (args.target_dir is None and not args.dry_run):
        root = tk.Tk()
        PhotoOrganizerApp(root)
        root.mainloop()
    else:
        organize(args.target_dir or TARGET_DIR, dry_run=args.dry_run)
