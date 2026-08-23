# -*- coding: utf-8 -*-
"""AI 筛检服务 — 调用快速模型逐批筛检废片，结果写入 分类结果.json。

SSE 事件类型: ai_filter
"""

import base64
import json
import logging
import os
import re
import threading
import time
from io import BytesIO

import requests
from PIL import Image

from config import read_config
from media import PHOTO_EXTS

logger = logging.getLogger(__name__)

BATCH_SIZE = 40
PROMPT = (
    "你是一位严格的图片质量评审专家。请按以下优先级逐张判断，\n"
    "每张图片只归入优先级最高的一个类别（一旦命中即停止判断后续类别）：\n\n"
    "优先级 1 → type 4（内容无物 / 无保留价值）\n"
    "判断：只要这张照片拍得不好、没什么价值、不值得保留，就直接归入此类。\n"
    "包括但不限于：画面中无可辨认的有效拍摄对象（纯色墙面/地面/天空、手指遮挡镜头、\n"
    "镜头盖、完全失焦到无形状、全黑/全白）、主体严重模糊糊成一片、构图完全崩坏\n"
    "（只拍到半根手指/一只脚尖/地面虚影）、曝光严重失误导致画面不可用、随手误拍的\n"
    "空镜头/桌面/衣角/杂物特写等没有任何意义、没有可看性、不值得交付给用户的照片。\n"
    "原则：你判断这张照片没拍好、没价值，就一律归入 type 4（内容无物）。\n\n"
    "优先级 2 → type 3（模糊不清）\n"
    "判断：画面有可辨认对象，但主体明显模糊/失焦/抖动虚影（尚不至于糊成一片、整体报废）。\n"
    "注意：即使同时存在曝光问题，只要主体模糊，就归为此类，不再判断曝光。\n\n"
    "优先级 3 → type 2（欠曝过曝）\n"
    "判断：画面主体清晰，但曝光明显不合理。\n"
    "包括但不限于：大面积死白（>15%区域）、大面积死黑（>30%区域）。\n\n"
    "优先级 4 → type 1（通过）\n"
    "判断：以上三类都不符合，照片清晰、有价值、值得保留。\n\n"
    "请严格按以下 JSON 格式输出，不添加任何多余说明：\n\n"
    '{"results": [\n'
    '  {"index": 0, "type": 1},\n'
    '  {"index": 1, "type": 2}\n'
    "]}\n\n"
    "其中 index 从 0 开始，对应图片在本次请求中的出现顺序。\n"
    "type 只能是 1、2、3、4 之一。"
)

from sse_service import ProgressState
from media import PHOTO_EXTS, count_project_files

_ai_progress = ProgressState()


def set_sse_callback(fn):
    _ai_progress.set_sse_callback(fn)


def count_ai_filterable_photos(project_dir: str) -> int:
    """统计项目中待 AI 筛检的照片总数。"""
    return count_project_files(project_dir, exts=PHOTO_EXTS)


def _push_progress(project_key: str, **kwargs):
    _ai_progress.push(project_key, **kwargs)


def get_progress(project_key: str) -> dict | None:
    return _ai_progress.get(project_key)


def start_ai_filter(project_dir: str, project_key: str):
    t = threading.Thread(
        target=_run_ai_filter,
        args=(project_dir, project_key),
        daemon=True,
    )
    t.start()


def _compress_for_ai(filepath: str, max_size: int = 1000) -> bytes | None:
    try:
        with Image.open(filepath) as img:
            if img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")
            elif img.mode != "RGB":
                img = img.convert("RGB")
            w, h = img.size
            if w > max_size or h > max_size:
                ratio = min(max_size / w, max_size / h)
                img = img.resize(
                    (int(w * ratio), int(h * ratio)), Image.Resampling.LANCZOS
                )
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=85, optimize=True)
            return buf.getvalue()
    except Exception as e:
        logger.warning("压缩失败 %s: %s", filepath, e)
        return None


