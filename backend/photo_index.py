# -*- coding: utf-8 -*-
"""照片索引 — step3 照片处理期间项目内全部照片的唯一真相层。

职责:
  1. 构建期: 扫描项目内全部照片（排除隐藏目录 / 废弃物 / 视频素材），
     物理重命名为简短名 P0001/P0002/...，并把记录写入项目根 index.json
  2. 真相层: index.json 记录永远反映照片的真实信息（文件名 name + 相对路径
     path 随移动/重命名即时更新；time / author 固化不变）
  3. 事件同步: 工具执行完 移动/重命名 后调用 sync_index_from_operations
     更新记录——用动作的 from→to 直接定位并改写 name/path，无其他机制

记录字段: {"name", "path", "time", "author"}
  name   — 当前文件名（如 P0001.jpg / 三年二班_班牌.jpg）
  path   — 相对「图片素材」目录、正斜杠、含文件名（唯一真实位置；AI 与工具
           均以此为基准。构建期照片在作者文件夹时暂用项目根基准，提取后同步归一）
  time   — 拍摄时间（构建期从原始文件名解析；缺失为 None）
  author — 作者（构建期从所在作者文件夹反查 id.json）
"""

import json
import logging
import os
import re
import shutil
from datetime import datetime

from media import PHOTO_EXTS, VIDEO_EXTS

logger = logging.getLogger(__name__)

INDEX_FILE = "index.json"

# 文件名时间戳: 2025_10_26_19_26_27_0102 → 提取前 5 段
_TS_RE = re.compile(r"^(\d{4})_(\d{2})_(\d{2})_(\d{2})_(\d{2})")

# 默认排除的顶层目录
TOP_EXCLUDE = {"废弃物", "视频素材"}

# step3 前置提取的目标顶层目录
PHOTO_DIR = "图片素材"
VIDEO_DIR = "视频素材"


def _skip_dir(name: str) -> bool:
    return name.startswith(".") or name in TOP_EXCLUDE


def _parse_ts(name: str) -> str | None:
    """从文件名解析拍摄时间: '2025_10_26_19_26_27_0102' → '2025-10-26 19:26'。"""
    m = _TS_RE.match(name)
    if not m:
        return None
    y, mo, d, h, mi = (m.group(i) for i in range(1, 6))
    return f"{y}-{mo}-{d} {h}:{mi}"


def _rel(project_dir: str, abs_path: str) -> str:
    """绝对路径 → 项目相对路径（正斜杠）。"""
    return os.path.relpath(abs_path, project_dir).replace("\\", "/")


def _photos_real(project_dir: str) -> str:
    """「图片素材」目录的 realpath。"""
    return os.path.realpath(os.path.join(project_dir, PHOTO_DIR))


def _index_rel(project_dir: str, abs_path: str) -> str:
    """绝对路径 → index 记录路径。

    照片位于「图片素材」内 → 相对它的路径（基准）；否则（构建期仍在
    作者文件夹）→ 相对项目根，待提取后由 sync 归一。
    """
    abs_path = os.path.realpath(abs_path)
    photos_real = _photos_real(project_dir)
    if abs_path == photos_real or abs_path.startswith(photos_real + os.sep):
        return os.path.relpath(abs_path, photos_real).replace("\\", "/")
    return os.path.relpath(abs_path, os.path.realpath(project_dir)).replace("\\", "/")


def _rel_photos(project_dir: str, abs_path: str) -> str:
    """绝对路径 → 相对「图片素材」的路径（提取后 index 基准）。"""
    return os.path.relpath(os.path.realpath(abs_path), _photos_real(project_dir)).replace("\\", "/")


def _path_abs(project_dir: str, rel: str) -> str | None:
    """index 记录路径 → 磁盘绝对路径。

    兼容两种基准：带 图片素材/ 前缀（旧数据）与相对 图片素材（当前基准）。
    """
    if not rel:
        return None
    rel_os = rel.replace("/", os.sep)
    if rel == PHOTO_DIR or rel.startswith(PHOTO_DIR + "/"):
        return os.path.join(project_dir, rel_os)
    p1 = os.path.join(project_dir, rel_os)
    if os.path.exists(p1):
        return p1
    return os.path.join(_photos_real(project_dir), rel_os)


def scan_project_photos(project_dir: str) -> list[str]:
    """扫描项目内全部照片绝对路径（跳过隐藏/废弃物/视频素材）。"""
    photos = []
    try:
        for entry in os.scandir(project_dir):
            if not entry.is_dir():
                continue
            if _skip_dir(entry.name):
                continue
            for root, dirs, files in os.walk(entry.path):
                dirs[:] = [d for d in dirs if not _skip_dir(d)]
                for fn in files:
                    if os.path.splitext(fn)[1].lower() in PHOTO_EXTS:
                        photos.append(os.path.join(root, fn))
    except Exception as e:
        logger.exception("扫描照片失败: %s", e)
    return sorted(photos)


