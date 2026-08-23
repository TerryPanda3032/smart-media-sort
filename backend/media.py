# -*- coding: utf-8 -*-
"""媒体文件工具 — 扫描、统计、GPU 检测。"""

import logging
import os
import subprocess

logger = logging.getLogger(__name__)

PHOTO_EXTS = {".dng", ".png", ".jpg", ".jpeg"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv"}


def scan_media(folder_dir: str) -> tuple[list[str], list[str]]:
    """扫描文件夹中的所有照片和视频文件。"""
    photos, videos = [], []
    for root, _, files in os.walk(folder_dir):
        for fn in files:
            ext = os.path.splitext(fn)[1].lower()
            fp = os.path.join(root, fn)
            if ext in PHOTO_EXTS:
                photos.append(fp)
            elif ext in VIDEO_EXTS:
                videos.append(fp)
    return photos, videos


def count_project_files(project_dir: str, exts: set | None = None) -> int:
    """统计项目子文件夹中的文件数（跳过隐藏目录与「废弃物」），可按扩展名过滤。"""
    total = 0
    try:
        for entry in os.scandir(project_dir):
            if not entry.is_dir():
                continue
            if entry.name.startswith("."):
                continue
            if entry.name == "废弃物":
                continue
            for root, _, files in os.walk(entry.path):
                for fn in files:
                    if exts is None or os.path.splitext(fn)[1].lower() in exts:
                        total += 1
    except Exception:
        return 0
    return total


def scan_folder_stats(folder_dir: str) -> dict:
    """扫描单个文件夹的媒体统计。"""
    photo_count = 0
    video_count = 0
    folder_size = 0
    for root, _, files in os.walk(folder_dir):
        for fn in files:
            ext = os.path.splitext(fn)[1].lower()
            try:
                fp = os.path.join(root, fn)
                sz = os.path.getsize(fp)
                folder_size += sz
                if ext in PHOTO_EXTS:
                    photo_count += 1
                elif ext in VIDEO_EXTS:
                    video_count += 1
            except Exception:
                pass
    return {
        "photo_count": photo_count,
        "video_count": video_count,
        "total_size_bytes": folder_size,
    }


def detect_gpu(ffmpeg_bin: str = "ffmpeg") -> str | None:
    """检测可用的 GPU 编码器。

    改进: 同时检查 h264 和 hevc 编码器，实际编码测试确认硬件可用。
    """
    # 第一步: 检查 -encoders 列表
    try:
        r = subprocess.run(
            [ffmpeg_bin, "-encoders"],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        out = r.stdout
        candidates = []
        if "h264_nvenc" in out or "hevc_nvenc" in out:
            candidates.append("nvenc")   # NVIDIA 优先
        if "h264_qsv" in out or "hevc_qsv" in out:
            candidates.append("qsv")
        if "h264_amf" in out or "hevc_amf" in out:
            candidates.append("amf")
    except Exception:
        return None

    if not candidates:
        return None

    # 第二步: 实际编码测试（快速，只编码 1 帧）
    # 优先测试 h264 编码器（兼容性更好），再测试 hevc
    for gpu in candidates:
        for codec in ["h264", "hevc"]:
            encoder_map = {
                "qsv": {"h264": "h264_qsv", "hevc": "hevc_qsv"},
                "amf": {"h264": "h264_amf", "hevc": "hevc_amf"},
                "nvenc": {"h264": "h264_nvenc", "hevc": "hevc_nvenc"},
            }
            encoder = encoder_map[gpu][codec]
            # 用 512x512 而非 64x64：NVENC/AMF 有分辨率下限（64x64 过小会
            # 报 "Frame Dimension less than the minimum supported value" 导致误判回退 CPU）
            test_cmd = [
                ffmpeg_bin, "-y", "-f", "lavfi", "-i", "testsrc=duration=0.1:size=512x512",
                "-c:v", encoder, "-frames:v", "1", "-f", "null", "-"
            ]
            try:
                result = subprocess.run(
                    test_cmd,
                    capture_output=True, timeout=15,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                if result.returncode == 0:
                    logger.info("GPU 编码器测试通过: %s (%s)", gpu, encoder)
                    return gpu
            except Exception:
                continue

    logger.info("GPU 编码器在 -encoders 中列出但实际测试均失败，回退到 CPU")
    return None
