# -*- coding: utf-8 -*-
"""项目数据管理 — id.json 读写、步骤管理、路径解析。"""

import json
import os
import uuid
from urllib.parse import quote

from fastapi import HTTPException

from config import read_config

STEPS_ORDER = ["collect", "filter", "ai_classify", "deliver"]
STEPS_LABEL = {"collect": "收集", "filter": "过滤", "ai_classify": "AI分类", "deliver": "交付"}
STEPS_ICON = {"collect": "step-collect", "filter": "step-filter", "ai_classify": "step-ai-classify", "deliver": "step-deliver"}
STEP_TEMPLATE = {1: "collect", 2: "filter", 3: "ai_classify", 4: "deliver"}


def init_step() -> int:
    return 1


def get_step_from_id(id_data: dict) -> int:
    if "step" in id_data:
        return int(id_data["step"])
    old = id_data.get("progress", {})
    done_count = sum(1 for s in STEPS_ORDER if old.get(s) == "done")
    return done_count + 1


def read_project_idjson(project_dir: str) -> dict | None:
    p = os.path.join(project_dir, "id.json")
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    old_p = os.path.join(project_dir, "id.txt")
    if os.path.exists(old_p):
        try:
            with open(old_p, "r", encoding="utf-8") as f:
                data = json.load(f)
            step = get_step_from_id(data)
            new_data = {"name": data.get("name", ""), "id": data.get("id", ""), "step": step}
            with open(p, "w", encoding="utf-8") as f:
                json.dump(new_data, f, ensure_ascii=False, indent=2)
            try:
                os.remove(old_p)
            except Exception:
                pass
            return new_data
        except Exception:
            return None
    return None


def write_project_idjson(project_dir: str, data: dict):
    with open(os.path.join(project_dir, "id.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def resolve_project(name: str) -> tuple[str, dict]:
    """根据项目名解析出 (project_dir, id_data)，失败抛出 HTTPException。

    防路径遍历: 禁止 name 中包含 .. 或反斜杠。
    """
    # 路径遍历防护
    safe_name = name.replace("..", "").replace("\\", "/").strip("/")
    if not safe_name or safe_name != name:
        # 原始 name 含有可疑字符
        raise HTTPException(status_code=400, detail="非法项目名")

    cfg = read_config()
    if cfg is None:
        raise HTTPException(status_code=400, detail="请先配置工作目录")
    work_dir = cfg.get("work_dir", "")
    if not work_dir or not os.path.isdir(work_dir):
        raise HTTPException(status_code=400, detail="工作目录无效")

    project_dir = os.path.join(work_dir, safe_name)
    # 二次校验: realpath 必须在 work_dir 下
    real_project = os.path.realpath(project_dir)
    real_work = os.path.realpath(work_dir)
    if not real_project.startswith(real_work + os.sep):
        raise HTTPException(status_code=403, detail="非法路径")

    if not os.path.isdir(project_dir):
        raise HTTPException(status_code=404, detail="项目不存在")
    id_data = read_project_idjson(project_dir)
    if id_data is None:
        raise HTTPException(status_code=500, detail="项目数据损坏")
    if "step" not in id_data:
        id_data["step"] = get_step_from_id(id_data)
        write_project_idjson(project_dir, id_data)
    return project_dir, id_data


def scan_projects() -> list[dict]:
    cfg = read_config()
    if cfg is None:
        return []
    work_dir = cfg.get("work_dir", "")
    if not work_dir or not os.path.isdir(work_dir):
        return []
    projects = []
    try:
        for entry in sorted(os.listdir(work_dir)):
            entry_path = os.path.join(work_dir, entry)
            if os.path.isdir(entry_path) and (
                os.path.exists(os.path.join(entry_path, "id.json")) or
                os.path.exists(os.path.join(entry_path, "id.txt"))
            ):
                ctime = os.path.getctime(entry_path)
                from datetime import datetime
                created_at = datetime.fromtimestamp(ctime).strftime("%Y-%m-%d %H:%M")
                id_data = read_project_idjson(entry_path) or {}
                step = get_step_from_id(id_data)
                projects.append({
                    "name": entry,
                    "name_encoded": quote(entry, safe=''),
                    "path": entry_path,
                    "created_at": created_at,
                    "step": step,
                })
    except Exception:
        pass
    return projects


def create_project(name: str) -> dict:
    """创建新项目，返回 {"status": "ok", "path": ...} 或 {"status": "error", "message": ...}。"""
    if not name:
        return {"status": "error", "message": "项目名称不能为空"}
    cfg = read_config()
    if cfg is None:
        return {"status": "error", "message": "请先配置工作目录"}
    work_dir = cfg.get("work_dir", "")
    if not work_dir or not os.path.isdir(work_dir):
        return {"status": "error", "message": "工作目录无效"}
    project_path = os.path.join(work_dir, name)
    if os.path.exists(project_path):
        return {"status": "error", "message": "项目已存在"}
    os.makedirs(project_path, exist_ok=True)
    id_data = {
        "name": name,
        "id": str(uuid.uuid4()),
        "step": init_step(),
    }
    write_project_idjson(project_path, id_data)
    return {"status": "ok", "path": project_path}