def _normalize_index_entries(entries: list[dict] | None) -> list[dict] | None:
    """兼容旧基准：把 图片素材/xxx 前缀归一为相对「图片素材」的路径（新基准）。"""
    if not entries:
        return entries
    for e in entries:
        if not isinstance(e, dict):
            continue
        p = (e.get("path") or "").replace("\\", "/")
        if p == PHOTO_DIR:
            e["path"] = ""
        elif p.startswith(PHOTO_DIR + "/"):
            e["path"] = p[len(PHOTO_DIR) + 1:]
    return entries


def read_index(project_dir: str) -> list[dict] | None:
    """读取项目 index.json（photos 列表）；缺失/损坏返回 None。

    返回条目的 path 统一为相对「图片素材」的新基准（自动兼容旧 图片素材/ 前缀）。
    """
    p = os.path.join(project_dir, INDEX_FILE)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        photos = data.get("photos") if isinstance(data, dict) else None
        if isinstance(photos, list):
            return _normalize_index_entries(photos)
        return None
    except Exception:
        return None


def load_index_map(project_dir: str) -> dict[str, dict]:
    """返回 相对路径 → 记录 的映射（真相层查询基础）。"""
    entries = read_index(project_dir) or []
    return {e.get("path"): e for e in entries if e.get("path")}


def list_authors(project_dir: str) -> list[str]:
    """从 index.json 提取全部作者（去重、保序）。"""
    authors: list[str] = []
    seen: set[str] = set()
    for e in read_index(project_dir) or []:
        a = (e.get("author") or "").strip()
        if a and a not in seen:
            seen.add(a)
            authors.append(a)
    return authors


def write_index(project_dir: str, photos: list[dict]):
    """原子写 index.json。"""
    payload = {
        "index": INDEX_FILE,
        "version": 2,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(photos),
        "photos": photos,
    }
    tmp = os.path.join(project_dir, INDEX_FILE + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, os.path.join(project_dir, INDEX_FILE))


def sync_index_from_operations(project_dir: str, ops: list[dict]) -> int:
    """按动作映射（事件同步）更新 index 记录。

    ops: [{"from": 旧相对路径, "to": 新相对路径}, ...]
    对每条: 用 from 定位记录 → 改写 name/path → 完成。time/author 锁定不动。
    返回更新条数；index 缺失时返回 0（不创建）。
    """
    entries = read_index(project_dir)
    if not entries:
        return 0
    by_path = {e.get("path"): e for e in entries if e.get("path")}
    changed = []
    for op in ops or []:
        f, t = op.get("from"), op.get("to")
        if not f or not t or f == t:
            continue
        e = by_path.get(f)
        if not e:
            continue
        e["path"] = t
        e["name"] = os.path.basename(t)
        by_path.pop(f, None)
        by_path[t] = e
        changed.append(f)
    if changed:
        write_index(project_dir, entries)
        logger.info("photo_index 同步 %d 条: %s", len(changed), changed[:5])
    return len(changed)


def _author_of(rel_path: str, id_data: dict | None) -> str | None:
    """由相对路径第一级文件夹名 → id.json folders 的 author。"""
    if not id_data:
        return None
    parts = rel_path.replace("\\", "/").split("/")
    if len(parts) < 2:
        return None
    folder_name = parts[0]
    for f in id_data.get("folders", []):
        if f.get("name") == folder_name:
            return f.get("author")
    return None


def _free_seq(used: set[int]) -> int:
    seq = 1
    while seq in used:
        seq += 1
    return seq


