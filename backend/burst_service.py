# -*- coding: utf-8 -*-
"""连拍/相似图分组检测服务（step2 三级过滤的姊妹功能）。

对「图片素材」内 **三级过滤判定为 pass 的照片**（读 筛选结果.json）做相似图分组，
供用户在独立的「相似图组筛选模式」中逐组选留。本模块只写状态（连拍组.json），
**不移动任何文件** —— 废片的物理移动统一收口在 /ai-filter-confirm（与三级过滤一起）。

分组判据（SSCD 深度描述子，取代早期的 pHash + AKAZE 几何验证）:
  ① 每张图过一次 SSCD（sscd_disc_mixup，ResNet50，512 维 L2 归一化描述子）；
  ② 只在「顺序相邻」的照片之间比相似度 —— 连拍的本质是时间上连续的一串；
     而全局最近邻在同场活动数据上会把大量相似照片串成一张连通图（实测 281 张
     运动会照片：无论阈值怎么调，全局 top-K 要么只出一个巨团、要么一个都出不来）；
  ③ 相邻窗口内余弦相似度 >= SIM_THRESHOLD 的两张用并查集相连，取连通分量成组。
  最后保留 >= MIN_GROUP_SIZE 张的组；超过 MAX_GROUP_SIZE 的按原始顺序切块。

组语义是「时间上相邻、画面相似的一串」（连拍/连续抓拍）；顺序窗口天然阻断了
跨作者、跨时段的远距离相似图合并。

组内最佳推荐：复用 筛选结果.json 的 metrics（过曝/欠曝块数、全画幅 Tenengrad、
face_quality）加权排序，取分值最高一张标记 recommended（前端显示标签）。

连拍组.json 记录格式:
  {
    "file": "连拍组.json", "group_count": n,
    "groups": [{
      "id": 0, "author": "...", "count": m,
      "photos": [{"path": "图片素材/P0001.jpg", "name": "P0001.jpg",
                  "recommended": true, "keep": false}, ...]
    }]
  }
  keep 默认 false（全部舍弃 = 全红）；用户在面板单击切换。

依赖: onnxruntime + numpy + opencv-python-headless；
     模型 backend/models/sscd_disc_mixup.onnx（用 backend/export_sscd.py 一次性导出）。

SSE/轮询事件类型: burst
"""

import hashlib
import json
import logging
import os
import re
import threading

try:
    import cv2
    import numpy as np
    _HAVE_CV = True
except Exception:  # 依赖缺失时不致命，运行时报错提示
    cv2 = None
    np = None
    _HAVE_CV = False

try:
    import onnxruntime as ort
except Exception:
    ort = None

from sse_service import ProgressState
from photo_index import PHOTO_DIR, read_index
import quality_filter

logger = logging.getLogger(__name__)

# ===========================================================================
# 可调阈值 —— 全部集中在此，方便实测标定
# ===========================================================================

# ---- SSCD 描述子（backend/models/sscd_disc_mixup.onnx）----
SSCD_MODEL_NAME = "sscd_disc_mixup.onnx"
SSCD_INPUT_SIZE = 320            # 方形输入边长（官方 skew_320：直接缩放到 320×320）
SSCD_MEAN = (0.485, 0.456, 0.406)   # ImageNet 归一化均值（与官方预处理一致）
SSCD_STD = (0.229, 0.224, 0.225)    # ImageNet 归一化标准差

# ---- 相似判定 ----
SIM_THRESHOLD = 0.70         # 相邻帧余弦相似度下限（按自有数据标定：0.70 兼顾召回与准确）
SEQ_WINDOW = 8               # 顺序相邻窗口：每个位置只与其后 W 张比相似度

# ---- 分组门槛 ----
MIN_GROUP_SIZE = 2           # 一组最少张数（不足不成组）
MAX_GROUP_SIZE = 30          # 一组上限（防传递性并出的巨组，超出按顺序切块）

# ---- 文件 / 目录约定 ----
GROUPS_FILE = "连拍组.json"

