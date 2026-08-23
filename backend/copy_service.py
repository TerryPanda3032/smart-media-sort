# -*- coding: utf-8 -*-
"""复制服务 — 后台文件复制 + 实时进度。"""

import logging
import os
import threading
import time

from fileops import CopyProgressManager, count_files, get_next_number
from project import resolve_project, write_project_idjson

logger = logging.getLogger(__name__)

# 全局单例
_progress = CopyProgressManager()

# SSE 推送回调 — 由 main.py 注册
_sse_callback = None


def set_sse_callback(fn):
    """注册 SSE 推送回调函数（由 main.py 调用）。"""
    global _sse_callback
    _sse_callback = fn


def _notify_sse(name: str):
    """推送当前复制进度到 SSE。"""
    if _sse_callback:
        try:
            data = _progress.get(name)
            if data:
                _sse_callback(name, dict(data))
        except Exception:
            pass


def start_copy(name: str, source_path: str, dest_path: str,
               folder_name: str, author: str, add_watermark: bool,
               compress_photo: bool = True, compress_video: bool = True):
    """启动后台复制线程。"""
    t = threading.Thread(
        target=_do_copy_background,
        args=(name, source_path, dest_path, folder_name, author,
              add_watermark, compress_photo, compress_video),
        daemon=True,
    )
    t.start()


def get_progress(name: str) -> dict | None:
    return _progress.get(name)


def _do_copy_background(name: str, source_path: str, dest_path: str,
                        folder_name: str, author: str, add_watermark: bool,
                        compress_photo: bool = True, compress_video: bool = True):
    try:
        total = count_files(source_path)
        _progress.set(name, {
            "total": total,
            "done": 0,
            "current_pct": 0.0,
            "current_file": "",
            "current_file_done": 0,
            "current_file_total": 0,
            "status": "copying",
            "message": "",
            "_created_at": time.time(),
        })
        _notify_sse(name)

        if total == 0:
            _progress.update(name, status="done", message="文件夹为空")
            _notify_sse(name)
            _write_copy_metadata(name, folder_name, author, add_watermark, compress_photo, compress_video)
            return

        counter = get_next_number(dest_path)
        for dirpath, _, filenames in os.walk(source_path):
            for fname in sorted(filenames):
                src = os.path.join(dirpath, fname)
                ext = os.path.splitext(fname)[1]
                dest = os.path.join(dest_path, f"{counter}{ext}")

                file_size = os.path.getsize(src)
                _progress.update(name,
                    current_file=fname,
                    current_pct=0.0,
                    current_file_done=0,
                    current_file_total=file_size)
                _notify_sse(name)

                with open(src, "rb") as f_src, open(dest, "wb") as f_dst:
                    copied = 0
                    while True:
                        buf = f_src.read(1024 * 1024)
                        if not buf:
                            break
                        f_dst.write(buf)
                        copied += len(buf)
                        if file_size > 0:
                            _progress.update(name,
                                current_pct=copied / file_size,
                                current_file_done=copied)
                            # 每 1MB 推送一次 SSE，避免洪泛
                            if copied % (1024 * 1024) < len(buf):
                                _notify_sse(name)

                try:
                    import shutil
                    shutil.copystat(src, dest)
                except Exception:
                    pass

                counter += 1
                current = _progress.get(name)
                if current:
                    _progress.update(name, done=current["done"] + 1)
                _notify_sse(name)

        _write_copy_metadata(name, folder_name, author, add_watermark, compress_photo, compress_video)
        _progress.update(name, status="done", current_pct=1.0)
        _notify_sse(name)

    except Exception as e:
        logger.exception("复制失败: %s", e)
        _progress.update(name, status="error", message=str(e))
        _notify_sse(name)


def _write_copy_metadata(name: str, folder_name: str, author: str,
                         add_watermark: bool,
                         compress_photo: bool = True,
                         compress_video: bool = True):
    try:
        project_dir, id_data = resolve_project(name)
        if "folders" not in id_data:
            id_data["folders"] = []
        exists = any(f["name"] == folder_name for f in id_data["folders"])
        if not exists:
            id_data["folders"].append({
                "name": folder_name,
                "author": author,
                "addWatermark": add_watermark,
                "compressPhoto": compress_photo,
                "compressVideo": compress_video,
            })
            write_project_idjson(project_dir, id_data)
    except Exception as e:
        logger.exception("写入元数据失败: %s", e)