def _call_ai_api(
    api_url: str,
    api_key: str,
    model: str,
    prompt: str,
    images_b64: list[str],
    max_retries: int = 3,
) -> dict | None:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    image_parts = [
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
        for b64 in images_b64
    ]

    messages = [
        {"role": "system", "content": "请直接回答，不要进行逐步推理。"},
        {"role": "system",
         "content": "你是一位严格的图片质量评审专家。每次评审都是独立任务，不受之前的任何结果影响。"},
        {"role": "user", "content": [{"type": "text", "text": prompt}, *image_parts]},
    ]

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 2048,
    }

    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(
                api_url, json=payload, headers=headers, timeout=600
            )
            resp.raise_for_status()
            result = resp.json()
            if not isinstance(result, dict):
                raise ValueError("AI 返回非 JSON 对象")
            choices = result.get("choices")
            if not isinstance(choices, list) or not choices:
                raise ValueError(f"AI 返回异常结构 (choices 缺失或为空): {str(result)[:300]}")
            first = choices[0]
            msg = first.get("message") if isinstance(first, dict) else None
            content = msg.get("content", "") if isinstance(msg, dict) else ""
            if not content:
                raise ValueError(f"AI 返回空内容: {str(result)[:300]}")

            parsed = _extract_json(content)
            if parsed is None:
                raise ValueError("AI 返回非 JSON 格式")
            return parsed

        except Exception as e:
            logger.warning(
                "AI API 请求失败 (尝试 %d/%d): %s", attempt, max_retries, e
            )
            if attempt < max_retries:
                time.sleep(2**attempt)
    return None


def _extract_json(text: str) -> dict | None:
    text = text.strip()
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


def _run_ai_filter(project_dir: str, project_key: str):
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
        model = cfg.get("fast_model", "Qwen/Qwen3.5-35B-A3B")

        all_photos: list[str] = []
        for entry in os.scandir(project_dir):
            if not entry.is_dir():
                continue
            name = entry.name
            if name.startswith("."):
                continue
            if name == "废弃物":
                continue
            for root, _, files in os.walk(entry.path):
                for fn in files:
                    ext = os.path.splitext(fn)[1].lower()
                    if ext in PHOTO_EXTS:
                        all_photos.append(os.path.join(root, fn))

        total = len(all_photos)
        if total == 0:
            _push_progress(
                project_key,
                status="done",
                total=0,
                done=0,
                percent=100,
                message="没有需要 AI 筛检的照片",
            )
            return

        _push_progress(
            project_key,
            status="running",
            total=total,
            done=0,
            percent=0,
        )

        results_path = os.path.join(project_dir, "分类结果.json")
        all_results: list[dict] = []
        done_count = 0

        for batch_start in range(0, total, BATCH_SIZE):
            batch_photos = all_photos[batch_start : batch_start + BATCH_SIZE]

            compressed_list: list[str] = []
            valid_indices: list[int] = []
            for i, fp in enumerate(batch_photos):
                img_bytes = _compress_for_ai(fp)
                if img_bytes:
                    compressed_list.append(
                        base64.b64encode(img_bytes).decode("utf-8")
                    )
                    valid_indices.append(i)

            if not compressed_list:
                for _ in batch_photos:
                    done_count += 1
                    pct = int(done_count / total * 100)
                    _push_progress(
                        project_key,
                        status="running",
                        total=total,
                        done=done_count,
                        percent=pct,
                    )
                    time.sleep(0.05)
                continue

            result = _call_ai_api(api_url, api_key, model, PROMPT, compressed_list)

            if result is None:
                logger.error("批次 %d AI 调用失败，跳过", batch_start // BATCH_SIZE + 1)
                for _ in batch_photos:
                    done_count += 1
                    pct = int(done_count / total * 100)
                    _push_progress(
                        project_key,
                        status="running",
                        total=total,
                        done=done_count,
                        percent=pct,
                    )
                    time.sleep(0.05)
                continue

            raw_results = result.get("results", []) if isinstance(result, dict) else []
            type_by_index: dict[int, int] = {}
            for item in raw_results:
                idx = item.get("index")
                t = item.get("type")
                if isinstance(idx, int) and t in (1, 2, 3, 4):
                    type_by_index[idx] = t

            for pos_in_batch, fp in enumerate(batch_photos):
                rel_path = os.path.relpath(fp, project_dir)
                img_type = type_by_index.get(pos_in_batch, 1)

                all_results.append({"path": rel_path, "type": img_type})
                done_count += 1

                try:
                    with open(results_path, "w", encoding="utf-8") as f:
                        json.dump(all_results, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    logger.warning("写入结果文件失败: %s", e)

                pct = int(done_count / total * 100)
                _push_progress(
                    project_key,
                    status="running",
                    total=total,
                    done=done_count,
                    percent=pct,
                )
                time.sleep(0.05)

        _push_progress(
            project_key,
            status="done",
            total=total,
            done=total,
            percent=100,
            message=f"AI 筛检完成，共 {total} 张照片",
        )
        logger.info("AI 筛检完成: %s, 结果已写入 %s", project_key, results_path)

    except Exception as e:
        logger.exception("AI 筛检失败")
        _push_progress(
            project_key,
            status="error",
            total=0,
            done=0,
            percent=0,
            message=str(e),
        )
