# -*- coding: utf-8 -*-
"""智能素材分类系统 — FastAPI 后端入口。

重构后的架构:
  config.py        — 配置读写
  project.py       — 项目数据管理 (id.json, 步骤, 路径解析)
  fileops.py       — 文件操作 (安全删除, 复制进度, 对话框)
  media.py         — 媒体扫描, GPU 检测
  copy_service.py  — 后台复制服务
  batch.py         — 批处理 (照片/视频压缩+水印)
  main.py          — FastAPI 应用入口 + 路由注册 (本文件)
"""

import io
import logging
import os
import subprocess
import sys
import webbrowser
from datetime import datetime

# Windows 中文输出兼容
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
except Exception:
    pass

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("sort2")

import asyncio
import json
import os
import shutil
import requests
import uvicorn
from fastapi import FastAPI, Query, Request, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import read_config, write_config, DEFAULT_CONFIG, get_ffmpeg_path
from project import (
    STEPS_ORDER, STEPS_LABEL, STEPS_ICON, STEP_TEMPLATE,
    init_step, get_step_from_id, read_project_idjson, write_project_idjson,
    resolve_project, scan_projects, create_project,
)
from fileops import force_delete, run_folder_dialog, run_file_dialog
from media import scan_folder_stats, detect_gpu
from copy_service import start_copy, get_progress as get_copy_progress
from batch import _batch_progress, process_batch, set_sse_callback as batch_set_sse
from copy_service import set_sse_callback as copy_set_sse
import deliver_service
import filter_service
import ai_filter
import video_tagger
import photo_index
import photo_classify
import photo_classify_exec

# SSE 事件总线 — 简单的 async queue per project
_sse_queues: dict[str, list[asyncio.Queue]] = {}


def _sse_broadcast(project_key: str, event: dict):
    """向所有监听该项目的 SSE 客户端推送事件（线程安全）。

    队列满时不再静默丢弃新事件（否则拷贝期间洪泛可能丢掉终态 done，
    导致前端读条永久卡在最后一帧）；改为挤掉最旧一帧腾出空间，
    保证新事件（尤其 done/error 终态）一定能送达。
    被挤掉的是中间进度帧，前端进度条只是跳进，不会卡死。
    """
    for q in _sse_queues.get(project_key, []):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            try:
                q.get_nowait()  # 挤掉最旧一帧
            except asyncio.QueueEmpty:
                pass
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass


def _sse_push_batch(project_key: str, data: dict):
    """批处理进度推送（从子线程调用，线程安全）。"""
    event = dict(data)
    event["_type"] = "batch"
    _sse_broadcast(project_key, event)


def _sse_push_copy(project_key: str, data: dict):
    """复制进度推送（从子线程调用，线程安全）。"""
    event = dict(data)
    event["_type"] = "copy"
    _sse_broadcast(project_key, event)


# 注册 SSE 回调到子模块
batch_set_sse(_sse_push_batch)
copy_set_sse(_sse_push_copy)


def _sse_push_filter(project_key: str, data: dict):
    event = dict(data)
    event["_type"] = "filter"
    _sse_broadcast(project_key, event)


filter_service.set_sse_callback(_sse_push_filter)


def _sse_push_ai_filter(project_key: str, data: dict):
    event = dict(data)
    event["_type"] = "ai_filter"
    _sse_broadcast(project_key, event)


ai_filter.set_sse_callback(_sse_push_ai_filter)


def _sse_push_video_tagger(project_key: str, data: dict):
    event = dict(data)
    event["_type"] = "video_tagger"
    _sse_broadcast(project_key, event)


video_tagger.set_sse_callback(_sse_push_video_tagger)


def _sse_push_photo_classify(project_key: str, data: dict):
    event = dict(data)
    event["_type"] = "photo_classify"
    _sse_broadcast(project_key, event)


photo_classify_exec.set_sse_callback(_sse_push_photo_classify)


def _sse_push_deliver(project_key: str, data: dict):
    event = dict(data)
    event["_type"] = "deliver"
    _sse_broadcast(project_key, event)


deliver_service.set_sse_callback(_sse_push_deliver)

# ===========================================================================
# 应用初始化
# ===========================================================================

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")
START_TS = str(int(datetime.now().timestamp()))

app = FastAPI(title="智能素材分类系统", version="0.2.0")

templates = Jinja2Templates(directory=TEMPLATES_DIR)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def no_cache_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


# ===========================================================================
# 页面路由
# ===========================================================================

@app.get("/favicon.ico")
async def favicon():
    return FileResponse(os.path.join(STATIC_DIR, "favicon.png"), media_type="image/png")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"start_ts": START_TS})


@app.get("/main", response_class=HTMLResponse)
async def main_page(request: Request):
    return templates.TemplateResponse(request, "main.html", {"start_ts": START_TS})


# ===========================================================================
# 自检
# ===========================================================================

