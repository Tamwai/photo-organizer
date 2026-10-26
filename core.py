"""photo-organizer 核心逻辑模块（纯逻辑层，MCP 化改造第一阶段）。

本模块承载 photo_organizer.py 的全部纯逻辑能力：文件收集扫描、日期解析与
EXIF 读取、organize 核心整理流程、原子传输 _safe_transfer、操作清单读写、
回滚还原 restore_from_log、配置读写，以及 VIDEO_EXTS 等常量。
CLI / GUI / 后续 mcp_server.py 统一从此模块导入。

约束：
- 仅依赖标准库 + Pillow（可选），严禁 import tkinter 或任何 GUI 相关模块；
- 日志消息统一走结构化回调：item = (code, params)，code 为英文键、params 为
  结构化参数；上层负责翻译展示（本模块提供英文默认渲染 _render_en）；
- 返回值统一使用 dict，不使用 dataclass。

依赖：Python 3.10+；Pillow 可选（缺失时 EXIF 读取静默降级为文件名/文件时间）。
"""
import os
import re
import hashlib
import shutil
import json
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Literal

try:
    from PIL import Image
    from PIL.ExifTags import TAGS
    HAS_PIL = True
    PIL_LOAD_ERROR = ""
except ImportError as _pil_err:
    HAS_PIL = False
    PIL_LOAD_ERROR = str(_pil_err)

# ========== 常量区 ==========
DATE_FMT = "%Y-%m-%d"  # 日期文件夹格式（如 2026-07-12）
# 基础图片格式（默认勾选整理的 9 种）
BASE_PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp", ".heic", ".heif"}
# 扩展图片格式（可选勾选，Pillow 可直接读取）
EXTRA_PHOTO_EXTS = {".avif", ".jfif", ".jpe", ".ico", ".jxl", ".psd", ".tga",
                    ".pcx", ".ppm", ".pgm", ".pbm", ".jp2"}
PHOTO_EXTS = set(BASE_PHOTO_EXTS) | set(EXTRA_PHOTO_EXTS)
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm", ".m4v", ".3gp", ".ts", ".mts"}
INCLUDE_VIDEO = True  # 是否同时整理视频
APP_NAME = "photo-organizer"
DEFAULT_LOG_KEEP = 30

# 文件名中的日期模式，支持多种写法：
#   IMG_20260806_144736.jpg / Screenshot_2026-08-09-05-03-57.jpg
#   2026.08.06.jpg / 2026 08 06.jpg / 20260806.jpg / VID_2026-08-06.mp4
NAME_DATE_PATTERNS = [
    re.compile(r"(?<!\d)(\d{4})\s*[-_.]?\s*(\d{2})\s*[-_.]?\s*(\d{2})(?!\d)"),
]
# Unix 时间戳模式：13 位毫秒（mmexport1785419041663 等）/ 10 位秒（1785419041 等）
TS_PATTERNS = [
    re.compile(r"(?<!\d)(\d{13})(?!\d)"),
    re.compile(r"(?<!\d)(\d{10})(?!\d)"),
]
# 合理年份范围（照片/视频拍摄时间不会超出）
MIN_YEAR, MAX_YEAR = 1970, 2099
# 文件名防御：控制字符（二审 SEC3）
_CTRL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
# 日期文件夹格式中禁止混入的时间指令（避免生成带时分秒的文件夹名，二审 M4）
_TIME_DIRECTIVES = ("%H", "%I", "%M", "%S", "%f", "%p", "%X", "%c")


