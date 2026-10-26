# Photo Organizer 照片按拍摄日期归类工具

> ⚠️ 注意：本包为 `photo-organizer-mcp`，与 PyPI 上 Supporterino 维护的 `photo-organizer` / `photo_organizer` 无任何关联，请认准包名安装。

将照片/视频按拍摄日期自动归类到日期文件夹的小工具。支持命令行（CLI）与图形界面（GUI），二者合并为单一脚本。

## 版本

当前版本 **v0.4.0**。可用 `python photo_organizer.py --version` 查看。

更新历史见 [CHANGELOG.md](CHANGELOG.md)。

## 功能

- **双语界面与语言切换**：界面与输出支持中文/英文，首次启动跟随系统语言（默认 zh），GUI 顶部一键切换，CLI 可用 `--lang zh|en` 指定；语言选择持久化保存
- 三级日期判定：EXIF 拍摄日期 → 文件名日期 → 文件修改时间（Windows 另含创建时间，取较早者）；可用 `--no-exif` 跳过 EXIF 读取，大批量整理时显著提速
- 支持常见图片格式（JPG/PNG/HEIC 等，可在界面勾选/自定义扩展名）与视频格式（MP4/MOV 等），视频默认一并整理，可用 `--no-video` 关闭
- 支持文件名日期多种写法：`IMG_20260806_144736.jpg`、`Screenshot_2026-08-09-05-03-57.jpg`、`2026.08.06.jpg`、`mmexport1785419041663.jpg`（仅限 `mmexport`/`wx_camera_` 等已知拍摄前缀的 10 位时间戳）等；日期判定带数字边界保护，避免误判普通数字串
- 分组方式可选：**单层日期文件夹**（如 `2026-10-20`），或**按年 / 按年+月 / 按年+月+日**嵌套分组，各层文件夹名格式可单独自定义（strftime）
- 嵌套分组时可勾选「分组时创建日期文件夹」，在最内层再按日期格式建文件夹
- 可选**递归扫描**源文件夹的所有子文件夹；目标文件夹在源内部时自动排除，不会重复扫描已归类文件
- **跳过已有日期文件夹**默认开启：仅原地整理（源=目标且递归）时跳过已存在的日期文件夹，避免重复处理；关闭用 `--no-skip-existing-date-folders`
- **可整理隐藏文件与 .trashed 文件**：默认跳过隐藏文件（Windows 隐藏属性或文件名以点开头）与 `.trashed-` 开头的 Android 回收站文件，避免误整理；需处理时勾选「包含隐藏文件和 .trashed 文件」或用 `--include-hidden-trash` 开启
- 重名冲突默认**跳过**，可切换为**自动加来源文件夹名前缀**（如 `trip_IMG_20260806.jpg`）
- 默认**移动**文件到日期文件夹，可切换**复制模式**（原文件保留）
- **模拟运行**独立按钮/参数：只预览归类结果，不移动、不复制任何文件
- 单文件出错不影响整体，末尾汇总报告（含图片/视频分类与跳过统计）
- **日志着色**：整理日志按事件类型着色（错误红 / 警告橙 / 跳过黄 / 移动绿 / 复制蓝 / 重命名紫 / 模拟青 / 信息蓝），CLI 彩色标签默认开启，可用 `--mono` 关闭
- **操作清单（.jsonl）**：每次整理可保存动作清单，支持查看明细 / 删除所选 / 导出 / 导入 / 一键还原变更 / 清空
- **清理数据**：一键删除配置文件与全部操作清单（GUI 按钮或 `--clear-data`）
- **清理半成品**：手动清理上次中断遗留的 `.photo-org-tmp` 半成品文件（GUI「清理半成品」按钮或 `--cleanup-parts`，需确认后执行）
- 配置持久化：界面设置自动保存到配置目录 `config.json`，支持一键恢复默认
- 界面悬停提示（ToolTip）说明各项含义；「格式说明」弹窗列出 strftime 记号与常用组合
- **标题栏版本号**：窗口标题显示当前版本（如「照片按拍摄日期归类 v0.2.1」）

## 图形界面

```bash
python photo_organizer.py          # 无参数运行（或双击 EXE），直接打开图形界面
python photo_organizer.py --gui    # 强制图形界面
# 或
python photo_organizer.py -g
```

界面顶部提供**语言切换**（中文 / English），切换即时生效并持久化保存。

界面支持：选择源/目标文件夹（输入行带「清除记录」按钮，一键清空对应路径）、勾选整理视频、切换移动/复制模式、包含子文件夹、重名加前缀、分组方式与日期格式、模拟运行/开始整理、恢复默认设置；下方日志区实时滚动显示整理过程与结果（按事件类型着色）。

