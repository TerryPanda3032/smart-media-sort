# -*- coding: utf-8 -*-
"""视频 AI 标签提取 — 使用 ffmpeg 抽帧 + 多模态 AI 分析。

特性：
  - ffmpeg 抽帧（2fps），转 base64 JPEG
  - 并发请求 = 2，自动重试（5次，间隔5s）
  - 结果写入 视频标签结果.json
"""

import base64
import json
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image

from config import get_ffmpeg_path, read_config
from media import VIDEO_EXTS
from sse_service import ProgressState

logger = logging.getLogger(__name__)

# ---------- 配置常量 ----------
FPS = 2                        # 抽帧帧率
JPEG_QUALITY = 25              # JPEG 压缩质量
MAX_FRAME_WIDTH = 512          # 帧最大宽度
MAX_FRAMES = 30                # 每视频最多送入 AI 的帧数（超出均匀抽样）
MAX_RETRIES = 5                # 最大重试次数
RETRY_INTERVAL = 5             # 重试间隔（秒）
CONCURRENCY = 2                # 并发请求数
TIMEOUT = 600                  # API 超时（秒）

# ---------- 进度管理 ----------
_video_tag_progress = ProgressState()


def set_sse_callback(fn):
    _video_tag_progress.set_sse_callback(fn)


def _push_progress(project_key: str, **kwargs):
    _video_tag_progress.push(project_key, **kwargs)


def _broadcast_event(project_key: str, **kwargs):
    """仅广播逐视频事件，不覆盖总进度缓存（video_start / video_done）。"""
    _video_tag_progress.broadcast(project_key, **kwargs)


def get_progress(project_key: str) -> dict | None:
    return _video_tag_progress.get(project_key)


# ---------- 帧提取 ----------

