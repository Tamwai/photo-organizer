"""
照片按拍摄日期归类脚本（命令行版 + 图形界面版二合一）

用法：
    python photo_organizer.py                        # 无参数：打开图形界面（双击 EXE 同此）
    python photo_organizer.py <源文件夹>              # 命令行：整理源文件夹到默认目标文件夹
    python photo_organizer.py <源文件夹> <目标文件夹>  # 命令行：从源文件夹归类到目标文件夹
    python photo_organizer.py <源文件夹> -r           # 命令行：递归扫描源文件夹的子文件夹
    python photo_organizer.py <源文件夹> --prefix     # 命令行：重名时自动加来源文件夹名前缀
    python photo_organizer.py <源文件夹> --dry-run    # 命令行：模拟运行，只预览不移动
    python photo_organizer.py <源文件夹> --copy       # 命令行：复制到日期文件夹，原文件保留
    python photo_organizer.py <源文件夹> --group year-month-day --date-folder  # 层级分组时再建日期文件夹
    python photo_organizer.py --dry-run              # 命令行：模拟整理默认源文件夹
    python photo_organizer.py --gui                  # 强制启动图形界面
    python photo_organizer.py -g                     # 同上

文件分区导航（二审 L1，便于单文件维护）：
    [1] 常量与基础工具（扩展名/正则/日期解析/传输/收集）     约 28-330 行
    [2] 整理核心 organize 与日志写入                        约 331-737 行
    [3] 日志还原与标签着色                                 约 738-880 行
    [4] i18n 字符串表（zh/en）                             约 881-1590 行
    [5] 配置读写与通用工具（_run_bg/_ToolTip/format_log_*）  约 1591-2050 行
    [6] GUI PhotoOrganizerApp                             约 2051-2790 行
    [7] CLI main() 与入口                                  约 2791-3024 行
"""
import sys

__version__ = "0.3.0"

import os
import re
import shutil
import json
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

# ========== 配置区 ==========
# 取程序（源码或 EXE）所在目录，避免写死个人路径
if getattr(sys, "frozen", False):  # PyInstaller 打包成 EXE 后
    BASE_DIR = Path(sys.executable).resolve().parent
else:  # 源码运行
    BASE_DIR = Path(__file__).resolve().parent
SOURCE_DIR = str(BASE_DIR)  # 源文件夹：从中读取照片（默认程序所在目录）
DEST_DIR = str(BASE_DIR)    # 目标文件夹：照片按日期归类到此处（默认程序所在目录）
DATE_FMT = "%Y-%m-%d"  # 日期文件夹格式（如 2026-07-12）
# 基础图片格式（默认勾选整理的 9 种）
BASE_PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp", ".heic", ".heif"}
# 扩展图片格式（可选勾选，Pillow 可直接读取）
EXTRA_PHOTO_EXTS = {".avif", ".jfif", ".jpe", ".ico", ".jxl", ".psd", ".tga",
                    ".pcx", ".ppm", ".pgm", ".pbm", ".jp2"}
PHOTO_EXTS = set(BASE_PHOTO_EXTS) | set(EXTRA_PHOTO_EXTS)
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm", ".m4v", ".3gp", ".ts", ".mts"}
INCLUDE_VIDEO = True  # 是否同时整理视频




# ========== 核心逻辑导入（MCP 化第一阶段：核心逻辑已拆分至 core.py，纯逻辑无 GUI 依赖） ==========
from core import (APP_NAME, DEFAULT_LOG_KEEP, _tag_pad, _clamp_log_keep,
                  get_config_dir, ensure_config_dir, get_config_file,
                  load_config, save_config, reset_config, clear_all_data,
                  get_exif_dt, get_exif_date, get_name_dt, get_name_date,
                  get_file_dt, get_file_dt_noexif, get_file_date,
                  _sanitize_name, is_trashed_file, is_hidden_file,
                  collect_files, _safe_transfer, _cleanup_parts,
                  organize, get_logs_dir, list_logs, write_organize_log,
                  restore_from_log, _stats_to_meta)
# ========== 日志标签着色（GUI 与 CLI 共用） ==========
_LOG_TAG = Literal["err", "warn", "skip", "sim", "move", "copy", "rename", "info", "hidden", "trash", "video"]


def _match_log_tag(msg: str) -> _LOG_TAG | None:
    """按消息前缀匹配动作标签类别，返回着色标签名（供 GUI ScrolledText 与 CLI ANSI 共用）。

    匹配顺序：错误/删除红 → 警告橙 → 跳过黄 → 模拟青 → 移动绿 → 复制蓝 → 重命名紫 → 信息蓝。
    """
    if msg.startswith(_t("tag_error")) or msg.startswith(_t("tag_delete")):
        return "err"
    if msg.startswith(_t("tag_warning")):
        return "warn"
    if msg.startswith(_t("tag_skip")):
        return "skip"
    if msg.startswith(_t("tag_sim")):
        return "sim"
    if msg.startswith(_t("tag_move")):
        return "move"
    if msg.startswith(_t("tag_copy")):
        return "copy"
    if msg.startswith(_t("tag_rename")):
        return "rename"
    if msg.startswith(_t("tag_info")):
        return "info"
    if msg.startswith(_t("tag_hidden")):
        return "hidden"
    if msg.startswith(_t("tag_trash")):
        return "trash"
    if msg.startswith(_t("tag_video")):
        return "video"
    return None


def _log_label_pairs() -> list:
    """返回 (标签文本, 着色tag) 列表，供 GUI 只给标签上色（正文保持默认色）。

    标签文本按当前语言动态生成，与 STRINGS 保持同步（含 [移动]/[错误] 等动作标签
    与 移动:/复制:/未处理:/失败:/新建日期文件夹:/EXIF 判定:/兜底判定: 等摘要标签）。
    """
    pairs = [
        (_t("tag_error"), "err"),
        (_t("tag_delete"), "err"),
        (_t("tag_warning"), "warn"),
        (_t("tag_skip"), "skip"),
        (_t("tag_sim"), "sim"),
        (_t("tag_move"), "move"),
        (_t("tag_copy"), "copy"),
        (_t("tag_rename"), "rename"),
        (_t("tag_info"), "info"),
        (_t("tag_tip"), "info"),
        (_t("tag_hidden"), "hidden"),
        (_t("tag_trash"), "trash"),
        (_t("tag_video"), "video"),
        (_t("sum_move"), "move"),
        (_t("sum_copied_sep").split("|")[-1].strip() + " ", "copy"),
        (_t("sum_skipped_sep").split("|")[-1].strip() + " ", "skip"),
        (_t("sum_failed_sep").split("|")[-1].strip() + " ", "err"),
        (_t("sum_new_dirs"), "dir"),
        (_t("sum_hidden_pre"), "hidden"),
        (_t("sum_trash_sep").split("|")[-1].strip() + " ", "trash"),
        (_t("sum_exif"), "exif"),
        (_t("sum_fallback_sep").split("|")[-1].strip() + " ", "fallback"),
        # 处理明细/按文件类型：标题行着色 + 明细行 EXIF/兜底 词着色（中英双语一致）
        (_t("detail_title").strip(), "info"),
        (_t("detail_by_ext").strip(), "info"),
        ("EXIF", "exif"),
        (_t("detail_fallback_sep").strip(" |"), "fallback"),
    ]
    return [(a, b) for a, b in pairs if a]


def format_log_summary(rec: dict) -> str:
    """从 meta 记录中生成处理结果摘要（多行文本，供 GUI / CLI --show-log 在明细末尾展示）。"""
    s = rec.get("stats")
    if not isinstance(s, dict):
        return ""
    out = [
        f"{_t('tag_info')} {_t('sum_move')}{s.get('moved', 0)}{_t('sum_copied_sep')}{s.get('copied', 0)}"
        f"{_t('sum_skipped_sep')}{s.get('skipped', 0)}{_t('sum_failed_sep')}{s.get('failed', 0)}{_t('unit_zhang')}",
        f"{_t('tag_info')} {_t('sum_new_dirs')}{s.get('new_dirs', 0)}{_t('unit_ge')}",
        f"{_t('tag_info')} {_t('sum_exif')}{s.get('exif', 0)}{_t('sum_fallback_sep')}{s.get('fallback', 0)}{_t('unit_zhang')}",
    ]
    if s.get("hidden") or s.get("trash"):
        out.append(f"{_t('tag_info')} {_t('sum_hidden_pre')}{s.get('hidden', 0)}"
                   f"{_t('sum_trash_sep')}{s.get('trash', 0)}{_t('unit_zhang')}")
    by_ext = s.get("by_ext")
    if isinstance(by_ext, dict) and by_ext:
        out.append(_t("detail_by_ext"))
        for ext, c in sorted(by_ext.items()):
            out.append(f"  {ext}: {c.get('total', 0)}{_t('detail_exif_sep')}{c.get('exif', 0)}"
                       f"{_t('detail_fallback_sep')}{c.get('fallback', 0)})")
    return "\n".join(out)


def _meta_get(rec: dict, fixed: str, tkey: str) -> str:
    """读取 meta 记录字段：优先新格式固定英文 key，兼容旧格式 _t() 本地化 key。"""
    v = rec.get(fixed)
    if v is None:
        v = rec.get(_t(tkey))
    return v or ""


def format_log_line(rec: dict) -> str:
    """把 .jsonl 中的一条记录解析为可读标签行（供 GUI 查看明细 / CLI --show-log 共用）。

    - meta 记录 → [信息] 时间 | 模式 | 源文件夹: ... | 目标文件夹: ...
    - plan_move/plan_copy → [模拟][移动/复制] src -> dst（还原时忽略）
    - move/copy → [移动/复制] src -> dst
    - 未知类型 → 原样 JSON
    """
    typ = rec.get("type")
    if typ == "meta":
        t = _meta_get(rec, "time", "meta_time")
        mode = _meta_get(rec, "mode", "meta_mode")
        src = _meta_get(rec, "src", "meta_src")
        dst = _meta_get(rec, "dest", "meta_dest")
        parts = []
        if t:
            parts.append(f"{_t('tag_info')} {t}")
        if mode:
            parts.append(mode)
        if src:
            parts.append(f"{_t('line_src')}{src}")
        if dst:
            parts.append(f"{_t('line_dest')}{dst}")
        return " | ".join(parts)
    if typ in ("plan_move", "plan_copy"):
        return f"{_t('tag_sim')}{_t('tag_copy') if typ == 'plan_copy' else _t('tag_move')} {rec.get('src', '')} -> {rec.get('dst', '')}"
    if typ == "move":
        return f"{_t('tag_move')} {rec.get('src', '')} -> {rec.get('dst', '')}"
    if typ == "copy":
        return f"{_t('tag_copy')} {rec.get('src', '')} -> {rec.get('dst', '')}"
    return json.dumps(rec, ensure_ascii=False)

# ========== 图形界面（--gui / -g 启动） ==========
import queue
import threading


def _group_key_to_label(key: str) -> str:
    """内部分组代号 → 界面文案（兼容旧配置直接存文案的情况）。"""
    labels = {"flat": _t("group_flat"), "Y": _t("group_year"), "YM": _t("group_ym"), "YMD": _t("group_ymd")}
    return labels.get(key, key)


def _group_label_to_key(label: str) -> str:
    """界面文案 → 内部分组代号（未知值回退单层）。"""
    keys = {_t("group_flat"): "flat", _t("group_year"): "Y", _t("group_ym"): "YM", _t("group_ymd"): "YMD"}
    return keys.get(label, "flat")
