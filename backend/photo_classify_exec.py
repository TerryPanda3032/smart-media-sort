# -*- coding: utf-8 -*-
"""照片分类执行 — 后端 AI 调度编排（无 function-calling，AI 只输出结构化分组）。

任务来源: 用户在计划对话里与快模型多轮协商出的 plan（点「开始执行」时
已落盘为 分类方案.json）。

编排模型（双 AI：验收 AI 定 step，分类 AI 只执行该 step）:
  验收调度循环：从第一轮开始就由验收 AI 决定「下一步做 step 几」。
    - 首轮：先验收，让验收 AI 指明第一步（通常为 step 1，完成第一级分类）。
    - 验收 AI 输入：计划清单(全部 steps) + 当前目录结构 + 历史进度记忆文件；
      输出："step N"（只做哪个步骤）。
    - 程序把 step 通过 parent_folder 映射到目标目录，分类 AI 只看到该 step 的内容
      （看不到方案其它步骤，杜绝越层/一步到位）。
    - 循环直至验收 AI 宣布 done（所有步骤达成）。

每批照片数 BATCH_LIMIT，会话独立、一次调用出结果:
  1. 从 index 取当前文件夹下尚未分类的照片，取前 BATCH_LIMIT 张
     （每批重新编号 1~N）
  2. 单档压缩成临时文件（最长边 <=960px、JPEG q20，10KB 量级；
     项目下隐藏目录 .photo_classify_tmp，index 扫描自动跳过）
  3. 组装全新消息（与上一批完全隔离，不带上批任何内容）:
     - system: 分类规则（当前层约束）+ 当前 step 内容 + 输出格式
     - user 文本: 本批照片的 编号|作者|拍摄时间；已存在类别及每类 3 张样片说明
     - 附图: 各类别样片图 + 本批照片图（按编号顺序）
  4. AI 输出结构化分组（类别块 + 编号行，"编号 = 新名" 表示重命名，
     是否重命名由 AI 依据方案自行判断，无开关）
  5. 后端解析 → 按编号找回原文件 → 后端执行搬移（AI 只分类不移动）
     → 同步 index（time/author 锁死不动）→ 删除本批临时文件
"""

import base64
import json
import logging
import os
import re
import shutil
import threading
import time
from io import BytesIO

import requests

from config import read_config
from sse_service import ProgressState

import photo_index

# 复用 photo_classify 的 JSON 提取（含未转义引号修复），用于解析验收 AI 的结构化输出
from photo_classify import _extract_json

logger = logging.getLogger(__name__)

BATCH_LIMIT = 35          # 每批照片数
SAMPLE_PER_CAT = 2        # 每个类别取样张数
MAX_IMAGES_PER_BATCH = 45 # 单批图片总数上限（样张 + 本批照片），样张多时须缩小本批
MAX_PARSE_RETRY = 3       # 单批解析失败/遗漏重试次数
MAX_BATCHES = 2000        # 批次安全上限
MAX_ROUNDS = 200          # AI 调度循环安全上限（防止死循环）

# 验收 AI 目标返回的内部哨兵：本轮选择无效（需下轮重新验收）。
# 与「根目录（""）」和「任务完成（None）」区分开，避免语义冲突。
_INVALID_TARGET = "_AC_SKIP_INVALID"

PLAN_FILE = "分类方案.json"
# 剩余计划清单：进度即「清单里还剩下哪些 step 没做」，做完的 step 从清单删掉。
# 不再使用轮次工作记忆。
REMAINING_FILE = "分类剩余步骤.json"

_progress = ProgressState()

# ---------------------------------------------------------------------------
# 执行日志 — 每轮完整上下文（提示词/AI输出/搬移结果），供右侧面板展示
# ---------------------------------------------------------------------------

_logs: dict[str, list] = {}
LOG_LIMIT = 3000   # 内存中最多保留条数


def get_logs(project_key: str) -> list[dict]:
    return list(_logs.get(project_key, []))


def _log(project_key: str, batch_no: int, kind: str, text) -> None:
    """追加一条日志并经 SSE 实时推送（不覆盖进度快照）。

    kind: system / user / assistant / info / error
    """
    entry = {
        "ts": time.strftime("%H:%M:%S"),
        "batch": batch_no,
        "kind": kind,
        "text": str(text),
    }
    buf = _logs.setdefault(project_key, [])
    buf.append(entry)
    if len(buf) > LOG_LIMIT:
        del buf[: len(buf) - LOG_LIMIT]
    _progress.broadcast(project_key, status="log", entry=entry)


def set_sse_callback(fn):
    _progress.set_sse_callback(fn)


def get_progress(project_key: str) -> dict | None:
    return _progress.get(project_key)


def _push(project_key: str, **kwargs):
    _progress.push(project_key, **kwargs)


def _broadcast(project_key: str, **kwargs):
    """仅推送事件，不覆盖进度快照。"""
    _progress.broadcast(project_key, **kwargs)


# ---------------------------------------------------------------------------
# API 失败升级重试 — 内部 1s/3s/5s → 弹窗问用户 → 确认后 10s → 循环
# ---------------------------------------------------------------------------

_INTERNAL_BACKOFFS = (1, 3, 5)   # 内部重试间隔
_POST_CONFIRM_WAIT = 10          # 用户确认继续后的等待秒数

# 等待用户确认的状态: {project_key: {"event": Event, "confirmed": bool}}
_confirm_states: dict[str, dict] = {}


def has_pending_confirm(project_key: str) -> bool:
    return project_key in _confirm_states


def confirm_retry(project_key: str, cont: bool) -> bool:
    """用户答复弹窗：cont=True 继续重试，False 停止任务。"""
    st = _confirm_states.get(project_key)
    if not st:
        return False
    st["confirmed"] = bool(cont)
    st["event"].set()
    return True


def _request_user_confirm(project_key: str, batch_no: int) -> bool:
    """弹窗请求用户决定是否继续重试；阻塞直到用户答复。返回是否继续。

    若 5 分钟无答复（SSE 断连 / 用户切走面板），自动视为继续，避免任务永久挂起。
    """
    ev = threading.Event()
    _confirm_states[project_key] = {"event": ev, "confirmed": False}
    try:
        _log(project_key, batch_no, "error",
             "API 连续调用失败（已按 1/3/5 秒重试均失败），等待用户决定是否继续")
        _broadcast(project_key, status="api_confirm",
                   message="API 连续调用失败（可能因限流或超时），是否继续重试？")
        if not ev.wait(300):  # 5 分钟无答复 → 自动继续
            _log(project_key, batch_no, "warning",
                 "等待用户确认超时（5 分钟），自动继续重试")
            return True
        return _confirm_states[project_key]["confirmed"]
    finally:
        _confirm_states.pop(project_key, None)


# ===========================================================================
# 索引访问：文件夹内未分类照片 / 子类别（支持递归）
# ===========================================================================

def _photos_real(project_dir: str) -> str:
    return os.path.realpath(os.path.join(project_dir, photo_index.PHOTO_DIR))


def _prefix(folder_rel: str) -> str:
    """返回 index 路径前缀；空字符串表示根目录。"""
    f = folder_rel.strip("/").replace("\\", "/")
    return f + "/" if f else ""