def _get_video_duration(video_path: str, ffmpeg_bin: str) -> float:
    """用 ffprobe 获取视频时长（秒）。"""
    ffprobe_bin = ffmpeg_bin.replace("ffmpeg", "ffprobe")
    cmd = [
        ffprobe_bin, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0", video_path,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def _extract_frames(video_path: str, ffmpeg_bin: str, output_dir: str) -> list[str]:
    """用 ffmpeg 从视频中按 2fps 抽帧，返回帧文件路径列表（已排序）。"""
    os.makedirs(output_dir, exist_ok=True)
    pattern = os.path.join(output_dir, "frame_%04d.jpg")
    cmd = [
        ffmpeg_bin, "-y", "-i", video_path,
        "-vf", f"fps={FPS}",
        "-q:v", str(JPEG_QUALITY),
        "-f", "image2",
        pattern,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            logger.error("ffmpeg 抽帧失败 %s: %s",
                         os.path.basename(video_path),
                         result.stderr.decode("utf-8", errors="replace")[:500])
            return []
    except subprocess.TimeoutExpired:
        logger.error("ffmpeg 抽帧超时 %s", os.path.basename(video_path))
        return []
    except Exception as e:
        logger.error("ffmpeg 抽帧异常 %s: %s", os.path.basename(video_path), e)
        return []

    frames = sorted(
        os.path.join(output_dir, f)
        for f in os.listdir(output_dir)
        if f.endswith(".jpg")
    )
    return frames


def _compress_frame(frame_path: str) -> bytes | None:
    """压缩帧为 JPEG，限制最大宽度，返回字节。"""
    try:
        with Image.open(frame_path) as img:
            if img.mode != "RGB":
                img = img.convert("RGB")
            w, h = img.size
            if w > MAX_FRAME_WIDTH:
                ratio = MAX_FRAME_WIDTH / w
                img = img.resize(
                    (int(w * ratio), int(h * ratio)),
                    Image.Resampling.LANCZOS,
                )
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
            return buf.getvalue()
    except Exception as e:
        logger.warning("帧压缩失败 %s: %s", frame_path, e)
        return None


def _frame_to_base64(frame_path: str) -> str | None:
    """压缩帧并转为 base64 字符串。"""
    data = _compress_frame(frame_path)
    if data:
        return base64.b64encode(data).decode("utf-8")
    return None


# ---------- AI 请求 ----------

def _call_ai_api(
    api_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> dict | None:
    """调用 AI API，带重试机制。"""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(
                api_url, json=payload, headers=headers, timeout=TIMEOUT,
            )
            resp.raise_for_status()
            result = resp.json()
            content = _extract_content(result)
            return result
        except Exception as e:
            logger.warning(
                "AI API 请求失败 (尝试 %d/%d): %s",
                attempt, MAX_RETRIES, e,
            )
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_INTERVAL)
    return None


def _extract_json(text: str) -> dict | None:
    """从 AI 回复中提取 JSON 对象。"""
    text = text.strip()
    # 去除 markdown 代码块
    if text.startswith("```"):
        lines = text.splitlines()
        start = 1
        for i, line in enumerate(lines):
            if line.startswith("```"):
                start = i + 1
            else:
                break
        text = "\n".join(lines[start:])
        if text.endswith("```"):
            text = text[:-3].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                return None
        return None


def _extract_content(result: dict) -> str:
    """防御式提取 AI 响应中的 content；结构异常时抛出带响应原文的 ValueError。"""
    if not isinstance(result, dict):
        raise ValueError(f"AI 返回非 JSON 对象: {str(result)[:300]}")
    choices = result.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError(f"AI 返回异常结构 (choices 缺失或为空): {str(result)[:300]}")
    first = choices[0]
    if not isinstance(first, dict):
        raise ValueError(f"AI 返回异常结构 (choices[0] 非对象): {str(result)[:300]}")
    msg = first.get("message")
    if not isinstance(msg, dict):
        raise ValueError(f"AI 返回异常结构 (message 缺失): {str(result)[:300]}")
    content = msg.get("content", "")
    if not isinstance(content, str) or not content.strip():
        raise ValueError(f"AI 返回空内容: {str(result)[:300]}")
    return content


# ---------- 单个视频处理 ----------

def _process_single_video(
    video_path: str,
    project_dir: str,
    ffmpeg_bin: str,
    api_url: str,
    api_key: str,
    model: str,
) -> tuple[str, list[str] | None]:
    """处理单个视频：抽帧 → AI 标签提取。返回 (相对路径, 标签列表或None)。"""
    rel_path = os.path.relpath(video_path, project_dir)
    logger.info("开始处理视频: %s", rel_path)

    # 创建临时目录存放帧
    tmp_dir = tempfile.mkdtemp(prefix="vtag_")
    try:
        # 抽帧
        frames = _extract_frames(video_path, ffmpeg_bin, tmp_dir)
        if not frames:
            logger.warning("视频抽帧为空，跳过: %s", rel_path)
            return rel_path, None

        # 帧数过多时均匀抽样，控制请求体积
        if len(frames) > MAX_FRAMES:
            step = math.ceil(len(frames) / MAX_FRAMES)
            frames = frames[::step]
            logger.info("视频 %s 帧数过多，均匀抽样至 %d 帧", rel_path, len(frames))

        logger.info("视频 %s 抽取 %d 帧", rel_path, len(frames))

        # 转 base64
        image_parts = []
        for frame_path in frames:
            b64 = _frame_to_base64(frame_path)
            if b64:
                image_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                })

        if not image_parts:
            logger.warning("视频帧全部压缩失败: %s", rel_path)
            return rel_path, None

        # 构建 prompt
        duration = _get_video_duration(video_path, ffmpeg_bin)
        total_seconds = int(duration) if duration > 0 else "未知"

        system_prompt = (
            "你是一个专业的视频内容分析专家。"
            "你需要仔细观察每个视频的逐帧截图，准确理解视频内容。"
            "每次分析都是独立任务，不受之前任何结果的影响。"
        )

        user_prompt = (
            f"以下提供了1个视频（文件路径：{rel_path}）的逐帧截图，"
            f"视频时长约{total_seconds}秒，共{len(image_parts)}帧。\n\n"
            "请仔细观察这些截图，为这个视频提取最能代表该视频内容的标签。\n"
            "每个视频必须包含以下要素的标签：\n"
            "1. 景别（如：全景、中景、特写、远景、航拍、仰拍、俯拍等）\n"
            "2. 主要内容（如：会议演讲、户外运动、产品展示、风景、人物等）\n"
            "3. 主要动作（如：奔跑、演讲、操作设备、行走、交谈等）\n\n"
            "同一素材标签不能有语义重复，分类标签注重画面主体而不是背景杂物。\n\n"
            "每个视频最多输出5个标签。标签之间用中文逗号分隔。\n\n"
            "请严格按照以下 JSON 格式输出，不添加任何多余说明：\n"
            '{"标签": ["标签1", "标签2", "标签3", "标签4", "标签5"]}'
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": [{"type": "text", "text": user_prompt}, *image_parts]},
        ]

        # 调用 AI
        result = _call_ai_api(api_url, api_key, model, messages)
        if result is None:
            logger.error("AI 调用失败: %s", rel_path)
            return rel_path, None

        try:
            content = _extract_content(result)
        except ValueError as e:
            logger.error("AI 返回异常: %s → %s", rel_path, e)
            return rel_path, None

        # 解析 JSON
        parsed = _extract_json(content)
        if parsed is None:
            logger.error("AI 返回非 JSON: %s, content=%s", rel_path, content[:300])
            return rel_path, None

        tags = parsed.get("标签", [])
        if not isinstance(tags, list):
            tags = []

        # 限制最多5个标签
        tags = [str(t) for t in tags[:5]]

        logger.info("视频标签提取成功: %s → %s", rel_path, tags)
        return rel_path, tags

    finally:
        # 清理临时目录
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