# ============ 国际化（中英双语，跟随系统语言） ============
STRINGS = {
    "zh":     {
        "win_title": "照片按拍摄日期归类",
        "lbl_src": "源文件夹:",
        "lbl_dest": "目标文件夹:",
        "btn_browse": "浏览...",
        "chk_video": "同时整理视频",
        "chk_copy_mode": "复制模式",
        "chk_prefix": "重名自动加前缀",
        "chk_recursive": "包含子文件夹",
        "chk_skip_existing": "跳过已有日期文件夹",
        "chk_hidden_trash": "包含隐藏文件和.trashed 文件",
        "btn_formats": "图片格式…",
        "lbl_group": "文件夹分组:",
        "group_flat": "单层格式",
        "group_year": "按年",
        "group_ym": "按年+月",
        "group_ymd": "按年+月+日",
        "chk_date_folder": "分组时创建日期文件夹",
        "btn_fmt_help": "格式说明",
        "btn_help": "帮助",
        "lbl_date_fmt": "日期格式:",
        "lbl_year": "年层:",
        "lbl_month": "月层:",
        "lbl_day": "日层:",
        "mode_dry": "模拟运行",
        "btn_start": "开始整理",
        "btn_reset": "恢复默认设置",
        "status_ready": "就绪",
        "status_pil_err": "Pillow 异常，EXIF 不可用",
        "chk_save_log": "保存操作清单",
        "chk_mono": "单色输出",
        "lbl_keep": "保留",
        "lbl_parts": "份",
        "lbl_log": "操作清单:",
        "btn_detail": "操作记录",
        "btn_export": "导出",
        "btn_import": "导入",
        "btn_rollback": "还原变更",
        "btn_delete_sel": "删除所选",
        "btn_clear": "清空全部",
        "tip_src_browse": "选择源文件夹（从中读取照片）",
        "tip_dest_browse": "选择目标文件夹（照片归类到此处）",
        "btn_clear_paths": "清除路径记录",
        "tip_clear_paths": "仅清除界面上记住的源/目标文件夹历史记录（配置里的记忆），不会删除任何真实文件夹或文件",
        "msg_clear_paths_confirm": "将清除界面记住的源/目标文件夹历史记录，仅删除配置中的记忆，不会删除任何真实文件夹或文件。\n\n确定清除？",
        "log_paths_cleared": "已清除源/目标文件夹历史记录",
        "btn_clear_src": "清除记录",
        "tip_clear_src": "仅清除配置中记住的源文件夹路径记录（不删除任何真实文件夹或文件）",
        "msg_clear_src_confirm": "将清除配置中记住的源文件夹路径记录。\n此操作只删除记忆，不会删除任何真实文件夹或文件。\n\n确定清除？",
        "log_src_cleared": "[路径] 已清除源文件夹路径记录",
        "btn_clear_dest": "清除记录",
        "tip_clear_dest": "仅清除配置中记住的目标文件夹路径记录（不删除任何真实文件夹或文件）",
        "msg_clear_dest_confirm": "将清除配置中记住的目标文件夹路径记录。\n此操作只删除记忆，不会删除任何真实文件夹或文件。\n\n确定清除？",
        "log_dest_cleared": "[路径] 已清除目标文件夹路径记录",
        "msg_paths_none": "没有已记住的路径记录",
        "tip_video": "同时整理视频文件（如 .mp4/.mov），按拍摄时间归入日期文件夹",
        "tip_copy_mode": "文件复制到日期文件夹，原文件保留（默认是移动）",
        "tip_prefix": "重名冲突时自动加来源文件夹名前缀：文件夹名_IMG_001.jpg；顶层文件用源文件夹名，仍冲突则追加序号",
        "tip_recursive": "递归扫描源文件夹内所有子文件夹；不勾选只处理源文件夹顶层文件",
        "tip_cleanup_parts": "整理开始时清理目标目录下上次中断遗留的半成品文件（.photo-org-tmp）",
        "tip_skip_existing": "仅勾选“包含子文件夹”且源=目标（原地整理）时生效：源目录下已有的日期文件夹视为已整理，其中文件直接跳过，不判断文件日期（EXIF/文件名）",
        "tip_hidden_trash": "勾选后才会整理隐藏文件与 .trashed 文件。隐藏判定跨平台：Windows 隐藏属性或文件名以点开头；.trashed 为 Android 回收站文件（文件名以 .trashed- 开头），优先于隐藏判定、不叠加。默认不勾选，避免误整理回收站文件。",
        "tip_formats": "勾选要整理哪些图片格式，可自定义添加其他格式",
        "tip_group": "目标文件夹的整理方式：单层日期文件夹，或按 年/月/日 嵌套",
        "tip_date_folder": "在 年/月/日 层级分组的最内层，再按日期格式创建文件夹",
        "tip_fmt_help": "日期格式（strftime）说明：%Y 年、%m 月、%d 日等记号与常用组合示例",
        "tip_help": "命令行用法说明（双击程序或带参数运行时的各选项含义）",
        "tip_date_fmt": "strftime 日期格式，如 %Y-%m-%d 生成 2026-10-20、%Y年%m月%d日 生成 2026年10月20日",
        "tip_year_fmt": "%Y = 四位年份，如 2026",
        "tip_month_fmt": "%m = 两位月份，如 10",
        "tip_day_fmt": "%d = 两位日期，如 20",
        "tip_dry_run": "只预览归类结果，不移动、不复制任何文件",
        "tip_start": "按当前设置整理：读取文件拍摄日期并归入日期文件夹",
        "tip_reset": "清除已保存的配置，恢复默认值",
        "tip_save_log": "每次整理后把操作清单（.jsonl）保存到配置文件目录的 logs/ 下，可据此追溯或还原变更",
        "tip_keep": "保留最近几份操作清单，超出部分自动删除",
        "tip_mono": "关闭彩色标签，日志以纯文本显示",
        "btn_clean_parts": "清理半成品",
        "tip_clean_parts_btn": "扫描并删除目标目录下上次中断遗留的半成品文件（.photo-org-tmp），逐条输出删除结果",
        "log_clean_parts_scan": "[信息] 扫描目标目录下的半成品文件...",
        "msg_clean_parts_none": "[信息] 没有残留的半成品文件需要清理",
        "msg_clean_parts_confirm": "将在目标目录下删除 {n} 个半成品文件（.photo-org-tmp）。确定删除？",
        "log_clean_parts_done": "[信息] 半成品清理完成，共删除 {n} 个文件",
        "tip_log_combo": "选择一份历史操作清单：日志窗口顶部会提示选中项与动作条数，可用于显示明细 / 导出 / 还原变更（按保存时间倒序）",
        "tip_show_detail": "在下方日志窗口显示选中操作清单的完整明细（会话信息 + 每条动作）",
        "tip_export": "导出选中的操作清单（.jsonl）",
        "tip_import": "把外部操作清单（.jsonl）导入 logs 目录，之后可下拉选择",
        "tip_rollback": "按选中的操作清单还原：移动模式移回原位置，复制模式删除副本",
        "tip_delete_sel": "删除下拉框中选中的那一份操作清单（需确认）",
        "tip_clear": "删除 logs 目录下全部操作清单（需确认）",
        "title_info": "提示",
        "title_error": "错误",
        "msg_need_select": "请先在操作清单下拉框选择一份清单",
        "msg_log_missing_show": "该操作清单文件不存在，无法显示",
        "msg_log_missing_export": "该操作清单文件不存在，无法导出",
        "win_export": "导出操作清单",
        "win_import": "导入操作清单",
        "filetype_log": "操作清单",
        "filetype_all": "所有文件",
        "msg_no_logs": "没有操作清单需要清空",
        "win_clear": "清空操作清单",
        "msg_reset_confirm": "确定要恢复默认设置吗？\n所有已保存的设置将被清除。",
        "msg_reset_none": "没有需要恢复的配置文件",
        "log_reset_done": "已恢复默认设置，配置已清除",
        "win_formats": "支持的图片格式",
        "prompt_formats": "勾选要整理的图片格式：",
        "lbl_other_fmt": "其他格式（如 .raw）",
        "btn_add": "添加",
        "msg_min_format": "至少保留一种图片格式",
        "btn_ok": "确定",
        "btn_cancel": "取消",
        "menu_copy": "复制",
        "verb_move": "移动",
        "menu_copy_all": "复制全部",
        "menu_select_all": "全选",
        "btn_got_it": "知道了",
        "msg_need_src": "请先选择源文件夹",
        "msg_need_dest": "请先选择目标文件夹",
        "status_working": "正在整理...",
        "status_done": "完成",
        "tag_error": "[错误]",
        "tag_warning": "[警告]",
        "tag_skip": "[跳过]",
        "tag_sim": "[模拟]",
        "tag_move": "[移动]",
        "tag_copy": "[复制]",
        "tag_rename": "[重命名]",
        "tag_info": "[信息]",
        "tag_tip": "[提示]",
        "tag_hidden": "[隐藏]",
        "tag_trash": "[Trash]",
        "tag_video": "[视频]",
        "tag_delete": "[删除]",
        "err_src_missing": "[错误] 源文件夹不存在: ",
        "err_src_not_dir": "[错误] 源路径不是文件夹: ",
        "err_dest_missing": "[错误] 目标文件夹不存在: ",
        "err_dest_not_dir": "[错误] 目标路径不是文件夹: ",
        "log_dest_created": "[信息] 目标文件夹不存在，已自动创建: ",
        "err_dest_create": "[错误] 目标文件夹创建失败: ",
        "err_bad_fmt": "[错误] 日期格式无效: ",
        "warn_pil_pre": "[警告] Pillow 未加载成功（原因: ",
        "unknown_err": "未知错误",
        "warn_pil_post": "），无法读取 EXIF 拍摄时间，将改用文件名/文件时间兜底判定。请安装或修复 Pillow（pip install pillow）后 EXIF 判定才可用。",
        "info_no_files": "[提示] 源文件夹下没有照片文件",
        "incl_subfolders": "（含子文件夹）",
        "info_skip_need_recursive": "“跳过已有日期文件夹”仅在勾选“包含子文件夹”时生效，本次忽略该选项",
        "info_skip_need_inplace": "“跳过已有日期文件夹”仅在源=目标（原地整理）时生效，本次忽略该选项",
        "info_detect_pre": "[提示] 检测到 ",
        "info_skip_dirs_post": " 个已有日期文件夹，内部文件将视为已整理直接跳过",
        "warn_scan_dirs": "[警告] 扫描已有日期文件夹失败: ",
        "skip_in_place_post": "（已有日期文件夹，视为已整理）",
        "skip_no_date_post": "（无法识别日期）",
        "skip_exists_post": "（重名已存在）",
        "mode_copy_done": "复制完成",
        "mode_org_done": "整理完成",
        "sum_move": "移动: ",
        "sum_copied_sep": " 张 | 复制: ",
        "sum_skipped_sep": " 张 | 未处理: ",
        "sum_failed_sep": " 张 | 失败: ",
        "unit_zhang": " 张",
        "sum_new_dirs": "新建日期文件夹: ",
        "unit_ge": " 个",
        "sum_hidden_pre": "扫描到隐藏文件 ",
        "sum_trash_sep": " 个 | .trashed 文件 ",
        "sum_exif": "EXIF 判定: ",
        "sum_fallback_sep": " 张 | 兜底判定: ",
        "detail_title": "\n处理明细:",
        "detail_imgs_pre": "  图片 ",
        "detail_videos_pre": "  视频 ",
        "detail_exif_sep": " 张 (EXIF ",
        "detail_fallback_sep": " | 兜底 ",
        "detail_by_ext": "按文件类型:",
        "detail_skip_pre": "跳过明细: 已在目标位置 ",
        "detail_skip_dup_sep": " 张 | 重名已存在 ",
        "detail_skip_nodate_sep": " 张 | 无法识别日期 ",
        "detail_skip_hidden_sep": " 张 | 隐藏跳过 ",
        "detail_skip_trash_sep": " 张 | .trashed 跳过 ",
        "err_read_sel": "[错误] 无法读取操作清单 ",
        "sel_log_pre": "已选择操作清单 ",
        "sel_log_post": " 条动作）",
        "err_read_fail": "读取失败: ",
        "detail_header": "—— 操作清单明细：",
        "detail_shown_pre": "已显示 ",
        "detail_shown_post": " 明细",
        "log_exported": "[日志] 已导出操作清单: ",
        "err_export": "导出失败: ",
        "log_imported": "[日志] 已导入操作清单: ",
        "err_import": "导入失败: ",
        "rollback_confirm_pre": "将按操作清单「",
        "rollback_confirm_post": "」还原文件变更：\n· 移动模式的文件：从目标日期文件夹移回原位置\n· 复制模式的副本：删除复制产生的副本\n仅当目标文件仍存在且原位置已空出时执行，不会覆盖现有文件。\n\n确定执行还原？",
        "log_rollback_start": "[日志] 开始还原: ",
        "rollback_done_pre": "还原完成: 移回 ",
        "rollback_done_rm_sep": " 个 | 删除副本 ",
        "rollback_done_skip_sep": " 个 | 跳过 ",
        "rollback_done_missing_sep": " 个 | 目标文件已不存在 ",
        "clear_confirm_pre": "将删除 logs 目录下的全部 ",
        "clear_confirm_post": " 个操作清单（.jsonl），删除后不可恢复。\n\n确定清空？",
        "clear_sel_confirm_pre": "将删除选中操作清单「",
        "clear_sel_confirm_post": "」（.jsonl），删除后不可恢复。\n\n确定删除？",
        "log_cleared_pre": "[日志] 已清空 ",
        "log_cleared_post": " 个操作清单文件",
        "log_clear_fail": "[日志] 清空失败：部分文件未能删除",
        "err_read_log": "读取操作清单失败: ",
        "err_no_actions": "缺少操作清单文件（.jsonl），无法还原；该日志可能未记录文件动作",
        "err_restore_scope": "日志缺少有效的会话目录信息，为安全起见拒绝还原",
        "warn_restore_skip_scope": "[警告] 路径超出会话目录范围，已跳过还原: ",
        "skip_dst_missing": "[跳过] 目标文件已不存在: ",
        "skip_src_exists": "[跳过] 原位置已有文件，避免覆盖: ",
        "restore_move_pre": "[还原] ",
        "err_restore": "[错误] 还原失败: ",
        "err_move_src_rm": "[错误] 跨盘移动失败：目标已写入但无法删除源文件: ",
        "err_worker_crash": "[错误] 整理过程异常: ",
        "restore_rm_copy": "[还原] 删除复制副本: ",
        "err_rm_copy": "[错误] 删除副本失败: ",
        "skip_copy_missing": "[跳过] 副本已不存在: ",
        "line_src": "源文件夹: ",
        "line_dest": "目标文件夹: ",
        "line_video": "整理视频: ",
        "line_hidden_trash": "包含隐藏/.trashed 文件: ",
        "line_mode_sep": " | 模式: ",
        "line_recursive": "包含子文件夹: ",
        "line_conflict_sep": " | 重名处理: ",
        "yes": "是",
        "no": "否",
        "conflict_prefix": "加前缀",
        "conflict_skip": "跳过",
        "line_group": "分组方式: ",
        "line_group_flat": "分组方式: 单层格式 ",
        "line_year_sep": " | 年层=",
        "line_month_sep": " 月层=",
        "line_day_sep": " 日层=",
        "line_in_group_pre": " | 分组内再建日期文件夹（",
        "meta_time": "时间",
        "meta_mode": "模式",
        "meta_src": "源文件夹",
        "meta_dest": "目标文件夹",
        "meta_conflict": "重名处理",
        "meta_group": "分组方式",
        "meta_date_fmt": "日期格式",
        "meta_keep": "保留份数",
        "mode_move": "移动模式",
        "log_saved": "[日志] 已保存操作清单: ",
        "err_save_log": "[日志] 保存操作清单失败: ",
        "win_fmt_help": "日期格式说明",
        "log_fmt_help_pre": "以下为日期格式说明：",
        "log_help_body_pre": "以下为命令行用法说明：",
        "fmt_help_body": "【常用记号】（Python strftime 格式）\n%Y = 四位年份   → 2026\n%y = 两位年份   → 26\n%m = 两位月份   → 10\n%d = 两位日期   → 20\n%H = 小时(24h)  → 14\n%M = 分钟       → 30\n%S = 秒         → 05\n\n【常用组合示例】\n%Y         → 2026        （按年）\n%Y-%m      → 2026-10     （按月）\n%Y-%m-%d   → 2026-10-20  （按日）\n%Y年%m月%d日 → 2026年10月20日（中文）\n%Y/%m/%d   → 2026/10/20  （斜杠分隔）\n%Y.%m.%d   → 2026.10.20  （点号分隔）\n\n【小提示】\n连接符可以用 -  /  .  空格，也可以写中文。\n想按年就 %Y，按月 %Y-%m，按日 %Y-%m-%d。\n",
        "help_body": "用法:\n  photo-organizer [源文件夹] [目标文件夹] [选项]\n\n不带参数运行 → 自动打开图形界面；带参数运行 → 命令行模式\n\n【位置参数】\n  source_dir    源文件夹（从中读取照片）\n  dest_dir      目标文件夹（不提供时使用配置区 DEST_DIR）\n\n【常用选项】\n  -g, --gui                     强制启动图形界面\n  --dry-run                     模拟运行，只预览归类结果，不移动文件\n  --copy                        复制模式，原文件保留（默认移动）\n  -r, --recursive               递归扫描源文件夹的所有子文件夹\n  --prefix                      重名冲突时自动加来源文件夹名前缀\n  --skip-existing-date-folders  仅原地整理时跳过已有日期文件夹（默认启用）\n  --no-skip-existing-date-folders  关闭“跳过已有日期文件夹”\n  --date-folder                 层级分组时最内层再建日期文件夹\n  --date-format 格式            单层日期文件夹格式，默认 %Y-%m-%d\n  --group 层级                  year / year-month / year-month-day\n  --year-format / --month-format / --day-format  各层文件夹名格式\n  --add-photo-ext .raw          额外增加图片扩展名（可多次）\n  --remove-photo-ext .gif       临时排除图片扩展名（可多次）\n  --no-exif                     跳过 EXIF 读取，直接按文件名/文件时间判定（大批量时更快）\n  --include-hidden-trash        整理隐藏文件与 .trashed 文件（默认不处理）\n  --save-log                    保存操作清单到 jsonl（默认不保存）\n  --cleanup-parts               手动清理目标目录下的 .photo-org-tmp 半成品（逐条输出删除）\n  --clear-logs / --clear-data   清空操作清单 / 清理数据，均需附带 --yes 确认\n  --version                     显示版本号\n",
        "prog_desc": "照片按拍摄日期归类脚本（命令行 + 图形界面）",
        "arg_version": "显示版本号",
        "arg_src": "源文件夹（从中读取照片）；不提供且无其它参数时自动打开图形界面",
        "arg_dest": "目标文件夹（照片按日期归类到此处）；不提供时使用配置区 DEST_DIR",
        "arg_dry_run": "模拟运行，只预览归类结果，不移动任何文件（命令行模式）",
        "arg_copy_mode": "复制模式：文件复制到日期文件夹，原文件保留（默认移动）",
        "arg_recursive": "递归扫描源文件夹的所有子文件夹",
        "arg_skip_existing": "仅原地整理（源=目标）时生效：跳过源目录下已有的日期文件夹，其中文件视为已整理（不判断日期）；默认启用，加 --no-skip-existing-date-folders 可关闭",
        "arg_prefix": "重名冲突时自动加来源文件夹名前缀（默认跳过）",
        "arg_date_folder": "层级分组时，在分组得到的最内层文件夹里再建日期文件夹（按 --date-format，默认 %%Y-%%m-%%d）",
        "arg_date_fmt": "单层日期文件夹格式（strftime），如 %%Y 按年、%%Y-%%m 按月、%%Y-%%m-%%d 按日；默认 %%Y-%%m-%%d",
        "arg_group": "层级嵌套分组：year=先按年建文件夹、year-month=年>月、year-month-day=年>月>日",
        "arg_year_fmt": "层级分组时年层文件夹名格式（strftime），默认 %%Y",
        "arg_month_fmt": "层级分组时月层文件夹名格式（strftime），默认 %%m（可用 %%Y年%%m月 等）",
        "arg_day_fmt": "层级分组时日层文件夹名格式（strftime），默认 %%d",
        "arg_add_ext": "额外增加要整理的图片扩展名（可多次使用），如 --add-photo-ext .raw",
        "arg_remove_ext": "临时排除图片扩展名（可多次使用，仅在本次运行生效），如 --remove-photo-ext .gif",
        "arg_no_exif": "跳过 EXIF 读取，仅依据文件名与系统时间整理（速度更快）",
        "arg_yes": "直接确认高危操作（--clear-logs / --clear-data 必须附带）",
        "msg_need_yes": "该操作不可逆，请附带 --yes 明确确认后执行",
        "arg_gui": "强制启动图形界面",
        "arg_no_video": "不整理视频（默认同时整理图片与视频）",
        "arg_include_hidden_trash": "整理隐藏文件与 .trashed 文件（Android 回收站文件，文件名以 .trashed- 开头；默认不处理）",
        "arg_lang": "界面/输出语言：zh 或 en（默认按配置或 zh）",
        "arg_save_log": "保存操作清单到 jsonl（默认不保存，需显式指定）",
        "arg_log_keep": "保留最近 N 份操作清单（默认 30）",
        "arg_reset_config": "恢复默认配置（删除配置文件）",
        "arg_list_logs": "列出最近保存的操作清单",
        "arg_show_log": "查看指定操作清单明细（动态解析为可读标签行）",
        "arg_rollback": "按指定操作清单还原变更（移动移回，复制删除副本）",
        "arg_clear_logs": "清空全部操作清单（逐条输出删除）",
        "arg_clear_data": "清理数据：删除配置文件与全部操作清单",
        "arg_cleanup_parts": "手动清理目标目录下的半成品文件（.photo-org-tmp，逐条输出删除）",
        "arg_mono": "单色输出（关闭彩色标签）",
        "lang_switch_btn": "English",
        "tip_lang_switch": "切换到英文界面，选择会保存并在下次启动时沿用",
        "msg_lang_busy": "整理任务运行中，请等待完成后再切换语言",
        "log_lang_switched": "已切换界面语言",
        "btn_clear_data": "清理数据",
        "tip_clear_data": "删除配置文件与全部操作清单（三平台通用）",
        "msg_clear_data_confirm": "将删除配置文件与 {n} 份操作清单，且不可恢复。确定清理吗？",
        "log_clear_data_done": "已删除配置文件与 {n} 份操作清单",
        "log_clear_data_config": "已删除配置文件",
        "log_clear_data_logs_pre": "已删除 ",
        "log_clear_data_logs_post": " 份操作清单",
        "msg_clear_data_none": "没有可清理的数据（配置文件与操作清单均不存在）"
    },
    "en":     {
        "win_title": "Photo Organizer by Date",
        "lbl_src": "Source folder:",
        "lbl_dest": "Target folder:",
        "btn_browse": "Browse...",
        "chk_video": "Also organize videos",
        "chk_copy_mode": "Copy mode",
        "chk_prefix": "Auto-prefix on name conflict",
        "chk_recursive": "Include subfolders",
        "chk_skip_existing": "Skip existing date folders",
        "chk_hidden_trash": "Include hidden and .trashed files",
        "btn_formats": "Image formats...",
        "lbl_group": "Folder grouping:",
        "group_flat": "Flat format",
        "group_year": "By year",
        "group_ym": "By year+month",
        "group_ymd": "By year+month+day",
        "chk_date_folder": "Create date folder when grouping",
        "btn_fmt_help": "Format help",
        "btn_help": "Help",
        "lbl_date_fmt": "Date format:",
        "lbl_year": "Year level:",
        "lbl_month": "Month level:",
        "lbl_day": "Day level:",
        "mode_dry": "Dry run",
        "btn_start": "Start organizing",
        "btn_reset": "Restore defaults",
        "status_ready": "Ready",
        "status_pil_err": "Pillow error, EXIF unavailable",
        "chk_save_log": "Save operation log",
        "chk_mono": "Monochrome output",
        "lbl_keep": "Keep",
        "lbl_parts": "logs",
        "lbl_log": "Operation log:",
        "btn_detail": "Details",
        "btn_export": "Export",
        "btn_import": "Import",
        "btn_rollback": "Rollback",
        "btn_delete_sel": "Delete selected",
        "btn_clear": "Clear all",
        "tip_src_browse": "Choose source folder (photos are read from here)",
        "tip_dest_browse": "Choose target folder (photos are sorted here)",
        "btn_clear_paths": "Clear path history",
        "tip_clear_paths": "Only clears the remembered source/target folder history (memory in config); never deletes any real folder or file",
        "msg_clear_paths_confirm": "This will clear the remembered source/target folder history. Only the memory in config is removed; no real folder or file is deleted.\n\nContinue?",
        "log_paths_cleared": "Source/target folder history cleared",
        "btn_clear_src": "Clear history",
        "tip_clear_src": "Only clears the remembered source folder history in config; never deletes any real folder or file",
        "msg_clear_src_confirm": "This will clear the remembered source folder path history.\nOnly the memory is removed; no real folder or file will be deleted.\n\nContinue?",
        "log_src_cleared": "[Paths] Cleared source folder history",
        "btn_clear_dest": "Clear history",
        "tip_clear_dest": "Only clears the remembered target folder history in config; never deletes any real folder or file",
        "msg_clear_dest_confirm": "This will clear the remembered target folder path history.\nOnly the memory is removed; no real folder or file will be deleted.\n\nContinue?",
        "log_dest_cleared": "[Paths] Cleared target folder history",
        "msg_paths_none": "No path history is remembered",
        "tip_video": "Also organize video files (e.g. .mp4/.mov) into date folders by capture time",
        "tip_copy_mode": "Files are copied to date folders, originals kept (default is move)",
        "tip_prefix": "On name conflict, auto-add source folder name prefix: Folder_IMG_001.jpg; top-level files use the source folder name, append a sequence number if still conflicting",
        "tip_recursive": "Recursively scan all subfolders in source; uncheck to process only top-level files",
        "tip_cleanup_parts": "On start, clean up leftover partial files (.photo-org-tmp) from an interrupted run in target",
        "tip_skip_existing": "Only active when 'Include subfolders' is checked and source=target (in-place): existing date folders are treated as organized and skipped without checking file dates",
        "tip_hidden_trash": "Check to also organize hidden files and .trashed files. Hidden detection is cross-platform: Windows hidden attribute or a leading dot; .trashed files are Android recycle-bin files (names starting with '.trashed-'), which take priority over hidden and are not double-tagged. Unchecked by default to avoid mis-sorting recycle-bin files.",
        "tip_formats": "Choose which image formats to organize; custom formats can be added",
        "tip_group": "How to organize target: flat date folder, or nested by year/month/day",
        "tip_date_folder": "At the innermost level of year/month/day grouping, also create folders by date format",
        "tip_fmt_help": "strftime date format reference: %Y year, %m month, %d day, and common combinations",
        "tip_help": "Command-line usage (options when running with arguments)",
        "tip_date_fmt": "strftime format, e.g. %Y-%m-%d -> 2026-10-20, %Y年%m月%d日 -> 2026年10月20日",
        "tip_year_fmt": "%Y = 4-digit year, e.g. 2026",
        "tip_month_fmt": "%m = 2-digit month, e.g. 10",
        "tip_day_fmt": "%d = 2-digit day, e.g. 20",
        "tip_dry_run": "Only preview the results; no files are moved or copied",
        "tip_start": "Organize with current settings: read capture date and sort into date folders",
        "tip_reset": "Clear saved config and restore default values",
        "tip_save_log": "After each run, save the operation log (.jsonl) to logs/ under config dir for traceability or rollback",
        "tip_keep": "Keep the most recent N operation logs; older ones are auto-deleted",
        "tip_mono": "Disable colored labels, plain text output",
        "btn_clean_parts": "Clean up partials",
        "tip_clean_parts_btn": "Scan and delete leftover partial files (.photo-org-tmp) in the target folder, one line per deletion",
        "log_clean_parts_scan": "[Info] Scanning target folder for partial files...",
        "msg_clean_parts_none": "[Info] No leftover partial files to clean",
        "msg_clean_parts_confirm": "This will delete {n} partial file(s) (.photo-org-tmp) in the target folder. Continue?",
        "log_clean_parts_done": "[Info] Partial cleanup done, {n} file(s) deleted",
        "tip_log_combo": "Pick a historical operation log; the log window shows the selected item and action count. Use it to view details / export / rollback (newest first)",
        "tip_show_detail": "Show the full details of the selected operation log in the log window below (session info + each action)",
        "tip_export": "Export the selected operation log (.jsonl)",
        "tip_import": "Import an external operation log (.jsonl) into logs/ for later selection",
        "tip_rollback": "Rollback by the selected log: move mode moves files back, copy mode deletes copies",
        "tip_delete_sel": "Delete the single operation log selected in the dropdown (requires confirmation)",
        "tip_clear": "Delete all operation logs in logs/ (requires confirmation)",
        "title_info": "Info",
        "title_error": "Error",
        "msg_need_select": "Please select a log from the operation log dropdown first",
        "msg_log_missing_show": "The operation log file does not exist and cannot be shown",
        "msg_log_missing_export": "The operation log file does not exist and cannot be exported",
        "win_export": "Export Operation Log",
        "win_import": "Import Operation Log",
        "filetype_log": "Operation Log",
        "filetype_all": "All Files",
        "msg_no_logs": "There are no operation logs to clear",
        "win_clear": "Clear Operation Logs",
        "msg_reset_confirm": "Restore default settings?\nAll saved settings will be cleared.",
        "msg_reset_none": "No config file to reset",
        "log_reset_done": "Default settings restored; configuration cleared",
        "win_formats": "Supported Image Formats",
        "prompt_formats": "Check the image formats to organize:",
        "lbl_other_fmt": "Other formats (e.g. .raw)",
        "btn_add": "Add",
        "msg_min_format": "Keep at least one image format",
        "btn_ok": "OK",
        "btn_cancel": "Cancel",
        "menu_copy": "Copy",
        "verb_move": "Move",
        "menu_copy_all": "Copy All",
        "menu_select_all": "Select All",
        "btn_got_it": "Got it",
        "msg_need_src": "Please choose the source folder first",
        "msg_need_dest": "Please choose the target folder first",
        "status_working": "Organizing...",
        "status_done": "Done",
        "tag_error": "[Error]",
        "tag_warning": "[Warning]",
        "tag_skip": "[Skip]",
        "tag_sim": "[Sim]",
        "tag_move": "[Move]",
        "tag_copy": "[Copy]",
        "tag_rename": "[Rename]",
        "tag_info": "[Info]",
        "tag_tip": "[Info]",
        "tag_hidden": "[Hidden]",
        "tag_trash": "[Trash]",
        "tag_video": "[Video]",
        "tag_delete": "[Delete]",
        "err_src_missing": "[Error] Source folder does not exist: ",
        "err_src_not_dir": "[Error] Source path is not a folder: ",
        "err_dest_missing": "[Error] Target folder does not exist: ",
        "err_dest_not_dir": "[Error] Target path is not a folder: ",
        "log_dest_created": "[Info] Target folder did not exist; created automatically: ",
        "err_dest_create": "[Error] Failed to create target folder: ",
        "err_bad_fmt": "[Error] Invalid date format: ",
        "warn_pil_pre": "[Warning] Pillow failed to load (reason: ",
        "unknown_err": "unknown error",
        "warn_pil_post": "), EXIF capture time cannot be read; falling back to filename/file time. Install or fix Pillow (pip install pillow) to enable EXIF detection.",
        "info_no_files": "[Info] No photo files in the source folder",
        "incl_subfolders": " (including subfolders)",
        "info_skip_need_recursive": "'Skip existing date folders' only works when 'Include subfolders' is checked; ignored this time",
        "info_skip_need_inplace": "'Skip existing date folders' only works when source=target (in-place); ignored this time",
        "info_detect_pre": "[Info] Detected ",
        "info_skip_dirs_post": " existing date folder(s); files inside will be treated as organized and skipped",
        "warn_scan_dirs": "[Warning] Failed to scan existing date folders: ",
        "skip_in_place_post": " (existing date folder, treated as organized)",
        "skip_no_date_post": " (cannot identify date)",
        "skip_exists_post": " (duplicate exists)",
        "mode_copy_done": "Copy complete",
        "mode_org_done": "Organizing complete",
        "sum_move": "Moved: ",
        "sum_copied_sep": " | Copied: ",
        "sum_skipped_sep": " | Skipped: ",
        "sum_failed_sep": " | Failed: ",
        "unit_zhang": "",
        "sum_new_dirs": "New date folders: ",
        "unit_ge": "",
        "sum_hidden_pre": "Hidden files found: ",
        "sum_trash_sep": " | .trashed files: ",
        "sum_exif": "EXIF: ",
        "sum_fallback_sep": " | Fallback: ",
        "detail_title": "\nDetails:",
        "detail_imgs_pre": "  Images ",
        "detail_videos_pre": "  Videos ",
        "detail_exif_sep": " (EXIF ",
        "detail_fallback_sep": " | fallback ",
        "detail_by_ext": "By file type:",
        "detail_skip_pre": "Skip details: already in target ",
        "detail_skip_dup_sep": " | duplicate exists ",
        "detail_skip_nodate_sep": " | cannot identify date ",
        "detail_skip_hidden_sep": " | hidden skipped ",
        "detail_skip_trash_sep": " | .trashed skipped ",
        "err_read_sel": "[Error] Cannot read operation log ",
        "sel_log_pre": "Selected operation log ",
        "sel_log_post": " actions)",
        "err_read_fail": "Read failed: ",
        "detail_header": "—— Operation log details: ",
        "detail_shown_pre": "Shown ",
        "detail_shown_post": " details",
        "log_exported": "[Log] Exported operation log: ",
        "err_export": "Export failed: ",
        "log_imported": "[Log] Imported operation log: ",
        "err_import": "Import failed: ",
        "rollback_confirm_pre": "Files will be rolled back per operation log \"",
        "rollback_confirm_post": "\":\n- Move mode: files moved back from date folders to original paths\n- Copy mode: copies created by copy mode are deleted\nOnly executed when the target file still exists and the original location is free; existing files will not be overwritten.\n\nConfirm rollback?",
        "log_rollback_start": "[Log] Start rollback: ",
        "rollback_done_pre": "Rollback complete: moved back ",
        "rollback_done_rm_sep": " | deleted copies ",
        "rollback_done_skip_sep": " | skipped ",
        "rollback_done_missing_sep": " | target files missing ",
        "clear_confirm_pre": "This will delete all ",
        "clear_confirm_post": " operation log(s) (.jsonl) in logs/. This cannot be undone.\n\nConfirm clearing?",
        "clear_sel_confirm_pre": "This will delete the selected operation log \"",
        "clear_sel_confirm_post": "\" (.jsonl). This cannot be undone.\n\nConfirm deletion?",
        "log_cleared_pre": "[Log] Cleared ",
        "log_cleared_post": " operation log file(s)",
        "log_clear_fail": "[Log] Clear failed: some files could not be deleted",
        "err_read_log": "Failed to read operation log: ",
        "err_no_actions": "No operation log file (.jsonl); cannot rollback. The log may not have recorded file actions",
        "err_restore_scope": "Log lacks valid session folder info; rollback refused for safety",
        "warn_restore_skip_scope": "[Warning] Path outside session folders; rollback skipped: ",
        "skip_dst_missing": "[Skip] Target file no longer exists: ",
        "skip_src_exists": "[Skip] Original location already has a file; avoiding overwrite: ",
        "restore_move_pre": "[Restore] ",
        "err_restore": "[Error] Rollback failed: ",
        "err_move_src_rm": "[Error] Cross-drive move failed: target written but source could not be removed: ",
        "err_worker_crash": "[Error] Organizing process crashed: ",
        "restore_rm_copy": "[Restore] Deleting copy: ",
        "err_rm_copy": "[Error] Failed to delete copy: ",
        "skip_copy_missing": "[Skip] Copy no longer exists: ",
        "line_src": "Source folder: ",
        "line_dest": "Target folder: ",
        "line_video": "Videos: ",
        "line_hidden_trash": "Include hidden/.trashed files: ",
        "line_mode_sep": " | Mode: ",
        "line_recursive": "Include subfolders: ",
        "line_conflict_sep": " | Name conflict: ",
        "yes": "Yes",
        "no": "No",
        "conflict_prefix": "Add prefix",
        "conflict_skip": "Skip",
        "line_group": "Grouping: ",
        "line_group_flat": "Grouping: flat format ",
        "line_year_sep": " | Year=",
        "line_month_sep": " Month=",
        "line_day_sep": " Day=",
        "line_in_group_pre": " | with date folders inside group (",
        "meta_time": "Time",
        "meta_mode": "Mode",
        "meta_src": "Source folder",
        "meta_dest": "Target folder",
        "meta_conflict": "Name conflict",
        "meta_group": "Grouping",
        "meta_date_fmt": "Date format",
        "meta_keep": "Keep count",
        "mode_move": "Move mode",
        "log_saved": "[Log] Saved operation log: ",
        "err_save_log": "[Log] Failed to save operation log: ",
        "win_fmt_help": "Date Format Reference",
        "log_fmt_help_pre": "Date format reference:",
        "log_help_body_pre": "Command-line usage:",
        "fmt_help_body": "[Common markers] (Python strftime)\n%Y = 4-digit year  -> 2026\n%y = 2-digit year  -> 26\n%m = 2-digit month -> 10\n%d = 2-digit day   -> 20\n%H = hour (24h)    -> 14\n%M = minute        -> 30\n%S = second        -> 05\n\n[Common combinations]\n%Y        -> 2026        (by year)\n%Y-%m     -> 2026-10     (by month)\n%Y-%m-%d  -> 2026-10-20  (by day)\n%Y年%m月%d日 -> 2026年10月20日 (Chinese)\n%Y/%m/%d  -> 2026/10/20  (slash separated)\n%Y.%m.%d  -> 2026.10.20  (dot separated)\n\n[Tips]\nSeparators can be - / . space, or Chinese characters.\nBy year use %Y, by month %Y-%m, by day %Y-%m-%d.\n",
        "help_body": "Usage:\n  photo-organizer [SOURCE] [DEST] [OPTIONS]\n\nRunning without arguments opens the GUI; with arguments runs CLI mode.\n\n[Positional arguments]\n  source_dir    Source folder (photos are read from here)\n  dest_dir      Target folder (uses DEST_DIR from config if omitted)\n\n[Common options]\n  -g, --gui                     Force launch GUI\n  --dry-run                     Dry run: only preview, do not move files\n  --copy                        Copy mode: originals kept (default is move)\n  -r, --recursive               Recursively scan all subfolders\n  --prefix                      Auto-add source folder name prefix on conflict\n  --skip-existing-date-folders  In-place only: skip existing date folders (enabled by default)\n  --no-skip-existing-date-folders  disable skip-existing (enabled by default)\n  --date-folder                 Also create date folders at innermost level\n  --date-format FMT             Flat date folder format, default %Y-%m-%d\n  --group LEVEL                 year / year-month / year-month-day\n  --year-format / --month-format / --day-format  per-level folder name format\n  --add-photo-ext .raw          Add an image extension (repeatable)\n  --remove-photo-ext .gif       Temporarily exclude an extension (repeatable)\n  --no-exif                     Skip EXIF, use filename/file time only (faster for large batches)\n  --include-hidden-trash        Organize hidden and .trashed files (default: skip)\n  --save-log                    Save operation log to jsonl (default: do not save)\n  --cleanup-parts               Manually clean .photo-org-tmp partials in target (one line per deletion)\n  --clear-logs / --clear-data   Clear logs / wipe data; both require --yes\n  --version                     Show version\n",
        "prog_desc": "Photo organizer by capture date (CLI + GUI)",
        "arg_version": "show version",
        "arg_src": "source folder (photos are read from here); if omitted and no other arguments, opens GUI",
        "arg_dest": "target folder (photos are sorted here); uses DEST_DIR from config if omitted",
        "arg_dry_run": "dry run: only preview, move no files (CLI mode)",
        "arg_copy_mode": "copy mode: files copied to date folders, originals kept (default is move)",
        "arg_recursive": "recursively scan all subfolders of source",
        "arg_skip_existing": "in-place only (source=target): skip existing date folders, treat their files as organized (no date check); enabled by default, add --no-skip-existing-date-folders to disable",
        "arg_prefix": "auto-add source folder name prefix on name conflict (default: skip)",
        "arg_date_folder": "with hierarchical grouping, also create date folders at the innermost level (per --date-format, default %%Y-%%m-%%d)",
        "arg_date_fmt": "flat date folder format (strftime), e.g. %%Y by year, %%Y-%%m by month, %%Y-%%m-%%d by day; default %%Y-%%m-%%d",
        "arg_group": "hierarchical grouping: year=by year, year-month=year>month, year-month-day=year>month>day",
        "arg_year_fmt": "year-level folder name format (strftime), default %%Y",
        "arg_month_fmt": "month-level folder name format (strftime), default %%m (e.g. %%Y年%%m月)",
        "arg_day_fmt": "day-level folder name format (strftime), default %%d",
        "arg_add_ext": "add an image extension to organize (repeatable), e.g. --add-photo-ext .raw",
        "arg_remove_ext": "temporarily exclude an image extension (repeatable, current run only), e.g. --remove-photo-ext .gif",
        "arg_no_exif": "skip EXIF reading; organize by filename and system time only (faster)",
        "arg_yes": "confirm high-risk operations explicitly (required for --clear-logs / --clear-data)",
        "msg_need_yes": "This operation is irreversible; append --yes to confirm",
        "arg_gui": "force launching GUI",
        "arg_no_video": "do not organize videos (default: organize images and videos)",
        "arg_include_hidden_trash": "organize hidden and .trashed files (Android recycle-bin files named '.trashed-*'; skipped by default)",
        "arg_lang": "interface/output language: zh or en (default: config or zh)",
        "arg_save_log": "save operation log to jsonl (default: do not save; specify explicitly)",
        "arg_log_keep": "keep the most recent N operation logs (default 30)",
        "arg_reset_config": "restore default config (delete config file)",
        "arg_list_logs": "list the most recent operation logs",
        "arg_show_log": "show details of the given operation log (parsed as readable tagged lines)",
        "arg_rollback": "roll back changes by the given operation log (move back / delete copies)",
        "arg_clear_logs": "clear all operation logs (print one line per deletion)",
        "arg_clear_data": "clear data: delete config file and all operation logs",
        "arg_cleanup_parts": "manually clean partial files (.photo-org-tmp) in target folder (one line per deletion)",
        "arg_mono": "monochrome output (disable colored labels)",
        "lang_switch_btn": "中文",
        "tip_lang_switch": "Switch to Chinese interface; your choice is saved and reused on next launch",
        "msg_lang_busy": "An organize task is running. Please wait until it finishes before switching language.",
        "log_lang_switched": "Interface language switched",
        "btn_clear_data": "Clear Data",
        "tip_clear_data": "Delete config file and all operation logs (works on Windows/macOS/Linux)",
        "msg_clear_data_confirm": "This will permanently delete the config file and {n} operation logs. Continue?",
        "log_clear_data_done": "Config file and {n} operation logs deleted",
        "log_clear_data_config": "Config file deleted",
        "log_clear_data_logs_pre": "Deleted ",
        "log_clear_data_logs_post": " operation logs",
        "msg_clear_data_none": "Nothing to clear (config file and operation logs do not exist)"
    },
}