**操作清单区**：勾选「保存操作清单」并设置保留份数（默认 30 份）后，每次整理会在 `%APPDATA%\photo-organizer\logs\` 保存一份 `.jsonl` 动作清单。下拉框选择历史清单后，日志窗口顶部提示选中项与动作条数；「操作记录」按钮在日志窗口显示完整明细；支持导出 / 导入 / 还原变更 / 删除所选 / 清空清单。还原规则：移动模式的文件移回原位置、复制模式的副本删除，仅当目标文件仍存在且原位置已空出时执行，绝不覆盖现有文件。

**数据清理区**：提供「清理半成品」按钮（手动清理目标目录下的 `.photo-org-tmp` 半成品）与「清理数据」按钮（删除配置文件与全部操作清单），均需二次确认后执行。

## 命令行

```bash
# 无参数运行会打开图形界面；指定目录即进入命令行模式
# 指定源文件夹，归类到默认目标文件夹
python photo_organizer.py D:\照片源目录
# 指定源文件夹与目标文件夹
python photo_organizer.py D:\照片源目录 E:\整理后存放目录
# 递归扫描源文件夹的所有子文件夹
python photo_organizer.py D:\照片源目录 E:\整理后存放目录 -r
# 重名冲突时自动加来源文件夹名前缀（默认跳过）
python photo_organizer.py D:\照片源目录 E:\整理后存放目录 -r --prefix
# 模拟运行，只预览归类结果，不移动任何文件
python photo_organizer.py D:\照片源目录 --dry-run
# 复制模式：文件复制到日期文件夹，原文件保留
python photo_organizer.py D:\照片源目录 --copy
# 按年+月嵌套分组，年层/月层文件夹名自定义
python photo_organizer.py D:\照片源目录 E:\整理后存放目录 --group year-month --year-format "%Y年" --month-format "%m月"
# 嵌套分组最内层再建日期文件夹
python photo_organizer.py D:\照片源目录 E:\整理后存放目录 --group year-month-day --date-folder --date-format "%Y-%m-%d"
# 跳过已有日期文件夹默认开启；以下示例仅原地整理时关闭它
python photo_organizer.py D:\照片源目录 -r --no-skip-existing-date-folders
# 不整理视频
python photo_organizer.py D:\照片源目录 --no-video
# 整理隐藏文件与 .trashed 文件（默认跳过，需显式开启）
python photo_organizer.py D:\照片源目录 --include-hidden-trash
# 指定界面/输出语言（zh 或 en）
python photo_organizer.py D:\照片源目录 --lang en
# 本次运行保存操作清单（默认不保存）；保留最近 30 份
python photo_organizer.py D:\照片源目录 --save-log --log-keep 30
# 自定义图片扩展名 / 临时排除某扩展名（可多次）
python photo_organizer.py D:\照片源目录 --add-photo-ext .raw --remove-photo-ext .gif
# 单色输出（关闭彩色标签，默认彩色开启）
python photo_organizer.py D:\照片源目录 --mono
# 跳过 EXIF 读取，直接按文件名日期/文件时间判定（大批量时更快）
python photo_organizer.py D:\照片源目录 --no-exif
# 清理数据 / 清空操作清单为不可逆操作，需附带 --yes 确认
python photo_organizer.py --clear-data --yes
python photo_organizer.py --clear-logs --yes
# 手动清理目标目录下的 .photo-org-tmp 半成品（逐条输出删除）
python photo_organizer.py D:\照片源目录 --cleanup-parts
```

> 说明：命令行模式下 `python photo_organizer.py`（无参数）会打开图形界面；想用命令行整理默认目录，请配合 `--dry-run` 等选项或显式传入目录。

### CLI 参数表

| 参数 | 说明 | 默认值 |
|---|---|---|
| `source_dir` | 源文件夹（从中读取照片）；不提供且无其它参数时自动打开图形界面 | — |
| `dest_dir` | 目标文件夹（照片按日期归类到此处）；不提供时使用配置区 DEST_DIR；不存在时自动创建（盘根除外） | — |
| `-g, --gui` | 强制启动图形界面 | 关闭 |
| `--version` | 显示版本号 | — |
| `--dry-run` | 模拟运行，只预览归类结果，不移动任何文件（命令行模式） | 关闭 |
| `--copy` | 复制模式：文件复制到日期文件夹，原文件保留 | 移动 |
| `-r, --recursive` | 递归扫描源文件夹的所有子文件夹 | 关闭 |
| `--prefix` | 重名冲突时自动加来源文件夹名前缀 | 跳过 |
| `--skip-existing-date-folders` | 仅原地整理（源=目标）时生效：跳过源目录下已有的日期文件夹，其中文件视为已整理（不判断日期） | **开启** |
| `--no-skip-existing-date-folders` | 关闭「跳过已有日期文件夹」（默认开启，加本参数可关闭） | — |
| `--date-folder` | 层级分组时，在分组得到的最内层文件夹里再建日期文件夹 | 关闭 |
| `--date-format FMT` | 单层日期文件夹格式（strftime），如 `%Y` 按年、`%Y-%m` 按月、`%Y-%m-%d` 按日 | `%Y-%m-%d` |
| `--group LEVEL` | 层级嵌套分组：`year`=先按年建文件夹、`year-month`=年>月、`year-month-day`=年>月>日 | 单层 |
| `--year-format FMT` | 层级分组时年层文件夹名格式（strftime） | `%Y` |
| `--month-format FMT` | 层级分组时月层文件夹名格式（strftime），可用 `%Y年%m月` 等 | `%m` |
| `--day-format FMT` | 层级分组时日层文件夹名格式（strftime） | `%d` |
| `--add-photo-ext EXT` | 额外增加要整理的图片扩展名（可多次使用），如 `--add-photo-ext .raw` | — |
| `--remove-photo-ext EXT` | 临时排除图片扩展名（可多次使用，仅本次运行生效），如 `--remove-photo-ext .gif` | — |
| `--exclude REGEX` | 按正则排除文件：对源文件的相对路径执行 `re.search`，命中即跳过（与扩展名白名单叠加生效，先过类型白名单再按名字规则过滤），如 `--exclude "^2025\|\.tmp$"` | 不启用 |
| `--all-extensions` | 跳过扩展名白名单过滤，所有文件类型均可整理（可配合 `--exclude` 全量精确整理） | 关闭 |
| `--delete-duplicates` | 目标目录已存在内容一致文件时删除源文件，避免重复归档；判定先比大小、后比哈希（MD5，64KB 分块），大小不同直接放行；`--dry-run` 模拟运行仅报告不删除 | 关闭 |
| `--no-video` | 不整理视频 | 同时整理图片与视频 |
| `--include-hidden-trash` | 整理隐藏文件与 .trashed 文件（Android 回收站文件，文件名以 `.trashed-` 开头）；隐藏文件含 Windows 隐藏属性或文件名以点开头 | 不处理（默认跳过） |
| `--no-exif` | 跳过 EXIF 读取，直接按文件名日期/文件时间判定（大批量整理时更快） | 读取 EXIF |
| `--lang zh\|en` | 界面/输出语言：`zh` 或 `en` | 按配置或 `zh` |
| `--save-log` | 保存操作清单到 jsonl | 不保存（需显式指定） |
| `--no-save-log` | 不保存操作清单 | 默认即不保存 |
| `--log-keep N` | 保留最近 N 份操作清单 | `30` |
| `--mono` | 单色输出（关闭彩色标签） | 彩色开启 |
| `--reset-config` | 恢复默认配置（删除配置文件） | — |
| `--list-logs` | 列出最近保存的操作清单 | — |
| `--show-log NAME` | 查看指定操作清单明细（动态解析为可读标签行） | — |
| `--rollback NAME` | 按指定操作清单还原变更（移动移回，复制删除副本） | — |
| `--clear-logs` | 清空全部操作清单（逐条输出删除，需 `--yes` 确认） | — |
| `--clear-data` | 清理数据：删除配置文件与全部操作清单（需 `--yes` 确认） | — |
| `--yes` | 与 `--clear-data` / `--clear-logs` 配合的显式确认开关；不附带时拒绝执行并提示 | 关闭 |
| `--cleanup-parts` | 手动清理目标目录下的半成品文件（`.photo-org-tmp`，逐条输出删除） | — |

## 依赖

- Python 3.10+
- Pillow（可选，用于读取 EXIF 拍摄日期；未安装时自动降级为文件名/文件时间判定）

```bash
pip install -r requirements.txt
```

> requirements.txt 固定 `pillow==12.3.0`：Windows 开启 Smart App Control 时，Pillow 13 可能报 `DLL load failed`，12.3.0 为受信誉保护的可用版本；若未开启 Smart App Control，也可自行 `pip install pillow` 安装最新版。

## 配置

首次运行后界面设置会持久化到配置目录 `%APPDATA%\photo-organizer\config.json`（含源/目标文件夹、分组方式、日期格式、是否整理视频、是否包含隐藏/.trashed 文件、复制/移动模式、操作清单保留份数、界面语言等），无需每次重新设置；界面提供「恢复默认设置」一键还原，也可用 `--reset-config` 恢复默认。

默认值细节：操作清单保留 `30` 份（`--log-keep 30`）；默认**不**保存操作清单（需 `--save-log` 显式开启）；日志彩色输出默认**开启**（`--mono` 关闭）；「跳过已有日期文件夹」默认**开启**（仅原地整理时生效，`--no-skip-existing-date-folders` 关闭）；「包含隐藏文件和 .trashed 文件」默认**不勾选**（需 GUI 勾选或 `--include-hidden-trash` 开启）。

## MCP 接入

photo-organizer 提供 MCP（Model Context Protocol）服务器，供 AI 客户端（如 Claude Desktop、支持 MCP 的编辑器/助手）安全地调用整理能力：预览整理计划、执行整理、查看与还原操作清单。既有 CLI 与 GUI 功能不受影响。

### 安装

```bash
pip install photo-organizer-mcp
```

仓库内开发使用可执行 `pip install -e .`。

### 客户端配置

在客户端 MCP 配置（如 `claude_desktop_config.json` 的 `mcpServers` 字段）中添加：

```json
{
  "mcpServers": {
    "photo-organizer": {
      "command": "photo-organizer-mcp",
      "args": ["--allow", "C:/Users/你的用户名/Pictures", "--allow", "D:/照片库"],
      "type": "stdio"
    }
  }
}
```

`--allow` 可多次传入，声明允许访问的根目录；未传时依次回退到配置文件（见下）与默认 `~/Pictures`。

> **Windows 提示**：若客户端启动 `photo-organizer-mcp` 命令时报 `No module named 'mcp_server'`（部分客户端/进程管理器环境下 pip 生成的启动器找不到 Python site-packages），可改用 Python 解释器直接运行脚本：

```json
{
  "mcpServers": {
    "photo-organizer": {
      "command": "C:/Python311/python.exe",
      "args": ["C:/Python311/Lib/site-packages/mcp_server.py", "--allow", "D:/照片库"],
      "type": "stdio"
    }
  }
}
```

将示例中的路径替换为实际 Python 安装路径与 `site-packages` 下的 `mcp_server.py` 即可，功能完全一致。

### 工具

| 工具 | 说明 |
|---|---|
| `preview_organize` | 预览整理计划（只读）：扫描源目录并计算归类目标，返回计划清单与确认令牌 |
| `organize_photos` | 执行整理；默认模拟运行，实际移动/复制需传入匹配的确认令牌 |
| `list_logs` | 列出最近的操作清单 |
| `show_log` | 查看指定操作清单明细 |
| `revert_changes` | 按操作清单还原一次整理（默认预览，不实际还原） |
| `get_config` | 返回当前生效的访问白名单、单次最大文件数与版本 |

### 安全机制

- **目录白名单**：源/目标路径必须位于白名单根目录之下，越界返回 `PATH_NOT_ALLOWED`；目标目录必须已存在，禁止自动创建
- **不支持原地整理**：MCP 模式要求 `source` 与 `dest` 为不同目录（`source == dest` 或其子目录时返回 `DEST_IN_SOURCE`）；原地整理请使用 CLI 或 GUI
- **确认令牌（confirm_token）**：`preview_organize` 与 `organize_photos` 共用同一份 `core.organize(dry_run=True)` 生成的计划（含重命名文件名），令牌基于该计划生成，确保预览与执行完全一致
- **默认模拟运行**：破坏性操作默认 `dry_run=true`，仅统计不落盘
- **固定错误码**：统一返回 `SRC_MISSING` / `PATH_NOT_ALLOWED` / `DEST_MISSING` / `DEST_IN_SOURCE` / `CONFIRM_TOKEN_MISMATCH` / `MAX_FILES_EXCEEDED` / `INVALID_PARAM` / `NOT_FOUND` / `IO_ERROR`
- **无网络外联**：服务器不发起任何网络请求

### 可选配置

配置文件位于 `%APPDATA%\photo-organizer\mcp_config.json`（macOS/Linux 为对应平台配置目录），运行时按修改时间热加载，无需重启：

```json
{
  "allowed_roots": ["C:/Users/你的用户名/Pictures", "D:/照片库"],
  "max_files_per_run": 5000
}
```

- `allowed_roots`：访问白名单根目录列表；`--allow` 启动参数优先于此配置
- `max_files_per_run`：单次整理最大文件数上限（默认 5000），超过返回 `MAX_FILES_EXCEEDED`

## 许可证

[MIT License](LICENSE)