def _photos_in_folder(project_dir: str, folder_rel: str) -> list[dict]:
    """返回指定文件夹下尚未归入子类的照片记录列表。

    folder_rel="" 表示根目录(图片素材)；folder_rel="A" 表示 A/ 下。
    """
    entries = photo_index.read_index(project_dir) or []
    prefix = _prefix(folder_rel)
    out = []
    for e in entries:
        rel = (e.get("path") or "").replace("\\", "/").strip("/")
        if not rel:
            continue
        if prefix:
            if not rel.startswith(prefix):
                continue
            remaining = rel[len(prefix):]
        else:
            remaining = rel
        if "/" in remaining:
            continue  # 已归入子类/子目录
        if not remaining:
            continue
        out.append({
            "path": rel,
            "name": e.get("name") or remaining,
            "time": e.get("time"),
            "author": e.get("author"),
        })
    # 按拍摄时间升序注入（time 形如 "2025-10-26 19:26"，字符串序即时间序），
    # 让同一连续时空的照片相邻，AI 可据此识别断点；缺时间的排最后，用 path 兜底稳定排序。
    out.sort(key=lambda x: (1 if not (x.get("time") or "").strip() else 0,
                            x.get("time") or "",
                            x["path"]))
    return out


def _subcategories(project_dir: str, folder_rel: str, sample: int = SAMPLE_PER_CAT) -> list[dict]:
    """返回指定文件夹下的子类别（来自 index 真值层），每类挑最多 sample 张样片。

    folder_rel="" 返回根目录下一级类别；folder_rel="A" 返回 A/ 下的子类别。
    """
    entries = photo_index.read_index(project_dir) or []
    prefix = _prefix(folder_rel)
    by_name: dict[str, list[str]] = {}
    for e in entries:
        rel = (e.get("path") or "").replace("\\", "/").strip("/")
        if not rel:
            continue
        if prefix:
            if not rel.startswith(prefix):
                continue
            remaining = rel[len(prefix):]
        else:
            remaining = rel
        if "/" not in remaining:
            continue
        folder = remaining.split("/", 1)[0]
        if folder.startswith("."):
            continue
        by_name.setdefault(folder, []).append(rel)
    cats = []
    for name in sorted(by_name.keys()):
        rels = sorted(by_name[name])
        cats.append({"name": name, "count": len(rels), "samples": rels[:sample]})
    return cats


def _count_all_loose(project_dir: str, folder_rel: str = "") -> int:
    """递归统计某目录下（含所有子类别）的零散照片总数，用于计算整体进度。"""
    n = len(_photos_in_folder(project_dir, folder_rel))
    for cat in _subcategories(project_dir, folder_rel):
        sub = f"{folder_rel}/{cat['name']}" if folder_rel else cat["name"]
        n += _count_all_loose(project_dir, sub)
    return n


def _list_folders_with_loose(project_dir: str, folder_rel: str = "",
                             acc: list | None = None) -> list:
    """递归返回所有「有零散照片」的文件夹相对路径列表（含根目录 ""）。

    用于给验收 AI 作为可选目标白名单，避免 AI 选到没有零散照片的文件夹。
    """
    if acc is None:
        acc = []
    if _photos_in_folder(project_dir, folder_rel):
        acc.append(folder_rel)
    for cat in _subcategories(project_dir, folder_rel):
        sub = f"{folder_rel}/{cat['name']}" if folder_rel else cat["name"]
        _list_folders_with_loose(project_dir, sub, acc)
    return acc


# ===========================================================================
# 搬移执行（可选重命名）+ index 同步
# ===========================================================================

_BAD_CHARS = re.compile(r'[\\/:*?"<>|]')


def _safe_filename(name: str, ext: str) -> str | None:
    """清洗 AI 给出的新文件名；非法返回 None。"""
    nn = _BAD_CHARS.sub("", str(name).strip())
    if not nn or nn in (".", ".."):
        return None
    if not os.path.splitext(nn)[1]:
        nn += ext
    return nn


def _unique_path(dst: str) -> str:
    if not os.path.exists(dst):
        return dst
    stem, ext = os.path.splitext(dst)
    n = 2
    while os.path.exists(f"{stem}({n}){ext}"):
        n += 1
    return f"{stem}({n}){ext}"


def execute_assignments(project_dir: str, assignments: list) -> dict:
    """按 分组结果 由后端搬移原文件（AI 只分类，移动由后端执行）并同步 index。

    assignments: [{"photo_path": 根目录下文件名, "category": 类别名,
                   "new_name": 可选（AI 给了就重命名）}]
    """
    if not isinstance(assignments, list) or not assignments:
        return {"status": "error", "message": "assignments 为空"}
    real = _photos_real(project_dir)
    os.makedirs(real, exist_ok=True)
    ops: list[dict] = []
    moved = renamed = 0
    errors: list[str] = []
    for item in assignments:
        if not isinstance(item, dict):
            errors.append("非法条目")
            continue
        src_rel = (item.get("photo_path") or "").replace("\\", "/").strip("/")
        if not src_rel:
            errors.append(f"非法源路径: {src_rel}")
            continue
        category_raw = (item.get("category") or "").strip().strip("/").replace("\\", "/")
        cat_segments = [s for s in category_raw.split("/") if s and s not in (".", "..")]
        if not cat_segments:
            errors.append(f"非法类别名: {category_raw!r}")
            continue
        if any(s.startswith(".") for s in cat_segments):
            errors.append(f"非法类别名(含隐藏段): {category_raw!r}")
            continue
        # 程序硬性禁止同名目录：新建子类名（最后一段）若与路径上已有目录段同名，
        # 自动追加数字后缀，杜绝同名嵌套死循环（如 节目表演/.../节目表演）。
        base_seg = cat_segments[-1]
        ancestors = cat_segments[:-1]
        if base_seg in ancestors:
            n = 2
            candidate = f"{base_seg}{n}"
            while candidate in ancestors:
                n += 1
                candidate = f"{base_seg}{n}"
            cat_segments[-1] = candidate
        category = "/".join(cat_segments)
        src_abs = photo_index._path_abs(project_dir, src_rel)
        if not src_abs or not os.path.isfile(src_abs):
            errors.append(f"源文件不存在: {src_rel}")
            continue
        cat_dir = os.path.join(real, category)
        try:
            os.makedirs(cat_dir, exist_ok=True)
        except Exception as e:
            errors.append(f"创建类别目录失败 {category}: {e}")
            continue
        # AI 给了新名就重命名（是否重命名由 AI 依据方案自行判断）
        final_name = os.path.basename(src_rel)
        if item.get("new_name"):
            cleaned = _safe_filename(item["new_name"], os.path.splitext(final_name)[1])
            if cleaned:
                final_name = cleaned
                renamed += 1
        dst_abs = _unique_path(os.path.join(cat_dir, final_name))
        try:
            shutil.move(src_abs, dst_abs)
        except Exception as e:
            errors.append(f"移动失败 {src_rel}: {e}")
            continue
        dst_rel = os.path.relpath(dst_abs, real).replace("\\", "/")
        ops.append({"from": src_rel, "to": dst_rel})
        moved += 1
    if ops:
        photo_index.sync_index_from_operations(project_dir, ops)  # 只改 name/path
    result = {"status": "ok", "moved": moved, "renamed": renamed}
    if errors:
        result["errors"] = errors
    return result


# ===========================================================================
# 图片压缩临时层 — 单档压缩（~10KB、<1000px），落到临时目录
# ===========================================================================

TMP_DIR_NAME = ".photo_classify_tmp"   # 项目根下隐藏临时目录（index 扫描自动跳过）

# 唯一一档压缩参数: (最长边px, JPEG质量) — 单次压缩，无阶梯
# 实测样本均值 ~13.6KB，即 10KB 量级
COMPRESS_MAX_PX = 960
COMPRESS_QUALITY = 20


def _tmp_root(project_dir: str) -> str:
    return os.path.join(project_dir, TMP_DIR_NAME)