# ========== 配置文件（按各平台规范目录存放） ==========
def get_config_dir() -> Path:
    """返回平台规范的配置目录：Windows %APPDATA% / macOS Application Support / Linux XDG .config。"""
    if sys.platform == "win32":
        base = Path(os.getenv("APPDATA", str(Path.home() / "AppData/Roaming")))
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Application Support"
    else:
        base = Path(os.getenv("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return base / APP_NAME


def ensure_config_dir() -> Path:
    """确保配置目录存在（写入类操作前调用；读取类操作不创建目录，避免 import 副作用）。"""
    d = get_config_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def get_config_file() -> Path:
    return get_config_dir() / "config.json"


def load_config() -> dict:
    """读取配置文件；缺失或损坏时返回空字典（按默认值启动）。"""
    try:
        with open(get_config_file(), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def save_config(cfg: dict) -> bool:
    """保存配置到平台规范目录（写入前确保目录存在）。"""
    try:
        ensure_config_dir()
        with open(get_config_file(), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def reset_config() -> bool:
    """删除配置文件（恢复默认设置）。"""
    try:
        p = get_config_file()
        if p.exists():
            p.unlink()
        return True
    except Exception:
        return False


def clear_all_data() -> dict:
    """一键清理：删除配置文件与全部操作清单（跨平台，基于各平台规范配置目录）。

    返回统计: {"config": bool, "logs": n, "files": [被删除的文件路径]}
    （config=是否删除了配置文件，logs=删除的清单文件数，files=逐条删除的文件列表）
    """
    result = {"config": False, "logs": 0, "files": []}
    try:
        p = get_config_file()
        if p.exists():
            p.unlink()
            result["config"] = True
            result["files"].append(p)
    except OSError:
        pass
    try:
        d = get_config_dir() / "logs"
        if d.exists():
            n = 0
            for f in d.iterdir():
                if f.is_file():
                    try:
                        f.unlink()
                        n += 1
                        result["files"].append(f)
                    except OSError:
                        pass
            try:
                d.rmdir()  # 清空后移除空目录本身
            except OSError:
                pass
            result["logs"] = n
    except OSError:
        pass
    return result


# ========== 日期解析与 EXIF ==========
def get_exif_dt(filepath: str) -> datetime | None:
    """读取 EXIF 原始拍摄时间，失败或无 PIL 返回 None"""
    if not HAS_PIL:
        return None
    try:
        with Image.open(filepath) as img:
            exif = img.getexif()
            if exif:
                # 顶层 IFD0
                for tag_id, value in exif.items():
                    tag_name = TAGS.get(tag_id, "")
                    if tag_name == "DateTimeOriginal":
                        return datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
                # 未找到 DateTimeOriginal 时回退 DateTimeDigitized（tag 0x9004，评审 minor-3）
                for tag_id, value in exif.items():
                    if tag_id == 0x9004:
                        return datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
                # Exif 子 IFD（0x8769），DateTimeOriginal 通常存放在这里
                try:
                    sub = exif.get_ifd(0x8769)
                except Exception:
                    sub = {}
                for tag_id, value in sub.items():
                    tag_name = TAGS.get(tag_id, "")
                    if tag_name == "DateTimeOriginal":
                        return datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
                # 未找到 DateTimeOriginal 时回退 DateTimeDigitized（tag 0x9004，评审 minor-3）
                for tag_id, value in sub.items():
                    if tag_id == 0x9004:
                        return datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
    except Exception:
        pass
    return None


def get_exif_date(filepath: str, date_fmt: str = DATE_FMT) -> str | None:
    """读取 EXIF 原始拍摄日期（字符串格式），失败或无 PIL 返回 None"""
    dt = get_exif_dt(filepath)
    return dt.strftime(date_fmt) if dt else None


def get_name_dt(filename: str) -> datetime | None:
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
                return datetime(y, mo, d)
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
                elif not filename.startswith(("mmexport", "wx_camera_")):
                    # 10 位秒级时间戳：仅接受已知拍摄应用前缀，避免普通数字串误判（二审 S2）
                    continue
                dt = datetime.fromtimestamp(ts)
                # 合理性收紧：仅接受 2000-01-01 至当前时间之间（二审 S2：避免把普通数字串误判为拍摄时间）
                if datetime(2000, 1, 1) <= dt <= datetime.now():
                    return dt
            except (ValueError, OSError, OverflowError):
                continue
    return None


def get_name_date(filename: str, date_fmt: str = DATE_FMT) -> str | None:
    """从文件名中解析日期（字符串格式），失败返回 None"""
    dt = get_name_dt(filename)
    return dt.strftime(date_fmt) if dt else None


def get_file_dt(filepath: str) -> datetime | None:
    """获取文件日期对象：优先 EXIF，其次文件名日期，最后取文件时间。"""
    dt = get_exif_dt(filepath)
    if dt:
        return dt
    return get_file_dt_noexif(filepath)


def get_file_dt_noexif(filepath: str) -> datetime | None:
    """跳过 EXIF 的文件日期：文件名日期优先，其次文件时间（二审 PERF1 供 --no-exif 使用）。

    平台差异：Windows 取「修改时间」与「创建时间（st_ctime 近似）」中较早者；
    Linux/macOS 的 st_ctime 为元数据变更时间而非创建时间，仅取修改时间 st_mtime。
    """
    dt = get_name_dt(Path(filepath).name)
    if dt:
        return dt
    stat = os.stat(filepath)
    if os.name == "nt":
        earliest = min(stat.st_mtime, stat.st_ctime)
    else:
        earliest = stat.st_mtime
    return datetime.fromtimestamp(earliest)


def get_file_date(filepath: str, date_fmt: str = DATE_FMT) -> str:
    """获取文件日期（字符串格式）：优先 EXIF，其次文件名日期，最后取文件时间（Windows 含创建时间）"""
    dt = get_file_dt(filepath)
    return dt.strftime(date_fmt) if dt else ""


# ========== 基础工具 ==========
def _sanitize_name(name: str) -> str:
    """过滤文件名中的控制字符（防御性清洗，正常文件名不含；二审 SEC3）。"""
    return _CTRL_CHARS.sub("_", name)


def _clamp_log_keep(raw) -> int:
    """安全解析操作清单保留份数（非法/越界回退默认值；二审 M1 用于主线程快照）。"""
    try:
        n = int(str(raw).strip() or DEFAULT_LOG_KEEP)
    except (TypeError, ValueError):
        n = DEFAULT_LOG_KEEP
    return max(1, min(n, 999))


def is_trashed_file(name: str) -> bool:
    """.trashed 为 Android 回收站文件（文件名以 .trashed- 开头），优先于隐藏判定。"""
    return name.lower().startswith(".trashed-")


def is_hidden_file(path: Path) -> bool:
    """跨平台隐藏判定：Unix 点开头文件名，或 Windows 隐藏属性（FILE_ATTRIBUTE_HIDDEN）。"""
    if path.name.startswith("."):
        return True
    if os.name == "nt":
        try:
            return bool(path.stat().st_file_attributes & 0x2)
        except (AttributeError, OSError):
            return False
    return False


def _tag_pad(text: str, width: int = 12) -> str:
    """标签列定宽补空格：按显示宽度对齐（中文 2 列、英文 1 列），使文件名统一列开始。"""
    disp = sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)
    return text + " " * max(0, width - disp)


# ========== 文件收集与原子传输 ==========
def _md5_file(path: Path, block_size: int = 64 * 1024) -> str:
    """分块计算文件 MD5（64KB 分块读取），用于内容重复判定（先比大小、后比哈希）。"""
    h = hashlib.md5()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(block_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def collect_files(source: Path, dest_root: Path, supported: set, recursive: bool,
                  allow_all_extensions: bool = False):
    """收集源文件夹下待处理的照片/视频文件。
    - recursive: True 时递归扫描所有子文件夹，并排除目标文件夹（防止把目标扫进来）
    - allow_all_extensions: True 时跳过扩展名白名单过滤，所有文件类型均可整理
    - 返回文件路径列表（递归时保留相对路径用于日志）
    """
    if not recursive:
        return [f for f in source.iterdir()
                if f.is_file() and not f.is_symlink()
                and (allow_all_extensions or f.suffix.lower() in supported)]
    files = []
    dest_resolved = dest_root.resolve()
    for root, dirs, names in os.walk(source):
        root_path = Path(root)
        # 目标文件夹在源目录内时，剪掉该分支（其下所有子目录一并排除），避免把已归类文件当源文件
        dirs[:] = [d for d in dirs if (root_path / d).resolve() != dest_resolved]
        for name in names:
            p = root_path / name
            if p.is_file() and not p.is_symlink() \
                    and (allow_all_extensions or p.suffix.lower() in supported):
                files.append(p)
    return files


def _safe_transfer(src: Path, dest: Path, copy_mode: bool) -> None:
    """原子传输：先写 .photo-org-tmp 临时文件，完成后 os.replace 原子改名。
    - 复制模式：copy2 到 dest.photo-org-tmp → replace
    - 移动模式同盘：shutil.move（os.rename 本身原子）
    - 移动模式跨盘：copy2 到 dest.photo-org-tmp → replace → 删源
    失败时清理本次 .photo-org-tmp 残留，不留半成品。
    """
    tmp = None
    try:
        if copy_mode:
            tmp = dest.with_name(dest.name + ".photo-org-tmp")
            shutil.copy2(str(src), str(tmp))
            os.replace(tmp, dest)
        else:
            if os.stat(src).st_dev == os.stat(dest.parent).st_dev:
                shutil.move(str(src), str(dest))
            else:
                tmp = dest.with_name(dest.name + ".photo-org-tmp")
                shutil.copy2(str(src), str(tmp))
                os.replace(tmp, dest)
                # 跨盘移动：目标副本已原子就位，源删除失败时抛出明确错误（目标文件仍完整可用）
                try:
                    src.unlink()
                except OSError as e:
                    raise OSError(f"Failed to remove source file: {src} ({e})") from e
    except Exception:
        if tmp is not None:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
        raise


def _cleanup_parts(dest_root: Path) -> list:
    """扫描目标目录下残留的 .photo-org-tmp 半成品文件（本脚本专属后缀），返回文件列表（不删除）。

    盘根保护：目标目录是文件系统根（如 D:/）时不做全盘递归扫描，直接返回空列表，
    避免 rglob 全盘遍历造成长时间卡顿。
    """
    if not dest_root.exists():
        return []
    if dest_root.resolve() == Path(dest_root.anchor).resolve():
        return []
    try:
        return [p for p in dest_root.rglob("*.photo-org-tmp")]
    except OSError:
        return []


# ========== 消息机制（英文 code + 结构化参数） ==========
# 英文默认渲染表（供 print / 无翻译层的调用方兜底展示；上层应自行翻译）
_EN_MODES = {"dry": "DRY RUN", "copy": "COPY DONE", "move": "DONE"}
_EN_TAGS = {"skip": "[Skip]", "sim": "[Sim]", "move": "[Move]", "copy": "[Copy]",
            "rename": "[Rename]", "error": "[Error]", "hidden": "[Hidden]",
            "trash": "[Trash]", "video": "[Video]", "delete": "[Delete]"}
_EN_TAILS = {"skip_in_place_post": " (inside an existing date folder)",
             "skip_no_date_post": " (no date)",
             "skip_exists_post": " (already exists) ",
             "skip_exclude_post": " (excluded by regex) "}
_EN_TEMPLATES = {
    "src_missing": "Source folder does not exist: {path}",
    "src_not_dir": "Source is not a folder: {path}",
    "dest_created": "Destination folder created: {path}",
    "dest_create_err": "Failed to create destination folder: {path} ({err})",
    "dest_missing": "Destination folder does not exist: {path}",
    "dest_not_dir": "Destination is not a folder: {path}",
    "bad_fmt": "Invalid date format: {err}",
    "bad_regex": "Invalid exclude regex: {err}",
    "bad_group": "Invalid group level: {level}",
    "fmt_time": "Time directive not allowed in {label}: {fmt}",
    "warn_group_need": "[Info] Date folder inside group requires group levels to take effect; ignored",
    "warn_pil": "[Warning] Pillow unavailable: {err} EXIF dates will be skipped",
    "no_files": "[Info] No supported photo/video files found{recursive}",
    "skip_need_recursive": "[Tip] Skip existing date folders requires recursive scan to take effect",
    "skip_need_inplace": "[Tip] Skip existing date folders only works for in-place organizing",
    "detect_dirs": "[Info] Detected {n} existing date folder(s), files inside will be skipped",
    "warn_scan_dirs": "[Warning] Failed to scan date folders: {err}",
    "summary": "Moved: {moved} | Copied: {copied} | Skipped: {skipped} | Failed: {failed}",
    "summary_duplicates": "Duplicates deleted: {duplicates}",
    "summary_dirs": "New date folders: {n}",
    "summary_hidden_trash": "Hidden: {hidden} | Trash: {trash}",
    "summary_exif": "EXIF: {exif} | Fallback: {fallback}",
    "detail_title": "Detail by category:",
    "detail_imgs": "Images: {total} (EXIF: {exif}, fallback: {fallback}))",
    "detail_videos": "Videos: {total} (EXIF: {exif}, fallback: {fallback}))",
    "detail_by_ext": "Detail by extension:",
    "detail_ext_line": "  {ext}: {total} (EXIF: {exif}, fallback: {fallback}))",
    "detail_skip": "Skipped by reason: in-place {in_place} | duplicate {dup} | no-date {nodate} | hidden {hidden} | trash {trash}",
    "detail_skip_hidden_scanned": "hidden scanned: {hidden}",
    "detail_skip_trash_scanned": "trash scanned: {trash}",
    "warn_restore_skip_scope": "[Warning] Skipped: outside recorded scope: {src} -> {dst}",
    "skip_dst_missing": "[Skip] Destination file missing: {dst}",
    "skip_src_exists": "[Skip] Original file already exists: {src}",
    "restore_move_pre": "[Restore] Moved back: {dst} -> {src}",
    "err_restore": "[Error] Restore failed: {dst} -> {src} ({err})",
    "restore_rm_copy": "[Restore] Removed copy: {dst}",
    "err_rm_copy": "[Error] Remove copy failed: {dst} ({err})",
    "skip_copy_missing": "[Skip] Copy missing: {dst}",
    "err_no_actions": "[Error] No operation log found",
    "err_restore_scope": "[Error] Log missing source/destination scope; restore refused",
    "err_read_log": "[Error] Failed to read operation log: {err}",
}


def _emit(code: str, params: dict | None = None):
    """构造结构化日志消息：返回 (code, params)，code 为英文键。"""
    return (code, params or {})


def _render_en(item) -> str:
    """英文默认渲染：把结构化消息 (code, params) 渲染为英文文本（供 print 兜底）。"""
    if isinstance(item, str):
        return item
    code, params = item
    if code == "log_line":
        tags = "".join(_EN_TAGS.get(t, "") for t in params.get("tags", []) if t)
        tail_i18n = params.get("tail_i18n")
        return (_tag_pad(tags) + params.get("name", "")
                + (_EN_TAILS.get(tail_i18n, "") if tail_i18n else "")
                + (params.get("tail") or ""))
    if code == "mode_header":
        return f"\n===== {_EN_MODES.get(params.get('mode', ''), params.get('mode', ''))} ====="
    if code == "detail_skip":
        base = _EN_TEMPLATES["detail_skip"].format(**params)
        if params.get("include_hidden_trash"):
            base = (f"Skipped by reason: in-place {params['in_place']} | duplicate {params['dup']} "
                    f"| no-date {params['nodate']} | "
                    + _EN_TEMPLATES["detail_skip_hidden_scanned"].format(hidden=params["hidden"])
                    + " | "
                    + _EN_TEMPLATES["detail_skip_trash_scanned"].format(trash=params["trash"]))
        if params.get("exclude"):
            base += f" | regex-excluded {params['exclude']}"
        return base
    tpl = _EN_TEMPLATES.get(code)
    if tpl is None:
        return f"[{code}] {params}"
    try:
        return tpl.format(**params)
    except (KeyError, ValueError):
        return f"[{code}] {params}"


def _default_log(item) -> None:
    """core 默认日志回调：英文渲染后 print。"""
    print(_render_en(item))


# ========== 整理核心 ==========
def organize(source_dir: str, dest_dir: str, dry_run: bool = False,
             include_video: bool = INCLUDE_VIDEO, log_func=_default_log,
             copy_mode: bool = False, recursive: bool = False,
             prefix_duplicates: bool = False, date_fmt: str = DATE_FMT,
             group_levels: str | None = None,
             year_fmt: str = "%Y", month_fmt: str = "%m", day_fmt: str = "%d",
             date_folder_in_group: bool = False,
             skip_existing_date_folders: bool = True,
             photo_exts: set | None = None,
             add_photo_exts: set | list | None = None,
             remove_photo_exts: set | list | None = None,
             no_exif: bool = False,
             include_hidden_trash: bool = False,
             exclude_regex: str | None = None,
             allow_all_extensions: bool = False,
             delete_duplicates: bool = False,
             actions: list | None = None,
             pre_scanned: list | None = None):
    """整理源文件夹中的照片/视频到目标文件夹的日期子文件夹。

    - source_dir: 源文件夹（从中读取照片）
    - dest_dir: 目标文件夹（照片按日期归类到此）
    - dry_run: 模拟运行，只预览不移动
    - include_video: 是否同时整理视频
    - log_func: 日志回调，接收结构化消息 item=(code, params)；默认英文 print
    - copy_mode: True 时复制文件到日期文件夹（原文件保留）；默认 False 为移动
    - recursive: True 时递归扫描源文件夹的所有子文件夹
    - prefix_duplicates: True 时重名冲突自动加来源文件夹名前缀；默认跳过
    - date_fmt: 单层日期文件夹格式（strftime），如 %Y 按年、%Y-%m 按月、%Y-%m-%d 按日
    - group_levels: 层级分组模式，None/"Y" 仅年层、"YM" 年+月、"YMD" 年+月+日
    - year_fmt / month_fmt / day_fmt: 层级分组时各层文件夹名格式（strftime），默认 %Y / %m / %d
    - date_folder_in_group: 仅层级分组时生效。True 时在分组得到的最内层文件夹里再建一个日期文件夹（按 date_fmt），文件放日期文件夹；False 时文件直接放在最内层（年/月/日）文件夹里
    - skip_existing_date_folders: 仅源=目标（原地整理）时生效；源目录下名字能匹配目标日期格式的子文件夹视为已整理，其中文件直接跳过（不判断文件日期）
    - photo_exts: 图片格式集合（默认基础 9 种 BASE_PHOTO_EXTS；GUI 传入本次运行勾选结果）
    - add_photo_exts: 额外增加的图片扩展名（CLI --add-photo-ext）
    - remove_photo_exts: 临时排除的图片扩展名（CLI --remove-photo-ext）
    - no_exif: True 时跳过 EXIF 读取（文件名日期优先，其次文件时间），大量文件时更快（二审 PERF1）
    - include_hidden_trash: True 时也整理隐藏文件与 .trashed 文件（默认 False：跳过并打 [跳过][隐藏]/[跳过][Trash] 标签统计）
    - exclude_regex: 正则表达式；对文件的源相对路径做 re.search，命中则跳过并计入跳过统计
      （先过扩展名白名单，再按名字规则过滤；留空/None 不启用）
    - allow_all_extensions: True 时跳过扩展名白名单过滤，所有文件类型均可整理
    - delete_duplicates: True 时整理前递归扫描目标目录建立大小索引，源文件先比大小、
      命中同大小才计算 MD5（64KB 分块）比对；内容一致判定为重复：实际执行删除源文件并写入
      操作清单（动作类型 delete，记录源路径与对应目标路径），--dry-run 仅报告不删除；
      命中重复时不再执行移动/复制（删除优先级最高）；统计新增 duplicates 计数
    - pre_scanned: 可选已扫描文件列表（Path 列表）；非 None 时跳过内部 collect_files，
      避免上层已扫描时二次扫描（评审 minor-5）。需保持与 collect_files 过滤语义一致。
    - actions: 可选列表；非模拟运行时，每次移动/复制成功追加
      {"type": "move"/"copy", "src": 原路径, "dst": 目标路径, "ok": True}，供保存操作清单与还原使用

    返回 stats dict（moved/copied/skipped/failed/new_dirs/exif/fallback/...），
    含 skipped_files 列表字段（被跳过文件路径，skip_dup/skip_nodate/skip_in_place/hidden/trash 分支收集）。
    """
    # 兼容命令行写法：year / year-month / year-month-day → Y / YM / YMD
    group_levels = {"year": "Y", "year-month": "YM", "year-month-day": "YMD"}.get(group_levels, group_levels)
    # 所有提前退出路径统一返回全 0 统计（键与正常路径一致），避免上层拿到 None（评审 #1）
    zero_stats = {"moved": 0, "copied": 0, "skipped": 0, "failed": 0, "new_dirs": set(), "exif": 0,
                  "fallback": 0, "skip_dup": 0, "skip_nodate": 0, "skip_in_place": 0,
                  "hidden": 0, "trash": 0, "skip_exclude": 0, "duplicates": 0,
                  "by_ext": {}, "skipped_files": []}
    source = Path(source_dir)
    if not source.exists():
        log_func(_emit("src_missing", {"path": source_dir}))
        return zero_stats
    if not source.is_dir():
        log_func(_emit("src_not_dir", {"path": source_dir}))
        return zero_stats
    dest_root = Path(dest_dir)
    if dest_root.resolve() != Path(dest_root.anchor).resolve() and not dest_root.exists():
        # 目标文件夹不存在时自动创建（盘根如 D:/ 除外，避免误建不可操作）
        try:
            dest_root.mkdir(parents=True, exist_ok=True)
            log_func(_emit("dest_created", {"path": str(dest_root)}))
        except OSError as e:
            log_func(_emit("dest_create_err", {"path": str(dest_root), "err": str(e)}))
            return zero_stats
    if not dest_root.exists():
        log_func(_emit("dest_missing", {"path": dest_dir}))
        return zero_stats
    if not dest_root.is_dir():
        log_func(_emit("dest_not_dir", {"path": dest_dir}))
        return zero_stats

    # 校验日期格式是否可用（如 %Y、%Y-%m 等 strftime 写法）
    try:
        datetime.now().strftime(date_fmt)
        if group_levels:
            datetime.now().strftime(year_fmt)
            if "M" in group_levels:
                datetime.now().strftime(month_fmt)
            if "D" in group_levels:
                datetime.now().strftime(day_fmt)
    except Exception as e:
        log_func(_emit("bad_fmt", {"err": str(e)}))
        return zero_stats

    # 层级分组模式白名单：仅接受 单层(None)/Y/YM/YMD（二审 M4）
    if group_levels not in (None, "Y", "YM", "YMD"):
        log_func(_emit("bad_group", {"level": group_levels}))
        return zero_stats

    # 拒绝日期格式中混入时间指令（避免生成带时分秒的文件夹名，二审 M4）
    for _label, _fmt in (("date_fmt", date_fmt),
                         ("year_fmt", year_fmt),
                         ("month_fmt", month_fmt),
                         ("day_fmt", day_fmt)):
        for _d in _TIME_DIRECTIVES:
            if _d in _fmt:
                log_func(_emit("fmt_time", {"label": _label, "fmt": _fmt}))
                return zero_stats
    if date_folder_in_group and not group_levels:
        # 无分组层级时「分组时创建日期文件夹」无实际效果：提示并忽略（不改行为）
        log_func(_emit("warn_group_need"))

    # 图片格式：默认基础 9 种，可叠加 CLI 加减
    exts = set(photo_exts) if photo_exts else set(BASE_PHOTO_EXTS)
    exts |= {str(e).strip().lower() for e in (add_photo_exts or []) if str(e).strip()}
    exts -= {str(e).strip().lower() for e in (remove_photo_exts or []) if str(e).strip()}
    supported = exts | (set(VIDEO_EXTS) if include_video else set())
    photos = pre_scanned if pre_scanned is not None else collect_files(source, dest_root, supported, recursive, allow_all_extensions)

    # Pillow 未加载时给出显式警告（EXIF 会静默失效，必须让用户看见）
    if not HAS_PIL:
        log_func(_emit("warn_pil", {"err": PIL_LOAD_ERROR}))

    # 半成品清理已改为手动按钮/命令触发（避免目标盘根时全盘扫描卡顿），此处不再自动执行

    if not photos:
        log_func(_emit("no_files", {"recursive": recursive}))
        return zero_stats

    stats = {"moved": 0, "copied": 0, "skipped": 0, "failed": 0, "new_dirs": set(), "exif": 0, "fallback": 0,
             "skip_dup": 0, "skip_nodate": 0, "skip_in_place": 0, "hidden": 0, "trash": 0,
             "skip_exclude": 0, "duplicates": 0, "by_ext": {},
             "skipped_files": []}
    seen_names: dict[str, set] = {}  # date_str -> 已占用文件名集合（dry-run 也能量重名）

    # 跳过已有日期文件夹：仅原地整理（源=目标）时生效。
    # 源目录下名字可被目标日期格式解析的子文件夹视为已整理，其中文件不再判断日期直接跳过
    classified_dirs: set[Path] = set()
    if skip_existing_date_folders:
        if not recursive:
            log_func(_emit("skip_need_recursive"))
        elif source.resolve() != dest_root.resolve():
            log_func(_emit("skip_need_inplace"))
        else:
            try:
                if group_levels:
                    # 层级分组：逐层匹配 年/月/日（date_folder_in_group 时最内层再套 date_fmt）
                    def _match_level(parent: Path, fmt: str) -> list[Path]:
                        # 严格匹配：strptime 解析成功后须 strftime 回比一致，
                        # 避免宽松格式（如 %Y）把任意 4 位数字目录误判为日期目录
                        matched = []
                        for d in parent.iterdir():
                            if d.is_dir():
                                try:
                                    dt = datetime.strptime(d.name, fmt)
                                    if dt.strftime(fmt) == d.name:
                                        matched.append(d)
                                except ValueError:
                                    pass
                        return matched
                    year_dirs = _match_level(source, year_fmt)
                    if "M" in group_levels:
                        for yd in year_dirs:
                            month_dirs = _match_level(yd, month_fmt)
                            if "D" in group_levels:
                                for md in month_dirs:
                                    day_dirs = _match_level(md, day_fmt)
                                    if date_folder_in_group:
                                        classified_dirs.update(_match_level(md, date_fmt))
                                    else:
                                        classified_dirs.update(day_dirs)
                            else:
                                if date_folder_in_group:
                                    classified_dirs.update(_match_level(yd, date_fmt))
                                else:
                                    classified_dirs.update(month_dirs)
                    else:
                        if date_folder_in_group:
                            classified_dirs.update(_match_level(source, date_fmt))
                        else:
                            classified_dirs.update(year_dirs)
                else:
                    for d in source.iterdir():
                        if d.is_dir():
                            try:
                                datetime.strptime(d.name, date_fmt)
                                classified_dirs.add(d)
                            except ValueError:
                                pass
                if classified_dirs:
                    log_func(_emit("detect_dirs", {"n": len(classified_dirs)}))
            except OSError as e:
                log_func(_emit("warn_scan_dirs", {"err": str(e)}))

    # 正则排除：编译一次，后续对每个文件的源相对路径做 re.search
    exclude_re = None
    if exclude_regex:
        try:
            exclude_re = re.compile(exclude_regex)
        except re.error as e:
            log_func(_emit("bad_regex", {"err": str(e)}))
            return zero_stats

    # 删除重复：整理前递归扫描目标目录建立"大小索引" {size -> [路径]}（不读内容）
    dup_size_index: dict[int, list] = {}
    if delete_duplicates:
        try:
            for _root, _dirs, _names in os.walk(dest_root):
                _rp = Path(_root)
                for _name in _names:
                    _fp = _rp / _name
                    if _fp.suffix.lower() == ".photo-org-tmp":
                        continue  # 排除半成品临时文件
                    try:
                        dup_size_index.setdefault(_fp.stat().st_size, []).append(_fp)
                    except OSError:
                        pass
        except OSError:
            dup_size_index = {}

    for p in photos:
        try:
            # 已有日期文件夹内的文件视为已整理，不判断日期直接跳过
            if p.parent in classified_dirs:
                stats["skipped"] += 1
                stats["skip_in_place"] += 1
                stats["skipped_files"].append(str(p))
                log_func(_emit("log_line", {"tags": ["skip"], "name": p.name,
                                            "tail_i18n": "skip_in_place_post"}))
                continue
            # 隐藏/.trashed 文件：默认不处理并打 [跳过][隐藏]/[跳过][Trash] 标签；
            # 勾选后正常归类。隐藏与 Trash 判定互不短路（可同时命中），视频额外补 [视频] 标签
            is_trash = is_trashed_file(p.name)
            is_hidden = is_hidden_file(p)
            is_video = p.suffix.lower() in VIDEO_EXTS
            if is_trash or is_hidden:
                if not include_hidden_trash:
                    stats["skipped"] += 1
                    if is_trash:
                        stats["trash"] += 1
                    else:
                        stats["hidden"] += 1
                    stats["skipped_files"].append(str(p))
                    skip_tags = (["skip"]
                                 + (["hidden"] if is_hidden else [])
                                 + (["trash"] if is_trash else [])
                                 + (["video"] if is_video else []))
                    log_func(_emit("log_line", {"tags": skip_tags, "name": p.name}))
                    continue
                if is_trash:
                    stats["trash"] += 1
                else:
                    stats["hidden"] += 1
            extra_tag = (["hidden"] if is_hidden else []) + (["trash"] if is_trash else []) + (["video"] if is_video else [])
            # 源相对路径：正则排除与删除重复分支共用（re.search 匹配与删除动作记录）
            try:
                rel_path = p.relative_to(source).as_posix()
            except ValueError:
                rel_path = p.name
            # 正则排除：对源相对路径 re.search，命中则跳过并计入跳过统计（先过类型白名单，再按名字规则过滤）
            if exclude_re is not None:
                if exclude_re.search(rel_path):
                    stats["skipped"] += 1
                    stats["skip_exclude"] += 1
                    stats["skipped_files"].append(str(p))
                    log_func(_emit("log_line", {"tags": ["skip"], "name": rel_path,
                                                "tail_i18n": "skip_exclude_post"}))
                    continue

            # 删除重复：源文件先比大小，命中同大小才计算 MD5 比对；内容一致判定为重复
            # （dry-run 仅报告不删除；命中重复时不再执行移动/复制，删除优先级最高）
            if delete_duplicates and dup_size_index:
                try:
                    p_size = p.stat().st_size
                except OSError:
                    p_size = None
                if p_size is not None and p_size in dup_size_index:
                    dup_target = None
                    try:
                        p_hash = _md5_file(p)
                    except OSError:
                        p_hash = None
                    if p_hash is not None:
                        for cand in dup_size_index[p_size]:
                            try:
                                if cand.resolve() == p.resolve():
                                    continue
                            except OSError:
                                continue
                            try:
                                if _md5_file(cand) == p_hash:
                                    dup_target = cand
                                    break
                            except OSError:
                                continue
                    if dup_target is not None:
                        if dry_run:
                            stats["duplicates"] += 1
                            log_func(_emit("log_line", {"tags": ["sim", "delete"], "name": rel_path,
                                                        "tail": f" -> {dup_target}"}))
                            if actions is not None:
                                actions.append({"type": "plan_delete", "src": str(p),
                                                "dst": str(dup_target), "ok": True})
                        else:
                            try:
                                p.unlink()
                                stats["duplicates"] += 1
                                log_func(_emit("log_line", {"tags": ["delete"], "name": rel_path,
                                                            "tail": f" -> {dup_target}"}))
                                if actions is not None:
                                    actions.append({"type": "delete", "src": str(p),
                                                    "dst": str(dup_target), "ok": True})
                            except OSError as e:
                                stats["failed"] += 1
                                log_func(_emit("log_line", {"tags": ["error"], "name": rel_path,
                                                            "tail": f" ({e})"}))
                        continue

            ext = p.suffix.lower()
            ext_stat = stats["by_ext"].setdefault(ext, {"total": 0, "exif": 0, "fallback": 0})
            ext_stat["total"] += 1
            if no_exif:
                # --no-exif：跳过 EXIF 读取，文件名日期优先，其次文件时间（二审 PERF1）
                dt = get_file_dt_noexif(str(p))
                stats["fallback"] += 1
                ext_stat["fallback"] += 1
            else:
                dt = get_exif_dt(str(p))
                if dt:
                    stats["exif"] += 1
                    ext_stat["exif"] += 1
                else:
                    dt = get_file_dt(str(p))
                    stats["fallback"] += 1
                    ext_stat["fallback"] += 1

            if dt is None:
                stats["skipped"] += 1
                stats["skip_nodate"] += 1
                stats["skipped_files"].append(str(p))
                log_func(_emit("log_line", {"tags": ["skip"], "name": p.name,
                                            "tail_i18n": "skip_no_date_post"}))
                continue

            # 目标相对路径：单层格式 or 年/月/日层级嵌套（分组时可再叠一层日期文件夹）
            if group_levels:
                parts = [dt.strftime(year_fmt)]
                if "M" in group_levels:
                    parts.append(dt.strftime(month_fmt))
                if "D" in group_levels:
                    parts.append(dt.strftime(day_fmt))
                if date_folder_in_group:
                    parts.append(dt.strftime(date_fmt))
                date_str = "/".join(parts)
            else:
                date_str = dt.strftime(date_fmt)

            dest_dir_path = dest_root / date_str
            is_new_dir = not dest_dir_path.exists()
            if not dry_run:
                dest_dir_path.mkdir(parents=True, exist_ok=True)
            if is_new_dir:
                stats["new_dirs"].add(date_str)

            # 显示用相对路径（递归时带子文件夹层级）
            try:
                rel = p.relative_to(source).as_posix()
            except ValueError:
                rel = p.name

            dest_path = dest_dir_path / p.name
            occupied = seen_names.setdefault(date_str, set())
            if dest_path.exists() or p.name in occupied:
                if not prefix_duplicates:
                    stats["skipped"] += 1
                    stats["skip_dup"] += 1
                    stats["skipped_files"].append(str(p))
                    log_func(_emit("log_line", {"tags": ["skip"], "name": rel,
                                                "tail_i18n": "skip_exists_post",
                                                "tail": f" -> {date_str}/"}))
                else:
                    # 自动加来源文件夹名前缀（顶层文件即源目录名），仍冲突则追加序号
                    prefix = p.parent.name
                    base, ext = os.path.splitext(p.name)
                    # 防御：剔除路径中不可见的控制字符（正常文件名不含，防御异常输入；二审 SEC3）
                    new_name = _sanitize_name(f"{prefix}_{p.name}")
                    dest_path = dest_dir_path / new_name
                    counter = 1
                    while dest_path.exists() or new_name in occupied:
                        new_name = _sanitize_name(f"{prefix}_{base}_{counter}{ext}")
                        dest_path = dest_dir_path / new_name
                        counter += 1
                    occupied.add(new_name)
                    if dry_run:
                        stats["copied" if copy_mode else "moved"] += 1
                        log_func(_emit("log_line", {"tags": ["sim", "rename"] + extra_tag,
                                                    "name": rel,
                                                    "tail": f" -> {date_str}/{new_name}"}))
                        if actions is not None:
                            actions.append({"type": "plan_copy" if copy_mode else "plan_move",
                                            "src": str(p), "dst": str(dest_path), "ok": True})
                    else:
                        _safe_transfer(p, dest_path, copy_mode)
                        stats["copied" if copy_mode else "moved"] += 1
                        log_func(_emit("log_line", {"tags": ["rename"] + extra_tag,
                                                    "name": rel,
                                                    "tail": f" -> {date_str}/{new_name}"}))
                        if actions is not None:
                            actions.append({"type": "copy" if copy_mode else "move",
                                            "src": str(p), "dst": str(dest_path), "ok": True})
            elif dry_run:
                stats["copied" if copy_mode else "moved"] += 1
                occupied.add(p.name)
                mode_tag = ["copy"] if copy_mode else ["move"]
                log_func(_emit("log_line", {"tags": ["sim"] + mode_tag + extra_tag,
                                            "name": rel,
                                            "tail": f" -> {date_str}/"}))
                if actions is not None:
                    actions.append({"type": "plan_copy" if copy_mode else "plan_move",
                                    "src": str(p), "dst": str(dest_path), "ok": True})
            else:
                _safe_transfer(p, dest_path, copy_mode)
                stats["copied" if copy_mode else "moved"] += 1
                occupied.add(p.name)
                mode_tag = ["copy"] if copy_mode else ["move"]
                log_func(_emit("log_line", {"tags": mode_tag + extra_tag,
                                            "name": rel,
                                            "tail": f" -> {date_str}/"}))
                if actions is not None:
                    actions.append({"type": "copy" if copy_mode else "move",
                                    "src": str(p), "dst": str(dest_path), "ok": True})
        except Exception as e:
            stats["failed"] += 1
            log_func(_emit("log_line", {"tags": ["error"], "name": p.name,
                                        "tail": f" ({e})"}))

    mode = "dry" if dry_run else ("copy" if copy_mode else "move")
    log_func(_emit("mode_header", {"mode": mode}))
    log_func(_emit("summary", {"moved": stats["moved"], "copied": stats["copied"],
                               "skipped": stats["skipped"], "failed": stats["failed"]}))
    log_func(_emit("summary_dirs", {"n": len(stats["new_dirs"])}))
    if stats["hidden"] or stats["trash"]:
        log_func(_emit("summary_hidden_trash", {"hidden": stats["hidden"], "trash": stats["trash"]}))
    log_func(_emit("summary_exif", {"exif": stats["exif"], "fallback": stats["fallback"]}))

    # 图片/视频大类统计
    img_exif = img_fb = vid_exif = vid_fb = 0
    for ext, c in stats["by_ext"].items():
        if ext in VIDEO_EXTS:
            vid_exif += c["exif"]
            vid_fb += c["fallback"]
        else:
            img_exif += c["exif"]
            img_fb += c["fallback"]
    log_func(_emit("detail_title"))
    log_func(_emit("detail_imgs", {"total": img_exif + img_fb, "exif": img_exif, "fallback": img_fb}))
    log_func(_emit("detail_videos", {"total": vid_exif + vid_fb, "exif": vid_exif, "fallback": vid_fb}))
    # 按扩展名展开
    log_func(_emit("detail_by_ext"))
    for ext, c in sorted(stats["by_ext"].items()):
        log_func(_emit("detail_ext_line", {"ext": ext, "total": c["total"],
                                           "exif": c["exif"], "fallback": c["fallback"]}))
    # 跳过原因细分
    if stats["skip_in_place"] or stats["skip_dup"] or stats["skip_nodate"] or stats["hidden"] or stats["trash"] or stats["skip_exclude"]:
        log_func(_emit("detail_skip", {"in_place": stats["skip_in_place"], "dup": stats["skip_dup"],
                                       "nodate": stats["skip_nodate"], "hidden": stats["hidden"],
                                       "trash": stats["trash"], "exclude": stats["skip_exclude"],
                                       "include_hidden_trash": include_hidden_trash}))
    return stats


# ========== 日志保存与还原 ==========
def get_logs_dir() -> Path:
    """日志目录：配置目录下的 logs/。"""
    d = get_config_dir() / "logs"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


def list_logs() -> list:
    """列出 logs 目录下已保存的操作清单（.jsonl），按修改时间倒序。"""
    d = get_logs_dir()
    try:
        logs = [p for p in d.iterdir() if p.suffix.lower() == ".jsonl"]
    except OSError:
        return []
    logs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return logs


def write_organize_log(meta: dict, lines: list, actions: list, keep: int,
                       logs_dir: str | Path | None = None) -> Path:
    """把一次整理会话的操作清单写成 .jsonl（首行为会话元信息，其余为动作清单），并清理超出保留份数的旧清单。

    - meta: 会话元信息（时间/模式/源/目标等），写入首行便于追溯
    - lines: 已废弃（deprecated）：整理过程日志行，仅用于界面/终端输出，不再落盘（评审 #8），调用方可传空列表
    - actions: 实际执行的动作清单（move/copy + src/dst）
    - keep: 保留最近几份操作清单
    - logs_dir: 可选日志目录；缺省走 get_logs_dir()（测试可注入临时目录，避免污染真实配置目录）
    """
    d = Path(logs_dir) if logs_dir else get_logs_dir()
    stem = "organize_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    jsonl_path = d / (stem + ".jsonl")
    n = 1
    while jsonl_path.exists():  # 同秒重名时追加序号，避免覆盖
        n += 1
        jsonl_path = d / f"{stem}_{n}.jsonl"
    stem = jsonl_path.stem
    try:
        with open(jsonl_path, "w", encoding="utf-8") as f:
            meta_rec = {"type": "meta", "ok": False, "action_count": len(actions), **meta}
            f.write(json.dumps(meta_rec, ensure_ascii=False) + "\n")
            for rec in actions:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        # 写入失败不返回假路径：抛出原始异常，由上层统一报错，避免用户误以为清单已保存
        raise OSError(str(e)) from e
    # 清理旧操作清单：保留最近 keep 份
    try:
        keep = max(1, int(keep))
        logs = sorted([p for p in d.iterdir() if p.suffix.lower() == ".jsonl"],
                      key=lambda p: p.stat().st_mtime, reverse=True)
        for old in logs[keep:]:
            old.unlink(missing_ok=True)
    except OSError:
        pass
    return jsonl_path


def _path_within(root: Path, p: Path) -> bool:
    """路径范围校验（二审 SEC1）：p 必须位于 root 目录之下。

    拒绝条件：路径含 '..' 段、解析失败、或解析结果为文件系统根。
    """
    try:
        r = root.resolve()
        q = p.resolve()
    except OSError:
        return False
    if ".." in p.parts or ".." in q.parts:
        return False
    if q == q.anchor:  # 文件系统根
        return False
    try:
        return q.is_relative_to(r)
    except AttributeError:
        return str(q).startswith(str(r) + os.sep)


def restore_from_log(stem: str, log_func=_default_log,
                     logs_dir: str | Path | None = None) -> dict:
    """根据日志的操作清单（.jsonl）还原变更。

    - 移动模式：把当时移动成功的文件从目标位置移回原路径；
    - 复制模式：删除当时复制产生的副本（原文件本就在源位置）。
    安全策略：仅当目标文件仍存在且原位置已空出时执行，避免覆盖现有文件。
    - logs_dir: 可选日志目录；缺省走 get_logs_dir()（测试可注入临时目录，避免污染真实配置目录）。
    返回统计: {"moved": n, "removed": n, "skipped": n, "missing": n, "error": item?}
    （error 为结构化消息 (code, params)，上层负责翻译展示）
    """
    d = Path(logs_dir) if logs_dir else get_logs_dir()
    jsonl_path = d / (stem + ".jsonl")
    if not jsonl_path.exists():
        return {"error": _emit("err_no_actions")}
    stats = {"moved": 0, "removed": 0, "skipped": 0, "missing": 0}
    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            # 首行为会话元信息：记录源/目标目录，作为还原路径范围校验基准（二审 SEC1）
            src_root = dest_root = None
            first = f.readline().strip()
            if first:
                try:
                    meta_rec = json.loads(first)
                except json.JSONDecodeError:
                    meta_rec = {}
                if meta_rec.get("type") == "meta":
                    src_root = Path(meta_rec["src"]) if meta_rec.get("src") else None
                    dest_root = Path(meta_rec["dest"]) if meta_rec.get("dest") else None
            if src_root is None or dest_root is None:
                return {"error": _emit("err_restore_scope")}
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not rec.get("ok"):
                    continue
                typ = rec.get("type")
                src = Path(rec["src"])
                dst = Path(rec["dst"])
                # 二审 SEC1：路径必须位于该次会话记录的源/目标目录之下，否则跳过并告警
                if not (_path_within(src_root, src) and _path_within(dest_root, dst)):
                    stats["skipped"] += 1
                    log_func(_emit("warn_restore_skip_scope", {"src": str(src), "dst": str(dst)}))
                    continue
                if typ == "move":
                    if not dst.exists():
                        stats["missing"] += 1
                        log_func(_emit("skip_dst_missing", {"dst": str(dst)}))
                        continue
                    if src.exists():
                        stats["skipped"] += 1
                        log_func(_emit("skip_src_exists", {"src": str(src)}))
                        continue
                    try:
                        src.parent.mkdir(parents=True, exist_ok=True)
                        _safe_transfer(dst, src, copy_mode=False)
                        stats["moved"] += 1
                        log_func(_emit("restore_move_pre", {"dst": str(dst), "src": str(src)}))
                    except Exception as e:
                        stats["skipped"] += 1
                        log_func(_emit("err_restore", {"dst": str(dst), "src": str(src), "err": str(e)}))
                elif typ == "copy":
                    if dst.exists():
                        try:
                            dst.unlink()
                            stats["removed"] += 1
                            log_func(_emit("restore_rm_copy", {"dst": str(dst)}))
                        except OSError as e:
                            stats["skipped"] += 1
                            log_func(_emit("err_rm_copy", {"dst": str(dst), "err": str(e)}))
                    else:
                        stats["missing"] += 1
                        log_func(_emit("skip_copy_missing", {"dst": str(dst)}))
    except OSError as e:
        return {"error": _emit("err_read_log", {"err": str(e)})}
    return stats


# ========== 数据转换 ==========
def _stats_to_meta(stats: dict) -> dict:
    """把 organize 返回的 stats 转成可 JSON 序列化的精简统计（写入操作清单 meta 供后续查看摘要）。"""
    return {
        "moved": int(stats.get("moved", 0)),
        "copied": int(stats.get("copied", 0)),
        "skipped": int(stats.get("skipped", 0)),
        "failed": int(stats.get("failed", 0)),
        "new_dirs": len(stats.get("new_dirs") or ()),
        "hidden": int(stats.get("hidden", 0)),
        "trash": int(stats.get("trash", 0)),
        "skip_exclude": int(stats.get("skip_exclude", 0)),
        "duplicates": int(stats.get("duplicates", 0)),
        "exif": int(stats.get("exif", 0)),
        "fallback": int(stats.get("fallback", 0)),
        "by_ext": stats.get("by_ext") or {},
    }