def _detect_language():
    """Detect Windows display language: zh for Chinese, en otherwise."""
    try:
        import ctypes
        lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        if lang_id in (0x0804, 0x0404):
            return "zh"
    except Exception:
        pass
    return "en"

def _initial_language():
    """首次启动语言：优先读取配置记忆；未设置过则跟随系统语言。"""
    lang = load_config().get("language")
    if lang in ("zh", "en"):
        return lang
    return _detect_language()

CURRENT_LANG = _initial_language()

def _set_language(lang):
    """运行时切换界面语言（仅接受 zh / en）。"""
    global CURRENT_LANG
    CURRENT_LANG = "zh" if lang == "zh" else "en"

def _t(key):
    lang_table = STRINGS.get(CURRENT_LANG) or STRINGS["zh"]
    if key in lang_table:
        return lang_table[key]
    if key in STRINGS["zh"]:
        return STRINGS["zh"][key]
    return key


# ========== core 结构化消息翻译层（MCP 化：core 返回英文 code + 结构化参数，此处译为界面语言） ==========
def _render_core_msg(item) -> str:
    """把 core 的结构化消息 (code, params) 译为当前界面语言文本。

    - 纯字符串（旧式调用/外部传入）原样透传；
    - log_line 按 tag 前缀着色约定拼装标签（[跳过]/[模拟]/[移动]/[复制]/[重命名]等）；
    - mode_header 按 dry/copy/move 映射模式文案；
    - 其余按 STRINGS 键模板 + 结构化参数拼接，未知 code 返回原始结构。
    """
    if isinstance(item, str):
        return item
    code, params = item
    if code == "log_line":
        tags = "".join(_t("tag_" + t) for t in params.get("tags", []) if t)
        tail_i18n = params.get("tail_i18n")
        return (_tag_pad(tags) + params.get("name", "")
                + (_t(tail_i18n) if tail_i18n else "")
                + (params.get("tail") or ""))
    if code == "mode_header":
        mode = {"dry": _t("mode_dry"), "copy": _t("mode_copy_done"), "move": _t("mode_org_done")}.get(
            params.get("mode", ""), params.get("mode", ""))
        return f"\n===== {mode} ====="
    if code == "summary":
        return (f"{_t('sum_move')}{params['moved']}{_t('sum_copied_sep')}{params['copied']}"
                f"{_t('sum_skipped_sep')}{params['skipped']}{_t('sum_failed_sep')}{params['failed']}{_t('unit_zhang')}")
    if code == "summary_dirs":
        return f"{_t('sum_new_dirs')}{params['n']}{_t('unit_ge')}"
    if code == "summary_hidden_trash":
        return f"{_t('sum_hidden_pre')}{params['hidden']}{_t('sum_trash_sep')}{params['trash']}{_t('unit_ge')}"
    if code == "summary_exif":
        return f"{_t('sum_exif')}{params['exif']}{_t('sum_fallback_sep')}{params['fallback']}{_t('unit_zhang')}"
    if code == "detail_imgs":
        return f"{_t('detail_imgs_pre')}{params['total']}{_t('detail_exif_sep')}{params['exif']}{_t('detail_fallback_sep')}{params['fallback']})"
    if code == "detail_videos":
        return f"{_t('detail_videos_pre')}{params['total']}{_t('detail_exif_sep')}{params['exif']}{_t('detail_fallback_sep')}{params['fallback']})"
    if code == "detail_ext_line":
        return f"  {params['ext']}: {params['total']}{_t('detail_exif_sep')}{params['exif']}{_t('detail_fallback_sep')}{params['fallback']})"
    if code == "detail_skip":
        return (f"{_t('detail_skip_pre')}{params['in_place']}{_t('detail_skip_dup_sep')}{params['dup']}"
                f"{_t('detail_skip_nodate_sep')}{params['nodate']}{_t('detail_skip_hidden_sep')}{params['hidden']}"
                f"{_t('detail_skip_trash_sep')}{params['trash']}{_t('unit_zhang')}")
    if code == "no_files":
        return _t("info_no_files") + (_t("incl_subfolders") if params.get("recursive") else "")
    if code == "warn_pil":
        return _t("warn_pil_pre") + (params.get("err") or _t("unknown_err")) + _t("warn_pil_post")
    if code == "dest_created":
        return f"{_t('log_dest_created')}{params['path']}"
    if code == "src_missing":
        return f"{_t('err_src_missing')}{params['path']}"
    if code == "src_not_dir":
        return f"{_t('err_src_not_dir')}{params['path']}"
    if code == "dest_create_err":
        return f"{_t('err_dest_create')}{params['path']} ({params['err']})"
    if code == "dest_missing":
        return f"{_t('err_dest_missing')}{params['path']}"
    if code == "dest_not_dir":
        return f"{_t('err_dest_not_dir')}{params['path']}"
    if code == "bad_fmt":
        return f"{_t('err_bad_fmt')}{params['err']}"
    if code == "bad_group":
        return f"{_t('err_bad_group')}{params['level']}"
    if code == "fmt_time":
        return f"{_t('err_fmt_time')}{params['label']} ({params['fmt']})"
    if code == "warn_group_need":
        return _t("warn_group_need")
    if code == "skip_need_recursive":
        return _t("tag_tip") + _t("info_skip_need_recursive")
    if code == "skip_need_inplace":
        return _t("tag_tip") + _t("info_skip_need_inplace")
    if code == "detect_dirs":
        return f"{_t('info_detect_pre')}{params['n']}{_t('info_skip_dirs_post')}"
    if code == "warn_scan_dirs":
        return f"{_t('warn_scan_dirs')}{params['err']}"
    if code == "detail_title":
        return _t("detail_title")
    if code == "detail_by_ext":
        return _t("detail_by_ext")
    if code == "warn_restore_skip_scope":
        return f"{_t('warn_restore_skip_scope')}{params['src']} -> {params['dst']}"
    if code == "skip_dst_missing":
        return f"{_t('skip_dst_missing')}{params['dst']}"
    if code == "skip_src_exists":
        return f"{_t('skip_src_exists')}{params['src']}"
    if code == "restore_move_pre":
        return f"{_t('restore_move_pre')}{params['dst']} -> {params['src']}"
    if code == "err_restore":
        return f"{_t('err_restore')}{params['dst']} -> {params['src']} ({params['err']})"
    if code == "restore_rm_copy":
        return f"{_t('restore_rm_copy')}{params['dst']}"
    if code == "err_rm_copy":
        return f"{_t('err_rm_copy')}{params['dst']} ({params['err']})"
    if code == "skip_copy_missing":
        return f"{_t('skip_copy_missing')}{params['dst']}"
    if code == "err_no_actions":
        return _t("err_no_actions")
    if code == "err_restore_scope":
        return _t("err_restore_scope")
    if code == "err_read_log":
        return f"{_t('err_read_log')}{params['err']}"
    return f"[{code}] {params}"



