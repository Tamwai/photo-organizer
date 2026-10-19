# Photo Organizer 照片按拍摄日期归类脚本

将照片/视频按拍摄日期自动归类到 `YYYY-MM-DD` 文件夹的小工具。命令行版与图形界面版已合并为单一脚本。

## 版本

当前版本 **v0.1.0**。可用 `python photo_organizer.py --version` 查看。

## 功能

- 三级日期判定：EXIF 拍摄日期 → 文件名日期 → 文件创建/修改时间
- 支持常见图片格式（JPG/PNG/HEIC 等）与视频格式（MP4/MOV 等）
- 支持文件名日期多种写法：`IMG_20260806_144736.jpg`、`Screenshot_2026-08-09-05-03-57.jpg`、`2026.08.06.jpg`、`mmexport1785419041663.jpg`（Unix 时间戳）等
- 重名文件自动跳过，不覆盖
- 单文件出错不影响整体，末尾汇总报告

## 用法

### 图形界面

```bash
python photo_organizer.py          # 无参数运行（或双击 EXE），直接打开图形界面
python photo_organizer.py --gui    # 强制图形界面
# 或
python photo_organizer.py -g
```

界面可一键选择目录、勾选是否整理视频、切换模拟预览，日志实时滚动显示。

### 命令行

```bash
# 整理指定目录
python photo_organizer.py D:\某个文件夹

# 模拟运行，只预览归类结果，不移动任何文件
python photo_organizer.py D:\某个文件夹 --dry-run

# 模拟整理配置区默认目录
python photo_organizer.py --dry-run
```

## 依赖

- Python 3.10+
- Pillow（可选，用于读取 EXIF 拍摄日期；未安装时自动降级为文件名/文件时间判定）

```bash
pip install pillow
```

## 配置

编辑脚本顶部 `配置区`：

- `TARGET_DIR`：默认整理的目标目录（自动取程序所在目录，不写死个人路径，也可手动修改）
- `INCLUDE_VIDEO`：是否同时整理视频
- `DATE_FMT`：日期文件夹格式（默认 `%Y-%m-%d`）

## 注意

- 首次使用建议先跑 `--dry-run` 预览归类结果
- 脚本只处理目标目录**顶层**文件，不递归子目录