def build_photo_index(project_dir: str, id_data: dict | None = None) -> dict:
    """构建/增量更新照片索引，物理重命名为短名。

    返回: {"status": changed|no_change|no_photos, "total": n, "added": m}
    """
    project_dir = os.path.realpath(project_dir)
    if not os.path.isdir(project_dir):
        raise ValueError(f"项目目录不存在: {project_dir}")

    old_entries = read_index(project_dir) or []

    # 已有索引: 路径 → 条目 映射（幂等：已索引不复改）
    by_path: dict[str, dict] = {}
    used_seq: set[int] = set()
    for e in old_entries:
        p = e.get("path")
        if p:
            by_path[p] = e
            m = re.match(r"^P(\d+)\b", os.path.basename(p))
            if m:
                used_seq.add(int(m.group(1)))

    # 磁盘上已存在的 P 短名也计入占用（index.json 缺失/损坏时防止重名覆盖）
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if not _skip_dir(d)]
        for fn in files:
            m = re.match(r"^P(\d+)\b", fn)
            if m:
                used_seq.add(int(m.group(1)))

    photos = scan_project_photos(project_dir)
    built: list[dict] = []
    added = 0
    changed = False

    def _entry(e: dict) -> dict:
        """条目规范化：仅保留 name/path/time/author（剔除遗留字段）。"""
        return {
            "name": e.get("name") or os.path.basename(e.get("path", "")),
            "path": e.get("path"),
            "time": e.get("time"),
            "author": e.get("author"),
        }

    for abs_path in photos:
        rel = _index_rel(project_dir, abs_path)
        if rel in by_path:
            # 已索引：直接复用原条目
            built.append(_entry(by_path[rel]))
            continue

        folder = os.path.dirname(abs_path)
        ext = os.path.splitext(os.path.basename(abs_path))[1].lower()

        seq = _free_seq(used_seq)
        used_seq.add(seq)
        new_abs = os.path.join(folder, f"P{seq:04d}{ext}")

        # 时间取自「改名前的原始文件名」
        ts = _parse_ts(os.path.basename(abs_path))

        before = abs_path
        if abs_path != new_abs:
            os.rename(abs_path, new_abs)
        changed = True  # 只要新增了条目即视为变更

        new_rel = _index_rel(project_dir, new_abs)
        built.append({
            "name": os.path.basename(new_abs),
            "path": new_rel,
            "time": ts,
            "author": _author_of(new_rel, id_data),
        })
        added += 1
        logger.info("photo_index: %s → %s", before, new_rel)

    # 清理：索引中已不存在的记录（文件被移走/删除）——保证 index 永远真实
    # 注意: 新条目的 path 是重命名后的 P 短名（磁盘实际位置），
    # 不能拿重命名前的 photos 路径集合来过滤，须按磁盘实际文件校验。
    live = [e for e in built
            if e.get("path") and os.path.isfile(_path_abs(project_dir, e["path"]) or "")]
    built = live

    write_index(project_dir, built)
    total = len(built)
    if total == 0:
        return {"status": "no_photos", "total": 0, "added": 0}
    status = "changed" if changed else "no_change"
    logger.info("photo_index: %s 完成 total=%d added=%d status=%s",
                project_dir, total, added, status)
    return {"status": status, "total": total, "added": added}


def _unique_path(dst: str) -> str:
    """目标路径已存在时追加 (n) 序号，返回可用路径。"""
    if not os.path.exists(dst):
        return dst
    stem, ext = os.path.splitext(dst)
    n = 2
    while os.path.exists(f"{stem}({n}){ext}"):
        n += 1
    return f"{stem}({n}){ext}"


def extract_to_material_dirs(project_dir: str, id_data: dict | None = None) -> dict:
    """step3 前置处理：先构建照片索引，再把视频/照片分别平铺提取到 视频素材/图片素材。

    顺序: 先 build_photo_index（照片仍在作者文件夹，author 可正确反查）→
    视频提取（全项目扫描，排除隐藏/废弃物/两个素材目录）→ 照片提取（按 index
    真相层，平铺到 图片素材/）→ sync 更新 path。幂等：重跑时照片已在
    图片素材/ 下、视频已在 视频素材/ 下，均跳过。
    返回 {"status", "videos_moved", "photos_moved", "photos_total"}。
    """
    project_dir = os.path.realpath(project_dir)
    if not os.path.isdir(project_dir):
        raise ValueError(f"项目目录不存在: {project_dir}")

    build_photo_index(project_dir, id_data)

    # ---- 视频提取: 扫描作者文件夹 → 平铺移动到 视频素材 ----
    video_target = os.path.join(project_dir, VIDEO_DIR)
    os.makedirs(video_target, exist_ok=True)
    videos_moved = 0
    for entry in os.scandir(project_dir):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if entry.name in TOP_EXCLUDE or entry.name == PHOTO_DIR:
            continue
        for root, dirs, files in os.walk(entry.path):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fn in files:
                if os.path.splitext(fn)[1].lower() not in VIDEO_EXTS:
                    continue
                src = os.path.join(root, fn)
                dst = _unique_path(os.path.join(video_target, fn))
                try:
                    shutil.move(src, dst)
                    videos_moved += 1
                except Exception:
                    logger.exception("视频提取失败: %s", src)

    # ---- 照片提取: 按 index 真相层 → 平铺移动到 图片素材 ----
    photo_target = os.path.join(project_dir, PHOTO_DIR)
    os.makedirs(photo_target, exist_ok=True)
    photos_real = _photos_real(project_dir)
    entries = read_index(project_dir) or []
    ops = []
    photos_moved = 0
    for e in entries:
        rel = (e.get("path") or "").replace("\\", "/")
        if not rel:
            continue
        src = _path_abs(project_dir, rel)
        if not src or not os.path.isfile(src):
            continue
        # 已在 图片素材 内（幂等重跑）→ 跳过
        if os.path.realpath(src) == photos_real or os.path.realpath(src).startswith(photos_real + os.sep):
            continue
        dst = _unique_path(os.path.join(photo_target, os.path.basename(src)))
        try:
            shutil.move(src, dst)
        except Exception:
            logger.exception("照片提取失败: %s", src)
            continue
        ops.append({"from": rel, "to": _rel_photos(project_dir, dst)})
        photos_moved += 1
    if ops:
        sync_index_from_operations(project_dir, ops)

    return {
        "status": "ok",
        "videos_moved": videos_moved,
        "photos_moved": photos_moved,
        "photos_total": len(entries),
    }