_progress = ProgressState()
_session = None
_sess_lock = threading.Lock()
_run_lock = threading.Lock()
_running: set[str] = set()      # 正在检测的项目 key，避免同一项目重复起任务


def set_sse_callback(fn):
    _progress.set_sse_callback(fn)


def get_progress(project_key: str) -> dict | None:
    return _progress.get(project_key)


def is_running(project_key: str) -> bool:
    """该项目是否正在跑连拍检测（供三级过滤做互斥判断）。"""
    with _run_lock:
        return project_key in _running


def _push(project_key: str, **kwargs):
    _progress.push(project_key, **kwargs)


def _get_session():
    """惰性加载 SSCD ONNX 推理会话（CPU）。仅缓存成功结果 —— 模型后补到位时无需重启进程。"""
    global _session
    if _session is not None:
        return _session
    with _sess_lock:
        if _session is not None:
            return _session
        model = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "models", SSCD_MODEL_NAME)
        if not os.path.isfile(model):
            logger.warning("SSCD 模型缺失: %s", model)
            return False
        try:
            _session = ort.InferenceSession(model, providers=["CPUExecutionProvider"])
        except Exception as e:
            logger.warning("加载 SSCD 模型失败: %s", e)
            return False
    return _session


# ===========================================================================
# 文件读写
# ===========================================================================

def groups_path(project_dir: str) -> str:
    return os.path.join(project_dir, GROUPS_FILE)


def load_groups(project_dir: str) -> dict | None:
    """读取 连拍组.json；缺失/损坏返回 None。"""
    p = groups_path(project_dir)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("groups"), list):
        return None
    return data


def save_groups(project_dir: str, groups: list, fingerprint: str | None = None) -> None:
    """写入 连拍组.json。

    fingerprint 为候选集指纹（见 candidate_fingerprint）；面板据此判断快照是否过期。
    不传时沿用文件里已有的指纹 —— keep 状态变更不影响候选集，无需重算。
    """
    if fingerprint is None:
        old = load_groups(project_dir)
        fingerprint = (old or {}).get("fingerprint", "")
    payload = {"file": GROUPS_FILE, "group_count": len(groups),
               "fingerprint": fingerprint, "groups": groups}
    tmp = groups_path(project_dir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, groups_path(project_dir))


# ===========================================================================
# SSCD 描述子
# ===========================================================================

def _imread_unicode(path: str):
    """cv2.imread 在 Windows 中文路径下会失败，用 imdecode 兜底。

    注意：默认解码标志（不传 IMREAD_IGNORE_ORIENTATION）会让 OpenCV **自动应用
    JPEG 的 EXIF 朝向**，即读到的像素已是视觉正向，无需再手工旋转。
    """
    try:
        buf = np.fromfile(path, dtype=np.uint8)
        if buf.size == 0:
            return None
        return cv2.imdecode(buf, cv2.IMREAD_COLOR)
    except Exception:
        return None


def _embed(project_dir: str, rel_path: str):
    """读图 → 缩放 320×320 → ImageNet 归一化 → SSCD 推理 → 512 维单位描述子。

    失败（读图失败 / 模型缺失 / 推理异常）返回 None，该图不参与分组。
    """
    sess = _get_session()
    if not sess:
        return None
    img = _imread_unicode(os.path.join(project_dir, rel_path))
    if img is None:
        return None
    try:
        img = cv2.resize(img, (SSCD_INPUT_SIZE, SSCD_INPUT_SIZE),
                         interpolation=cv2.INTER_AREA)
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        img = (img - np.asarray(SSCD_MEAN, dtype=np.float32)) / np.asarray(SSCD_STD, dtype=np.float32)
        blob = np.ascontiguousarray(img.transpose(2, 0, 1)[None, ...])
        out = sess.run(None, {sess.get_inputs()[0].name: blob})[0][0]
    except Exception:
        return None
    v = np.asarray(out, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(v))
    return v / norm if norm > 0 else v