def _clean_tmp(project_dir: str):
    """删除整个压缩临时目录（任务启动清残留 / 任务结束兜底）。"""
    shutil.rmtree(_tmp_root(project_dir), ignore_errors=True)


def _compress_to_tmp(src_abs: str, tmp_dir: str) -> str | None:
    """单档压缩: 最长边 <=960px、JPEG q20（10KB 量级），返回临时文件路径；失败 None。

    AI 只看这个压缩图；后续搬移/重命名仍作用于原文件。
    """
    try:
        from PIL import Image
        with Image.open(src_abs) as img:
            if img.mode != "RGB":
                img = img.convert("RGB")
            w, h = img.size
            if w > COMPRESS_MAX_PX or h > COMPRESS_MAX_PX:
                ratio = min(COMPRESS_MAX_PX / w, COMPRESS_MAX_PX / h)
                img = img.resize((int(w * ratio), int(h * ratio)),
                                 Image.Resampling.LANCZOS)
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=COMPRESS_QUALITY, optimize=True)
            data = buf.getvalue()
        stem = os.path.splitext(os.path.basename(src_abs))[0]
        dst = os.path.join(tmp_dir, f"{stem}.jpg")
        n = 1
        while os.path.exists(dst):  # 同名冲突追加序号
            dst = os.path.join(tmp_dir, f"{stem}_{n}.jpg")
            n += 1
        with open(dst, "wb") as f:
            f.write(data)
        return dst
    except Exception as e:
        logger.warning("压缩失败 %s: %s", src_abs, e)
        return None


def _append_images(project_dir: str, messages: list, rel_paths: list[str],
                   caption: str, tmp_dir: str) -> int:
    """把若干照片压缩后以 image 部件附到 messages 末尾；返回实际附图数量。"""
    parts = []
    for rel in rel_paths:
        src_abs = photo_index._path_abs(project_dir, rel)
        if not src_abs or not os.path.isfile(src_abs):
            continue
        comp = _compress_to_tmp(src_abs, tmp_dir)
        if not comp:
            continue
        with open(comp, "rb") as f:
            data = f.read()
        b64 = base64.b64encode(data).decode("utf-8")
        parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        })
    if parts:
        messages.append({
            "role": "user",
            "content": [{"type": "text", "text": caption}, *parts],
        })
    return len(parts)


# ===========================================================================
# 提示词与消息组装
# ===========================================================================

def build_system_prompt(plan: dict, batch_size: int,
                        folder_rel: str = "",
                        active_step: dict | None = None) -> str:
    """每批只注入验收 AI 指定的当前步骤（active_step），避免其他步骤干扰分类 AI。

    folder_rel="" 表示根目录；folder_rel="A" 表示正在处理 A/ 下的照片。
    active_step: 验收 AI 指定的当前步骤 dict；提供时只注入该步骤内容，
                 分类 AI 看不到方案里其它步骤，杜绝越层/一步到位。
    """
    summary = (plan.get("summary") or "").strip()
    location = f"当前正在处理：「{folder_rel}/」（根目录下）" if folder_rel else "当前正在处理根目录（图片素材）"

    # 当前层职责：由验收指定的当前步骤推导本批所能使用的大类目录名。
    if isinstance(active_step, dict):
        step_no = active_step.get("step")
        active_desc = (active_step.get("description") or "").strip()
        active_scope = (active_step.get("scope") or "").strip()
        active_result = (active_step.get("expected_result") or "").strip()
        folders = active_step.get("folders") or []
        fnames = [str(f.get("name") or "").strip() for f in folders if f.get("name")]
        plan_block = ["【当前这一步（步骤 %s）】" % step_no]
        if active_desc:
            plan_block.append(active_desc)
        if active_scope:
            plan_block.append("范围：" + active_scope)
        if fnames:
            plan_block.append("只能把照片归入本步规定的这些类别之一：")
            for f in folders:
                nm = str(f.get("name") or "").strip()
                cr = str(f.get("criteria") or "").strip()
                plan_block.append(f"- 「{nm}」" + (f"：{cr}" if cr else ""))
        else:
            plan_block.append("本步没有预设类别名，由你看图在本层新建直接子类。")
        if active_result:
            plan_block.append("期望结果：" + active_result)
        plan_text = "\n".join(plan_block)
        plan_title = "【当前步骤内容】"
        if fnames:
            scope_line = ("- 当前层职责（首要）：本批只做这一级分类。本层只能使用这些大类目录名："
                          + "、".join(f'"{n}"' for n in fnames)
                          + "，只可将照片归入其中之一或该类之下已经存在的子类，不要新建超出本步范围的类别。\n")
        else:
            scope_line = ("- 当前层职责（首要）：本批只做这一级分类。把当前目录内的照片细分为若干直接子类"
                          "（子类名由你看图自行命名）。本层分类至此即为最终结果，不要越层建更深的目录。\n")
    else:
        # 兜底（无指定 step）：注入完整方案，不影响旧调用
        plan_text = json.dumps(plan, ensure_ascii=False, indent=2)
        plan_title = "【完整分类方案】"
        scope_line = "- 当前层职责：把本批照片归入当前层应有的直接类别即可。\n"
    composite_line = (
        "- 严禁用「大类名-子类名」拼接成一个目录名（例如\"节目表演-节目1\"），这会绕过本应存在的大类"
        "父目录。必须在本层先建成真正的大类，更细的细分等验收进入对应父目录后的后续轮次再做。\n"
    )

    # 当前路径上已有的目录名（含祖先），新子类名不得与之重复，防止同名嵌套死循环
    ancestors = [s for s in folder_rel.split("/") if s] if folder_rel else []
    forbid_text = "、".join(f'"{a}"' for a in ancestors) if ancestors else "（无）"
    cur_dir_name = ancestors[-1] if ancestors else ""
    special_syntax_line = (
        f"- 特殊语法：本批照片属于当前这层目录（{cur_dir_name}）且需重命名时，"
        "用 category 取当前目录名、photos 每项写 {\"index\": N, \"new_name\": \"新文件名\"}，"
        "代表仅原地重命名、不新建子目录。\n"
        if cur_dir_name else ""
    )
    return (
        "你是图片分类专家。把本批照片按【分类方案】分组。\n"
        f"{location}\n\n"
        "【重要：大环境】本批照片已按拍摄时间升序排列，编号顺序即时间顺序，相邻同属一段时空，"
        "时间或画面明显跳变即断点。本项目照片总量远大于本批，本批只是时间轴上的一个片段，"
        "前后还有更多批次；计划要求的某类照片可能分散在多个批次才集齐——本批没出现的类别不要硬造。\n"
        "\n"
        "【分类原则：精准、实事求是、不凑数】\n"
        "- 只按本批照片的真实内容分组：能明确归入某类的就归，本批真实有几个分组就分几个，不增不减。\n"
        "- 严禁为了凑满方案的类别数量或用户提到的数量，把一组拆成多个无依据的类别；也不要整批硬塞成很多类。\n"
        "- 允许整批只分出 1 个类别（哪怕整批都属于同一类）。分清比凑够更重要。\n"
        "- 严禁随意新建「方案之外」的类别；需要新类时，必须确实是本批画面中真实、独立、且与已有类别有明显差异的分组。\n"
        "- 类别的异同已在【当前步骤内容】里写清（易混淆点与关键差异）：相同之处要避免混淆，不同之处作为分类依据。\n"
        f"\n{scope_line}"
        f"{composite_line}\n"
        "\n"
        "【输出格式】严格按下面的 JSON 输出本批全部照片的分组，其余不要任何文字、"
        "不要 markdown 代码围栏（```）：\n"
        "{\n"
        '  "note": "一句话说明本批分类的依据与理由（按什么判了哪些范围/断点/做了哪些取舍）",\n'
        '  "groups": [\n'
        '    {"category": "类别名1", "photos": [{"index": 1}, {"index": 2}]},\n'
        '    {"category": "类别名2", "photos": [{"index": 3, "new_name": "新文件名"}]}\n'
        "  ]\n"
        "}\n"
        "\n"
        "字段说明：\n"
        '- "note"：简短（一两句）说明本批分类依据与理由。\n'
        '- "groups"：按类别分的数组，每个元素是一个类别。\n'
        '- "category"：类别名，必须是当前层应有的大类或已有类别，不得含「/」。\n'
        '- "photos"：该类别下的照片编号数组，每张照片一项；\n'
        '  不重命名为 {"index": 编号}；重命名为 {"index": 编号, "new_name": "新文件名"}。\n'
        "\n"
        "硬性要求：\n"
        f"- 全部编号(1~{batch_size})必须出现且各一次；每个编号只能出现在一个类别（本批照片必须全部分类，不许留空）。\n"
        "- 属于已有类别则复用其名；确实没有合适类别、且本批确有独立分组时，才新建有明确依据的类别。\n"
        '- 需要重命名时写 {"index": 编号, "new_name": "新文件名"}；new_name 不要写扩展名'
        "（例如不要写 xxx.jpg），程序会自动补上原图片扩展名（如 .jpg / .jpeg），"
        "确保结果仍是图片文件；不需要重命名的照片写成 {\"index\": 编号}。\n"
        "- 分类与重命名一次完成：方案要求重命名的照片（如特写编号），归入类别时就在 new_name 里写好新名。\n"
        "- 禁止新建与当前路径上任何一级目录同名的子目录。当前已有目录名："
        f"{forbid_text}。\n"
        f"{special_syntax_line}"
        "- 类别名必须属于当前层，不得含「/」，禁止把照片放进更深子目录。\n"
        f"\n"
        f"【分类方案总述】{summary}\n\n"
        f"{plan_title}\n"
        f"{plan_text}"
    )


