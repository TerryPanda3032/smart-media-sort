# -*- coding: utf-8 -*-
"""配置读写工具。"""

import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

DEFAULT_CONFIG = {
    "work_dir": "",
    "api_url": "https://api-inference.modelscope.cn/v1/chat/completions",
    "api_key": "",
    "model": "Qwen/Qwen3.5-397B-A17B",
    "reasoning_effort": "medium",   # 主力模型思考强度：low / medium / high（不支持时自动忽略）
    "ffmpeg_path": "",
    # 视频压缩质量，对应 ffmpeg CRF 值（越小画质越高、编码越慢）；范围 18-35
    "video_quality": 23,
    # 水印字号（以 1080 标准高度为基准的像素值），照片/视频上屏物理尺寸恒定
    "watermark_size": 24,
    # 水印透明度（0-100，越大越不透明）
    "watermark_opacity": 80,
}


def read_config() -> dict | None:
    if not os.path.exists(CONFIG_PATH):
        return None
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def write_config(cfg: dict):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def get_ffmpeg_path() -> str:
    """从 config.json 读取 ffmpeg 路径，找不到则回退到 'ffmpeg'。"""
    try:
        cfg = read_config()
        if cfg:
            p = cfg.get("ffmpeg_path", "").strip()
            if p and os.path.isfile(p):
                return p
    except Exception:
        pass
    return "ffmpeg"