def _same(a, b) -> bool:
    """两图相似判定：SSCD 描述子余弦相似度 >= SIM_THRESHOLD。a/b 为 _embed 返回值。"""
    if a is None or b is None:
        return False
    return float(np.dot(a, b)) >= SIM_THRESHOLD


# ===========================================================================
# 分组：顺序相邻窗口内相似 → 并查集连通分量
# ===========================================================================

_P_RE = re.compile(r"^P(\d+)")


def _pnum(name: str) -> int:
    m = _P_RE.match(name or "")
    return int(m.group(1)) if m else 10 ** 9


class _DSU:
    """并查集（连通分量）。"""

    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, x: int) -> int:
        p = self.p
        while p[x] != x:
            p[x] = p[p[x]]
            x = p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def _sequential_groups(items: list[dict], feats: list, alive: list[int]) -> list[list[int]]:
    """顺序相邻窗口内的相似照片 → 连通分量成组。

    连拍是「时间上连续的一串」；只在每个 alive 位置的前后 SEQ_WINDOW 张内比相似度，
    既能容忍被三级过滤剔除后留下的空档，又阻断全局最近邻那种把整场活动串成一坨的
    远距离合并。items 已按 (author, P 序号) 排序，作者切换即跳出内层循环。
    """
    dsu = _DSU(len(items))
    n = len(alive)
    for k in range(n):
        i = alive[k]
        fi = feats[i]
        for k2 in range(k + 1, min(n, k + SEQ_WINDOW + 1)):
            j = alive[k2]
            if items[j]["author"] != items[i]["author"]:
                break
            if _same(fi, feats[j]):
                dsu.union(i, j)
    buckets: dict[int, list[int]] = {}
    for i in alive:
        buckets.setdefault(dsu.find(i), []).append(i)
    return [sorted(v) for v in buckets.values() if len(v) >= MIN_GROUP_SIZE]


def _score(metrics: dict | None) -> float:
    """组内打分（越高越好）：少过曝/欠曝 + 高 Tenengrad + 人脸质量加成。"""
    if not metrics:
        return -1e9
    s = 0.0
    s -= float(metrics.get("over_blocks") or 0)
    s -= float(metrics.get("under_blocks") or 0)
    s += float(metrics.get("tenengrad_mean") or 0) * 200.0
    fq = metrics.get("face_quality")
    if fq is not None:
        s += float(fq) * 20.0
    return s


def _collect_items(project_dir: str, project_key: str) -> list[dict]:
    """从 index + 筛选结果.json 收集待分组照片（仅 pass），按 (author, P 序号) 排序。

    筛选结果.json 的 path 是「项目相对」(图片素材/xxx.jpg)，index 的 path 是
    「相对图片素材」(xxx.jpg)；两者仅靠拼接前缀匹配会在带作者子目录时静默失配。
    这里同时用 basename 兜底（照片名经 _unique_path 唯一化，不会误配）。
    """
    entries = read_index(project_dir) or []
    # basename → 筛选记录（主）+ 完整相对路径 → 记录（辅）
    qmap: dict[str, dict] = {}
    qby_name: dict[str, dict] = {}
    for r in quality_filter.load_records(project_dir) or []:
        p = (r.get("path") or "").replace("\\", "/")
        if not p:
            continue
        qmap[p] = r
        qby_name.setdefault(os.path.basename(p), r)

    items: list[dict] = []
    for e in entries:
        rel = (e.get("path") or "").replace("\\", "/").strip("/")
        if not rel:
            continue
        full = f"{PHOTO_DIR}/{rel}"
        name = e.get("name") or os.path.basename(rel)
        rec = (qmap.get(full) or qmap.get(rel)
               or qby_name.get(name) or qby_name.get(os.path.basename(rel)))
        if rec is not None and rec.get("verdict") == "waste":
            continue  # 三级过滤判废的照片不参与连拍分组
        items.append({
            "path": full,
            "name": name,
            "author": (e.get("author") or "").strip(),
            "time": e.get("time"),
            "metrics": (rec or {}).get("metrics"),
        })
    items.sort(key=lambda x: (x["author"], _pnum(x["name"])))
    return items