@app.post("/api/preflight")
async def api_preflight():
    checks = {"python": sys.version.split()[0], "fastapi": "ok"}
    try:
        from PIL import Image  # noqa: F401
        checks["pillow"] = "ok"
    except Exception as exc:
        checks["pillow"] = f"missing: {exc}"
    healthy = checks["pillow"] == "ok"
    return {
        "status": "ok" if healthy else "degraded",
        "message": "程序正常" if healthy else "部分依赖缺失，请检查环境",
        "checks": checks,
    }


# ===========================================================================
# 配置 API
# ===========================================================================

@app.get("/api/config")
async def api_get_config():
    cfg = read_config()
    if cfg is None:
        return {"exists": False, "config": DEFAULT_CONFIG}
    # 掩码 API 密钥
    masked = dict(cfg)
    key = masked.get("api_key", "")
    if key and len(key) > 8:
        masked["api_key_masked"] = True
        masked["api_key"] = key[:4] + "*" * 8 + key[-4:]
    return {"exists": True, "config": masked}


@app.post("/api/config")
async def api_save_config(request: Request):
    body = await request.json()
    # 如果 api_key 是掩码值，保留原密钥
    existing = read_config()
    if existing and (body.get("api_key_masked") or "*" in body.get("api_key", "")):
        body["api_key"] = existing.get("api_key", "")
    body.pop("api_key_masked", None)
    write_config(body)
    return {"status": "ok"}


# ===========================================================================
# 文件夹/文件选择
# ===========================================================================

@app.post("/api/pick-folder")
async def api_pick_folder():
    import asyncio
    loop = asyncio.get_event_loop()
    path = await loop.run_in_executor(None, run_folder_dialog)
    return {"status": "ok" if path else "cancel", "path": path or ""}


@app.post("/api/pick-file")
async def api_pick_file():
    import asyncio
    loop = asyncio.get_event_loop()
    path = await loop.run_in_executor(None, run_file_dialog)
    return {"status": "ok" if path else "cancel", "path": path or ""}


# ===========================================================================
# API 连接测试
# ===========================================================================

@app.post("/api/test-connection")
async def api_test_connection(request: Request):
    import asyncio
    body = await request.json()
    api_url = body.get("api_url", "").strip()
    api_key = body.get("api_key", "").strip()
    model = body.get("model", "").strip()

    # 如果密钥是掩码值，用原密钥
    if "*" in api_key:
        existing = read_config()
        if existing:
            api_key = existing.get("api_key", "")

    if not api_url:
        return {"status": "error", "message": "API 地址不能为空"}
    if not api_key:
        return {"status": "error", "message": "API 密钥不能为空"}

    use_fast = body.get("is_fast", False)
    no_cot = body.get("fast_model_no_cot", False)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    messages = [{"role": "user", "content": "Hi"}]
    if use_fast and no_cot:
        messages.insert(0, {"role": "system", "content": "请直接回答，不要进行逐步推理。"})
    payload = {"model": model, "messages": messages, "max_tokens": 5}

    loop = asyncio.get_event_loop()

    def _test():
        try:
            resp = requests.post(api_url, json=payload, headers=headers, timeout=15)
            if resp.status_code == 200:
                return {"status": "ok", "message": "连接成功"}
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text[:200]
            return {"status": "error", "message": f"返回状态码 {resp.status_code}：{detail}"}
        except requests.exceptions.Timeout:
            return {"status": "error", "message": "连接超时，请检查 API 地址和网络"}
        except requests.exceptions.ConnectionError:
            return {"status": "error", "message": "无法连接，请检查 API 地址是否正确"}
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    return await loop.run_in_executor(None, _test)


# ===========================================================================
# 项目 API
# ===========================================================================

@app.get("/api/projects")
async def api_get_projects():
    return {"projects": scan_projects()}


@app.get("/api/projects-table", response_class=HTMLResponse)
async def api_projects_table(request: Request):
    projects = scan_projects()
    tmpl = templates.env.get_template("panels/_project_rows.html")
    return HTMLResponse(tmpl.render(projects=projects))


@app.post("/api/project/new")
async def api_new_project(request: Request):
    body = await request.json()
    name = body.get("name", "").strip()
    return create_project(name)


@app.post("/api/project/open")
async def api_open_project(request: Request):
    body = await request.json()
    path = body.get("path", "")
    # 安全校验: 路径必须在 work_dir 下
    cfg = read_config()
    if cfg is None:
        return {"status": "error", "message": "配置缺失"}
    work_dir = cfg.get("work_dir", "")
    real_path = os.path.realpath(path)
    real_work = os.path.realpath(work_dir)
    if not real_path.startswith(real_work + os.sep):
        return {"status": "error", "message": "非法路径"}
    if os.path.exists(real_path):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(real_path)])
        return {"status": "ok"}
    return {"status": "error", "message": "路径不存在"}