def _rollback_done_msg(stats: dict) -> str:
    """还原完成汇总行（i18n 拼接，避免硬编码中文）。"""
    return (f"{_t('rollback_done_pre')}{stats.get('moved', 0)}{_t('rollback_done_rm_sep')}{stats.get('removed', 0)}"
            f"{_t('rollback_done_skip_sep')}{stats.get('skipped', 0)}{_t('rollback_done_missing_sep')}{stats.get('missing', 0)}")




# ========== 布局与数量常量（GUI 专属，二审 STYLE1：消除魔法数字） ==========
FMT_DIALOG_GEOM = "440x400"
FMT_DIALOG_MINSIZE = (400, 340)
FMT_HELP_GEOM = "460x420"
FMT_HELP_MINSIZE = (420, 360)
HELP_GEOM = "560x500"
HELP_MINSIZE = (480, 380)
MAX_HISTORY = 10
FMT_GRID_COLS = 3  # 格式弹窗扩展名网格列数

# ============ 国际化结束 ============


class _ToolTip:
    """简单悬停提示：鼠标移入稍候显示，移出自动隐藏（延迟+防抖，不反复弹出）"""

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip = None
        self._after_id = None
        widget.bind("<Enter>", self._on_enter)
        widget.bind("<Leave>", self._on_leave)

    def _on_enter(self, event=None):
        self._cancel_schedule()
        self._after_id = self.widget.after(350, self._show)

    def _on_leave(self, event=None):
        self._cancel_schedule()
        self.hide()

    def _cancel_schedule(self):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _is_over_widget(self, cur):
        while cur is not None:
            if str(cur) == str(self.widget):
                return True
            cur = cur.master
        return False

    def _show(self):
        self._after_id = None
        if self.tip or not self.text:
            return
        # 延迟期间鼠标已移出控件则不弹出
        try:
            cur = self.widget.winfo_containing(*self.widget.winfo_pointerxy())
        except Exception:
            cur = None
        if not self._is_over_widget(cur):
            return
        x, y = self.widget.winfo_pointerxy()
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x + 12}+{y + 14}")
        label = tk.Label(self.tip, text=self.text, justify=tk.LEFT,
                         background="#ffffe0", relief=tk.SOLID, borderwidth=1,
                         padx=6, pady=4, wraplength=340)
        label.pack()
        # 鼠标移入提示窗口保持显示，移出才销毁
        self.tip.bind("<Enter>", self._on_tip_enter)
        self.tip.bind("<Leave>", self.hide)

    def _on_tip_enter(self, event=None):
        self._cancel_schedule()

    def hide(self, event=None):
        self._cancel_schedule()
        if self.tip:
            self.tip.destroy()
            self.tip = None