def _fmt_photo_line(idx: int, p: dict) -> str:
    author = (p.get("author") or "").strip() or "未知"
    ts = (p.get("time") or "").strip() or "未知"
    return f"{idx} | 作者：{author} | 时间：{ts}"


def build_batch_messages(project_dir: str, plan: dict,
                         photos: list[dict], categories: list[dict],
                         tmp_dir: str, folder_rel: str = "",
                         active_step: dict | None = None) -> list:
    """组装一批的完整消息: system + 文字信息 + 样片图 + 本批图（每批全新）。

    active_step: 验收 AI 指定的当前步骤，透传进 system 提示，只注入该步骤。
    """
    lines = ["【本批待分类照片】编号 | 作者 | 拍摄时间（图片随后附上，按编号顺序对应）"]
    for i, p in enumerate(photos, 1):
        lines.append(_fmt_photo_line(i, p))
    lines.append("")
    if categories:
        lines.append("【已存在的类别】（每类的样片图片随后附上，归类时类别名必须与之完全一致）")
        for c in categories:
            lines.append(f"- {c['name']}（共 {c['count']} 张）")
    else:
        lines.append("【已存在的类别】目前没有任何类别，第一批将由你建立。")
    text = "\n".join(lines)

    messages = [
        {"role": "system",
         "content": build_system_prompt(plan, len(photos), folder_rel,
                                        active_step)},
        {"role": "user", "content": [{"type": "text", "text": text}]},
    ]
    # 样片图: 每个类别一条消息（编号随意，仅供识别）
    for c in categories:
        samples = c.get("samples") or []
        if samples:
            _append_images(project_dir, messages, samples,
                           f"类别「{c['name']}」的 {len(samples)} 张样片：", tmp_dir)
    # 本批图
    _append_images(project_dir, messages, [p["path"] for p in photos],
                   f"以下是本批 {len(photos)} 张待分类照片，按编号 1~{len(photos)} 顺序对应：",
                   tmp_dir)
    return messages


# ===========================================================================
# AI 输出解析
# ===========================================================================

# 编号行: "1" 或 "1 = 新名"（= 可为全角）
_RE_NUM_LINE = re.compile(r"^(\d{1,3})\s*(?:[=＝]\s*(.+?))?\s*$")
# 类别头行: 非空名称 + 半角/全角冒号结尾
_RE_CAT_LINE = re.compile(r"^(.{1,60}?)\s*[：:]\s*$")


def _parse_json_classification(text: str, batch_size: int) -> dict | None:
    """尝试按标准 JSON 分组解析；非 JSON 或结构不符返回 None。

    预期结构（与提示词一致）：
      {"note": "...", "groups": [{"category": "...", "photos": [{"index": N},
                    {"index": N, "new_name": "..."}]}]}
    返回与 parse_classification 相同的统一结构（含可选的 note）。
    复用 _extract_json 的引号修复，避免 note 等长文本里的未转义引号破坏整批 JSON。
    """
    data = _extract_json(text)
    if not isinstance(data, dict):
        return None
    raw = data.get("groups") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return None
    groups: list[tuple] = []
    seen: dict[int, tuple] = {}
    duplicated: list[int] = []
    has_cat = False
    for g in raw:
        if not isinstance(g, dict):
            continue
        cat = str(g.get("category") or "").strip()
        # 防跳层容错：若含路径分隔符，仅保留最后一段
        if "/" in cat or "\\" in cat:
            cat = cat.replace("\\", "/").rsplit("/", 1)[-1].strip()
        if not cat:
            continue
        has_cat = True
        groups.append((cat, []))
        for ph in (g.get("photos") or []):
            if isinstance(ph, dict):
                idx = ph.get("index")
                new_name = (ph.get("new_name") or "").strip() or None
            elif isinstance(ph, (int, float)):
                idx = ph
                new_name = None
            else:
                continue
            try:
                idx = int(idx)
            except (TypeError, ValueError):
                continue
            if not (1 <= idx <= batch_size):
                continue  # 越界编号忽略
            if idx in seen:
                duplicated.append(idx)
                continue
            seen[idx] = (cat, new_name)
    if not has_cat:
        return None
    for idx, (cat, new_name) in seen.items():
        for g_cat, g_items in groups:
            if g_cat == cat:
                g_items.append((idx, new_name))
                break
    missing = [i for i in range(1, batch_size + 1) if i not in seen]
    note = str(data.get("note") or "").strip() or None
    return {"groups": groups, "missing": missing, "duplicated": duplicated, "note": note}