@app.post("/api/project/delete")
async def api_delete_project(request: Request):
    body = await request.json()
    path = body.get("path", "")
    if not os.path.exists(path) or not os.path.isdir(path):
        return {"status": "error", "message": "路径不存在"}
    # 安全校验: 路径必须在 work_dir 下
    cfg = read_config()
    if cfg is None:
        return {"status": "error", "message": "配置缺失"}
    work_dir = cfg.get("work_dir", "")
    real_path = os.path.realpath(path)
    real_work = os.path.realpath(work_dir)
    if not real_path.startswith(real_work + os.sep):
        return {"status": "error", "message": "非法路径：不在工作目录下"}
    try:
        force_delete(real_path)
    except Exception as e:
        logger.exception("删除失败")
        return {"status": "error", "message": f"删除失败: {str(e)}"}
    return {"status": "ok"}


# ===========================================================================
# 项目详情页 & 进度
# ===========================================================================

@app.get("/project/{name:path}", response_class=HTMLResponse)
async def project_page(request: Request, name: str):
    project_dir, id_data = resolve_project(name)
    current_step = get_step_from_id(id_data)
    from urllib.parse import quote
    return templates.TemplateResponse(request, "project.html", {
        "start_ts": START_TS,
        "project_name": name,
        "project_name_encoded": quote(name, safe=''),
        "current_step": current_step,
        "steps_order": STEPS_ORDER,
        "steps_label": STEPS_LABEL,
        "steps_icon": STEPS_ICON,
    })


@app.get("/api/project/{name:path}/progress")
async def api_get_progress(name: str):
    _, id_data = resolve_project(name)
    return {"status": "ok", "step": get_step_from_id(id_data)}


@app.post("/api/project/{name:path}/progress")
async def api_update_progress(request: Request, name: str):
    body = await request.json()
    step = body.get("step", 0)
    try:
        step = int(step)
    except (ValueError, TypeError):
        return {"status": "error", "message": "步骤号必须为数字"}
    if step < 1 or step > len(STEPS_ORDER):
        return {"status": "error", "message": f"步骤号超出范围 (1-{len(STEPS_ORDER)})"}
    project_dir, id_data = resolve_project(name)
    id_data["step"] = step
    write_project_idjson(project_dir, id_data)
    return {"status": "ok", "step": step}


# ===========================================================================
# 文件夹信息
# ===========================================================================

@app.post("/api/folder-info")
async def api_folder_info(request: Request):
    body = await request.json()
    folder = body.get("path", "").strip()
    if not folder or not os.path.isdir(folder):
        return {"status": "error", "message": "无效的文件夹路径"}
    folder_name = os.path.basename(folder)
    total_size = 0
    item_count = 0
    for root, _, files in os.walk(folder):
        for f in files:
            try:
                total_size += os.path.getsize(os.path.join(root, f))
                item_count += 1
            except Exception:
                pass
    return {
        "status": "ok",
        "folderName": folder_name,
        "itemCount": item_count,
        "totalSizeGB": round(total_size / (1024 ** 3), 2),
    }


@app.get("/api/project/{name:path}/folder-stats")
async def api_folder_stats(name: str):
    project_dir, id_data = resolve_project(name)
    folder_meta = {f["name"]: f for f in id_data.get("folders", [])}
    folders = []
    total_photos = 0
    total_videos = 0
    total_size = 0
    for entry in os.scandir(project_dir):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        meta = folder_meta.get(entry.name, {})
        stats = scan_folder_stats(entry.path)
        total_photos += stats["photo_count"]
        total_videos += stats["video_count"]
        total_size += stats["total_size_bytes"]
        folders.append({
            "name": entry.name,
            "author": meta.get("author", ""),
            "photoCount": stats["photo_count"],
            "videoCount": stats["video_count"],
            "totalSizeBytes": stats["total_size_bytes"],
            "totalSizeGB": round(stats["total_size_bytes"] / (1024 ** 3), 2),
        })
    folders.sort(key=lambda x: x["name"])
    return {
        "status": "ok",
        "folders": folders,
        "totalFiles": total_photos + total_videos,
        "totalPhotos": total_photos,
        "totalVideos": total_videos,
        "totalSizeGB": round(total_size / (1024 ** 3), 2),
    }


# ===========================================================================
# 步骤面板
# ===========================================================================

@app.get("/api/project/{name:path}/panel/{step:int}", response_class=HTMLResponse)
async def project_panel(request: Request, name: str, step: int):
    if step < 1 or step > len(STEPS_ORDER):
        raise HTTPException(status_code=400, detail="无效步骤号")
    template_name = f"panels/{STEP_TEMPLATE[step]}.html"
    extra = {}
    if step == 1:
        try:
            project_dir, id_data = resolve_project(name)
            extra["step1_locked"] = id_data.get("step1_locked", False)
            folder_meta = {f["name"]: f for f in id_data.get("folders", [])}
            subfolders = []
            for entry in os.scandir(project_dir):
                if entry.is_dir() and not entry.name.startswith('.'):
                    meta = folder_meta.get(entry.name, {})
                    subfolders.append({
                        "name": entry.name,
                        "author": meta.get("author", ""),
                        "addWatermark": meta.get("addWatermark", True),
                        "compressPhoto": meta.get("compressPhoto", True),
                        "compressVideo": meta.get("compressVideo", True),
                    })
            subfolders.sort(key=lambda x: x["name"])
            extra["subfolders"] = subfolders
        except Exception:
            extra["subfolders"] = []
    return templates.TemplateResponse(request, template_name, {
        "project_name": name,
        "current_step": step,
        **extra,
    })


