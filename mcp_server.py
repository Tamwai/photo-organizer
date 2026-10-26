# -*- coding: utf-8 -*-
"""photo-organizer MCP Server（FastMCP / stdio）

薄封装 core.py 的整理能力，向 MCP 客户端暴露 6 个工具：
    preview_organize / organize_photos / list_logs / show_log / revert_changes / get_config

安全模型：
  - 目录白名单：启动参数 --allow（可多次）优先 > %APPDATA%\\photo-organizer\\mcp_config.json
    （macOS: ~/Library/Application Support/photo-organizer/；Linux: $XDG_CONFIG_HOME 或 ~/.config/）
    > 默认 ~/Pictures。配置文件按 mtime 热加载（每次调用重新读取）。
  - max_files_per_run 上限（默认 5000），超限返回 MAX_FILES_EXCEEDED。
  - 禁止自动创建目标目录：目标不存在返回 DEST_MISSING（不调用 core.organize 的自动 mkdir）。
  - 破坏性操作（organize_photos / revert_changes）默认 dry_run，必须传匹配的 confirm_token。
  - 固定错误码：SRC_MISSING / PATH_NOT_ALLOWED / DEST_IN_SOURCE / DEST_MISSING /
    CONFIRM_TOKEN_MISMATCH / MAX_FILES_EXCEEDED / INVALID_PARAM / NOT_FOUND / IO_ERROR；
    工具返回统一 dict 含 ok/error 字段。
  - server 仅走 stdio，无任何网络外联。

用法：
    python mcp_server.py --allow D:/Photos --allow C:/Users/me/Pictures
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

from core import (
    APP_NAME,
    DATE_FMT,
    DEFAULT_LOG_KEEP,
    INCLUDE_VIDEO,
    PHOTO_EXTS,
    VIDEO_EXTS,
    collect_files,
    get_config_dir,
    get_logs_dir,
    list_logs as core_list_logs,
    load_config,
    organize,
    restore_from_log,
    write_organize_log,
)

VERSION = "0.3.2"
MAX_FILES_PER_RUN_DEFAULT = 5000

# ---- 固定错误码 ----
SRC_MISSING = "SRC_MISSING"
PATH_NOT_ALLOWED = "PATH_NOT_ALLOWED"
DEST_IN_SOURCE = "DEST_IN_SOURCE"
DEST_MISSING = "DEST_MISSING"
CONFIRM_TOKEN_MISMATCH = "CONFIRM_TOKEN_MISMATCH"
MAX_FILES_EXCEEDED = "MAX_FILES_EXCEEDED"
INVALID_PARAM = "INVALID_PARAM"
NOT_FOUND = "NOT_FOUND"
IO_ERROR = "IO_ERROR"

# ---- 系统目录禁区（Windows） ----
_FORBIDDEN_WINDOWS = (
    "C:\\Windows",
    "C:\\Program Files",
    "C:\\Program Files (x86)",
    "C:\\ProgramData",
    "C:\\Users\\Default",
)
_FORBIDDEN_POSIX = ("/etc", "/usr", "/bin", "/sbin", "/var", "/System", "/Library")

# ---- 白名单（CLI --allow 全局，main() 中注入） ----
_CLI_ALLOWED_ROOTS: list[str] = []
_CLI_MAX_FILES: Optional[int] = None

mcp = FastMCP("photo-organizer")


# ========== 安全模型 ==========

def _mcp_config_path() -> Path:
    """MCP 安全配置文件路径：平台规范配置目录下的 mcp_config.json。"""
    return get_config_dir() / "mcp_config.json"


def _load_mcp_config() -> dict:
    """读取 mcp_config.json；缺失/损坏返回空 dict。每次调用重新读盘实现热加载。"""
    p = _mcp_config_path()
    try:
        if not p.exists():
            return {}
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _effective_config() -> dict:
    """生效配置：allowed_roots + max_files_per_run。优先级 --allow > mcp_config.json > 默认。"""
    cfg = _load_mcp_config()
    if _CLI_ALLOWED_ROOTS:
        roots = [Path(x).expanduser() for x in _CLI_ALLOWED_ROOTS]
    else:
        raw = cfg.get("allowed_roots") or []
        roots = [Path(x).expanduser() for x in raw if isinstance(x, str) and x.strip()]
    if not roots:
        roots = [Path.home() / "Pictures"]
    try:
        max_files = int(_CLI_MAX_FILES or cfg.get("max_files_per_run") or MAX_FILES_PER_RUN_DEFAULT)
    except (TypeError, ValueError):
        max_files = MAX_FILES_PER_RUN_DEFAULT
    return {"allowed_roots": roots, "max_files_per_run": max(1, max_files)}


def _is_forbidden(p: Path) -> bool:
    """系统目录禁区检查（resolve 后前缀匹配）。"""
    try:
        rp = str(p.resolve()).rstrip(os.sep)
    except OSError:
        return True
    rp_lower = rp.lower()
    if os.name == "nt":
        for f in _FORBIDDEN_WINDOWS:
            fl = f.lower()
            if rp_lower == fl or rp_lower.startswith(fl + os.sep):
                return True
        return False
    if rp_lower == os.sep:  # 根目录 "/"
        return True
    for f in _FORBIDDEN_POSIX:
        fl = f.lower()
        if rp_lower == fl or rp_lower.startswith(fl + os.sep):
            return True
    return False


def _in_allowlist(p: Path) -> bool:
    """路径必须位于某个白名单根之下（或等于该根）。"""
    try:
        rp = p.resolve()
    except OSError:
        return False
    for root in _effective_config()["allowed_roots"]:
        try:
            rr = root.resolve()
        except OSError:
            continue
        if rp == rr or rp.is_relative_to(rr):
            return True
    return False


def _check_paths(source: str, dest: str) -> Optional[str]:
    """统一路径校验：返回错误码或 None（通过）。"""
    src = Path(source)
    dst = Path(dest)
    if not src.is_absolute() or not dst.is_absolute():
        return PATH_NOT_ALLOWED
    if _is_forbidden(src) or _is_forbidden(dst):
        return PATH_NOT_ALLOWED
    if not src.exists():
        return SRC_MISSING
    if not src.is_dir():
        return SRC_MISSING
    if not dst.exists():
        return DEST_MISSING  # 禁止自动创建目标目录
    if not dst.is_dir():
        return PATH_NOT_ALLOWED
    if not _in_allowlist(src) or not _in_allowlist(dst):
        return PATH_NOT_ALLOWED
    # dest 不得是 source 自身或其子目录（原地整理会产生 DEST_IN_SOURCE 冲突）
    try:
        sr = src.resolve()
        dr = dst.resolve()
        if dr == sr or dr.is_relative_to(sr):
            return DEST_IN_SOURCE
    except OSError:
        return IO_ERROR
    return None


# ========== plan 构建与 confirm_token ==========

def _confirm_token(plan: list[dict]) -> str:
    """confirm_token = SHA256(plan 稳定 JSON 序列化)。"""
    payload = json.dumps(plan, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _group_level(group: str) -> Optional[str]:
    return {"flat": None, "year": "Y", "year-month": "YM", "year-month-day": "YMD"}.get(group, None)


# ---- 日志名白名单（评审 #4）：仅允许 \w 与连字符，拒绝 ../ 等穿越写法 ----
_LOG_NAME_RE = re.compile(r"^[\w\-]+$")


def _log_stem(name: str) -> Optional[str]:
    """规范化日志名（可带 .jsonl 后缀），不合规返回 None。"""
    if not isinstance(name, str) or not name.strip():
        return None
    stem = name[:-6] if name.lower().endswith(".jsonl") else name
    return stem if _LOG_NAME_RE.match(stem) else None


def _public_stats(stats: dict) -> dict:
    """stats 转 JSON 安全结构（set 转 len、dict 保留），供 MCP 返回。"""
    out = {}
    for k, v in stats.items():
        out[k] = len(v) if isinstance(v, (set, list)) else v
    return out


def _build_plan(source: str, dest: str, recursive: bool, include_video: bool,
                group: str, date_format: str, on_conflict: str,
                photo_exts: Optional[list]) -> dict:
    """统一生成整理计划：preview 与 execute 共用 core.organize(dry_run=True)（评审 #3）。

    返回 {"ok": True, "plan", "stats", "total"} 或错误 dict。plan 即 core dry-run 生成的
    plan_move/plan_copy 动作记录（src/dst），confirm_token 以同一份 plan 为准，保证
    on_conflict=rename 时预览与执行的文件名完全一致；不再手搓重命名/分组逻辑。
    """
    err = _check_paths(source, dest)
    if err:
        return {"ok": False, "error": err}
    if on_conflict not in ("skip", "rename"):
        return {"ok": False, "error": INVALID_PARAM, "message": f"invalid on_conflict: {on_conflict!r}"}
    glevel = _group_level(group)
    if glevel is None and group != "flat":
        return {"ok": False, "error": INVALID_PARAM, "message": f"invalid group: {group!r}"}
    try:
        datetime.now().strftime(date_format)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": INVALID_PARAM, "message": f"bad date_format: {e}"}

    # 预扫文件数用于 max_files 上限校验（organize 内部不检查上限）
    src = Path(source)
    dst = Path(dest)
    supported: set = set(photo_exts) if photo_exts else set(PHOTO_EXTS)
    if include_video:
        supported |= set(VIDEO_EXTS)
    try:
        files = collect_files(src, dst, supported, recursive)
    except OSError as e:
        return {"ok": False, "error": IO_ERROR, "message": str(e)}

    max_files = _effective_config()["max_files_per_run"]
    if len(files) > max_files:
        return {"ok": False, "error": MAX_FILES_EXCEEDED, "total": len(files), "max": max_files}

    # 原地整理（source == dest）时跳过已有日期文件夹，其余场景关闭避免噪音（评审 #5）
    try:
        skip_inplace = src.resolve() == dst.resolve()
    except OSError:
        skip_inplace = False

    actions: list = []
    try:
        stats = organize(
            source,
            dest,
            dry_run=True,
            copy_mode=False,
            recursive=recursive,
            prefix_duplicates=(on_conflict == "rename"),
            include_video=include_video,
            date_fmt=date_format,
            group_levels=glevel,
            skip_existing_date_folders=skip_inplace,
            photo_exts=set(photo_exts) if photo_exts else None,
            include_hidden_trash=False,
            log_func=lambda item: None,
            actions=actions,
            pre_scanned=files,
        )
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": IO_ERROR, "message": str(e)}
    if stats is None:  # 兜底：core 不应返回 None（评审 #1）
        return {"ok": False, "error": IO_ERROR, "message": "organize returned no stats"}
    return {"ok": True, "plan": actions, "stats": stats, "total": len(actions)}


# ========== Tools ==========

@mcp.tool()
def preview_organize(
    source: str,
    dest: str,
    recursive: bool = False,
    include_video: bool = True,
    group: str = "flat",
    date_format: str = "%Y-%m-%d",
    on_conflict: str = "skip",
    photo_exts: Optional[list] = None,
) -> dict:
    """预览整理计划（只读）：扫描源目录，计算每个文件的归类目标，返回 plan 与 confirm_token。

    Args:
        source: 源文件夹绝对路径（必须位于白名单内）。
        dest: 目标文件夹绝对路径（必须已存在，禁止自动创建）。
        recursive: 是否递归扫描子文件夹。
        include_video: 是否同时整理视频。
        group: 分组方式 flat / year / year-month / year-month-day。
        date_format: flat 模式下日期文件夹名格式（strftime）。
        on_conflict: 重名冲突处理 skip / rename。
        photo_exts: 自定义图片扩展名列表（如 [".jpg", ".png"]），默认内置图片集。
    """
    r = _build_plan(source, dest, recursive, include_video, group, date_format, on_conflict, photo_exts)
    if not r["ok"]:
        return {"ok": False, "error": r["error"], **{k: v for k, v in r.items() if k not in ("ok", "error")}}
    return {
        "ok": True,
        "confirm_token": _confirm_token(r["plan"]),
        "total": r["total"],
        "plan": r["plan"],
        "skipped": r["stats"].get("skipped_files", []),
        "stats": _public_stats(r["stats"]),
    }


@mcp.tool()
def organize_photos(
    source: str,
    dest: str,
    recursive: bool = False,
    include_video: bool = True,
    group: str = "flat",
    date_format: str = "%Y-%m-%d",
    on_conflict: str = "skip",
    photo_exts: Optional[list] = None,
    dry_run: bool = True,
    copy: bool = False,
    confirm_token: Optional[str] = None,
) -> dict:
    """执行（或模拟）整理。默认 dry_run=True 只计算不移动；dry_run=False 必须传入与当前 plan 匹配的 confirm_token。

    Args:
        source / dest / recursive / include_video / group / date_format / on_conflict / photo_exts: 同 preview_organize。
        dry_run: True 仅预览统计；False 实际移动/复制。
        copy: 复制模式（原文件保留）。
        confirm_token: 来自 preview_organize 的 token；dry_run=False 时必填且必须匹配。
    """
    r = _build_plan(source, dest, recursive, include_video, group, date_format, on_conflict, photo_exts)
    if not r["ok"]:
        return {"ok": False, "error": r["error"], **{k: v for k, v in r.items() if k not in ("ok", "error")}}

    plan = r["plan"]
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "confirm_token": _confirm_token(plan),
            "total": r["total"],
            "moved": int(r["stats"].get("moved", 0)),
            "copied": int(r["stats"].get("copied", 0)),
            "skipped": int(r["stats"].get("skipped", 0)),
            "skipped_files": r["stats"].get("skipped_files", []),
            "failed": int(r["stats"].get("failed", 0)),
            "log_name": None,
            "failures": [],
        }

    token = _confirm_token(plan)
    if not confirm_token or confirm_token != token:
        return {"ok": False, "error": CONFIRM_TOKEN_MISMATCH, "message": "confirm_token does not match current plan"}

    # 执行：动作记录由 core 追加到 actions（move/copy），与预览 plan 同源同规则，
    # 保证 on_conflict=rename 时实际文件名与 preview 完全一致（评审 #3）
    actions: list = []
    failures: list = []

    def _log(item):
        code, params = item
        if code.startswith("err") or (params or {}).get("tags") and "error" in params["tags"]:
            failures.append({"code": code, "params": params})

    glevel = _group_level(group)
    try:
        # 原地整理（source == dest）时跳过已有日期文件夹，其余场景关闭避免噪音（评审 #5）
        skip_inplace = Path(source).resolve() == Path(dest).resolve()
        stats = organize(
            source,
            dest,
            dry_run=False,
            copy_mode=copy,
            recursive=recursive,
            prefix_duplicates=(on_conflict == "rename"),
            include_video=include_video,
            date_fmt=date_format,
            group_levels=glevel,
            skip_existing_date_folders=skip_inplace,
            photo_exts=set(photo_exts) if photo_exts else None,
            include_hidden_trash=False,
            log_func=_log,
            actions=actions,
        )
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": IO_ERROR, "message": str(e)}
    if stats is None:  # 兜底：core 不应返回 None（评审 #1）
        return {"ok": False, "error": IO_ERROR, "message": "organize returned no stats"}

    log_name = None
    try:
        meta = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "mode": "copy" if copy else "move",
            "src": source,
            "dest": dest,
            "recursive": recursive,
            "group": group,
            "date_fmt": date_format,
            "on_conflict": on_conflict,
            "include_video": include_video,
            "include_hidden_trash": False,
            "keep": str(DEFAULT_LOG_KEEP),
            "stats": {k: v for k, v in stats.items() if not isinstance(v, (set, list))},
        }
        # lines 已废弃（评审 #8）：不再收集日志行，传空列表
        log_path = write_organize_log(meta, [], actions, DEFAULT_LOG_KEEP)
        log_name = log_path.stem
    except Exception as e:  # noqa: BLE001
        failures.append({"code": "err_save_log", "params": {"err": str(e)}})

    return {
        "ok": True,
        "dry_run": False,
        "moved": int(stats.get("moved", 0)),
        "copied": int(stats.get("copied", 0)),
        "skipped": int(stats.get("skipped", 0)),
        "failed": int(stats.get("failed", 0)),
        "log_name": log_name,
        "failures": failures,
    }


@mcp.tool()
def list_logs(limit: int = 10) -> dict:
    """列出最近的操作清单（.jsonl），按修改时间倒序。"""
    try:
        logs = core_list_logs()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": IO_ERROR, "message": str(e)}
    result = []
    for p in logs[: max(1, int(limit))]:
        summary = {}
        try:
            first = p.read_text(encoding="utf-8").splitlines()[0]
            rec = json.loads(first)
            if rec.get("type") == "meta":
                summary = {
                    "time": rec.get("time"),
                    "mode": rec.get("mode"),
                    "src": rec.get("src"),
                    "dest": rec.get("dest"),
                    "action_count": rec.get("action_count"),
                    "stats": rec.get("stats"),
                }
        except Exception:  # noqa: BLE001
            pass
        result.append({
            "name": p.stem,
            "path": str(p),
            "mtime": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "size": p.stat().st_size,
            "summary": summary,
        })
    return {"ok": True, "logs": result}


@mcp.tool()
def show_log(name: str) -> dict:
    """查看指定操作清单明细。name 为日志名（stem，可带 .jsonl）。"""
    stem = _log_stem(name)
    if stem is None:
        return {"ok": False, "error": NOT_FOUND, "message": f"invalid log name: {name!r}"}
    p = get_logs_dir() / f"{stem}.jsonl"
    if not p.exists():
        return {"ok": False, "error": NOT_FOUND, "message": f"log not found: {stem}"}
    meta = {}
    actions = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("type") == "meta":
                meta = rec
            else:
                actions.append(rec)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": IO_ERROR, "message": str(e)}
    return {"ok": True, "name": stem, "meta": meta, "actions": actions, "count": len(actions)}


@mcp.tool()
def revert_changes(name: str, dry_run: bool = True) -> dict:
    """按操作清单还原一次整理。默认 dry_run=True 仅预览统计；False 实际还原（不覆盖已有文件）。

    Args:
        name: 日志名（stem，可带 .jsonl）。
        dry_run: True 预览（解析清单统计）；False 执行还原。
    """
    stem = _log_stem(name)
    if stem is None:
        return {"ok": False, "error": NOT_FOUND, "message": f"invalid log name: {name!r}"}
    p = get_logs_dir() / f"{stem}.jsonl"
    if not p.exists():
        return {"ok": False, "error": NOT_FOUND, "message": f"log not found: {stem}"}

    if dry_run:
        move_count = copy_count = 0
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                rec = json.loads(line)
                if rec.get("type") == "move":
                    move_count += 1
                elif rec.get("type") == "copy":
                    copy_count += 1
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": IO_ERROR, "message": str(e)}
        return {"ok": True, "dry_run": True, "name": stem, "move_count": move_count, "copy_count": copy_count,
                "total": move_count + copy_count}

    try:
        stats = restore_from_log(stem)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": IO_ERROR, "message": str(e)}
    if isinstance(stats, dict) and stats.get("error"):
        code, params = stats["error"]
        if code == "err_no_actions":
            return {"ok": False, "error": NOT_FOUND, "message": f"log not found: {stem}"}
        return {"ok": False, "error": IO_ERROR, "message": str(params) if isinstance(params, dict) else code}
    return {"ok": True, "dry_run": False, "name": stem,
            "moved": int(stats.get("moved", 0)),
            "removed": int(stats.get("removed", 0)),
            "skipped": int(stats.get("skipped", 0)),
            "missing": int(stats.get("missing", 0))}


@mcp.tool()
def get_config() -> dict:
    """返回当前生效的 MCP 安全配置：白名单根目录、单次最大文件数、server 版本。"""
    cfg = _effective_config()
    return {
        "ok": True,
        "allowed_roots": [str(p) for p in cfg["allowed_roots"]],
        "max_files_per_run": cfg["max_files_per_run"],
        "version": VERSION,
    }


# ========== 入口 ==========

def main() -> None:
    parser = argparse.ArgumentParser(description="photo-organizer MCP Server (stdio)")
    parser.add_argument("--allow", action="append", default=[],
                        help="允许访问的根目录（可多次；优先级高于 mcp_config.json 与默认 ~/Pictures）")
    parser.add_argument("--max-files", type=int, default=None,
                        help="单次整理最大文件数（覆盖 mcp_config.json 的 max_files_per_run）")
    args = parser.parse_args()

    global _CLI_ALLOWED_ROOTS, _CLI_MAX_FILES
    _CLI_ALLOWED_ROOTS = [x for x in args.allow if x.strip()]
    _CLI_MAX_FILES = args.max_files

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