def parse_classification(text: str, batch_size: int) -> dict:
    """解析 AI 的分组输出。

    优先解析标准 JSON 分组；若 AI 输出的是旧文本格式则回退文本解析。
    返回 {"groups": [(类别名, [(编号, 新名|None)])],
          "missing": [未覆盖编号], "duplicated": [重复编号]}
    编号越界、重复出现的以先出现为准（忽略后出现并记录）。
    """
    json_result = _parse_json_classification(text, batch_size)
    if json_result is not None:
        return json_result

    groups: list[tuple[str, list]] = []
    cur_cat = None
    seen: dict[int, tuple] = {}
    duplicated: list[int] = []
    if text.strip().startswith("```"):
        # 剥掉 markdown 代码围栏
        lines = text.strip().splitlines()
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        text = "\n".join(lines)
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _RE_NUM_LINE.match(line)
        if m and m.group(1) is not None:
            try:
                idx = int(m.group(1))
            except ValueError:
                continue
            if not (1 <= idx <= batch_size):
                continue  # 越界编号忽略
            if idx in seen:
                duplicated.append(idx)
                continue
            new_name = (m.group(2) or "").strip() or None
            if cur_cat is None:
                continue  # 编号出现在任何类别头之前 → 忽略
            seen[idx] = (cur_cat, new_name)
            continue
        mc = _RE_CAT_LINE.match(line)
        if mc:
            cat = mc.group(1).strip()
            # 防跳层容错：若 AI 输出含路径分隔符的类别名（如 "A/A3"），
            # 仅保留最后一段，强制归入当前层，避免在错误层级建目录。
            if "/" in cat or "\\" in cat:
                cat = cat.replace("\\", "/").rsplit("/", 1)[-1].strip()
            cur_cat = cat
            groups.append((cat, []))
            continue
        # 其他文字行忽略（容错）
    # 汇总每个类别的编号
    for idx, (cat, new_name) in seen.items():
        for g_cat, g_items in groups:
            if g_cat == cat:
                g_items.append((idx, new_name))
                break
    missing = [i for i in range(1, batch_size + 1) if i not in seen]
    return {"groups": groups, "missing": missing, "duplicated": duplicated, "note": None}


# ===========================================================================
# 模型调用（OpenAI 兼容，纯文本对话）
# ===========================================================================

def _call_model_once(api_url: str, api_key: str, model: str,
                     messages: list) -> tuple[str | None, dict | None]:
    """单次调用模型返回 (content, usage)；失败返回 (None, None)。

    会尝试从 config 读取 reasoning_effort 注入主力模型请求（思考强度）。
    若服务端不支持该参数导致请求失败，自动回退：去掉该参数重试一次，
    避免因未知字段影响分类。
    """
    cfg = read_config()
    effort = (cfg or {}).get("reasoning_effort") if cfg else "medium"
    if effort not in ("low", "medium", "high"):
        effort = "medium"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    base_payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 4096,
    }
    payload = {**base_payload, "reasoning_effort": effort}
    for attempt_payload in (payload, base_payload):
        try:
            resp = requests.post(api_url, json=attempt_payload,
                                 headers=headers, timeout=600)
            resp.raise_for_status()
            result = resp.json()
            usage = result.get("usage")
            choices = result.get("choices")
            if not isinstance(choices, list) or not choices:
                # choices 为空 + usage 全 0 + created 全 0 → 模型在推理前就被服务端拒绝，
                # 常见于不支持图片输入 / 消息格式不被接受（对分类这类必发图的任务尤其要留意）。
                u = result.get("usage") or {}
                if not u.get("total_tokens") and not result.get("created"):
                    logger.warning(
                        "服务端在推理前拒绝了请求（choices 为空、usage 全 0）：模型 %s 可能不接受本次消息，"
                        "请确认是支持图片输入的视觉模型。原始返回: %s",
                        model, str(result)[:400])
                raise ValueError(f"choices 缺失或为空: {str(result)[:300]}")
            msg = choices[0].get("message")
            if not isinstance(msg, dict):
                raise ValueError(f"message 异常: {str(result)[:300]}")
            content = (msg.get("content") or "").strip()
            if not content:
                raise ValueError("AI 返回空内容")
            return content, usage
        except Exception:
            if attempt_payload is base_payload:
                raise
    return None, None


def _call_model_resilient(api_url: str, api_key: str, model: str, messages: list,
                          project_key: str, batch_no: int) -> tuple[str | None, dict | None]:
    """带升级重试的调用: 失败 → 1s/3s/5s 逐次重试 → 仍失败弹窗问用户 →
    用户继续则 10s 后重试，再失败回到 1s/3s/5s → 弹窗，如此循环；
    用户停止则返回 (None, None)（任务中止）。成功返回 (content, usage)。
    usage 为本次调用链累计的 {"input_tokens": N, "output_tokens": N}。"""
    acc = {"input_tokens": 0, "output_tokens": 0}

    def _try() -> tuple[str | None, dict | None]:
        try:
            content, usage = _call_model_once(api_url, api_key, model, messages)
        except Exception as e:
            # 单次调用异常（含服务端返回空 choices 被拒等）视为可重试失败，
            # 交给外层 1s/3s/5s 退避 + 用户确认循环，而不是直接崩掉整批任务。
            logger.warning("模型调用异常（可重试）：%s", e)
            return None, acc
        if content is not None and usage:
            try:
                acc["input_tokens"] += int(usage.get("prompt_tokens") or 0)
                acc["output_tokens"] += int(usage.get("completion_tokens") or 0)
            except (TypeError, ValueError):
                pass
        return content, acc

    while True:
        content, acc_u = _try()
        if content is not None:
            return content, acc_u
        for wait in _INTERNAL_BACKOFFS:
            _log(project_key, batch_no, "info", f"API 调用失败，{wait} 秒后重试")
            time.sleep(wait)
            content, acc_u = _try()
            if content is not None:
                return content, acc_u
        # 内部重试全部失败 → 弹窗等待用户决定
        if not _request_user_confirm(project_key, batch_no):
            _log(project_key, batch_no, "error", "用户选择停止重试，任务中止")
            return None, acc
        _log(project_key, batch_no, "info", f"用户选择继续，{_POST_CONFIRM_WAIT} 秒后重试")
        time.sleep(_POST_CONFIRM_WAIT)
        # 若继续失败，回到循环顶部重新走 1s/3s/5s → 弹窗 的循环


# ===========================================================================
# 主执行循环（递归深度优先遍历）
# ===========================================================================

def start_classify(project_dir: str, project_key: str, plan: dict,
                   original_request: str):
    threading.Thread(
        target=_run_classify,
        args=(project_dir, project_key, plan, original_request),
        daemon=True,
    ).start()


def save_plan(project_dir: str, plan: dict, original_request: str) -> str:
    """把用户确认的方案落盘为 分类方案.json（唯一权威）。"""
    payload = {
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "original_request": original_request,
        "plan": plan,
    }
    path = os.path.join(project_dir, PLAN_FILE)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def load_plan(project_dir: str) -> dict | None:
    path = os.path.join(project_dir, PLAN_FILE)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _load_remaining(project_dir: str) -> list:
    """读取剩余计划清单（还没执行完的 step 列表）；缺失/损坏返回空。"""
    path = os.path.join(project_dir, REMAINING_FILE)
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            rem = data.get("remaining") if isinstance(data, dict) else None
            if isinstance(rem, list):
                return rem
        except Exception:
            pass
    return []


def _save_remaining(project_dir: str, steps: list) -> None:
    """写回剩余计划清单。"""
    path = os.path.join(project_dir, REMAINING_FILE)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "remaining": steps}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("保存剩余步骤清单失败: %s", e)


# ---------- 全局计数器（跨批次/跨文件夹）----------