@app.post("/api/project/{name:path}/lock-step1")
async def api_lock_step1(name: str):
    project_dir, id_data = resolve_project(name)
    id_data["step1_locked"] = True
    write_project_idjson(project_dir, id_data)
    return {"status": "ok"}


# ===========================================================================
# 收集 — 复制素材
# ===========================================================================

@app.post("/api/project/{name:path}/collect-copy")
async def api_collect_copy(request: Request, name: str):
    body = await request.json()
    source_path = body.get("sourcePath", "").strip()
    folder_name = body.get("folderName", "").strip()
    author = body.get("author", "").strip()
    add_watermark = body.get("addWatermark", True)
    compress_photo = body.get("compressPhoto", True)
    compress_video = body.get("compressVideo", True)

    if not source_path or not os.path.isdir(source_path):
        return {"status": "error", "message": "源文件夹无效"}
    if not folder_name:
        return {"status": "error", "message": "文件夹名称不能为空"}

    project_dir, id_data = resolve_project(name)
    dest_path = os.path.join(project_dir, folder_name)

    for existing in id_data.get("folders", []):
        if existing["name"] == folder_name:
            if existing["author"] != author:
                return {"status": "error", "message": f"文件夹「{folder_name}」已存在，作者必须与之前一致"}
            if existing.get("addWatermark") != add_watermark:
                return {"status": "error", "message": f"文件夹「{folder_name}」已存在，水印设置必须与之前一致"}
            if existing.get("compressPhoto") != compress_photo:
                return {"status": "error", "message": f"文件夹「{folder_name}」已存在，压缩照片设置必须与之前一致"}
            if existing.get("compressVideo") != compress_video:
                return {"status": "error", "message": f"文件夹「{folder_name}」已存在，压缩视频设置必须与之前一致"}

    try:
        os.makedirs(dest_path, exist_ok=True)
    except Exception as e:
        return {"status": "error", "message": f"创建目标文件夹失败: {str(e)}"}

    start_copy(name, source_path, dest_path, folder_name, author,
               add_watermark, compress_photo, compress_video)
    return {"status": "ok", "message": "拷贝任务已启动"}


@app.post("/api/project/{name:path}/folder/{folder_name:path}/update")
async def api_folder_update(request: Request, name: str, folder_name: str):
    body = await request.json()
    new_name = body.get("name", "").strip()
    if not new_name:
        return {"status": "error", "message": "名称不能为空"}
    author = body.get("author", "").strip()
    add_watermark = body.get("addWatermark", True)
    compress_photo = body.get("compressPhoto", True)
    compress_video = body.get("compressVideo", True)

    project_dir, id_data = resolve_project(name)
    folders = id_data.get("folders", [])
    target = None
    for f in folders:
        if f["name"] == folder_name:
            target = f
            break
    if target is None:
        return {"status": "error", "message": f"文件夹「{folder_name}」不存在"}

    if new_name != folder_name:
        old_dir = os.path.join(project_dir, folder_name)
        new_dir = os.path.join(project_dir, new_name)
        if os.path.exists(new_dir):
            return {"status": "error", "message": f"文件夹「{new_name}」已存在"}
        os.rename(old_dir, new_dir)
        target["name"] = new_name

    target["author"] = author
    target["addWatermark"] = add_watermark
    target["compressPhoto"] = compress_photo
    target["compressVideo"] = compress_video
    write_project_idjson(project_dir, id_data)
    return {"status": "ok"}


@app.get("/api/project/{name:path}/collect-copy-progress")
async def api_collect_copy_progress(name: str):
    p = get_copy_progress(name)
    if p is None:
        return {"status": "not_found"}
    return {
        "status": "ok",
        "total": p["total"],
        "done": p["done"],
        "current_pct": p["current_pct"],
        "current_file": p["current_file"],
        "current_file_done": p["current_file_done"],
        "current_file_total": p["current_file_total"],
        "task_status": p["status"],
        "message": p["message"],
    }


@app.post("/api/project/{name:path}/deliver-start")
async def api_deliver_start(request: Request, name: str):
    """交付：将 图片素材/视频素材 整体拷贝到用户指定的输出目录并校验。"""
    body = await request.json()
    dest_dir = body.get("destDir", "").strip()
    if not dest_dir or not os.path.isdir(dest_dir):
        return {"status": "error", "message": "目标输出目录无效，请先选择"}
    project_dir, _ = resolve_project(name)
    deliver_service.start_deliver(name, project_dir, dest_dir)
    return {"status": "ok", "message": "交付已启动"}


