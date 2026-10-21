# Photo Organizer 照片按拍摄日期归类工具

将照片/视频按拍摄日期自动归类到日期文件夹的小工具。支持命令行（CLI）与图形界面（GUI），二者合并为单一脚本。

## 版本

当前版本 **v0.2.0**。可用 `python photo_organizer.py --version` 查看。

## 功能

- **双语界面与语言切换**：界面与输出支持中文/英文，首次启动跟随系统语言（默认 zh），GUI 顶部一键切换，CLI 可用 `--lang zh|en` 指定；语言选择持久化保存
- 三级日期判定：EXIF 拍摄日期 → 文件名日期 → 文件创建/修改时间
- 支持常见图片格式（JPG/PNG/HEIC 等，可在界面勾选/自定义扩展名）与视频格式（MP4/MOV 等），视频默认一并整理，可用 `--no-video` 关闭
- 支持文件名日期多种写法：`IMG_20260806_144736.jpg`、`Screenshot_2026-08-09-05-03-57.jpg`、`2026.08.06.jpg`、`mmexport1785419041663.jpg`（Unix 时间戳）等
- 分组方式可选：**单层日期文件夹**（如 `2026-10-20`），或**按年 / 按年+月 / 按年+月+日**嵌套分组，各层文件夹名格式可单独自定义（strftime）
- 嵌套分组时可勾选「分组时创建日期文件夹」，在最内层再按日期格式建文件夹
- 可选**递归扫描**源文件夹的所有子文件夹；目标文件夹在源内部时自动排除，不会重复扫描已归类文件
- **跳过已有日期文件夹**默认开启：仅原地整理（源=目标且递归）时跳过已存在的日期文件夹，避免重复处理；关闭用 `--no-skip-existing-date-folders`
- 重名冲突默认**跳过**，可切换为**自动加来源文件夹名前缀**（如 `trip_IMG_20260806.jpg`）
- 默认**移动**文件到日期文件夹，可切换**复制模式**（原文件保留）
- **模拟运行**独立按钮/参数：只预览归类结果，不移动、不复制任何文件
- 单文件出错不影响整体，末尾汇总报告（含图片/视频分类与跳过统计）
- **日志着色**：整理日志按事件类型着色（错误红 / 警告橙 / 跳过黄 / 移动绿 / 复制蓝 / 重命名紫 / 模拟青 / 信息蓝），CLI 彩色标签默认开启，可用 `--mono` 关闭
- **操作清单（.jsonl）**：每次整理可保存动作清单，支持查看明细 / 删除所选 / 导出 / 导入 / 一键还原变更 / 清空
- **清理数据**：一键删除配置文件与全部操作清单（GUI 按钮或 `--clear-data`）
- **清理半成品**：启动时自动清理上次中断遗留的 `.po-tmp` 半成品（界面可勾选关闭）；也可手动清理（GUI 按钮或 `--cleanup-parts`）
- 配置持久化：界面设置自动保存到配置目录 `config.json`，支持一键恢复默认
- 界面悬停提示（ToolTip）说明各项含义；「格式说明」弹窗列出 strftime 记号与常用组合
- **标题栏版本号**：窗口标题显示当前版本（如「照片按拍摄日期归类 v0.2.0」）

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

**数据清理区**：提供「清理半成品」按钮（手动清理目标目录下的 `.po-tmp` 半成品）与「清理数据」按钮（删除配置文件与全部操作清单），均需二次确认后执行。

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
# 指定界面/输出语言（zh 或 en）
python photo_organizer.py D:\照片源目录 --lang en
# 本次运行保存操作清单（默认不保存）；保留最近 30 份
python photo_organizer.py D:\照片源目录 --save-log --log-keep 30
# 自定义图片扩展名 / 临时排除某扩展名（可多次）
python photo_organizer.py D:\照片源目录 --add-photo-ext .raw --remove-photo-ext .gif
# 单色输出（关闭彩色标签，默认彩色开启）
python photo_organizer.py D:\照片源目录 --mono
# 手动清理目标目录下的 .po-tmp 半成品（逐条输出删除）
python photo_organizer.py D:\照片源目录 --cleanup-parts
```

> 说明：命令行模式下 `python photo_organizer.py`（无参数）会打开图形界面；想用命令行整理默认目录，请配合 `--dry-run` 等选项或显式传入目录。

### CLI 参数表

| 参数 | 说明 | 默认值 |
|---|---|---|
| `source_dir` | 源文件夹（从中读取照片）；不提供且无其它参数时自动打开图形界面 | — |
| `dest_dir` | 目标文件夹（照片按日期归类到此处）；不提供时使用配置区 DEST_DIR | — |
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
| `--no-video` | 不整理视频 | 同时整理图片与视频 |
| `--lang zh\|en` | 界面/输出语言：`zh` 或 `en` | 按配置或 `zh` |
| `--save-log` | 保存操作清单到 jsonl | 不保存（需显式指定） |
| `--no-save-log` | 不保存操作清单 | 默认即不保存 |
| `--log-keep N` | 保留最近 N 份操作清单 | `30` |
| `--mono` | 单色输出（关闭彩色标签） | 彩色开启 |
| `--reset-config` | 恢复默认配置（删除配置文件） | — |
| `--list-logs` | 列出最近保存的操作清单 | — |
| `--show-log NAME` | 查看指定操作清单明细（动态解析为可读标签行） | — |
| `--rollback NAME` | 按指定操作清单还原变更（移动移回，复制删除副本） | — |
| `--clear-logs` | 清空全部操作清单（逐条输出删除） | — |
| `--clear-data` | 清理数据：删除配置文件与全部操作清单 | — |
| `--cleanup-parts` | 手动清理目标目录下的半成品文件（`.po-tmp`，逐条输出删除） | — |

## 依赖

- Python 3.10+
- Pillow（可选，用于读取 EXIF 拍摄日期；未安装时自动降级为文件名/文件时间判定）

```bash
pip install pillow
```

> 若在 Windows 上报 `DLL load failed` 且系统开启了 Smart App Control，建议安装 **Pillow 12.3.0**（受信誉保护的旧版）：`pip install pillow==12.3.0`

## 配置

首次运行后界面设置会持久化到配置目录 `%APPDATA%\photo-organizer\config.json`（含源/目标文件夹、分组方式、日期格式、是否整理视频、复制/移动模式、操作清单保留份数、界面语言等），无需每次重新设置；界面提供「恢复默认设置」一键还原，也可用 `--reset-config` 恢复默认。

默认值细节：操作清单保留 `30` 份（`--log-keep 30`）；默认**不**保存操作清单（需 `--save-log` 显式开启）；日志彩色输出默认**开启**（`--mono` 关闭）；「跳过已有日期文件夹」默认**开启**（仅原地整理时生效，`--no-skip-existing-date-folders` 关闭）。

## 许可证

[MIT License](LICENSE)，无传染性，可自由使用、修改、商用，保留版权声明即可。