def _run_classify(project_dir: str, project_key: str, plan: dict,
                  original_request: str):
    """入口：第一级分类 → 验收调度循环（AI 记录记忆 + 主动选择下一个目标）。"""
    try:
        project_dir = os.path.realpath(project_dir)
        if not os.path.isdir(project_dir):
            raise RuntimeError(f"项目目录不存在: {project_dir}")
        cfg = read_config()
        if not cfg:
            raise RuntimeError("无法读取 config.json")
        api_key = (cfg.get("api_key") or "").strip()
        if not api_key:
            raise RuntimeError("未配置 API 密钥")
        api_url = cfg.get("api_url") or ""
        model = cfg.get("model") or "Qwen/Qwen3.5-397B-A17B"

        _logs.pop(project_key, None)
        _log(project_key, 0, "user", f"用户原始要求：{original_request}")
        _log(project_key, 0, "info",
             "AI 调度编排：验收 AI 逐轮指定 step → 分类 AI 只围绕该 step 执行")

        _clean_tmp(project_dir)

        # 剩余计划清单：初始为全部 step，做完一个删一个（取代轮次工作记忆）。
        remaining = [s for s in (plan.get("steps") or [])]
        _save_remaining(project_dir, remaining)

        # 统计全项目零散照片总数，用于日志参考
        total_photos = _count_all_loose(project_dir)
        _log(project_key, 0, "info", f"共 {total_photos} 张零散照片待分类")

        total_steps = len(remaining)
        bc = _BatchCounter(project_key, project_dir, total_steps)
        _push_progress(bc, "开始执行")

        # 验收调度循环——由验收 AI 判定哪些 step 已完成（从清单删），并选出下一步。
        for rnd in range(1, MAX_ROUNDS + 1):
            active_step, folder, remaining = _accept_and_next(
                project_dir, project_key, remaining, bc, api_url, api_key, model)
            _save_remaining(project_dir, remaining)  # 验收判定的完成/清单变化落盘
            if active_step is None:
                break
            if active_step == _INVALID_TARGET:
                continue  # 本轮 step/folder 无效，下一轮重新验收
            label = folder if folder else "根目录（图片素材）"
            _log(project_key, bc.value(), "info",
                 f"══ 第 {rnd} 轮｜执行 step {active_step.get('step')}｜目标「{label}」══")
            _process_folder(project_dir, project_key, plan, folder,
                            api_url, api_key, model, bc, active_step)
            _push_progress(bc, f"完成 step {active_step.get('step')}「{label}」")
        else:
            _log(project_key, 0, "warning",
                 f"已达调度循环安全上限（{MAX_ROUNDS} 轮），强制结束")

        _log(project_key, 0, "info", "照片分类全流程完成")
        _push(project_key, status="done", total=0, done=0, percent=100,
              message="照片分类全部完成")
        logger.info("照片分类执行完成: %s", project_key)
    except Exception as e:
        logger.exception("照片分类执行失败")
        _log(project_key, 0, "error", f"执行失败：{e}")
        _push(project_key, status="error", message=str(e))
    finally:
        try:
            _clean_tmp(project_dir)
        except Exception:
            pass


# ---------- 全局批次计数器 + 进度推送辅助 ----------

class _BatchCounter:
    """跨文件夹/跨批次的统一递增计数器（线程安全），并累计 Token 用量。"""
    def __init__(self, project_key: str, project_dir: str, total_steps: int = 0):
        self.project_key = project_key
        self.project_dir = project_dir
        self.total_steps = total_steps
        self.input_tokens = 0
        self.output_tokens = 0
        self._lock = threading.Lock()
        self._val = 0

    def next(self) -> int:
        with self._lock:
            self._val += 1
            return self._val

    def value(self) -> int:
        with self._lock:
            return self._val

    def add_tokens(self, usage: dict | None):
        if not usage:
            return
        try:
            self.input_tokens += int(usage.get("input_tokens") or 0)
            self.output_tokens += int(usage.get("output_tokens") or 0)
        except (TypeError, ValueError):
            pass


def _push_progress(bc: _BatchCounter, message: str, status: str = "running"):
    """按「已从剩余清单移出(完成)的 step 数 / 总 step 数」计算进度百分比并推送。

    进度源来自 分类剩余步骤.json 的 remaining 长度，完成一个删一个。
    中途上限 99，完成由 done 事件收尾到 100。
    """
    remaining = _load_remaining(bc.project_dir)
    total = bc.total_steps
    done = max(0, total - len(remaining))
    pct = round(done / total * 100) if total else 0
    pct = max(0, min(pct, 99))  # 中途不上 100，完成由 done 事件收尾
    _push(bc.project_key, status=status, percent=pct, done=done, total=total,
          input_tokens=bc.input_tokens, output_tokens=bc.output_tokens,
          message=message)


# ---------- 文件夹内批处理循环（25 张一批，会话独立）----------