@app.get("/api/project/{name:path}/deliver-progress")
async def api_deliver_progress(name: str):
    data = deliver_service.get_progress(name)
    if data is None:
        return {"status": "not_found"}
    return data


@app.post("/api/project/{name:path}/deliver-reset")
async def api_deliver_reset(name: str):
    deliver_service.reset(name)
    return {"status": "ok"}


# ===========================================================================
# 批处理
# ===========================================================================

@app.post("/api/project/{name:path}/start-batch")
async def api_start_batch(name: str):
    project_dir, id_data = resolve_project(name)
    folders = id_data.get("folders", [])
    if not folders:
        return {"status": "error", "message": "项目中无文件夹"}
    t = __import__("threading")
    t.Thread(target=process_batch, args=(project_dir, folders, name), daemon=True).start()
    return {"status": "ok"}


@app.get("/api/project/{name:path}/batch-progress")
async def api_batch_progress(name: str):
    data = _batch_progress.get(name)
    if data is None:
        return {"status": "not_found"}
    return data


@app.post("/api/project/{name:path}/filter-start")
async def api_filter_start(name: str):
    project_dir, id_data = resolve_project(name)
    total = filter_service.count_filterable_files(project_dir)
    filter_service.start_filter(project_dir, name)
    return {"status": "ok", "message": "筛检已启动", "total": total}


@app.get("/api/project/{name:path}/filter-count")
async def api_filter_count(name: str):
    project_dir, id_data = resolve_project(name)
    total = filter_service.count_filterable_files(project_dir)
    return {"total": total}


@app.get("/api/project/{name:path}/filter-progress")
async def api_filter_progress(name: str):
    data = filter_service.get_progress(name)
    if data is None:
        return {"status": "not_found"}
    return data


@app.get("/api/project/{name:path}/ai-filter-count")
async def api_ai_filter_count(name: str):
    project_dir, id_data = resolve_project(name)
    total = ai_filter.count_ai_filterable_photos(project_dir)
    return {"total": total}


@app.post("/api/project/{name:path}/ai-filter-start")
async def api_ai_filter_start(name: str):
    project_dir, id_data = resolve_project(name)
    total = ai_filter.count_ai_filterable_photos(project_dir)
    ai_filter.start_ai_filter(project_dir, name)
    return {"status": "ok", "message": "AI 筛检已启动", "total": total}


@app.get("/api/project/{name:path}/ai-filter-progress")
async def api_ai_filter_progress(name: str):
    data = ai_filter.get_progress(name)
    if data is None:
        return {"status": "not_found"}
    return data


# ===========================================================================
# AI 筛检 — 分类结果.json 读写公共助手
# ===========================================================================

CLASSIFY_RESULT_FILE = "分类结果.json"


def _load_classify_result(project_dir: str) -> list | None:
    """读取 分类结果.json，返回列表；文件缺失/损坏/格式错误返回 None。"""
    results_path = os.path.join(project_dir, CLASSIFY_RESULT_FILE)
    if not os.path.isfile(results_path):
        return None
    try:
        with open(results_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, Exception):
        return None
    if not isinstance(data, list):
        return None
    return data