class PhotoOrganizerApp:
    # 窗口图标（内嵌 PNG base64，避免打包后路径失效）
    _ICON_B64 = (
        "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"
    )
    def __init__(self, root: "tk.Tk"):
        self.root = root
        self.log_q = queue.Queue()
        self.running = False
        self._photo_exts = set(BASE_PHOTO_EXTS)  # 图片格式集合（默认基础 9 种，配置加载后覆盖）
        self._src_history: list = []   # 源文件夹路径历史（仅配置记忆，不触碰真实文件夹）
        self._dest_history: list = []  # 目标文件夹路径历史（仅配置记忆，不触碰真实文件夹）
        self._build_ui()

    def _build_ui(self):
        """构建全部界面控件（语言切换后整体重建并恢复状态）。"""
        root = self.root
        self.root.title(f"{_t('win_title')} v{__version__}")
        self._set_window_icon()
    def _set_window_icon(self):
        """设置窗口图标（内嵌 base64，兼容打包后运行）"""
        try:
            import base64 as _b64
            from tkinter import PhotoImage
            _img = PhotoImage(data=_b64.b64decode(self._ICON_B64))
            self.root.iconphoto(True, _img)
        except Exception:
            pass

        self.root.geometry("680x600")
        self.root.minsize(560, 460)

        # 语言切换栏：窗口顶部右侧，按钮文字显示目标语言（中文界面显 English，英文界面显 中文）
        lang_bar = ttk.Frame(root, padding=(10, 8, 10, 0))
        lang_bar.pack(fill=tk.X)
        help_btn = ttk.Button(lang_bar, text=_t("btn_help"), command=self.show_help)
        help_btn.pack(side=tk.RIGHT, padx=(0, 6))
        _ToolTip(help_btn, _t("tip_help"))
        self.lang_btn = ttk.Button(lang_bar, text=_t("lang_switch_btn"), command=self._switch_language)
        self.lang_btn.pack(side=tk.RIGHT)
        _ToolTip(self.lang_btn, _t("tip_lang_switch"))

        # 源文件夹行（可编辑下拉框，带路径历史记忆）
        top = ttk.Frame(root, padding=(10, 10, 10, 4))
        top.pack(fill=tk.X)
        ttk.Label(top, text=_t("lbl_src")).pack(side=tk.LEFT)
        self.src_var = tk.StringVar(value=SOURCE_DIR)
        self.src_entry = ttk.Combobox(top, textvariable=self.src_var, values=self._src_history)
        self.src_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        src_btn = ttk.Button(top, text=_t("btn_browse"), command=self.browse_source)
        src_btn.pack(side=tk.LEFT)
        _ToolTip(src_btn, _t("tip_src_browse"))
        clear_btn = ttk.Button(top, text=_t("btn_clear_src"), command=lambda: self.on_clear_paths("src"))
        clear_btn.pack(side=tk.LEFT, padx=(4, 0))
        _ToolTip(clear_btn, _t("tip_clear_src"))

        # 目标文件夹行（可编辑下拉框，带路径历史记忆）
        dest = ttk.Frame(root, padding=(10, 4, 10, 4))
        dest.pack(fill=tk.X)
        ttk.Label(dest, text=_t("lbl_dest")).pack(side=tk.LEFT)
        self.dest_var = tk.StringVar(value=DEST_DIR)
        self.dest_entry = ttk.Combobox(dest, textvariable=self.dest_var, values=self._dest_history)
        self.dest_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        dest_btn = ttk.Button(dest, text=_t("btn_browse"), command=self.browse_dest)
        dest_btn.pack(side=tk.LEFT)
        _ToolTip(dest_btn, _t("tip_dest_browse"))
        clear_dest_btn = ttk.Button(dest, text=_t("btn_clear_dest"), command=lambda: self.on_clear_paths("dest"))
        clear_dest_btn.pack(side=tk.LEFT, padx=(4, 0))
        _ToolTip(clear_dest_btn, _t("tip_clear_dest"))

        # 选项行 1
        opts = ttk.Frame(root, padding=(10, 4, 10, 4))
        opts.pack(fill=tk.X)
        self.video_var = tk.BooleanVar(value=INCLUDE_VIDEO)
        self.copy_var = tk.BooleanVar(value=False)
        self.prefix_var = tk.BooleanVar(value=False)
        self.video_check = ttk.Checkbutton(opts, text=_t("chk_video"), variable=self.video_var)
        self.video_check.pack(side=tk.LEFT, padx=(0, 16))
        _ToolTip(self.video_check, _t("tip_video"))
        self.copy_check = ttk.Checkbutton(opts, text=_t("chk_copy_mode"), variable=self.copy_var)
        self.copy_check.pack(side=tk.LEFT, padx=(0, 16))
        _ToolTip(self.copy_check, _t("tip_copy_mode"))
        self.prefix_check = ttk.Checkbutton(opts, text=_t("chk_prefix"), variable=self.prefix_var)
        self.prefix_check.pack(side=tk.LEFT)
        _ToolTip(self.prefix_check, _t("tip_prefix"))

        # 选项行 2
        opts2 = ttk.Frame(root, padding=(10, 0, 10, 4))
        opts2.pack(fill=tk.X)
        self.recursive_var = tk.BooleanVar(value=False)
        self.skip_existing_var = tk.BooleanVar(value=True)
        self.recursive_check = ttk.Checkbutton(opts2, text=_t("chk_recursive"), variable=self.recursive_var,
                                               command=self._refresh_skip_existing_state)
        self.recursive_check.pack(side=tk.LEFT, padx=(0, 16))
        _ToolTip(self.recursive_check, _t("tip_recursive"))
        self.skip_existing_check = ttk.Checkbutton(opts2, text=_t("chk_skip_existing"),
                                                   variable=self.skip_existing_var)
        self.skip_existing_check.pack(side=tk.LEFT, padx=(16, 0))
        _ToolTip(self.skip_existing_check, _t("tip_skip_existing"))
        self.hidden_trash_var = tk.BooleanVar(value=False)
        self.hidden_trash_check = ttk.Checkbutton(opts2, text=_t("chk_hidden_trash"),
                                                  variable=self.hidden_trash_var)
        self.hidden_trash_check.pack(side=tk.LEFT, padx=(16, 0))
        _ToolTip(self.hidden_trash_check, _t("tip_hidden_trash"))
        self._refresh_skip_existing_state()
        # 图片格式按钮：点开弹窗勾选支持的图片格式
        self.fmt_btn = ttk.Button(opts2, text=_t("btn_formats"), command=self.open_format_dialog)
        self.fmt_btn.pack(side=tk.LEFT, padx=(16, 0))
        _ToolTip(self.fmt_btn, _t("tip_formats"))

        # 分组方式行
        self.fmt_row = ttk.Frame(root, padding=(10, 0, 10, 4))
        self.fmt_row.pack(fill=tk.X)
        ttk.Label(self.fmt_row, text=_t("lbl_group")).pack(side=tk.LEFT)
        self.fmt_combo = ttk.Combobox(self.fmt_row, state="readonly", width=14,
                                      values=[_t("group_flat"), _t("group_year"), _t("group_ym"), _t("group_ymd")])
        self.fmt_combo.current(0)
        self.fmt_combo.pack(side=tk.LEFT, padx=6)
        self.fmt_combo.bind("<<ComboboxSelected>>", self.on_fmt_change)
        _ToolTip(self.fmt_combo, _t("tip_group"))
        self.date_folder_var = tk.BooleanVar(value=False)
        self.date_folder_check = ttk.Checkbutton(self.fmt_row, text=_t("chk_date_folder"),
                                                 variable=self.date_folder_var,
                                                 command=self._refresh_date_fmt_row)
        self.date_folder_check.pack(side=tk.LEFT, padx=(16, 0))
        _ToolTip(self.date_folder_check, _t("tip_date_folder"))
        self.date_folder_check.configure(state=tk.DISABLED)  # 默认单层格式，先禁用
        # 格式说明固定显示：不随单层/嵌套格式行切换而消失
        fmt_help_btn = ttk.Button(self.fmt_row, text=_t("btn_fmt_help"), command=self.show_fmt_help)
        fmt_help_btn.pack(side=tk.LEFT, padx=(16, 0))
        _ToolTip(fmt_help_btn, _t("tip_fmt_help"))

        # 单层格式行（一个日期格式框）
        self.single_fmt_row = ttk.Frame(root, padding=(10, 0, 10, 4))
        self.single_fmt_row.pack(fill=tk.X)
        ttk.Label(self.single_fmt_row, text=_t("lbl_date_fmt")).pack(side=tk.LEFT)
        self.fmt_var = tk.StringVar(value=DATE_FMT)
        self.fmt_entry = ttk.Entry(self.single_fmt_row, textvariable=self.fmt_var, width=16)
        self.fmt_entry.pack(side=tk.LEFT, padx=(4, 0))
        _ToolTip(self.fmt_entry, _t("tip_date_fmt"))

        # 层级嵌套格式行（年/月/日三层，各自可指定）
        self.nest_fmt_row = ttk.Frame(root, padding=(10, 0, 10, 4))
        self.year_var = tk.StringVar(value="%Y")
        self.month_var = tk.StringVar(value="%m")
        self.day_var = tk.StringVar(value="%d")
        ttk.Label(self.nest_fmt_row, text=_t("lbl_year")).pack(side=tk.LEFT)
        self.year_entry = ttk.Entry(self.nest_fmt_row, textvariable=self.year_var, width=10)
        self.year_entry.pack(side=tk.LEFT, padx=(2, 0))
        _ToolTip(self.year_entry, _t("tip_year_fmt"))
        ttk.Label(self.nest_fmt_row, text=_t("lbl_month")).pack(side=tk.LEFT, padx=(10, 0))
        self.month_entry = ttk.Entry(self.nest_fmt_row, textvariable=self.month_var, width=10)
        self.month_entry.pack(side=tk.LEFT, padx=(2, 0))
        _ToolTip(self.month_entry, _t("tip_month_fmt"))
        ttk.Label(self.nest_fmt_row, text=_t("lbl_day")).pack(side=tk.LEFT, padx=(10, 0))
        self.day_entry = ttk.Entry(self.nest_fmt_row, textvariable=self.day_var, width=10)
        self.day_entry.pack(side=tk.LEFT, padx=(2, 0))
        _ToolTip(self.day_entry, _t("tip_day_fmt"))
        self.nest_fmt_row.pack_forget()

        # 按钮行
        self.btns = ttk.Frame(root, padding=(10, 4, 10, 4))
        self.btns.pack(fill=tk.X)
        self.dry_btn = ttk.Button(self.btns, text=_t("mode_dry"),
                                  command=lambda: self.start(dry_run=True))
        self.dry_btn.pack(side=tk.LEFT)
        _ToolTip(self.dry_btn, _t("tip_dry_run"))
        self.start_btn = ttk.Button(self.btns, text=_t("btn_start"),
                                    command=lambda: self.start(dry_run=False))
        self.start_btn.pack(side=tk.LEFT, padx=8)
        _ToolTip(self.start_btn, _t("tip_start"))
        self.reset_btn = ttk.Button(self.btns, text=_t("btn_reset"), command=self._reset_defaults)
        self.reset_btn.pack(side=tk.LEFT, padx=8)
        _ToolTip(self.reset_btn, _t("tip_reset"))
        self.clean_parts_btn = ttk.Button(self.btns, text=_t("btn_clean_parts"), command=self.on_clean_parts)
        self.clean_parts_btn.pack(side=tk.LEFT, padx=(0, 8))
        _ToolTip(self.clean_parts_btn, _t("tip_clean_parts_btn"))

        # 日志功能区（保存选项 + 保留份数）
        log_opts_row = ttk.Frame(root, padding=(10, 2, 10, 0))
        log_opts_row.pack(fill=tk.X, before=self.btns)
        self.save_log_var = tk.BooleanVar(value=False)
        self.save_log_check = ttk.Checkbutton(log_opts_row, text=_t("chk_save_log"),
                                              variable=self.save_log_var)
        self.save_log_check.pack(side=tk.LEFT)
        _ToolTip(self.save_log_check, _t("tip_save_log"))
        self.mono_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(log_opts_row, text=_t("chk_mono"), variable=self.mono_var).pack(side=tk.LEFT, padx=(12, 0))
        _ToolTip(log_opts_row.winfo_children()[-1], _t("tip_mono"))
        ttk.Label(log_opts_row, text=_t("lbl_keep")).pack(side=tk.LEFT, padx=(12, 0))
        self.log_keep_var = tk.StringVar(value=str(DEFAULT_LOG_KEEP))
        self.log_keep_spin = ttk.Spinbox(log_opts_row, from_=1, to=50, width=4,
                                         textvariable=self.log_keep_var)
        self.log_keep_spin.pack(side=tk.LEFT)
        _ToolTip(self.log_keep_spin, _t("tip_keep"))
        ttk.Label(log_opts_row, text=_t("lbl_parts")).pack(side=tk.LEFT, padx=(0, 8))
        # 日志导出/导入/删除所选/清空：放在“保存操作清单 + 保留份数”行末尾，避免操作行过长
        ttk.Button(log_opts_row, text=_t("btn_export"), command=self.on_log_export).pack(side=tk.LEFT, padx=(0, 4))
        _ToolTip(log_opts_row.winfo_children()[-1], _t("tip_export"))
        ttk.Button(log_opts_row, text=_t("btn_import"), command=self.on_log_import).pack(side=tk.LEFT, padx=(0, 4))
        _ToolTip(log_opts_row.winfo_children()[-1], _t("tip_import"))
        ttk.Button(log_opts_row, text=_t("btn_delete_sel"), command=self.on_log_delete_selected).pack(side=tk.LEFT, padx=(0, 4))
        _ToolTip(log_opts_row.winfo_children()[-1], _t("tip_delete_sel"))
        ttk.Button(log_opts_row, text=_t("btn_clear"), command=self.on_log_clear).pack(side=tk.LEFT, padx=(0, 4))
        _ToolTip(log_opts_row.winfo_children()[-1], _t("tip_clear"))

        # 日志操作行（下拉历史操作清单 + 详情/还原/清理数据）
        log_row = ttk.Frame(root, padding=(10, 2, 10, 4))
        log_row.pack(fill=tk.X, before=self.btns)
        ttk.Label(log_row, text=_t("lbl_log")).pack(side=tk.LEFT)
        self.log_combo_var = tk.StringVar()
        self.log_combo = ttk.Combobox(log_row, textvariable=self.log_combo_var,
                                      state="readonly", width=36)
        self.log_combo.pack(side=tk.LEFT, padx=(4, 0))
        self.log_combo.bind("<<ComboboxSelected>>", self.on_log_select)
        _ToolTip(self.log_combo, _t("tip_log_combo"))
        self._refresh_log_combo()
        ttk.Button(log_row, text=_t("btn_detail"), command=self.on_log_show_detail).pack(side=tk.LEFT, padx=(8, 0))
        _ToolTip(log_row.winfo_children()[-1], _t("tip_show_detail"))
        ttk.Button(log_row, text=_t("btn_rollback"), command=self.on_log_restore).pack(side=tk.LEFT, padx=(4, 0))
        _ToolTip(log_row.winfo_children()[-1], _t("tip_rollback"))
        ttk.Button(log_row, text=_t("btn_clear_data"), command=self.on_clear_data).pack(side=tk.LEFT, padx=(8, 0))
        _ToolTip(log_row.winfo_children()[-1], _t("tip_clear_data"))

        # 底部独立状态栏：左侧状态文本（先于日志区 pack，固定贴底不被挤出窗口）
        status_bar = ttk.Frame(root, padding=(10, 2, 10, 6))
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_var = tk.StringVar(value=_t("status_ready"))
        self.status_lbl = ttk.Label(status_bar, textvariable=self.status_var)
        self.status_lbl.pack(side=tk.LEFT)
        if not HAS_PIL:
            self.status_var.set(_t("status_pil_err"))
            self.status_lbl.configure(foreground="#c00000")

        # 日志区
        log_frame = ttk.Frame(root, padding=(10, 4, 10, 10))
        self.log_text = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        log_frame.pack(fill=tk.BOTH, expand=True)
        # 日志着色：错误/删除红 / 警告橙 / 跳过黄 / 模拟青 / 移动绿 / 复制蓝 / 重命名紫 / 信息蓝
        self.log_text.tag_configure("err", foreground="#c00000")
        self.log_text.tag_configure("warn", foreground="#ea580c")
        self.log_text.tag_configure("skip", foreground="#9a6700")
        self.log_text.tag_configure("sim", foreground="#0891b2")
        self.log_text.tag_configure("move", foreground="#15803d")
        self.log_text.tag_configure("copy", foreground="#1f6feb")
        self.log_text.tag_configure("rename", foreground="#7c3aed")
        self.log_text.tag_configure("info", foreground="#1f6feb")
        # 新增标签：提示/隐藏/回收站/视频
        self.log_text.tag_configure("hidden", foreground="#6b7280")
        self.log_text.tag_configure("trash", foreground="#d97706")
        self.log_text.tag_configure("video", foreground="#0e7490")
        # 摘要标签：新建日期文件夹 青绿 / EXIF 判定 深蓝 / 兜底判定 棕
        self.log_text.tag_configure("dir", foreground="#0f766e")
        self.log_text.tag_configure("exif", foreground="#0369a1")
        self.log_text.tag_configure("fallback", foreground="#92400e")
        # 选中提示行：蓝字 + 浅蓝底 + 选中时白字蓝底（修复原 sel 蓝字蓝底不可见问题）
        self.log_text.tag_configure("hint", foreground="#1f6feb", background="#eef4fd",
                                    selectforeground="#ffffff", selectbackground="#1f6feb")
        # 日志区右键菜单：可复制日志文本（含报错信息）
        self._bind_copy_menu(self.log_text)

        # 控件全部就绪后，加载持久化配置（缺失/损坏时按默认值）
        self._apply_config(load_config())

    def _refresh_skip_existing_state(self):
        """“跳过已有日期文件夹”仅在“包含子文件夹”勾选时可用，否则置灰。"""
        if self.recursive_var.get():
            self.skip_existing_check.configure(state=tk.NORMAL)
        else:
            self.skip_existing_check.configure(state=tk.DISABLED)

    # ---------- 日志保存/导出/导入/还原 ----------
    def _refresh_log_combo(self):
        """刷新日志下拉列表（按修改时间倒序，保留当前选择）。"""
        logs = list_logs()
        names = [p.name for p in logs]
        cur = self.log_combo_var.get()
        self.log_combo["values"] = names
        if cur in names:
            self.log_combo_var.set(cur)
        elif names:
            self.log_combo_var.set(names[0])
        else:
            self.log_combo_var.set("")

    def _selected_log_stem(self):
        """返回下拉框选中日志的文件主名；未选择返回 None。"""
        name = self.log_combo_var.get().strip()
        if not name:
            return None
        return Path(name).stem

    def _set_select_hint(self, msg: str):
        """在日志区底部追加「已选择操作清单」提示行（不清空、不删除旧日志）。"""
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, msg + "\n", "hint")
        self.log_text.configure(state=tk.DISABLED)
        self.log_text.see(tk.END)

    def on_log_select(self, event=None):
        """下拉框切换选中清单时，在日志区顶部提示选中项及动作条数。"""
        name = self.log_combo_var.get().strip()
        if not name:
            return
        path = get_logs_dir() / name
        count = 0
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if rec.get("type") != "meta":
                        count += 1
        except OSError:
            self._set_select_hint(f"{_t('err_read_sel')}{name}")
            return
        self._set_select_hint(f"{_t('sel_log_pre')}{name}（{count}{_t('sel_log_post')}")

    def on_log_show_detail(self):
        """在日志窗口追加显示选中操作清单的明细（动态解析 jsonl 为可读标签行，不清空旧日志）。"""
        stem = self._selected_log_stem()
        if not stem:
            messagebox.showinfo(_t("title_info"), _t("msg_need_select"), parent=self.root)
            return
        path = get_logs_dir() / (stem + ".jsonl")
        if not path.exists():
            messagebox.showinfo(_t("title_info"), _t("msg_log_missing_show"), parent=self.root)
            return
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()
        except OSError as e:
            messagebox.showerror(_t("title_error"), f"{_t('err_read_fail')}{e}", parent=self.root)
            return
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"{_t('detail_header')}{path.name} ——\n", "info")
        summary_rec = None
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                self._append_log(line, "warn")
                continue
            if rec.get("type") == "meta" and rec.get("stats"):
                summary_rec = rec
            self._append_log(format_log_line(rec))
        # 处理结果摘要（放最后）
        if summary_rec is not None:
            summary_text = format_log_summary(summary_rec)
            if summary_text:
                for sline in summary_text.splitlines():
                    self._append_log(sline)
        self._append_log("")
        self.log_text.see(tk.END)
        self.status_var.set(f"{_t('detail_shown_pre')}{path.name}{_t('detail_shown_post')}")

    def on_log_export(self):
        """导出选中的操作清单（.jsonl）。"""
        stem = self._selected_log_stem()
        if not stem:
            messagebox.showinfo(_t("title_info"), _t("msg_need_select"), parent=self.root)
            return
        d = get_logs_dir()
        src = d / (stem + ".jsonl")
        if not src.exists():
            messagebox.showinfo(_t("title_info"), _t("msg_log_missing_export"), parent=self.root)
            return
        target = filedialog.asksaveasfilename(
            title=_t("win_export"), defaultextension=".jsonl",
            initialfile=src.name,
            filetypes=[(_t("filetype_log"), "*.jsonl"), ("JSON", "*.json"), (_t("filetype_all"), "*.*")])
        if not target:
            return
        try:
            shutil.copy2(src, target)
            self.log(f"{_t('log_exported')}{target}")
        except OSError as e:
            messagebox.showerror(_t("title_error"), f"{_t('err_export')}{e}", parent=self.root)

    def on_log_import(self):
        """把外部操作清单（.jsonl）导入 logs 目录，之后可下拉选择；重名自动追加序号。"""
        src = filedialog.askopenfilename(
            title=_t("win_import"), filetypes=[(_t("filetype_log"), "*.jsonl"), (_t("filetype_all"), "*.*")])
        if not src:
            return
        src_path = Path(src)
        d = get_logs_dir()
        target = d / src_path.name
        n = 1
        stem_ = target.stem
        while target.exists():
            target = d / f"{stem_}_{n}{target.suffix}"
            n += 1
        try:
            shutil.copy2(src_path, target)
            self.log(f"{_t('log_imported')}{target.name}")
            self._refresh_log_combo()
            self.log_combo_var.set(target.name)
        except OSError as e:
            messagebox.showerror(_t("title_error"), f"{_t('err_import')}{e}", parent=self.root)

    def on_log_restore(self):
        """根据选中的操作清单还原变更：移动模式移回原位置，复制模式删除副本（后台执行，避免 UI 冻结）。"""
        stem = self._selected_log_stem()
        if not stem:
            messagebox.showinfo(_t("title_info"), _t("msg_need_select"), parent=self.root)
            return
        if not messagebox.askyesno(
                _t("btn_rollback"),
                f"{_t('rollback_confirm_pre')}{stem}{_t('rollback_confirm_post')}",
                parent=self.root):
            return
        self._run_bg(
            lambda logf: restore_from_log(stem, log_func=lambda item: logf(_render_core_msg(item))),
            on_done=self._on_restore_done,
            log_start=f"{_t('log_rollback_start')}{stem}")

    def _on_restore_done(self, stats):
        if "error" in stats:
            messagebox.showerror(_t("title_error"), _render_core_msg(stats["error"]), parent=self.root)
            return
        self.log(_rollback_done_msg(stats))
        self.root.bell()

    def on_log_clear(self):
        """清空 logs 目录下的全部操作清单（需确认，后台执行避免 UI 冻结）。"""
        d = get_logs_dir()
        try:
            targets = [p for p in d.iterdir() if p.suffix.lower() == ".jsonl"]
        except OSError:
            targets = []
        if not targets:
            messagebox.showinfo(_t("title_info"), _t("msg_no_logs"), parent=self.root)
            return
        if not messagebox.askyesno(
                _t("win_clear"),
                f"{_t('clear_confirm_pre')}{len(targets)}{_t('clear_confirm_post')}",
                parent=self.root):
            return
        self._run_bg(
            lambda logf: self._do_clear_logs(targets, logf),
            on_done=self._on_clear_logs_done)

    def _do_clear_logs(self, targets, logf):
        removed = 0
        for p in sorted(targets, key=lambda x: x.name):
            try:
                p.unlink()
                removed += 1
                logf(f"{_t('tag_delete')} {p.name}")
            except OSError:
                pass
        return removed

    def _on_clear_logs_done(self, removed):
        self._refresh_log_combo()
        if removed:
            self.log(f"{_t('log_cleared_pre')}{removed}{_t('log_cleared_post')}")
        else:
            self.log(_t("log_clear_fail"))

    def on_log_delete_selected(self):
        """删除下拉框中选中的那一份操作清单（需确认，后台执行避免 UI 冻结）。"""
        stem = self._selected_log_stem()
        if not stem:
            messagebox.showinfo(_t("title_info"), _t("msg_need_select"), parent=self.root)
            return
        path = get_logs_dir() / (stem + ".jsonl")
        if not path.exists():
            messagebox.showinfo(_t("title_info"), _t("msg_log_missing_export"), parent=self.root)
            return
        if not messagebox.askyesno(
                _t("win_clear"),
                f"{_t('clear_sel_confirm_pre')}{path.name}{_t('clear_sel_confirm_post')}",
                parent=self.root):
            return
        self._run_bg(
            lambda logf: self._do_delete_log(path, logf),
            on_done=self._on_delete_log_done)

    def _do_delete_log(self, path, logf):
        try:
            path.unlink()
            logf(f"{_t('tag_delete')} {path.name}")
        except OSError as e:
            return {"error": f"{_t('err_export')}{e}"}
        return None

    def _on_delete_log_done(self, result):
        if result and "error" in result:
            messagebox.showerror(_t("title_error"), result["error"], parent=self.root)
            return
        self._refresh_log_combo()

    def on_clear_data(self):
        """清理数据：删除配置文件与全部操作清单（需确认）。"""
        n = 0
        try:
            d = get_config_dir() / "logs"
            if d.exists():
                n = sum(1 for p in d.iterdir() if p.is_file() and p.suffix.lower() == ".jsonl")
        except OSError:
            pass
        if n == 0 and not get_config_file().exists():
            messagebox.showinfo(_t("title_info"), _t("msg_clear_data_none"), parent=self.root)
            return
        if not messagebox.askyesno(
                _t("btn_clear_data"),
                _t("msg_clear_data_confirm").format(n=n),
                parent=self.root):
            return
        stats = clear_all_data()
        parts = []
        if stats["config"]:
            parts.append(_t("log_clear_data_config"))
        if stats["logs"]:
            parts.append(f"{_t('log_clear_data_logs_pre')}{stats['logs']}{_t('log_clear_data_logs_post')}")
        if parts:
            self.log(" | ".join(parts))
        for f in stats.get("files", []):
            if f.suffix.lower() == ".jsonl":
                self.log(f"{_t('tag_delete')} {f.name}")
        self._refresh_log_combo()
        messagebox.showinfo(_t("title_info"), _t("log_clear_data_done").format(n=stats["logs"]), parent=self.root)

    def _run_bg(self, job, on_done=None, log_start=None):
        """后台线程执行耗时操作：日志经列表收集后回主线程回放，避免 Tk 控件跨线程写入。

        - job: 后台执行函数，接收一个 logf 回调（线程内收集日志行）
        - on_done: 主线程回调，接收 job 返回值（异常时收到 {"error": msg}）
        - log_start: 可选，执行前在主线程输出的起始日志
        """
        logs: list = []

        def collect(s):
            logs.append(s)

        def runner():
            try:
                result = job(collect)
            except Exception as e:
                result = {"error": str(e)}
            self.root.after(0, lambda: self._finish_bg(logs, result, on_done))

        if log_start:
            self.log(log_start)
        threading.Thread(target=runner, daemon=True).start()

    def _finish_bg(self, logs, result, on_done):
        for s in logs:
            self.log(s)
        if on_done:
            on_done(result)

    def on_clean_parts(self):
        """手动清理目标目录下的半成品文件（.photo-org-tmp）：后台扫描 → 确认 → 后台删除，避免 UI 冻结。"""
        dest_dir = self.dest_var.get().strip()
        if not dest_dir:
            messagebox.showwarning(_t("title_info"), _t("msg_need_dest"))
            return
        dest_root = Path(dest_dir)
        if not dest_root.exists():
            messagebox.showerror(_t("title_error"), f"{_t('err_dest_missing')}{dest_dir}", parent=self.root)
            return
        self._run_bg(
            lambda logf: _cleanup_parts(dest_root),
            on_done=self._on_clean_scan_done,
            log_start=_t("log_clean_parts_scan"))

    def _on_clean_scan_done(self, parts):
        if not parts:
            self.log(_t("msg_clean_parts_none"))
            return
        if not messagebox.askyesno(
                _t("btn_clean_parts"),
                _t("msg_clean_parts_confirm").format(n=len(parts)),
                parent=self.root):
            return
        self._run_bg(
            lambda logf: self._do_clean_parts(parts, logf),
            on_done=self._on_clean_parts_done)

    def _do_clean_parts(self, parts, logf):
        removed = 0
        for p in sorted(parts, key=lambda x: str(x)):
            try:
                p.unlink()
                removed += 1
                logf(f"{_t('tag_delete')} {p}")
            except OSError:
                pass
        return removed

    def _on_clean_parts_done(self, removed):
        self.log(_t("log_clean_parts_done").format(n=removed))

    # ---------- 配置持久化 ----------
    def _default_config(self) -> dict:
        """默认配置快照，用于恢复默认设置（与 GUI 首次启动的默认值一致）。"""
        return {
            "source_dir": SOURCE_DIR,
            "dest_dir": DEST_DIR,
            "photo_exts": sorted(BASE_PHOTO_EXTS),
            "include_video": True,
            "copy_mode": False,
            "recursive": False,
            "skip_existing_date_folders": True,
            "include_hidden_trash": False,
            "prefix_duplicates": False,
            "group_mode": "flat",
            "date_fmt": "%Y-%m-%d",
            "year_fmt": "%Y",
            "month_fmt": "%m",
            "day_fmt": "%d",
            "date_folder_in_group": False,
            "save_log": False,
            "log_keep_count": 30,
        }

    def _apply_config(self, cfg: dict):
        """把配置字典应用到界面控件。"""
        if not cfg:
            return
        self.src_var.set(str(cfg.get("source_dir", SOURCE_DIR)))
        self.dest_var.set(str(cfg.get("dest_dir", DEST_DIR)))
        self._src_history = self._clean_history(cfg.get("source_history", []))
        self._dest_history = self._clean_history(cfg.get("dest_history", []))
        if hasattr(self, "src_entry") and hasattr(self, "dest_entry"):
            self.src_entry.configure(values=self._src_history)
            self.dest_entry.configure(values=self._dest_history)
        exts = cfg.get("photo_exts")
        if isinstance(exts, list) and exts:
            self._photo_exts = {str(e).lower() for e in exts}
        self.video_var.set(bool(cfg.get("include_video", True)))
        self.copy_var.set(bool(cfg.get("copy_mode", False)))
        self.recursive_var.set(bool(cfg.get("recursive", False)))
        self.skip_existing_var.set(bool(cfg.get("skip_existing_date_folders", True)))
        self.hidden_trash_var.set(bool(cfg.get("include_hidden_trash", False)))
        self.prefix_var.set(bool(cfg.get("prefix_duplicates", False)))
        group = _group_key_to_label(str(cfg.get("group_mode", "flat")))
        if group in self.fmt_combo["values"]:
            self.fmt_combo.set(group)
        self.fmt_var.set(str(cfg.get("date_fmt", "%Y-%m-%d")))
        self.year_var.set(str(cfg.get("year_fmt", "%Y")))
        self.month_var.set(str(cfg.get("month_fmt", "%m")))
        self.day_var.set(str(cfg.get("day_fmt", "%d")))
        self.date_folder_var.set(bool(cfg.get("date_folder_in_group", False)))
        self.save_log_var.set(bool(cfg.get("save_log", False)))
        try:
            keep = int(cfg.get("log_keep_count", DEFAULT_LOG_KEEP))
        except (TypeError, ValueError):
            keep = 30
        self.log_keep_var.set(str(max(1, keep)))
        # 分组相关行/复选框显隐与状态统一刷新
        self.on_fmt_change()
        # “跳过已有日期文件夹”可用性跟随“包含子文件夹”
        self._refresh_skip_existing_state()

    def _collect_config(self) -> dict:
        """把界面当前值收集成配置字典。"""
        src = self.src_var.get().strip()
        dest = self.dest_var.get().strip()
        return {
            "source_dir": src,
            "dest_dir": dest,
            "source_history": self._record_path(src, self._src_history),
            "dest_history": self._record_path(dest, self._dest_history),
            "photo_exts": sorted(self._photo_exts),
            "include_video": self.video_var.get(),
            "copy_mode": self.copy_var.get(),
            "recursive": self.recursive_var.get(),
            "skip_existing_date_folders": self.skip_existing_var.get(),
            "include_hidden_trash": self.hidden_trash_var.get(),
            "prefix_duplicates": self.prefix_var.get(),
            "group_mode": _group_label_to_key(self.fmt_combo.get()),
            "date_fmt": self.fmt_var.get(),
            "year_fmt": self.year_var.get(),
            "month_fmt": self.month_var.get(),
            "day_fmt": self.day_var.get(),
            "date_folder_in_group": self.date_folder_var.get(),
            "save_log": self.save_log_var.get(),
            "log_keep_count": int(self.log_keep_var.get() or DEFAULT_LOG_KEEP),
            "language": CURRENT_LANG,
        }

    def _save_current_config(self):
        """把界面当前值写入配置文件。"""
        save_config(self._collect_config())

    def _switch_language(self):
        """切换界面语言：保存状态→切语言→写配置→重建界面并恢复状态。"""
        if self.running:
            messagebox.showwarning(_t("title_info"), _t("msg_lang_busy"), parent=self.root)
            return
        cfg = self._collect_config()
        cfg["language"] = "en" if CURRENT_LANG == "zh" else "zh"
        save_config(cfg)
        _set_language(cfg["language"])
        for w in self.root.winfo_children():
            w.destroy()
        self._build_ui()
        self.log(_t("log_lang_switched"))

    def _reset_defaults(self):
        """恢复默认设置：确认后删除配置并复位界面控件，但保留当前源/目标路径。"""
        if not messagebox.askyesno(_t("btn_reset"), _t("msg_reset_confirm"), parent=self.root):
            return
        src = self.src_var.get().strip()
        dest = self.dest_var.get().strip()
        reset_config()
        cfg = self._default_config()
        cfg["source_dir"] = src or SOURCE_DIR
        cfg["dest_dir"] = dest or DEST_DIR
        self._apply_config(cfg)
        save_config(cfg)
        self.log(_t("log_reset_done"))

    def _clean_history(self, raw) -> list:
        """清洗路径历史：去空、去重、保持顺序，最多保留 10 条。"""
        seen = set()
        out = []
        for item in raw if isinstance(raw, list) else []:
            s = str(item).strip()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
        return out[-10:]

    def _record_path(self, path: str, history: list) -> list:
        """把一条路径记入历史（最新在前，去重，最多 MAX_HISTORY 条）。"""
        path = path.strip()
        if not path:
            return history
        out = [path] + [h for h in history if h != path]
        return out[:MAX_HISTORY]

    def on_clear_paths(self, side: str):
        """清除界面记住的源或目标文件夹路径记录（不弹窗确认，并同步清空当前输入框内容）。"""
        if side == "src":
            done = _t("log_src_cleared")
            hist_attr, entry_attr, var_attr, cfg_key = "_src_history", "src_entry", "src_var", "source_history"
        else:
            done = _t("log_dest_cleared")
            hist_attr, entry_attr, var_attr, cfg_key = "_dest_history", "dest_entry", "dest_var", "dest_history"
        setattr(self, hist_attr, [])
        if hasattr(self, entry_attr):
            getattr(self, entry_attr).configure(values=[])
        if hasattr(self, var_attr):
            getattr(self, var_attr).set("")
        cfg = self._collect_config()
        cfg[cfg_key] = []
        save_config(cfg)
        self.log(done)

    def browse_source(self):
        selected = filedialog.askdirectory(title=_t("tip_src_browse"))
        if selected:
            self.src_var.set(selected)
            self._src_history = self._record_path(selected, self._src_history)
            self.src_entry.configure(values=self._src_history)

    def browse_dest(self):
        selected = filedialog.askdirectory(title=_t("tip_dest_browse"))
        if selected:
            self.dest_var.set(selected)
            self._dest_history = self._record_path(selected, self._dest_history)
            self.dest_entry.configure(values=self._dest_history)

    def open_format_dialog(self):
        """图片格式勾选弹窗：默认 9 种已勾选，可增减，可自定义添加，仅本次运行生效。"""
        win = tk.Toplevel(self.root)
        win.title(_t("win_formats"))
        win.transient(self.root)
        win.grab_set()
        win.geometry(FMT_DIALOG_GEOM)
        win.minsize(400, 340)

        cur = set(self._photo_exts)  # 本次运行内已勾选集合（默认基础 9 种）
        # 候选 = 全部已知格式 + 本次会话添加过的自定义格式
        candidates = set(PHOTO_EXTS) | cur

        canvas = tk.Canvas(win, highlightthickness=0, height=190)
        sb = ttk.Scrollbar(win, orient=tk.VERTICAL, command=canvas.yview)
        inner = ttk.Frame(canvas)

        def _update_scrollregion(_e=None):
            """滚动区域取内容与视口的较大值，内容不满屏时滚动条不可滚动、无空白。"""
            bbox = canvas.bbox("all")
            if not bbox:
                return
            view_w = canvas.winfo_width()
            view_h = canvas.winfo_height()
            canvas.configure(scrollregion=(0, 0, max(bbox[2], view_w), max(bbox[3], view_h)))

        inner.bind("<Configure>", _update_scrollregion)
        canvas.bind("<Configure>", _update_scrollregion)
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        def _on_wheel(e):
            canvas.yview_scroll(int(-e.delta / 120), "units")
        canvas.bind_all("<MouseWheel>", _on_wheel)

        ttk.Label(inner, padding=(10, 10, 10, 4), text=_t("prompt_formats")).pack(anchor="w")

        # 格式列表：3 列网格，避免单列过长
        grid_frame = ttk.Frame(inner)
        grid_frame.pack(fill=tk.X, anchor="w", padx=(4, 4))
        for col in range(FMT_GRID_COLS):
            grid_frame.columnconfigure(col, weight=1)

        check_vars: dict[str, tk.BooleanVar] = {}
        for i, ext in enumerate(sorted(candidates)):
            var = tk.BooleanVar(value=ext in cur)
            ttk.Checkbutton(grid_frame, text=ext, variable=var, padding=(12, 1)) \
                .grid(row=i // FMT_GRID_COLS, column=i % FMT_GRID_COLS, sticky="w", padx=8)
            check_vars[ext] = var

        # 自定义添加行（紧随列表下方）
        add_row = ttk.Frame(inner, padding=(10, 6, 10, 4))
        add_row.pack(fill=tk.X)
        entry_var = tk.StringVar()
        entry = ttk.Entry(add_row, textvariable=entry_var, width=12)
        entry.pack(side=tk.LEFT)
        hint = ttk.Label(add_row, text=_t("lbl_other_fmt"))
        hint.pack(side=tk.LEFT, padx=6)

        def add_custom():
            raw = entry_var.get().strip()
            ext = raw if raw.startswith(".") else "." + raw
            ext = ext.lower()
            if not ext or ext == ".":
                return
            if ext in check_vars:
                check_vars[ext].set(True)
            else:
                var = tk.BooleanVar(value=True)
                i = len(check_vars)
                ttk.Checkbutton(grid_frame, text=ext, variable=var, padding=(12, 1)) \
                    .grid(row=i // FMT_GRID_COLS, column=i % FMT_GRID_COLS, sticky="w", padx=8)
                check_vars[ext] = var
            entry_var.set("")
            inner.update_idletasks()
            _update_scrollregion()
        entry.bind("<Return>", lambda e: add_custom())
        ttk.Button(add_row, text=_t("btn_add"), command=add_custom).pack(side=tk.LEFT, padx=4)

        # 确定/取消按钮（紧随添加行下方）
        btns = ttk.Frame(inner, padding=(10, 6, 10, 10))
        btns.pack(fill=tk.X)

        def on_ok():
            selected = {ext for ext, var in check_vars.items() if var.get()}
            if not selected:
                messagebox.showwarning(_t("title_info"), _t("msg_min_format"), parent=win)
                return
            self._photo_exts = selected  # 本次运行生效
            # 二审 M2：仅持久化 photo_exts，避免格式弹窗确认连带覆盖其它界面状态（其余状态在点击开始整理时统一保存）
            _cfg = load_config()
            _cfg["photo_exts"] = sorted(selected)
            save_config(_cfg)
            win.grab_release()
            win.destroy()

        def on_cancel():
            win.grab_release()
            win.destroy()

        ttk.Button(btns, text=_t("btn_ok"), command=on_ok).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(btns, text=_t("btn_cancel"), command=on_cancel).pack(side=tk.RIGHT)

        def _on_close():
            on_cancel()
        win.protocol("WM_DELETE_WINDOW", _on_close)

        def _unbind_wheel():
            canvas.unbind_all("<MouseWheel>")
        win.bind("<Destroy>", lambda e: _unbind_wheel() if e.widget is win else None)

        entry.focus_set()

    def _bind_copy_menu(self, widget):
        """给只读文本框绑定右键菜单：复制 / 复制全部 / 全选。"""
        menu = tk.Menu(widget, tearoff=0)
        menu.add_command(label=_t("menu_copy"), command=lambda: self._copy_selection(widget))
        menu.add_command(label=_t("menu_copy_all"), command=lambda: self._copy_all(widget))
        menu.add_command(label=_t("menu_select_all"), command=lambda: self._select_all(widget))

        def _popup(event):
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()

        widget.bind("<Button-3>", _popup)

    def _copy_selection(self, widget):
        """复制只读文本框中的选中内容（无选中则静默）。"""
        try:
            text = widget.get("sel.first", "sel.last")
        except tk.TclError:
            text = ""
        if text:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)

    def _copy_all(self, widget):
        """复制只读文本框的全部内容。"""
        text = widget.get("1.0", "end-1c")
        if text:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)

    def _select_all(self, widget):
        """只读文本框全选（保留选区 tag，配合右键“复制”使用）。"""
        widget.configure(state=tk.NORMAL)
        widget.tag_add("sel", "1.0", tk.END)
        widget.configure(state=tk.DISABLED)

    def _show_help_window(self, win_attr: str, title_key: str, geom: str, minsize: tuple, text_key: str):
        """公共帮助窗口（二审 STYLE2）：单例窗口检查 → Toplevel → ScrolledText 展示 → 右键菜单 → 关闭回调。"""
        exist = getattr(self, win_attr, None)
        if exist is not None:
            try:
                if exist.winfo_exists():
                    exist.lift()
                    exist.focus_set()
                    return
            except Exception:
                pass

        win = tk.Toplevel(self.root)
        setattr(self, win_attr, win)
        win.title(_t(title_key))
        win.transient(self.root)
        win.geometry(geom)
        win.minsize(*minsize)

        text = _t(text_key)

        def _close():
            setattr(self, win_attr, None)
            if win.winfo_exists():
                win.destroy()
        # 按钮先 pack、固定在底部，文本区后 pack 吃掉剩余空间（避免文本长时按钮被挤出可视区）
        ttk.Button(win, text=_t("btn_got_it"), command=_close).pack(side=tk.BOTTOM, pady=8)

        body = scrolledtext.ScrolledText(win, wrap=tk.WORD, font=("Consolas", 11),
                                         padx=14, pady=10, relief=tk.FLAT, state=tk.DISABLED)
        body.pack(fill=tk.BOTH, expand=True)
        body.configure(state=tk.NORMAL)
        body.insert(tk.END, text)
        body.configure(state=tk.DISABLED)
        self._bind_copy_menu(body)  # 右键复制 / 全选

        win.protocol("WM_DELETE_WINDOW", _close)

    def show_fmt_help(self):
        """弹出日期格式说明窗口；同时把说明正文写入日志窗口一份（开头带 [信息] 提示行）。"""
        self._show_help_window("_fmt_help_win", "win_fmt_help",
                               FMT_HELP_GEOM, (420, 360), "fmt_help_body")
        self.log(f"{_t('log_fmt_help_pre')}\n{_t('fmt_help_body')}")

    def show_help(self):
        """弹出命令行用法说明窗口；同时把说明正文写入日志窗口一份（开头带 [信息] 提示行）。"""
        self._show_help_window("_help_win", "btn_help",
                               HELP_GEOM, (480, 380), "help_body")
        self.log(f"{_t('log_help_body_pre')}\n{_t('help_body')}")

    def on_fmt_change(self, event=None):
        """分组方式切换：单层格式 <-> 层级嵌套（年/月/日三层），并联动日期格式行显隐。"""
        self._refresh_date_fmt_row()

    def _refresh_date_fmt_row(self):
        """按当前分组方式与「分组时创建日期文件夹」勾选状态，刷新日期格式行显隐与位置（始终紧贴「文件夹分组」行下方）。"""
        mode = self.fmt_combo.get()
        if mode == _t("group_flat"):
            self.nest_fmt_row.pack_forget()
            self.single_fmt_row.pack(fill=tk.X, after=self.fmt_row)  # 紧跟分组行下方
            # 单层格式本身就是要建日期文件夹，复选框无意义 → 禁用
            self.date_folder_check.configure(state=tk.DISABLED)
        else:
            self.single_fmt_row.pack_forget()
            self.nest_fmt_row.pack(fill=tk.X, after=self.fmt_row)  # 紧跟分组行下方
            self.date_folder_check.configure(state=tk.NORMAL)
            # 分组模式下：勾选「分组时创建日期文件夹」→ 显示分组内日期文件夹的格式输入框（年/月/日行之后）
            if self.date_folder_var.get():
                self.single_fmt_row.pack(fill=tk.X, after=self.nest_fmt_row)
            else:
                self.single_fmt_row.pack_forget()

    def _apply_label_tags(self, text: str, start: str):
        """给刚插入的日志文本中的标签子串着色（正文保持默认色）。start 为插入前记录的起始位置。"""
        if self.mono_var.get():
            return
        base_row, base_col = map(int, start.split("."))
        for label, tag in _log_label_pairs():
            if not label:
                continue
            pos = 0
            while True:
                i = text.find(label, pos)
                if i < 0:
                    break
                prefix_lines = text[:i].split("\n")
                row = base_row + len(prefix_lines) - 1
                col = len(prefix_lines[-1]) + (base_col if len(prefix_lines) == 1 else 0)
                self.log_text.tag_add(tag, f"{row}.{col}", f"{row}.{col + len(label)}")
                pos = i + len(label)

    def _append_log(self, text: str, tag: str | None = None):
        """追加一行日志：tag 为空时只给标签子串着色，tag 非空时整行着色。"""
        self.log_text.configure(state=tk.NORMAL)
        start = self.log_text.index("end-1c")
        self.log_text.insert(tk.END, text + "\n", tag)
        if tag is None:
            self._apply_label_tags(text, start)
        self.log_text.configure(state=tk.DISABLED)

    def log(self, msg: str):
        self._append_log(msg)
        self.log_text.see(tk.END)

    def start(self, dry_run: bool):
        if self.running:
            return
        source_dir = self.src_var.get().strip()
        dest_dir = self.dest_var.get().strip()
        fmt = self.fmt_var.get().strip() or DATE_FMT
        group = {_t("group_year"): "Y", _t("group_ym"): "YM", _t("group_ymd"): "YMD"}.get(self.fmt_combo.get())
        year_fmt = self.year_var.get().strip() or "%Y"
        month_fmt = self.month_var.get().strip() or "%m"
        day_fmt = self.day_var.get().strip() or "%d"
        if not source_dir:
            messagebox.showwarning(_t("title_info"), _t("msg_need_src"))
            return
        if not dest_dir:
            messagebox.showwarning(_t("title_info"), _t("msg_need_dest"))
            return

        self.running = True
        self._save_current_config()  # 整理前把当前界面设置持久化
        self.start_btn.configure(state=tk.DISABLED)
        self.dry_btn.configure(state=tk.DISABLED)
        self.status_var.set(_t("status_working"))
        self.log("----------------------------------------")
        self.log(f"{_t('line_src')}{source_dir}")
        self.log(f"{_t('line_dest')}{dest_dir}")
        self.log(f"{_t('line_video')}{_t('yes') if self.video_var.get() else _t('no')}{_t('line_mode_sep')}{_t('mode_dry') if dry_run else (_t('chk_copy_mode') if self.copy_var.get() else _t('mode_move'))}")
        self.log(f"{_t('line_hidden_trash')}{_t('yes') if self.hidden_trash_var.get() else _t('no')}")
        self.log(f"{_t('line_recursive')}{_t('yes') if self.recursive_var.get() else _t('no')}{_t('line_conflict_sep')}{_t('conflict_prefix') if self.prefix_var.get() else _t('conflict_skip')}")
        if group:
            self.log(f"{_t('line_group')}{self.fmt_combo.get()}{_t('line_year_sep')}{year_fmt}{_t('line_month_sep')}{month_fmt}{_t('line_day_sep')}{day_fmt}"
                     + (f"{_t('line_in_group_pre')}{fmt}）" if self.date_folder_var.get() else ""))
        else:
            self.log(f"{_t('line_group_flat')}{fmt}")
        self.log("----------------------------------------")

        # 二审 M1：主线程快照 Tk 变量，工作线程只读快照（避免跨线程读 Tk 变量）
        snap = {
            "include_video": self.video_var.get(),
            "copy_mode": self.copy_var.get(),
            "recursive": self.recursive_var.get(),
            "prefix_duplicates": self.prefix_var.get(),
            "date_folder_in_group": self.date_folder_var.get(),
            "skip_existing": self.skip_existing_var.get(),
            "include_hidden_trash": self.hidden_trash_var.get(),
            "save_log": self.save_log_var.get(),
            "log_keep": self.log_keep_var.get().strip(),
            "group": self.fmt_combo.get(),
            "photo_exts": set(self._photo_exts),
        }

        t = threading.Thread(target=self.worker,
                             args=(source_dir, dest_dir, fmt, group, year_fmt, month_fmt, day_fmt, dry_run, snap),
                             daemon=True)
        t.start()
        self.root.after(100, self.poll)

    def worker(self, source_dir: str, dest_dir: str, fmt: str,
               group: str | None, year_fmt: str, month_fmt: str, day_fmt: str,
               dry_run: bool, snap: dict):
        actions: list = []

        def _log(item):
            self.log_q.put(_render_core_msg(item))

        try:
            stats = organize(source_dir, dest_dir, dry_run=dry_run,
                     include_video=snap["include_video"], log_func=_log,
                     copy_mode=snap["copy_mode"], recursive=snap["recursive"],
                     prefix_duplicates=snap["prefix_duplicates"], date_fmt=fmt,
                     group_levels=group, year_fmt=year_fmt,
                     month_fmt=month_fmt, day_fmt=day_fmt,
                     date_folder_in_group=snap["date_folder_in_group"],
                     skip_existing_date_folders=snap["skip_existing"],
                     include_hidden_trash=snap["include_hidden_trash"],
                     photo_exts=snap["photo_exts"],
                     actions=actions)
            if snap["save_log"]:
                try:
                    keep = int(snap["log_keep"] or DEFAULT_LOG_KEEP)
                except (TypeError, ValueError):
                    keep = 30
                meta = {
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "mode": _t("mode_dry") if dry_run else (_t("chk_copy_mode") if snap["copy_mode"] else _t("mode_move")),
                    "src": source_dir,
                    "dest": dest_dir,
                    "recursive": _t("yes") if snap["recursive"] else _t("no"),
                    "include_hidden_trash": _t("yes") if snap["include_hidden_trash"] else _t("no"),
                    "conflict": _t("conflict_prefix") if snap["prefix_duplicates"] else _t("conflict_skip"),
                    "group": snap["group"],
                    "date_fmt": fmt,
                    "keep": str(keep),
                }
                if stats:
                    meta["stats"] = _stats_to_meta(stats)
                try:
                    log_path = write_organize_log(meta, [], actions, keep)
                    self.log_q.put(f"{_t('log_saved')}{log_path.name}")
                except Exception as e:
                    self.log_q.put(f"{_t('err_save_log')}{e}")
        except Exception as e:
            try:
                self.log_q.put(f"{_t('err_worker_crash')}{e}")
            except Exception:
                pass
        finally:
            self.log_q.put(None)

    def poll(self):
        try:
            while True:
                item = self.log_q.get_nowait()
                if item is None:
                    self.running = False
                    self.start_btn.configure(state=tk.NORMAL)
                    self.dry_btn.configure(state=tk.NORMAL)
                    self.status_var.set(_t("status_done"))
                    self._refresh_log_combo()  # 整理完成后刷新日志下拉列表
                    self.root.bell()
                    return
                self.log(item)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)