def _process_batch_loop(project_dir: str, project_key: str, plan: dict,
                        folder_rel: str, bc: _BatchCounter,
                        api_url: str, api_key: str, model: str,
                        active_step: dict | None = None) -> None:
    """对指定文件夹下的零散照片执行 25 张一批的编排循环。

    完成后文件夹内再无零散照片（全部分入子类别）。
    active_step: 验收 AI 指定的当前步骤，本循环分类只围绕该步骤。
    """
    pool = _photos_in_folder(project_dir, folder_rel)
    if not pool:
        _log(project_key, bc.value(), "info",
             f"「{folder_rel or '根目录'}」下无零散照片，跳过批处理")
        return

    total_local = len(pool)
    _log(project_key, bc.value(), "info",
         f"进入「{folder_rel or '根目录'}」：{total_local} 张零散照片等待分类")

    for _ in range(1, MAX_BATCHES + 1):
        pool = _photos_in_folder(project_dir, folder_rel)
        if not pool:
            break
        categories = _subcategories(project_dir, folder_rel)
        # 动态批次数量：样张总数 + 本批照片 <= MAX_IMAGES_PER_BATCH。
        # 样张多（类别多）时缩小本批照片数，避免单批图片过多。
        sample_total = sum(len(c["samples"]) for c in categories)
        batch_limit = BATCH_LIMIT
        if sample_total > 10:
            batch_limit = max(1, MAX_IMAGES_PER_BATCH - sample_total)
        photos = pool[:batch_limit]
        batch_no = bc.next()

        tmp_dir = os.path.join(_tmp_root(project_dir), f"batch_{batch_no:04d}")
        os.makedirs(tmp_dir, exist_ok=True)

        try:
            _log(project_key, batch_no, "info",
                 f"════ 第 {batch_no} 批｜{folder_rel or '根目录'}｜本批 {len(photos)} 张（样张 {sample_total}），剩余 {len(pool)} 张 ════")
            messages = build_batch_messages(
                project_dir, plan, photos, categories, tmp_dir, folder_rel, active_step)
            _log(project_key, batch_no, "system", messages[0]["content"])
            _log(project_key, batch_no, "user", messages[1]["content"][0]["text"])
            for extra in messages[2:]:
                t = extra["content"][0].get("text", "")
                _log(project_key, batch_no, "user", f"[附图] {t}")

            parsed = None
            for attempt in range(1, MAX_PARSE_RETRY + 1):
                content, usage = _call_model_resilient(
                    api_url, api_key, model, messages, project_key, batch_no)
                if not content:
                    raise RuntimeError("API 调用失败：用户已停止重试")
                bc.add_tokens(usage)
                _log(project_key, batch_no, "assistant", content)
                r = parse_classification(content, len(photos))
                if r.get("note"):
                    _log(project_key, batch_no, "info", f"[AI 依据] {r['note']}")
                if r["groups"] and not r["missing"]:
                    parsed = r
                    break
                miss = r["missing"]
                _log(project_key, batch_no, "info",
                     f"第 {attempt} 次输出不完整（未覆盖编号: "
                     f"{'、'.join(str(i) for i in miss) or '无'}），要求补充")
                # 最小化重发：重建精简消息，只重发缺失编号的照片图 + 编号清单，
                # 不再把整批照片图原样重传（图未变，重复传输浪费 token）。
                existing_lines = [
                    f"{idx} → {cat}"
                    for cat, items in r["groups"]
                    for idx, _ in items
                ]
                retry_lines = [
                    f"【本批共 {len(photos)} 张，编号 1~{len(photos)}】",
                    "你之前已给出的归属（必须原样保留、不得改动，也不得遗漏）：",
                    ("\n".join(existing_lines) if existing_lines else "（暂无）"),
                    "",
                    "以下编号尚未给出归属，请查看其照片后归类（照片随后附上）：",
                ]
                retry_lines += [_fmt_photo_line(i, photos[i - 1]) for i in miss]
                if categories:
                    retry_lines += [
                        "",
                        "【已存在的类别名】" + "、".join(c["name"] for c in categories),
                    ]
                retry_lines.append(
                    f"请严格按输出格式重新给出本批全部编号（1~{len(photos)}）的完整分组，"
                    "全部编号都必须出现且只出现一次，不要遗漏。")
                messages = [
                    {"role": "system",
                     "content": build_system_prompt(plan, len(photos), folder_rel,
                                                    active_step)},
                    {"role": "user",
                     "content": [{"type": "text", "text": "\n".join(retry_lines)}]},
                ]
                _append_images(project_dir, messages,
                               [photos[i - 1]["path"] for i in miss],
                               f"以下是缺失编号的照片，按编号 "
                               f"{'、'.join(str(i) for i in miss)} 顺序对应：",
                               tmp_dir)
                _log(project_key, batch_no, "info",
                     f"重试重发：仅 {len(miss)} 张缺失照片图 + 编号清单，未整批重传")
            if parsed is None:
                raise RuntimeError(
                    f"第 {batch_no} 批（{folder_rel or '根目录'}）：AI 连续 {MAX_PARSE_RETRY} 次未输出完整分组，已中止")

            if parsed["duplicated"]:
                _log(project_key, batch_no, "info",
                     "重复编号（以首次出现为准）: " + "、".join(str(i) for i in parsed["duplicated"]))

            assignments = []
            # 特殊语法识别：AI 输出唯一类别名与当前目录 basename 相同、且该组全部附带命名，
            # 表示"这些照片本就属于当前目录，仅做原地重命名，不新建子目录"。
            cur_dir_name = folder_rel.split("/")[-1] if folder_rel else ""
            special_rename = False
            if (
                cur_dir_name and len(parsed["groups"]) == 1
                and parsed["groups"][0][0] == cur_dir_name
                and parsed["groups"][0][1]
                and all(new_name for _, new_name in parsed["groups"][0][1])
            ):
                special_rename = True
                _log(project_key, batch_no, "info",
                     f"识别到特殊语法：类别「{cur_dir_name}」与本目录同名且全部附带命名，"
                     "仅执行原地重命名，不新建子目录")

            for cat, items in parsed["groups"]:
                # 特殊语法：目标即当前目录（folder_rel），照片留在本目录原地重命名
                if special_rename:
                    full_cat = folder_rel
                else:
                    # 子文件夹：类别名前缀（如 folder_rel="A" 时 "A子类"→"A/A子类"）
                    full_cat = f"{folder_rel}/{cat}" if folder_rel else cat
                for idx, new_name in items:
                    a = {"photo_path": photos[idx - 1]["path"], "category": full_cat}
                    if new_name:
                        a["new_name"] = new_name
                    assignments.append(a)
            summary_lines = [
                f"类别「{cat}」← {len(items)} 张："
                + "、".join(str(i) for i, _ in items)
                for cat, items in parsed["groups"]
            ]
            _log(project_key, batch_no, "info",
                 "分组结果：\n" + "\n".join(summary_lines))
            result = execute_assignments(project_dir, assignments)
            _log(project_key, batch_no, "info",
                 f"搬移完成：moved={result.get('moved')} renamed={result.get('renamed')}"
                 + (f" errors={result.get('errors')}" if result.get("errors") else ""))
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        _push_progress(bc, f"第 {batch_no} 批完成｜{folder_rel or '根目录'}")

    _log(project_key, bc.value(), "info",
         f"「{folder_rel or '根目录'}」零散照片已全部归类")


# ---------- 目录结构文本（供验收用）----------

def _build_tree_text(project_dir: str, folder_rel: str, indent: str = "") -> str:
    """递归构建当前目录结构的文本表示（只含文件夹层级，不含图片信息）。"""
    subcats = _subcategories(project_dir, folder_rel)
    lines = []
    if not folder_rel:
        lines.append("图片素材/（根目录）")
    else:
        lines.append(f"{indent}{folder_rel}/")
    for cat in subcats:
        sub_lines = _build_tree_text(project_dir,
                                     f"{folder_rel}/{cat['name']}" if folder_rel else cat["name"],
                                     indent + "  ")
        lines.extend(sub_lines)
    return lines


# ---------- 验收调度：读剩余清单选 step+folder ----------

def _format_steps(steps: list, project_dir: str | None = None) -> str:
    """把剩余计划清单格式化为给验收 AI 的文本。

    每个 step 保留描述/范围/目标目录。project_dir 提供时，额外标注该 step
    当前是否「还有待分类照片」——这是程序算出的客观事实，供验收判定完成。
    """
    if not steps:
        return "（已无剩余步骤）"
    blocks = []
    for s in steps:
        lines = [
            f"step {s.get('step')}：{(s.get('description') or '').strip()}"
        ]
        parent = (s.get("parent_folder") or "").strip()
        if parent:
            lines.append(f"  目标目录：{parent}")
        scope = (s.get("scope") or "").strip()
        if scope:
            lines.append(f"  范围：{scope}")
        folders = s.get("folders") or []
        if folders:
            names = "、".join(str(f.get("name") or "") for f in folders if f.get("name"))
            lines.append(f"  建议类别：{names}")
        if project_dir is not None:
            avail = _folder_candidates(project_dir, s)
            if avail:
                lines.append("  当前状态：还有待分类照片（可继续做）")
            else:
                lines.append("  当前状态：该步范围内已无待分类照片（可判定为本步完成）")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _find_step(steps: list, step_no) -> dict | None:
    """在 step 清单中按编号查找 step 对象；不在则返回 None。"""
    for _s in steps:
        if str(_s.get("step")) == str(step_no):
            return _s
    return None


def _step_base_of(step_obj: dict) -> str:
    """返回该 step 的目标基目录（parent_folder 归一；根步为 ""）。"""
    _parent = str(step_obj.get("parent_folder") or "").strip().strip("/").replace("\\", "/")
    if _parent in ("", "null", "None"):
        return ""
    return _parent


def _folder_candidates(project_dir: str, step_obj: dict) -> list:
    """动态推导该 step 可接受的目标目录集合（folder 合法范围）。

    计划阶段不预知父目录时使用 parent_scope="all_children"：候选 = 父层下所有
    仍有零散照片的直接子目录（如先分景点、再对每个景点细分）。否则候选 =
    单一父目录本身（有零散照片时）。返回相对 图片素材 的目录列表。
    """
    base = _step_base_of(step_obj)
    scope = (step_obj.get("parent_scope") or "exact").strip()
    out = []
    if scope == "all_children":
        for cat in _subcategories(project_dir, base):
            sub = f"{base}/{cat['name']}" if base else cat["name"]
            if _photos_in_folder(project_dir, sub):
                out.append(sub)
    else:
        if _photos_in_folder(project_dir, base):
            out.append(base)
    return out


def _normalize_folder(project_dir: str, folder) -> str | None:
    """把验收 AI 指定的目标目录规范化为相对 图片素材 的路径（folder 基准）。

    根目录的多种写法(""/"根目录"/"（根目录）"/带"图片素材/"前缀) → 返回 ""。
    非法（隐藏/越界/空指针）→ 返回 None。
    """
    if folder is None or isinstance(folder, (list, dict)):
        return None
    t = str(folder).strip().strip("/").replace("\\", "/")
    root = photo_index.PHOTO_DIR
    if t == root or t.startswith(root + "/"):
        t = t[len(root):].strip("/")
    if t in ("", "根目录", "（根目录）", "root", "Root"):
        return ""
    if t.startswith(".") or ".." in t.split("/"):
        return None
    real = _photos_real(project_dir)
    full = os.path.realpath(os.path.join(real, t))
    if full != real and not full.startswith(real + os.sep):
        return None
    return t