def _save_classify_result(project_dir: str, data: list) -> bool:
    """写回 分类结果.json，成功返回 True。"""
    try:
        with open(os.path.join(project_dir, CLASSIFY_RESULT_FILE), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def _classify_summary(data: list) -> dict:
    """统计各类废片数量 (type 2/3/4)。"""
    summary = {"type2": 0, "type3": 0, "type4": 0}
    for item in data:
        t = item.get("type")
        if t == 2:
            summary["type2"] += 1
        elif t == 3:
            summary["type3"] += 1
        elif t == 4:
            summary["type4"] += 1
    return summary


@app.get("/api/project/{name:path}/ai-filter-summary")
async def api_ai_filter_summary(name: str):
    """读取 分类结果.json，汇总各类废片数量。"""
    project_dir, id_data = resolve_project(name)
    data = _load_classify_result(project_dir)
    if data is None:
        return {"exists": False, "type2": 0, "type3": 0, "type4": 0}
    return {"exists": True, **_classify_summary(data)}


@app.post("/api/project/{name:path}/video-tagger/start")
async def api_video_tagger_start(name: str):
    """启动视频标签提取任务。"""
    project_dir, id_data = resolve_project(name)
    video_tagger.start_video_tagger(project_dir, name)
    return {"status": "ok", "message": "视频标签提取已启动"}


@app.get("/api/project/{name:path}/video-tagger-status")
async def api_video_tagger_progress(name: str):
    """获取视频标签提取进度（独立后缀，避免被 /progress 路由遮蔽）。"""
    data = video_tagger.get_progress(name)
    if data is None:
        return {"status": "not_found"}
    return data


@app.get("/api/project/{name:path}/video-tagger/result")
async def api_video_tagger_result(name: str):
    """读取 视频标签结果.json。"""
    project_dir, id_data = resolve_project(name)
    results_path = os.path.join(project_dir, "视频标签结果.json")
    if not os.path.isfile(results_path):
        return {"exists": False, "tags": {}}
    try:
        with open(results_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, Exception):
        return {"exists": False, "tags": {}}
    return {"exists": True, "tags": data.get("视频标签结果", {})}


@app.get("/api/project/{name:path}/videos")
async def api_list_videos(name: str):
    """列出项目内所有视频（相对路径），只扫描 视频素材 目录。"""
    project_dir, id_data = resolve_project(name)
    video_dir = os.path.join(project_dir, "视频素材")
    videos: list[str] = []
    if os.path.isdir(video_dir):
        for root, _, files in os.walk(video_dir):
            for fn in files:
                ext = os.path.splitext(fn)[1].lower()
                if ext in video_tagger.VIDEO_EXTS:
                    videos.append(os.path.relpath(os.path.join(root, fn), project_dir))
    return {"videos": videos}


@app.get("/api/project/{name:path}/ai-filter-photos")
async def api_ai_filter_photos(name: str, type: int = Query(...)):
    """读取 分类结果.json，返回指定类型的照片相对路径列表。"""
    project_dir, id_data = resolve_project(name)
    data = _load_classify_result(project_dir)
    if data is None:
        return {"paths": []}
    paths = [
        item["path"]
        for item in data
        if item.get("type") == type
    ]
    return {"paths": paths}


@app.get("/api/project/{name:path}/photo-file/{path:path}")
async def api_photo_file(name: str, path: str):
    """按项目相对路径返回图片文件。做路径安全检查。"""
    project_dir, id_data = resolve_project(name)
    abs_path = os.path.normpath(os.path.join(project_dir, path))
    if not abs_path.startswith(os.path.normpath(project_dir) + os.sep) and abs_path != os.path.normpath(project_dir):
        raise HTTPException(status_code=403, detail="路径越权")
    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail="文件不存在")
    ext = os.path.splitext(path)[1].lower()
    media_types = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".gif": "image/gif",
        ".webp": "image/webp", ".bmp": "image/bmp",
        ".tiff": "image/tiff", ".tif": "image/tiff",
    }
    mime = media_types.get(ext, "application/octet-stream")
    return FileResponse(abs_path, media_type=mime)


@app.post("/api/project/{name:path}/ai-filter-rescue")
async def api_ai_filter_rescue(name: str, request: Request):
    """将某张废片标记为通过（type 2/3/4 → 1）。"""
    body = await request.json()
    target_path = body.get("path", "").strip()
    project_dir, id_data = resolve_project(name)
    data = _load_classify_result(project_dir)
    if data is None:
        return {"status": "error", "message": "分类结果不存在或格式错误"}
    changed = False
    for item in data:
        if item.get("path") == target_path and item.get("type") in (2, 3, 4):
            item["type"] = 1
            changed = True
            break
    if not changed:
        return {"status": "error", "message": "未找到匹配项或已是通过"}
    if not _save_classify_result(project_dir, data):
        return {"status": "error", "message": "写入分类结果失败"}
    return {"status": "ok", "summary": _classify_summary(data)}


@app.post("/api/project/{name:path}/ai-filter-confirm")
async def api_ai_filter_confirm(name: str):
    """确认废片结果：将 type 2/3/4 的文件移入废弃物，锁定 step2，前进到 step3。"""
    project_dir, id_data = resolve_project(name)
    data = _load_classify_result(project_dir)
    if data is None:
        return {"status": "error", "message": "分类结果不存在或格式错误"}
    waste_dir = os.path.join(project_dir, "废弃物")
    os.makedirs(waste_dir, exist_ok=True)
    moved = 0
    for item in data:
        t = item.get("type")
        if t not in (2, 3, 4):
            continue
        rel_path = item.get("path", "")
        if not rel_path:
            continue
        src = os.path.normpath(os.path.join(project_dir, rel_path))
        if not src.startswith(os.path.normpath(project_dir) + os.sep):
            continue
        if os.path.isfile(src):
            try:
                base = os.path.basename(rel_path)
                dst = os.path.join(waste_dir, base)
                # 避免重名
                if os.path.exists(dst):
                    name_only, ext = os.path.splitext(base)
                    dst = os.path.join(waste_dir, f"{name_only}_{moved}{ext}")
                shutil.move(src, dst)
                moved += 1
            except Exception as e:
                logger.warning("移动失败: %s → %s (%s)", src, dst, e)
    id_data["step"] = 3
    id_data["step2_locked"] = True
    write_project_idjson(project_dir, id_data)
    logger.info("废片确认完成: 移动 %d 个文件到废弃物，已前进到 step3", moved)

    # step3 前置处理: 构建照片索引并提取视频/照片到 视频素材/图片素材
    extract_result = {"status": "ok", "videos_moved": 0, "photos_moved": 0, "photos_total": 0}
    try:
        extract_result = photo_index.extract_to_material_dirs(project_dir, id_data)
        logger.info("step3 素材提取完成: 视频 %d 个, 照片 %d 张",
                    extract_result.get("videos_moved", 0),
                    extract_result.get("photos_moved", 0))
    except Exception as e:
        logger.exception("step3 素材提取失败: %s", e)
        extract_result = {"status": "error", "videos_moved": 0, "photos_moved": 0, "photos_total": 0}

    return {
        "status": "ok",
        "moved": moved,
        "step": 3,
        "photosMoved": extract_result.get("photos_moved", 0),
        "videosMoved": extract_result.get("videos_moved", 0),
        "extractStatus": extract_result.get("status", "error"),
    }