# ---------- 主流程 ----------

def start_video_tagger(project_dir: str, project_key: str):
    """启动视频标签提取任务（后台线程）。"""
    t = __import__("threading").Thread(
        target=_run_video_tagger,
        args=(project_dir, project_key),
        daemon=True,
    )
    t.start()


def _safe_file_stem(stem: str) -> str:
    """清洗标签拼出的文件名主干：去掉非法字符与首尾空白。"""
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).strip().rstrip(". ")
    return stem or "未命名"


def _organize_tagged_videos(project_dir: str, results: dict) -> int:
    """按标签重命名视频并统一移动到项目下 视频素材 文件夹。

    返回成功移动的数量；移动后结果文件中的路径同步更新为新位置。
    """
    target_dir = os.path.join(project_dir, "视频素材")
    os.makedirs(target_dir, exist_ok=True)
    moved = 0
    updated: dict[str, list[str]] = {}
    for rel_path, tags in list(results.items()):
        if not tags:
            continue
        src = os.path.join(project_dir, rel_path)
        if not os.path.isfile(src):
            continue
        ext = os.path.splitext(rel_path)[1].lower() or os.path.splitext(rel_path)[1]
        stem = _safe_file_stem("-".join(tags))
        dest = os.path.join(target_dir, stem + ext)
        n = 2
        while os.path.exists(dest):
            dest = os.path.join(target_dir, f"{stem}({n}){ext}")
            n += 1
        try:
            shutil.move(src, dest)
        except Exception as e:
            logger.error("移动视频失败 %s: %s", rel_path, e)
            continue
        updated[os.path.relpath(dest, project_dir)] = tags
        moved += 1
    if updated:
        results_path = os.path.join(project_dir, "视频标签结果.json")
        try:
            with open(results_path, "w", encoding="utf-8") as f:
                json.dump({"视频标签结果": updated}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("更新结果文件失败: %s", e)
    return moved


def _run_video_tagger(project_dir: str, project_key: str):
    """视频标签提取主流程。"""
    try:
        if not os.path.isdir(project_dir):
            raise FileNotFoundError(f"项目目录不存在: {project_dir}")

        cfg = read_config()
        if not cfg:
            raise RuntimeError("无法读取 config.json")
        api_key = cfg.get("api_key", "").strip()
        if not api_key:
            raise RuntimeError("未配置 API 密钥")
        api_url = cfg.get("api_url", "")
        model = cfg.get("model", "Qwen/Qwen3.5-397B-A17B")
        ffmpeg_bin = get_ffmpeg_path()

        # 扫描所有视频（step3 前置提取后统一位于 视频素材 目录）
        all_videos = []
        video_dir = os.path.join(project_dir, "视频素材")
        if os.path.isdir(video_dir):
            for root, _, files in os.walk(video_dir):
                for fn in files:
                    ext = os.path.splitext(fn)[1].lower()
                    if ext in VIDEO_EXTS:
                        all_videos.append(os.path.join(root, fn))

        total = len(all_videos)
        if total == 0:
            _push_progress(
                project_key,
                status="done",
                total=0,
                done=0,
                percent=100,
                message="没有需要分析的视频",
            )
            return

        _push_progress(
            project_key,
            status="running",
            total=total,
            done=0,
            percent=0,
            message=f"开始分析 {total} 个视频...",
        )

        results: dict[str, list[str]] = {}
        done_count = 0
        failed_count = 0

        # 分批处理，每批 CONCURRENCY 个视频并发
        for batch_start in range(0, total, CONCURRENCY):
            batch_videos = all_videos[batch_start:batch_start + CONCURRENCY]

            def _track_and_process(v: str):
                """单个视频：开始/完成时广播逐视频事件，供前端行内实时刷新。"""
                rel = os.path.relpath(v, project_dir)
                _broadcast_event(project_key, status="video_start", path=rel)
                rel_path, tags = _process_single_video(
                    v, project_dir, ffmpeg_bin,
                    api_url, api_key, model,
                )
                _broadcast_event(
                    project_key,
                    status="video_done",
                    path=rel_path,
                    tags=tags if tags is not None else [],
                    failed=tags is None,
                )
                return rel_path, tags

            with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
                futures = {
                    pool.submit(_track_and_process, v): v
                    for v in batch_videos
                }

                for future in as_completed(futures):
                    video_path = futures[future]
                    try:
                        rel_path, tags = future.result()
                        if tags is not None:
                            results[rel_path] = tags
                        else:
                            failed_count += 1
                    except Exception as e:
                        logger.error("视频处理异常 %s: %s", video_path, e)
                        failed_count += 1

                    done_count += 1
                    pct = int(done_count / total * 100)
                    _push_progress(
                        project_key,
                        status="running",
                        total=total,
                        done=done_count,
                        percent=pct,
                        message=f"已分析 {done_count}/{total} 个视频...",
                    )

        # 保存结果
        results_path = os.path.join(project_dir, "视频标签结果.json")
        output = {"视频标签结果": results}
        with open(results_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

        # 按标签重命名并统一移动到 视频素材 文件夹
        organized = 0
        try:
            organized = _organize_tagged_videos(project_dir, results)
        except Exception:
            logger.exception("按标签整理视频失败")

        msg = f"分析完成：成功 {len(results)} 个，失败 {failed_count} 个"
        if organized:
            msg += f"，已按标签重命名并移动到「视频素材」文件夹 {organized} 个"
        _push_progress(
            project_key,
            status="done",
            total=total,
            done=total,
            percent=100,
            message=msg,
        )
        logger.info("视频标签提取完成: %s, 结果保存至 %s", project_key, results_path)

    except Exception as e:
        logger.exception("视频标签提取失败")
        _push_progress(
            project_key,
            status="error",
            total=0,
            done=0,
            percent=0,
            message=str(e),
        )
