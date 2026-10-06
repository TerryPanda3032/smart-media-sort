# -*- coding: utf-8 -*-
"""复制服务 — 后台文件复制 + 实时进度。

支持：
  · 时间范围过滤（今天 / 近三天 / 近一周 / 自定义日期，按文件修改/创建时间较新者判断）
  · 转移源文件：复制完成后把本次已复制的源文件移入「源目录/已拷贝_随机数」，避免重复导入
"""

import logging
import os
import random
import shutil
import threading
import time
from datetime import datetime, timedelta

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


def _filter_start_ts(date_range: str, custom_date: str) -> datetime | None:
    """计算时间过滤起点。返回 None 表示不过滤（全部导入）。"""
    if date_range in ("today", "3days", "7days"):
        try:
            days = {"today": 0, "3days": 3, "7days": 7}[date_range]
        except KeyError:
            return None
        now = datetime.now()
        return (now - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    if date_range == "custom" and custom_date:
        try:
            return datetime.strptime(custom_date.strip(), "%Y-%m-%d")
        except ValueError:
            logger.warning("自定义日期格式无效: %s", custom_date)
            return None
    return None


def _file_time(fp: str) -> float:
    """文件时间：取修改时间与创建时间较新者（秒）。"""
    try:
        st = os.stat(fp)
        return max(st.st_mtime, st.st_ctime)
    except Exception:
        return 0.0


def _unique_path(dst: str) -> str:
    if not os.path.exists(dst):
        return dst
    stem, ext = os.path.splitext(dst)
    n = 2
    while os.path.exists(f"{stem}_{n}{ext}"):
        n += 1
    return f"{stem}_{n}{ext}"


def start_copy(name: str, source_path: str, dest_path: str,
               folder_name: str, author: str, add_watermark_photo: bool,
               add_watermark_video: bool = True,
               compress_photo: bool = True, compress_video: bool = True,
               date_range: str = "all", custom_date: str = "",
               move_source: bool = False):
    """启动后台复制线程。"""
    t = threading.Thread(
        target=_do_copy_background,
        args=(name, source_path, dest_path, folder_name, author,
              add_watermark_photo, add_watermark_video, compress_photo, compress_video),
        kwargs={"date_range": date_range, "custom_date": custom_date,
                "move_source": move_source},
        daemon=True,
    )
    t.start()


def get_progress(name: str) -> dict | None:
    return _progress.get(name)


def _do_copy_background(name: str, source_path: str, dest_path: str,
                        folder_name: str, author: str, add_watermark_photo: bool,
                        add_watermark_video: bool = True,
                        compress_photo: bool = True, compress_video: bool = True,
                        date_range: str = "all", custom_date: str = "",
                        move_source: bool = False):
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
            _write_copy_metadata(name, folder_name, author, add_watermark_photo, add_watermark_video, compress_photo, compress_video)
            return

        counter = get_next_number(dest_path)
        copied_srcs: list[str] = []
        copied_count = 0
        skipped_count = 0
        start_ts = _filter_start_ts(date_range, custom_date)

        for dirpath, _, filenames in os.walk(source_path):
            for fname in sorted(filenames):
                src = os.path.join(dirpath, fname)
                ext = os.path.splitext(fname)[1]

                # 时间范围过滤：文件时间（修改/创建较新者）早于起点 → 跳过
                if start_ts is not None:
                    ft = datetime.fromtimestamp(_file_time(src))
                    if ft < start_ts:
                        skipped_count += 1
                        current = _progress.get(name)
                        if current:
                            _progress.update(name, done=current["done"] + 1)
                        continue

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
                    shutil.copystat(src, dest)
                except Exception:
                    pass

                counter += 1
                copied_count += 1
                copied_srcs.append(src)
                current = _progress.get(name)
                if current:
                    _progress.update(name, done=current["done"] + 1)
                _notify_sse(name)

        # 转移源文件：把本次已复制的源文件移入「源目录/已拷贝_随机数」
        if move_source and copied_srcs:
            _move_sources_to_copied(source_path, copied_srcs)

        msg = f"复制完成：{copied_count} 个"
        if skipped_count:
            msg += f"，跳过 {skipped_count} 个"
        if move_source and copied_srcs:
            msg += "，源文件已转移"
        _write_copy_metadata(name, folder_name, author, add_watermark_photo, add_watermark_video, compress_photo, compress_video)
        _progress.update(name, status="done", current_pct=1.0, message=msg)
        _notify_sse(name)

    except Exception as e:
        logger.exception("复制失败: %s", e)
        _progress.update(name, status="error", message=str(e))
        _notify_sse(name)


def _move_sources_to_copied(source_path: str, srcs: list[str]) -> None:
    """把已复制的源文件移入「源目录/已拷贝_随机数」，避免下次重复导入。"""
    try:
        copied_dir = os.path.join(source_path, f"已拷贝_{random.randint(10000, 99999)}")
        os.makedirs(copied_dir, exist_ok=True)
        for src in srcs:
            dst = _unique_path(os.path.join(copied_dir, os.path.basename(src)))
            try:
                shutil.move(src, dst)
            except Exception as e:
                logger.warning("源文件转移失败 %s: %s", src, e)
        logger.info("已转移 %d 个源文件到 %s", len(srcs), copied_dir)
    except Exception as e:
        logger.warning("创建已拷贝目录失败: %s", e)


def _write_copy_metadata(name: str, folder_name: str, author: str,
                         add_watermark_photo: bool,
                         add_watermark_video: bool = True,
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
                "addWatermarkPhoto": add_watermark_photo,
                "addWatermarkVideo": add_watermark_video,
                "compressPhoto": compress_photo,
                "compressVideo": compress_video,
            })
            write_project_idjson(project_dir, id_data)
    except Exception as e:
        logger.exception("写入元数据失败: %s", e)