def main():
    global tk, filedialog, messagebox, scrolledtext, ttk  # tkinter 延迟到 GUI 分支导入（import 时无副作用）
    import argparse

    parser = argparse.ArgumentParser(description=_t("prog_desc"))
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}",
                        help=_t("arg_version"))
    parser.add_argument("source_dir", nargs="?", default=None,
                        help=_t("arg_src"))
    parser.add_argument("dest_dir", nargs="?", default=None,
                        help=_t("arg_dest"))
    parser.add_argument("--dry-run", action="store_true",
                        help=_t("arg_dry_run"))
    parser.add_argument("--copy", action="store_true",
                        help=_t("arg_copy_mode"))
    parser.add_argument("-r", "--recursive", action="store_true",
                        help=_t("arg_recursive"))
    parser.add_argument("--skip-existing-date-folders", dest="skip_existing_date_folders",
                        action=argparse.BooleanOptionalAction, default=True,
                        help=_t("arg_skip_existing"))
    parser.add_argument("--prefix", action="store_true",
                        help=_t("arg_prefix"))
    parser.add_argument("--date-folder", dest="date_folder", action="store_true",
                        help=_t("arg_date_folder"))
    parser.add_argument("--no-video", action="store_true",
                        help=_t("arg_no_video"))
    parser.add_argument("--include-hidden-trash", dest="include_hidden_trash",
                        action="store_true", default=False,
                        help=_t("arg_include_hidden_trash"))
    parser.add_argument("--lang", choices=["zh", "en"], default=None,
                        help=_t("arg_lang"))
    parser.add_argument("--save-log", dest="save_log",
                        action=argparse.BooleanOptionalAction, default=False,
                        help=_t("arg_save_log"))
    parser.add_argument("--log-keep", dest="log_keep", type=int, default=DEFAULT_LOG_KEEP,
                        help=_t("arg_log_keep"))
    parser.add_argument("--reset-config", dest="reset_config", action="store_true",
                        help=_t("arg_reset_config"))
    parser.add_argument("--list-logs", dest="list_logs", action="store_true",
                        help=_t("arg_list_logs"))
    parser.add_argument("--show-log", dest="show_log", metavar="NAME",
                        help=_t("arg_show_log"))
    parser.add_argument("--rollback", dest="rollback", metavar="NAME",
                        help=_t("arg_rollback"))
    parser.add_argument("--clear-logs", dest="clear_logs", action="store_true",
                        help=_t("arg_clear_logs"))
    parser.add_argument("--clear-data", dest="clear_data", action="store_true",
                        help=_t("arg_clear_data"))
    parser.add_argument("--cleanup-parts", dest="cleanup_parts", action="store_true",
                        help=_t("arg_cleanup_parts"))
    parser.add_argument("--mono", action="store_true",
                        help=_t("arg_mono"))
    parser.add_argument("--date-format", dest="date_format", default=None,
                        help=_t("arg_date_fmt"))
    parser.add_argument("--group", dest="group", choices=["year", "year-month", "year-month-day"],
                        default=None,
                        help=_t("arg_group"))
    parser.add_argument("--year-format", dest="year_format", default="%Y",
                        help=_t("arg_year_fmt"))
    parser.add_argument("--month-format", dest="month_format", default="%m",
                        help=_t("arg_month_fmt"))
    parser.add_argument("--day-format", dest="day_format", default="%d",
                        help=_t("arg_day_fmt"))
    parser.add_argument("--add-photo-ext", dest="add_photo_exts", action="append", default=[],
                        help=_t("arg_add_ext"))
    parser.add_argument("--remove-photo-ext", dest="remove_photo_exts", action="append", default=[],
                        help=_t("arg_remove_ext"))
    parser.add_argument("--no-exif", dest="no_exif", action="store_true",
                        help=_t("arg_no_exif"))
    parser.add_argument("--yes", dest="yes", action="store_true",
                        help=_t("arg_yes"))
    parser.add_argument("-g", "--gui", action="store_true",
                        help=_t("arg_gui"))
    args = parser.parse_args()

    # --lang 需在任何输出前生效（_t 动态查表，全局切换即可）
    if args.lang:
        _set_language(args.lang)

    # 操作类命令：优先于整理执行
    if args.reset_config:
        p = get_config_file()
        if p.exists():
            p.unlink()
            print(_t("log_reset_done"))
        else:
            print(_t("msg_reset_none"))
        return
    if args.list_logs:
        logs = list_logs()
        if isinstance(logs, list):
            for f in logs:
                print(f.name)
        else:
            print(logs)
        return
    if args.show_log:
        name = args.show_log
        path = get_logs_dir() / (name if name.endswith(".jsonl") else name + ".jsonl")
        if not path.exists():
            print(f"{_t('err_read_fail')}{path}")
            return
        try:
            summary_rec = None
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        print(line)
                        continue
                    if rec.get("type") == "meta" and rec.get("stats"):
                        summary_rec = rec
                    print(format_log_line(rec))
            if summary_rec is not None:
                summary_text = format_log_summary(summary_rec)
                if summary_text:
                    print()
                    print(summary_text)
        except OSError as e:
            print(f"{_t('err_read_fail')}{e}")
        return
    if args.rollback:
        stem = args.rollback[:-6] if args.rollback.endswith(".jsonl") else args.rollback
        stats = restore_from_log(stem, log_func=lambda item: print(_render_core_msg(item)))
        if "error" in stats:
            print(_render_core_msg(stats["error"]))
            return
        print(_rollback_done_msg(stats))
        return
    if args.clear_logs:
        if not args.yes:
            print(_t("msg_need_yes"))
            return
        d = get_logs_dir()
        try:
            targets = [p for p in d.iterdir() if p.suffix.lower() == ".jsonl"]
        except OSError:
            targets = []
        if not targets:
            print(_t("msg_no_logs"))
            return
        removed = 0
        for p in sorted(targets, key=lambda x: x.name):
            try:
                p.unlink()
                removed += 1
                print(f"{_t('tag_delete')} {p.name}")
            except OSError:
                pass
        print(f"{_t('log_cleared_pre')}{removed}{_t('log_cleared_post')}")
        return
    if args.clear_data:
        if not args.yes:
            print(_t("msg_need_yes"))
            return
        stats = clear_all_data()
        for f in stats.get("files", []):
            if f.suffix.lower() == ".jsonl":
                print(f"{_t('tag_delete')} {f.name}")
        parts = []
        if stats["config"]:
            parts.append(_t("log_clear_data_config"))
        if stats["logs"]:
            parts.append(f"{_t('log_clear_data_logs_pre')}{stats['logs']}{_t('log_clear_data_logs_post')}")
        if parts:
            print(" | ".join(parts))
        return
    if args.cleanup_parts:
        dest_root = Path(args.dest_dir or DEST_DIR)
        if not dest_root.exists():
            print(f"{_t('err_dest_missing')}{dest_root}")
            return
        print(_t("log_clean_parts_scan"))
        parts = _cleanup_parts(dest_root)
        if not parts:
            print(_t("msg_clean_parts_none"))
            return
        removed = 0
        for p in sorted(parts, key=lambda x: str(x)):
            try:
                p.unlink()
                removed += 1
                print(f"{_t('tag_delete')} {p}")
            except OSError:
                pass
        print(_t("log_clean_parts_done").format(n=removed))
        return

    # 双击无参数 → 图形界面；命令行带参数 → 命令行模式
    if args.gui or (args.source_dir is None and not args.dry_run):
        import tkinter as tk
        from tkinter import filedialog, messagebox, scrolledtext, ttk
        root = tk.Tk()
        PhotoOrganizerApp(root)
        root.mainloop()
    else:
        actions: list = []

        def _log(item):
            print(_render_core_msg(item))

        stats = organize(args.source_dir or SOURCE_DIR, args.dest_dir or DEST_DIR,
                 dry_run=args.dry_run, copy_mode=args.copy,
                 recursive=args.recursive, prefix_duplicates=args.prefix,
                 include_video=not args.no_video,
                 date_fmt=args.date_format or DATE_FMT,
                 group_levels=args.group, year_fmt=args.year_format,
                 month_fmt=args.month_format, day_fmt=args.day_format,
                 date_folder_in_group=args.date_folder,
                 skip_existing_date_folders=args.skip_existing_date_folders,
                 include_hidden_trash=args.include_hidden_trash,
                 add_photo_exts=args.add_photo_exts,
                 remove_photo_exts=args.remove_photo_exts,
                 no_exif=args.no_exif,
                 log_func=_log, actions=actions)

        save_log = args.save_log  # CLI 默认不保存操作清单（BooleanOptionalAction 支持 --no-save-log 显式关闭）
        if save_log:
            try:
                keep = int(args.log_keep or DEFAULT_LOG_KEEP)
            except (TypeError, ValueError):
                keep = 30
            meta = {
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "mode": _t("mode_dry") if args.dry_run else (_t("chk_copy_mode") if args.copy else _t("mode_move")),
                "src": args.source_dir or SOURCE_DIR,
                "dest": args.dest_dir or DEST_DIR,
                "recursive": _t("yes") if args.recursive else _t("no"),
                "include_hidden_trash": _t("yes") if args.include_hidden_trash else _t("no"),
                "conflict": _t("conflict_prefix") if args.prefix else _t("conflict_skip"),
                "group": args.group or "",
                "date_fmt": args.date_format or DATE_FMT,
                "keep": str(keep),
            }
            if stats:
                meta["stats"] = _stats_to_meta(stats)
            try:
                log_path = write_organize_log(meta, [], actions, keep)
                print(f"{_t('log_saved')}{log_path.name}")
            except Exception as e:
                print(f"{_t('err_save_log')}{e}")


if __name__ == "__main__":
    main()
