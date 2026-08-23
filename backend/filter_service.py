# -*- coding: utf-8 -*-
"""筛检服务 — 遍历项目子文件夹，移除非白名单文件至「废弃物」。

SSE 事件类型: filter
"""

import logging
import os
import shutil
import threading

from sse_service import ProgressState
from media import count_project_files

logger = logging.getLogger(__name__)

KEEP_EXTS = {".png", ".dng", ".jpg", ".jpeg", ".mp4", ".mov", ".avi"}

_filter_progress = ProgressState()


def set_sse_callback(fn):
    _filter_progress.set_sse_callback(fn)


def count_filterable_files(project_dir: str) -> int:
    return count_project_files(project_dir)


def _push_progress(project_key: str, **kwargs):
    _filter_progress.push(project_key, **kwargs)


def get_progress(project_key: str) -> dict | None:
    return _filter_progress.get(project_key)


def start_filter(project_dir: str, project_key: str):
    t = threading.Thread(
        target=_run_filter,
        args=(project_dir, project_key),
        daemon=True,
    )
    t.start()


def _run_filter(project_dir: str, project_key: str):
    try:
        if not os.path.isdir(project_dir):
            raise FileNotFoundError(f"项目目录不存在: {project_dir}")

        entries = []
        try:
            for entry in os.scandir(project_dir):
                if not entry.is_dir():
                    continue
                name = entry.name
                if name.startswith("."):
                    continue
                if name == "废弃物":
                    continue
                entries.append(entry.path)
        except PermissionError as e:
            raise PermissionError(f"无法访问项目目录: {e}")

        if not entries:
            _push_progress(project_key, status="done", total=0, done=0, percent=100,
                           kept=0, removed=0, message="没有找到子文件夹")
            return

        all_files = []
        for folder_path in entries:
            if not os.path.isdir(folder_path):
                continue
            try:
                for root, _, files in os.walk(folder_path):
                    for fn in files:
                        all_files.append(os.path.join(root, fn))
            except Exception as e:
                logger.warning("遍历文件夹失败 %s: %s", folder_path, e)

        total = len(all_files)
        if total == 0:
            _push_progress(project_key, status="done", total=0, done=0, percent=100,
                           kept=0, removed=0, message="没有需要筛检的文件")
            return

        waste_dir = os.path.join(project_dir, "废弃物")
        try:
            os.makedirs(waste_dir, exist_ok=True)
        except PermissionError as e:
            raise PermissionError(f"无法创建废弃物目录: {e}")

        removed = 0
        for idx, fp in enumerate(all_files):
            ext = os.path.splitext(fp)[1].lower()
            if ext not in KEEP_EXTS:
                try:
                    dest = os.path.join(waste_dir, os.path.basename(fp))
                    if os.path.exists(dest):
                        base, ext2 = os.path.splitext(os.path.basename(fp))
                        counter = 1
                        while os.path.exists(dest):
                            dest = os.path.join(waste_dir, f"{base}_{counter}{ext2}")
                            counter += 1
                    shutil.move(fp, dest)
                    removed += 1
                except Exception as e:
                    logger.warning("移动失败 %s: %s", fp, e)

            done = idx + 1
            pct = int(done / total * 100)
            _push_progress(project_key,
                           status="running",
                           total=total,
                           done=done,
                           percent=pct,
                           kept=done - removed,
                           removed=removed)

        _push_progress(project_key,
                       status="done",
                       total=total,
                       done=total,
                       percent=100,
                       kept=total - removed,
                       removed=removed,
                       message=f"筛检完成，保留 {total - removed} 项，移除 {removed} 项")

    except Exception as e:
        logger.exception("筛检失败")
        _push_progress(project_key, status="error", total=0, done=0, percent=0,
                       kept=0, removed=0, message=str(e))