def candidate_fingerprint(project_dir: str) -> str:
    """候选集指纹：对当前「参与连拍的 pass 照片名集合」取 md5。

    筛选结果变化（新增废片 / 拯救照片）会改变指纹，面板据此判定快照过期并重算。
    """
    names = sorted(it["name"] for it in _collect_items(project_dir, ""))
    return hashlib.md5("\n".join(names).encode("utf-8")).hexdigest() if names else ""


def is_stale(project_dir: str) -> bool:
    """连拍组快照是否与最新筛选结果不一致（文件缺失、索引缺失也算过期）。"""
    if read_index(project_dir) is None:
        # 索引尚未建立（照片还没平铺/编号）→ 必须先跑前置提取，
        # 否则 _collect_items 恒为空、指纹也恒为空，会被误判成「本来就没有连拍」。
        return True
    data = load_groups(project_dir)
    if data is None:
        return True
    return (data.get("fingerprint") or "") != candidate_fingerprint(project_dir)


def _run_detection(project_dir: str, project_key: str):
    try:
        if not _HAVE_CV:
            raise RuntimeError("缺少 opencv-python-headless / numpy 依赖，无法运行相似图检测")
        if ort is None:
            raise RuntimeError("缺少 onnxruntime 依赖，无法运行相似图检测")
        if not _get_session():
            raise RuntimeError(
                f"SSCD 模型缺失，请先运行 backend/export_sscd.py 生成 models/{SSCD_MODEL_NAME}")

        items = _collect_items(project_dir, project_key)
        total = len(items)
        fp = candidate_fingerprint(project_dir)
        if total == 0:
            save_groups(project_dir, [], fingerprint=fp)
            _push(project_key, status="done", total=0, done=0, percent=100,
                  groups=0, message="没有需要检测的照片")
            return

        # 重算时按照片名沿用上次的 keep 选择，避免用户已筛结果丢失
        prev_keep: dict[str, bool] = {}
        old = load_groups(project_dir)
        if old:
            for g in old.get("groups", []):
                for ph in g.get("photos", []):
                    p = (ph.get("path") or "").replace("\\", "/")
                    if p:
                        prev_keep[os.path.basename(p)] = bool(ph.get("keep"))

        _push(project_key, status="running", total=total, done=0, percent=0,
              groups=0, message="正在计算 SSCD 描述子…")

        # ---- ① 逐张提取 SSCD 描述子 ----
        for idx, it in enumerate(items):
            it["feat"] = _embed(project_dir, it["path"])
            if idx % 5 == 0 or idx + 1 == total:
                _push(project_key, status="running", total=total, done=idx + 1,
                      percent=int((idx + 1) / total * 70), groups=0,
                      message="正在计算 SSCD 描述子…")

        feats = [it["feat"] for it in items]
        alive = [i for i, f in enumerate(feats) if f is not None]
        if len(alive) < MIN_GROUP_SIZE:
            save_groups(project_dir, [], fingerprint=fp)
            _push(project_key, status="done", total=total, done=total, percent=100,
                  groups=0, message="可读取的照片不足，未形成相似图组")
            return

        # ---- ② 顺序窗口内相邻相似 → 连通分量成组 ----
        _push(project_key, status="running", total=total, done=total, percent=80,
              groups=0, message="正在比对相邻照片…")
        raw_groups = _sequential_groups(items, feats, alive)

        # ---- 组装输出：顺序切块 + 逐组打分标 recommended ----
        chunks: list[list[int]] = []
        for gidx in raw_groups:
            for s in range(0, len(gidx), MAX_GROUP_SIZE):
                chunk = gidx[s:s + MAX_GROUP_SIZE]
                if len(chunk) >= MIN_GROUP_SIZE:
                    chunks.append(chunk)
        chunks.sort(key=lambda c: min(c))

        groups = []
        for gi, chunk in enumerate(chunks):
            best_i, best_s = -1, None
            for k, i in enumerate(chunk):
                s = _score(items[i].get("metrics"))
                if best_s is None or s > best_s:
                    best_s, best_i = s, k
            photos = []
            for k, i in enumerate(chunk):
                it = items[i]
                photos.append({
                    "path": it["path"],
                    "name": it["name"],
                    "recommended": k == best_i and best_s is not None and best_s > -1e8,
                    "keep": prev_keep.get(os.path.basename(it["path"]), False),
                })
            groups.append({"id": gi, "author": items[chunk[0]]["author"],
                           "count": len(photos), "photos": photos})

        save_groups(project_dir, groups, fingerprint=fp)
        _push(project_key, status="done", total=total, done=total, percent=100,
              groups=len(groups),
              message=f"相似图检测完成，共 {len(groups)} 组")
        logger.info("相似图检测完成: %s 照片 %d 张 → %d 组", project_key, total, len(groups))

    except Exception as e:
        logger.exception("相似图检测失败")
        _push(project_key, status="error", total=0, done=0, percent=0,
              groups=0, message=str(e))
    finally:
        with _run_lock:
            _running.discard(project_key)