@app.post("/api/project/{name:path}/photo-material-init")
async def api_photo_material_init(name: str):
    """step3 素材前置初始化（幂等）：构建照片索引并提取视频/照片到 视频素材/图片素材。

    正常流程由 ai-filter-confirm 在进入 step3 时同步执行；本接口供面板加载时兜底，
    老项目（step 已为 3 但未提取）也会自动补齐。重复调用安全：已提取的跳过。
    """
    project_dir, id_data = resolve_project(name)
    try:
        result = photo_index.extract_to_material_dirs(project_dir, id_data)
    except ValueError as e:
        return {"status": "error", "message": str(e)}
    except Exception as e:
        logger.exception("素材前置初始化失败")
        return {"status": "error", "message": f"素材前置初始化失败: {e}"}
    return {"status": "ok", **result}


@app.get("/api/project/{name:path}/photo-folder-tree")
async def api_photo_folder_tree(name: str):
    """照片分类：返回「图片素材」文件夹结构树（数据只读 index.json，纯显示）。

    根节点即 图片素材；photos[].path 相对其逐级构建层级，文件节点 fileType=image。
    """
    project_dir, _ = resolve_project(name)
    root = {"name": photo_index.PHOTO_DIR, "path": "", "type": "folder", "children": []}
    folders: dict = {}

    def sort_children(node: dict) -> None:
        node["children"].sort(key=lambda c: (c["type"] != "folder", c["name"]))
        for c in node["children"]:
            if c["type"] == "folder":
                sort_children(c)

    for e in photo_index.read_index(project_dir) or []:
        rel = (e.get("path") or "").replace("\\", "/").strip("/")
        if not rel:
            continue
        parts = rel.split("/")
        parent = root
        cur = ""
        for part in parts[:-1]:
            cur = f"{cur}/{part}" if cur else part
            if cur not in folders:
                folders[cur] = {"name": part, "path": cur, "type": "folder", "children": []}
                parent["children"].append(folders[cur])
            parent = folders[cur]
        parent["children"].append({
            "name": e.get("name") or parts[-1],
            "path": rel,
            "type": "file",
            "fileType": "image",
        })

    sort_children(root)
    return {"status": "ok", "tree": root}


@app.post("/api/project/{name:path}/photo-classify/plan")
async def api_photo_classify_plan(request: Request, name: str):
    """照片分类计划拆解 — 调用快速模型。

    请求体:
      {
        "original_request": "用户第一次输入的完整要求",
        "clarifications": [{"question": "AI 提问", "answer": "用户回答"}, ...]
      }
    作者名单自动从项目 index.json 提取并注入提示词。
    返回:
      {"status": "ok", "result": {...}}  或  {"status": "error", "message": "..."}
    """
    body = await request.json()
    original_request = (body.get("original_request") or "").strip()
    if not original_request:
        return {"status": "error", "message": "原始要求不能为空"}
    clarifications = body.get("clarifications")
    if not isinstance(clarifications, list):
        clarifications = []
    project_dir, _ = resolve_project(name)
    authors = photo_index.list_authors(project_dir)
    loop = asyncio.get_event_loop()

    def _call():
        return photo_classify.call_plan(original_request, clarifications, authors)

    try:
        result = await loop.run_in_executor(None, _call)
    except Exception as e:
        logger.warning("照片分类计划拆解失败: %s", e)
        return {"status": "error", "message": str(e)}
    return {"status": "ok", "result": result}


@app.post("/api/project/{name:path}/photo-classify/execute")
async def api_photo_classify_execute(request: Request, name: str):
    """开始执行 — 用户点「开始执行」即接受 AI 方案。

    请求体: {"original_request": "...", "plan": {...}}
    动作: 方案落盘为 分类方案.json（唯一权威）→ 启动后台 agent 循环。
    """
    body = await request.json()
    original_request = (body.get("original_request") or "").strip()
    plan = body.get("plan")
    if not isinstance(plan, dict) or not plan.get("steps"):
        return {"status": "error", "message": "分类方案缺失或无效"}
    project_dir, _ = resolve_project(name)

    # 已在执行中 → 拒绝重复启动
    cur = photo_classify_exec.get_progress(name)
    if cur and cur.get("status") == "running":
        return {"status": "error", "message": "分类任务正在执行中"}

    try:
        plan_path = photo_classify_exec.save_plan(project_dir, plan, original_request)
    except Exception as e:
        logger.exception("分类方案落盘失败")
        return {"status": "error", "message": f"方案保存失败: {e}"}

    photo_classify_exec.start_classify(project_dir, name, plan, original_request)
    logger.info("照片分类执行已启动: %s 方案已存 %s", name, plan_path)
    return {"status": "ok", "message": "分类任务已启动", "plan_file": photo_classify_exec.PLAN_FILE}