def _accept_and_next(project_dir: str, project_key: str, remaining: list,
                     bc: _BatchCounter,
                     api_url: str, api_key: str, model: str) -> tuple:
    """验收调度：读 剩余清单 + 目录树，判定已完成 step、选定下一步 step + 目录。

    不做轮次记忆——进度由剩余清单本身表示；step 的完成由验收 AI 判定
    （completed 字段），程序据此把对应 step 从清单删掉。

    返回：
      (step_obj, folder, remaining)
        step_obj 非 None 时：下一步在 folder 目录执行 step_obj；
        folder="" 表示根目录，否则为相对 图片素材 的子目录；
      (None, None, remaining)      — 任务完成（done=true 或 剩余清单已空）；
      (_INVALID_TARGET, None, remaining) — 本轮 step/folder 无效（下轮重新验收）。
      remaining 均为验收判定完成(completed)后更新过的清单。
    """
    batch_no = bc.next()
    if not remaining:
        _log(project_key, batch_no, "info", "[验收] 剩余清单为空，任务完成")
        return (None, None, remaining)

    tree_text = "\n".join(_build_tree_text(project_dir, ""))
    steps_text = _format_steps(remaining, project_dir)

    prompt = (
        "你是照片分类任务的调度员。请阅读【剩余计划】【当前目录结构】，完成三件事：\n"
        "说明：分类时照片按拍摄时间升序注入，同一连续时空相邻。"
        "当某 step 要求「按记录内容/场景归类」时，可依据时间连续性判定一组照片是否属于同一连续时空。\n"
        "1. 判定【剩余计划】里哪些 step 已完成（completed）——只能顺序判完成："
        "只能把【剩余计划】【最前面、连续的】step 判为完成。例如剩余为 1、2、3 时，"
        "只能判 1（或 1、2）已完成，绝不能只判 3 完成（1、2 还没做）。"
        "对照该 step 的状态：若标注「该步范围内已无待分类照片」即视为该步完成。\n"
        "2. 从【剩余计划】中选出下一步要执行的那个 step（step，必须是剩余清单里还没判为完成的）。\n"
        "3. 给出该 step 范围内具体在哪个目录干活（folder）——只能选【当前目录结构】里"
        "仍含待分类照片的目录。\n"
        "规则：\n"
        "- step 与已判完成（completed）不得重复；选剩余清单里最早的未完成 step 即可。\n"
        "- folder：必须是该 step 的目标目录中「还含待分类照片」的目录；第一级分类时 folder 为根目录（\"\" 或 \"（根目录）\"）。\n"
        "- 完成判定按顺序：某 step 判完成后才能判它后面那个（例如先判 1 完成，才能判 2 完成）。\n"
        "- 若【剩余计划】里已没有需要再执行的 step，则 step 为 null、folder 为 null、done 为 true。\n"
        "只输出 JSON，不要任何其他文字：\n"
        '{"step": N 或 null, "folder": "文件夹相对路径" 或 "" 或 null, "done": true 或 false, '
        '"completed": [本判定顺序完成的 step 编号数组，可空], "progress": "一句话说明进度与理由"}\n\n'
        f"【剩余计划】\n{steps_text}\n\n"
        f"【当前目录结构】\n{tree_text}"
    )
    messages = [
        {"role": "system", "content": "你是一位严谨的任务调度员，只输出 JSON。"},
        {"role": "user", "content": [{"type": "text", "text": prompt}]},
    ]
    content, usage = _call_model_resilient(api_url, api_key, model, messages,
                                           project_key, batch_no)
    bc.add_tokens(usage)
    if not content:
        raise RuntimeError("验收调度调用失败")
    _log(project_key, batch_no, "assistant", f"[验收] {content.strip()}")

    parsed = _extract_json(content)
    if not isinstance(parsed, dict):
        raise RuntimeError("验收 AI 返回非 JSON")

    progress = str(parsed.get("progress") or "").strip()
    done = bool(parsed.get("done"))
    step_no = parsed.get("step")

    # 1) 只允许顺序完成：只能删「剩余清单最前面连续」的已完成 step，防止跳步/漏步
    completed_raw = parsed.get("completed") or []
    completed_set = {int(c) for c in completed_raw if str(c).isdigit()}
    removed = []
    _i = 0
    while _i < len(remaining) and int(remaining[_i].get("step")) in completed_set:
        removed.append(remaining[_i])
        _i += 1
    if removed:
        _log(project_key, batch_no, "info",
             "[验收] 顺序判定完成并从清单移出 step："
             + "、".join(str(s.get("step")) for s in removed))
        remaining = remaining[_i:]

    # 2) 任务完成：done=true 或未给出有效 step
    if done or step_no is None or str(step_no).strip() in ("", "null", "None"):
        _log(project_key, batch_no, "info",
             f"[验收] 任务完成：{progress or '剩余清单已空或步骤已达成'}")
        return (None, None, remaining)

    step_obj = _find_step(remaining, step_no)
    # step 不在剩余清单中（可能刚被判定完成删掉/不存在）→ 本轮无效，下轮重新验收
    if step_obj is None:
        _log(project_key, batch_no, "warning",
             f"[验收] step {step_no} 不在剩余计划清单中（可能已完成），本轮选择无效")
        return (_INVALID_TARGET, None, remaining)

    # 3) folder 规范化 + 沙箱校验
    folder = _normalize_folder(project_dir, parsed.get("folder"))
    # 范围校验：folder 必须命中该 step 动态推导出的可接受目录集
    candidates = _folder_candidates(project_dir, step_obj)
    if folder is None or folder not in candidates:
        hint = "、".join(f"「{c or '根目录'}」" for c in candidates) or "（暂无可用目录）"
        _log(project_key, batch_no, "warning",
             f"[验收] step {step_obj.get('step')} 的目标目录「{folder or '空'}」不在该步可接受范围"
             f"（当前可选：{hint}），本轮选择无效")
        return (_INVALID_TARGET, None, remaining)

    if not _photos_in_folder(project_dir, folder):
        _log(project_key, batch_no, "info",
             f"[验收] step {step_obj.get('step')} 的目标目录「{folder or '根目录'}」当前已无零散照片，"
             "将交由该步执行（空批次会被跳过），请评估是否把它判为完成")

    label = folder if folder else "根目录（图片素材）"
    _log(project_key, batch_no, "info",
         f"[验收] 下一步 → 执行 step {step_obj.get('step')}（目标「{label}」）：{progress}")
    return (step_obj, folder, remaining)


# ---------- 单文件夹批处理（由验收调度循环驱动）----------

def _process_folder(project_dir: str, project_key: str, plan: dict,
                    folder_rel: str,
                    api_url: str, api_key: str, model: str,
                    bc: _BatchCounter,
                    active_step: dict | None = None) -> None:
    """处理一个指定文件夹：把它的零散照片全部分到子类别（不递归、不验收）。

    folder_rel="" 表示图片素材根目录；由验收调度循环把 step 映射到目录后调用。
    active_step: 本次分类只围绕该步骤执行。
    """
    _process_batch_loop(project_dir, project_key, plan, folder_rel,
                        bc, api_url, api_key, model, active_step)