def start_burst_detection(project_dir: str, project_key: str) -> bool:
    """启动检测线程；同一项目已在跑时返回 False（不再重复起线程）。

    启动前先同步把进度重置为 running：上一轮的 done 若不清掉，紧接着的首次轮询
    会读到旧终态，前端立刻拿旧快照打开面板 —— 看起来就是「打开没刷新」。
    """
    with _run_lock:
        if project_key in _running:
            return False
        _running.add(project_key)
    _push(project_key, status="running", total=0, done=0, percent=0,
          groups=0, message="正在启动相似图检测…")
    t = threading.Thread(target=_run_detection, args=(project_dir, project_key), daemon=True)
    t.start()
    return True


# ===========================================================================
# 状态读写（面板交互）
# ===========================================================================

def set_photo_keep(project_dir: str, group_id: int, path: str, keep: bool) -> dict:
    """设置组内单张照片的保留状态（只改状态，不动文件）。"""
    data = load_groups(project_dir)
    if data is None:
        return {"status": "error", "message": "连拍组文件不存在"}
    target = next((g for g in data["groups"] if g.get("id") == group_id), None)
    if target is None:
        return {"status": "error", "message": f"连拍组 {group_id} 不存在"}
    norm = (path or "").replace("\\", "/")
    hit = None
    for ph in target.get("photos", []):
        if (ph.get("path") or "").replace("\\", "/") == norm:
            ph["keep"] = bool(keep)
            hit = ph
            break
    if hit is None:
        return {"status": "error", "message": "未找到该照片"}
    save_groups(project_dir, data["groups"])
    return {"status": "ok", "group_id": group_id, "path": norm, "keep": bool(keep)}


def set_group_keep(project_dir: str, group_id: int, keep: bool) -> dict:
    """组级：全部保留 / 全部舍弃（只改状态，不动文件）。"""
    data = load_groups(project_dir)
    if data is None:
        return {"status": "error", "message": "连拍组文件不存在"}
    target = next((g for g in data["groups"] if g.get("id") == group_id), None)
    if target is None:
        return {"status": "error", "message": f"连拍组 {group_id} 不存在"}
    for ph in target.get("photos", []):
        ph["keep"] = bool(keep)
    save_groups(project_dir, data["groups"])
    return {"status": "ok", "group_id": group_id, "keep": bool(keep),
            "count": len(target.get("photos", []))}


def collect_discard_paths(project_dir: str) -> list[str]:
    """汇总连拍组中「未保留」(keep!=true) 的照片项目相对路径，供统一收口移入废弃物。"""
    data = load_groups(project_dir)
    if data is None:
        return []
    out = []
    for g in data["groups"]:
        for ph in g.get("photos", []):
            if not ph.get("keep"):
                p = (ph.get("path") or "").replace("\\", "/")
                if p:
                    out.append(p)
    return out