@app.get("/api/project/{name:path}/photo-classify/progress")
async def api_photo_classify_progress(name: str):
    """照片分类执行进度快照。"""
    data = photo_classify_exec.get_progress(name)
    if data is None:
        return {"status": "not_found"}
    return data


@app.get("/api/project/{name:path}/photo-classify/logs")
async def api_photo_classify_logs(name: str):
    """照片分类完整执行日志（提示词/AI输出/工具调用），供面板恢复渲染。"""
    return {"status": "ok", "logs": photo_classify_exec.get_logs(name)}


@app.post("/api/project/{name:path}/photo-classify/retry-confirm")
async def api_photo_classify_retry_confirm(request: Request, name: str):
    """用户答复「API 失败是否继续重试」弹窗。body: {"continue": true/false}"""
    body = await request.json()
    ok = photo_classify_exec.confirm_retry(name, bool(body.get("continue")))
    return {"status": "ok" if ok else "not_found"}


@app.get("/api/project/{name:path}/photo-classify/plan-saved")
async def api_photo_classify_plan_saved(name: str):
    """查询是否已有落盘的分类方案（供面板恢复执行态）。"""
    project_dir, _ = resolve_project(name)
    data = photo_classify_exec.load_plan(project_dir)
    if data is None:
        return {"exists": False}
    return {
        "exists": True,
        "saved_at": data.get("saved_at"),
        "original_request": data.get("original_request"),
        "plan": data.get("plan"),
    }


@app.get("/api/project/{name:path}/events")
async def api_sse_events(name: str):
    """SSE 端点 — 实时推送批处理和复制进度。"""
    q = asyncio.Queue(maxsize=64)
    _sse_queues.setdefault(name, []).append(q)

    async def event_stream():
        # 连接时立即推送当前状态
        batch_data = _batch_progress.get(name)
        if batch_data:
            yield f"event: batch\ndata: {json.dumps(batch_data, ensure_ascii=False)}\n\n"
        filter_data = filter_service.get_progress(name)
        if filter_data:
            yield f"event: filter\ndata: {json.dumps(filter_data, ensure_ascii=False)}\n\n"
        ai_filter_data = ai_filter.get_progress(name)
        if ai_filter_data:
            yield f"event: ai_filter\ndata: {json.dumps(ai_filter_data, ensure_ascii=False)}\n\n"
        copy_data = get_copy_progress(name)
        if copy_data:
            yield f"event: copy\ndata: {json.dumps(copy_data, ensure_ascii=False)}\n\n"
        deliver_data = deliver_service.get_progress(name)
        if deliver_data:
            yield f"event: deliver\ndata: {json.dumps(deliver_data, ensure_ascii=False)}\n\n"
        video_tagger_data = video_tagger.get_progress(name)
        if video_tagger_data:
            yield f"event: video_tagger\ndata: {json.dumps(video_tagger_data, ensure_ascii=False)}\n\n"
        photo_classify_data = photo_classify_exec.get_progress(name)
        if photo_classify_data:
            yield f"event: photo_classify\ndata: {json.dumps(photo_classify_data, ensure_ascii=False)}\n\n"
        # 若正在等待用户答复「是否继续重试」，重连/刷新后补发弹窗事件
        if photo_classify_exec.has_pending_confirm(name):
            yield f"event: photo_classify\ndata: {json.dumps({'status': 'api_confirm', 'message': 'API 连续调用失败（可能因限流或超时），是否继续重试？'}, ensure_ascii=False)}\n\n"
        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"event: {event.get('_type', 'progress')}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    # 心跳
                    yield f": heartbeat\n\n"
        finally:
            if name in _sse_queues:
                try:
                    _sse_queues[name].remove(q)
                except ValueError:
                    pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


# ===========================================================================
# 启动
# ===========================================================================

def _open_browser():
    url = "http://127.0.0.1:1145"
    logger.info("正在打开浏览器: %s", url)
    webbrowser.open(url)


if __name__ == "__main__":
    import threading
    # 一键启动脚本（启动.bat）已用 Edge 应用模式打开界面，传入 --no-browser 避免重复弹出默认浏览器。
    if "--no-browser" not in sys.argv:
        threading.Timer(1.5, _open_browser).start()
    # 关闭 reload：任务运行期间（step1 拷贝/批处理/交付）会让 watchfiles 检测到文件变化从而反复重启服务，
    # 重启会杀掉在途线程并清空 SSE 事件队列，导致 step1 读条中断卡住。开发时改完代码手动重启即可。
    uvicorn.run("main:app", host="127.0.0.1", port=1145, reload=False)
