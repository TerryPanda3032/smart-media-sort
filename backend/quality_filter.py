# -*- coding: utf-8 -*-
"""三级废片过滤服务（step2 质量分类）。

在 Stage1（删格式/损坏）与「分流」之后运行，仅针对「图片素材」内的照片：

  第一级 分区块直方图曝光 —— 8x8=64 宫格，块内过曝/欠曝像素占比超阈值记该块异常，
          过曝异常块数达 OVER_BLOCK_COUNT、或欠曝异常块数达 UNDER_BLOCK_COUNT 判废
          （reason=exposure）。若检出人脸，则另有一条更严的规则：人脸框周围网格内
          过曝块数达 FACE_OVER_BLOCK_COUNT 即判废（人脸区域过曝）；无人脸时此规则不生效。
  第二级 分块清晰度（整幅对焦）—— 把工作尺度图切成 12x12 块，逐块算 Tenengrad
          均值；「有细节块占比」低于 SHARP_BLOCK_RATIO_THRESHOLD 判整幅对焦不足
          （reason=focus）。用分块占比而非全图均值，是只看「画面是否处处缺高频细节」，
          避免浅景深（背景虚、主体清晰）被误判。
  第三级 人脸质量（eDifFIQA，机器学习）—— YuNet(DNN) 检测全部人脸（含 5 个关键点），
          忽略边长过小者，按人脸框面积取「画面最大的三张脸」：用 5 关键点把每张脸对齐
          成 112×112，送 eDifFIQA(T) 小模型输出 0~1 的质量分（越高越清晰）。三张中只要
          有任一张质量分 ≥ FACE_QUALITY_THRESHOLD 即判合格；三张全低于阈值才判「人脸
          不清楚」（reason=face）。无合格人脸则不判（reason=face 不成立）。
          用机器学习质量分而非「人脸核心区梯度 ÷ 画面最清晰区」的相对比值，是因为后者
          的分母随相机/场景漂移、阈值难以跨机统一；质量分绝对且跨机更稳定。

判废只写 筛选结果.json（不移动文件）；点「下一步」才把仍为「放弃」的废片移入废弃物。

筛选结果.json 记录格式（每条）:
  {
    "path": "图片素材/xxx.jpg",   # 项目相对路径
    "verdict": "pass" | "waste", # 当前状态：pass=合格/已拯救，waste=放弃
    "reason": null | "exposure" | "focus" | "face",  # 判废原因（非空即曾判为废片候选）
    "metrics": { ... },          # 各项计算值
    "user_set": true             # 可选：用户在面板上显式改过状态，检测运行不得覆盖
  }

SSE/轮询事件类型: quality_filter
"""

import base64
import json
import logging
import os
import threading

try:
    import cv2
    import numpy as np
    _HAVE_CV = True
except Exception:  # 依赖缺失时不致命，运行时报错提示
    cv2 = None
    np = None
    _HAVE_CV = False

from media import PHOTO_EXTS
from sse_service import ProgressState

logger = logging.getLogger(__name__)

# ===========================================================================
# 可调阈值 —— 全部集中在此，方便调参
# ===========================================================================

# ---- 第一级：分区块直方图曝光 ----
GRID = 8                    # 宫格划分（8 → 64 宫格）
OVER_GRAY = 245             # 过曝灰度阈值（收紧：更亮的近白像素也算过曝）
UNDER_GRAY = 5              # 欠曝灰度阈值
OVER_BLOCK_PIXEL_RATIO = 0.20   # 单块内过曝像素占比阈值（收紧：更易判为过曝块）
UNDER_BLOCK_PIXEL_RATIO = 0.30  # 单块内欠曝像素占比阈值
OVER_BLOCK_COUNT = 10       # 过曝异常块数达此值即判废（64 宫格取 10）
UNDER_BLOCK_COUNT = 20      # 欠曝异常块数达此值即判废（64 宫格取 20，暗部更宽容）

# ---- 第二级：分块清晰度（整幅对焦）----
WORK_MAX_SIDE = 1600        # 计算前缩放的长边上限（提速 + 尺度统一）
SHARP_BLOCK_GRID = 12       # 分块边长（12×12=144 块）
SHARP_BLOCK_MIN_TG = 0.10   # 单块 Tenengrad 均值达此值视为「有高频细节」
SHARP_BLOCK_RATIO_THRESHOLD = 0.45  # 「有细节块占比」低于此值 → 整幅对焦不足
SHARP_REF_PERCENTILE = 90   # 「画面最清晰区域」参考值 = 分块 Tenengrad 的 90 分位
HIGH_FREQ_GRAD_THRESHOLD = 0.080    # 归一化梯度幅值的高频判定阈值（仅用于高频占比指标展示）

# ---- 第三级：人脸质量（YuNet 检测 + eDifFIQA 质量评估）----
FACE_MODEL_NAME = "face_detection_yunet_2023mar.onnx"   # 位于 backend/models/
FACE_INPUT_SIZE = 320       # YuNet 初始输入尺寸（每张图会按实际尺寸 setInputSize 覆盖）
FACE_SCORE_THRESHOLD = 0.35 # 置信度阈值（调低以召回「已明显失焦」的人脸，否则整张脸检不出→漏判）
FACE_NMS_THRESHOLD = 0.30   # NMS 阈值
FACE_TOP_K = 5000           # 保留候选上限
FACE_MIN_SIZE_PX = 100      # 忽略边长小于此值（原图像素）的小脸
FACE_OVER_BLOCK_COUNT = 2   # 人脸周围网格中过曝块达此值即判废（人脸区域过曝）
FACE_OVER_BLOCK_EXPAND = 1  # 「人脸周围」：在人脸框覆盖网格基础上向外扩的格数
FACE_CROP_MARGIN = 0.15     # 人脸缩略图裁剪时检测框外扩比例

# ---- 第三级：人脸质量分（eDifFIQA(T) 小模型，位于 backend/models/）----
FACE_QUALITY_MODEL_NAME = "ediffiqa_tiny_jun2024.onnx"
FACE_QUALITY_INPUT_SIZE = 112       # 模型输入边长（对齐后的人脸）
FACE_QUALITY_TOP_N = 3              # 只看画面最大的 N 张脸
FACE_QUALITY_THRESHOLD = 0.55       # 质量分下限：≥ 视为清晰；三张全 < 才判废
# 人脸对齐参考点（与 eDifFIQA 官方 demo 一致，112×112 空间内的 5 点）
FACE_QUALITY_REF_POINTS = [
    [38.2946, 51.6963],
    [73.5318, 51.5014],
    [56.0252, 71.7366],
    [41.5493, 92.3655],
    [70.729904, 92.2041],
]

# ---- 文件 / 目录约定 ----
RESULT_FILE = "筛选结果.json"
MATERIAL_PHOTO_DIR = "图片素材"
REASON_LABELS = {"exposure": "曝光问题", "focus": "对焦问题", "face": "人脸问题"}

_progress = ProgressState()
_face_detector = None
_quality_net = None
_face_lock = threading.Lock()
_run_lock = threading.Lock()
_running: set[str] = set()      # 正在检测的项目 key，避免与连拍快筛并行


def set_sse_callback(fn):
    _progress.set_sse_callback(fn)


def get_progress(project_key: str) -> dict | None:
    return _progress.get(project_key)


def is_running(project_key: str) -> bool:
    """该项目是否正在跑三级过滤（供连拍快筛做互斥判断）。"""
    with _run_lock:
        return project_key in _running


def _push_progress(project_key: str, **kwargs):
    _progress.push(project_key, **kwargs)


# ===========================================================================
# 人脸检测器
# ===========================================================================

def _get_face_detector():
    """懒加载 YuNet 人脸检测器（DNN）。模型缺失时返回 False。"""
    global _face_detector
    if _face_detector is not None:
        return _face_detector
    with _face_lock:
        if _face_detector is not None:
            return _face_detector
        try:
            model = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "models", FACE_MODEL_NAME)
            if not os.path.isfile(model):
                logger.warning("人脸模型缺失: %s", model)
                _face_detector = False
            else:
                _face_detector = cv2.FaceDetectorYN.create(
                    model, "", (FACE_INPUT_SIZE, FACE_INPUT_SIZE),
                    FACE_SCORE_THRESHOLD, FACE_NMS_THRESHOLD, FACE_TOP_K,
                )
        except Exception as e:
            logger.warning("加载 YuNet 人脸模型失败: %s", e)
            _face_detector = False
    return _face_detector


def _get_quality_net():
    """懒加载 eDifFIQA(T) 人脸质量模型（DNN）。模型缺失时返回 False。"""
    global _quality_net
    if _quality_net is not None:
        return _quality_net
    with _face_lock:
        if _quality_net is not None:
            return _quality_net
        try:
            model = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "models", FACE_QUALITY_MODEL_NAME)
            if not os.path.isfile(model):
                logger.warning("人脸质量模型缺失: %s", model)
                _quality_net = False
            else:
                _quality_net = cv2.dnn.readNetFromONNX(model)
        except Exception as e:
            logger.warning("加载人脸质量模型失败: %s", e)
            _quality_net = False
    return _quality_net


# ===========================================================================
# 文件读写
# ===========================================================================

def result_path(project_dir: str) -> str:
    return os.path.join(project_dir, RESULT_FILE)


def load_records(project_dir: str) -> list | None:
    """读取 筛选结果.json；缺失/损坏返回 None。"""
    p = result_path(project_dir)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    return data if isinstance(data, list) else None


def save_records(project_dir: str, data: list) -> bool:
    try:
        with open(result_path(project_dir), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.warning("写入 %s 失败: %s", RESULT_FILE, e)
        return False


def preserve_user_overrides(project_dir: str, records: list) -> None:
    """写盘前保留用户在面板上的显式判断（拯救 / 放弃）。

    _run 每处理一张就整表覆盖写盘；用户在检测运行期间点「拯救」，下一次整表
    覆盖会把 verdict 抹回机器判定值（记录被全部重置）。这里把磁盘上带
    user_set 标记的 verdict 盖回内存记录，保证用户选择不被覆盖。
    """
    disk = load_records(project_dir) or []
    overrides: dict[str, str] = {}
    for it in disk:
        if it.get("user_set") and it.get("path") and it.get("verdict"):
            overrides[it["path"]] = it["verdict"]
    if not overrides:
        return
    for it in records:
        v = overrides.get(it.get("path"))
        if v is not None:
            it["verdict"] = v
            it["user_set"] = True


def summarize(records: list) -> dict:
    """统计：总数、当前放弃数、三类计数、废片率。"""
    counts = {"exposure": 0, "focus": 0, "face": 0}
    waste = 0
    for it in records:
        if it.get("verdict") == "waste":
            waste += 1
            r = it.get("reason")
            if r in counts:
                counts[r] += 1
    total = len(records)
    rate = round(waste / total * 100, 1) if total else 0.0
    return {"total": total, "waste": waste, "counts": counts, "waste_rate": rate}


def count_photos(project_dir: str) -> int:
    """统计「图片素材」内待检照片数（不含视频）。"""
    photo_dir = os.path.join(project_dir, MATERIAL_PHOTO_DIR)
    n = 0
    if os.path.isdir(photo_dir):
        for _root, _dirs, files in os.walk(photo_dir):
            for fn in files:
                if os.path.splitext(fn)[1].lower() in PHOTO_EXTS:
                    n += 1
    return n


def list_photos(project_dir: str) -> list[str]:
    """列出「图片素材」内照片的项目相对路径（排序稳定）。"""
    photo_dir = os.path.join(project_dir, MATERIAL_PHOTO_DIR)
    out = []
    if os.path.isdir(photo_dir):
        for root, _dirs, files in os.walk(photo_dir):
            for fn in files:
                if os.path.splitext(fn)[1].lower() in PHOTO_EXTS:
                    abs_p = os.path.join(root, fn)
                    out.append(os.path.relpath(abs_p, project_dir))
    out.sort()
    return out


# ===========================================================================
# 图像算法
# ===========================================================================

def _imread_unicode(path: str):
    """cv2.imread 在 Windows 中文路径下会失败，用 imdecode 兜底。"""
    try:
        buf = np.fromfile(path, dtype=np.uint8)
        if buf.size == 0:
            return None
        return cv2.imdecode(buf, cv2.IMREAD_COLOR)
    except Exception:
        return None


def _tenengrad(gray):
    """返回 (归一化梯度均值, 高频像素占比)。Tenengrad：Sobel x/y 幅值。"""
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy) / 255.0
    mean_val = float(mag.mean())
    high_ratio = float((mag > HIGH_FREQ_GRAD_THRESHOLD).mean())
    return mean_val, high_ratio


def _grid_exposure(gray) -> tuple[list, list]:
    """9 宫格逐块统计，返回 (过曝块列表, 欠曝块列表)。"""
    over_blocks, under_blocks = [], []
    rows = np.array_split(gray, GRID, axis=0)
    for r, band in enumerate(rows):
        if band.size == 0:
            continue
        cols = np.array_split(band, GRID, axis=1)
        for c, blk in enumerate(cols):
            if blk.size == 0:
                continue
            over = int(np.count_nonzero(blk > OVER_GRAY))
            under = int(np.count_nonzero(blk < UNDER_GRAY))
            if over / blk.size > OVER_BLOCK_PIXEL_RATIO:
                over_blocks.append([r, c])
            if under / blk.size > UNDER_BLOCK_PIXEL_RATIO:
                under_blocks.append([r, c])
    return over_blocks, under_blocks


def _detect_faces(img):
    """YuNet 检测人脸，返回 (dets, scale=1.0)。

    传入的是工作尺度 BGR 图。YuNet 检测器带内部状态且非线程安全，调用需加锁；
    输出坐标即输入图尺度，无需再做缩放换算。

    dets 每项: {"box": (x, y, w, h), "kps": [(x, y) * 5], "score": float}
    5 个关键点顺序为：右眼、左眼、鼻尖、右嘴角、左嘴角。
    """
    det = _get_face_detector()
    if not det:
        return [], 1.0
    h, w = img.shape[:2]
    if h <= 0 or w <= 0:
        return [], 1.0
    try:
        with _face_lock:
            det.setInputSize((w, h))
            _retval, faces_arr = det.detect(img)
    except Exception as e:
        logger.warning("人脸检测失败: %s", e)
        return [], 1.0
    if faces_arr is None or len(faces_arr) == 0:
        return [], 1.0
    dets = []
    for f in faces_arr:
        box = (int(round(float(f[0]))), int(round(float(f[1]))),
               int(round(float(f[2]))), int(round(float(f[3]))))
        kps = [(float(f[4 + 2 * i]), float(f[5 + 2 * i])) for i in range(5)]
        dets.append({"box": box, "kps": kps, "score": float(f[14])})
    return dets, 1.0


def _sharp_block_stats(gray):
    """分块清晰度：返回 (有细节块占比, 画面最清晰区域参考值)。

    把工作尺度灰度图切成 SHARP_BLOCK_GRID × SHARP_BLOCK_GRID 块，逐块算 Tenengrad
    均值。有细节块占比 = 达到 SHARP_BLOCK_MIN_TG 的块占比，用于判整幅对焦不足；
    参考值 = 分块 Tenengrad 的 SHARP_REF_PERCENTILE 分位，代表「画面最清晰处」。
    """
    rows = np.array_split(gray, SHARP_BLOCK_GRID, axis=0)
    vals = []
    for band in rows:
        if band.size == 0:
            continue
        for blk in np.array_split(band, SHARP_BLOCK_GRID, axis=1):
            if blk.size == 0:
                continue
            vals.append(_tenengrad(blk)[0])
    if not vals:
        return 1.0, 0.0
    arr = np.asarray(vals, dtype=np.float32)
    hi_ratio = float((arr >= SHARP_BLOCK_MIN_TG).mean())
    ref = float(np.percentile(arr, SHARP_REF_PERCENTILE))
    return hi_ratio, ref


def _top_faces(dets, to_nat, n=FACE_QUALITY_TOP_N):
    """按人脸框面积从大到小取前 n 张（忽略边长过小者），返回下标列表。

    to_nat 为「工作尺度 → 原图尺度」放大系数，用于按原图像素比较边长。
    """
    cand = []
    for i, d in enumerate(dets):
        _x, _y, fw, fh = d["box"]
        if fw * to_nat < FACE_MIN_SIZE_PX or fh * to_nat < FACE_MIN_SIZE_PX:
            continue
        cand.append((fw * fh, i))
    cand.sort(key=lambda t: t[0], reverse=True)
    return [i for _a, i in cand[:n]]


def _align_face(img, kps):
    """用 5 关键点把脸对齐成 112×112（与 eDifFIQA 参考点对齐）；失败返回 None。

    img 需为与 kps 同尺度的 BGR 图（用原图可保留最多细节）。
    关键点顺序：右眼、左眼、鼻尖、右嘴角、左嘴角。
    """
    try:
        src = np.asarray(kps, dtype=np.float32).reshape(5, 2)
        ref = np.asarray(FACE_QUALITY_REF_POINTS, dtype=np.float32)
        tfm, _ = cv2.estimateAffinePartial2D(src, ref, method=cv2.LMEDS)
        if tfm is None:
            return None
        return cv2.warpAffine(img, tfm, (FACE_QUALITY_INPUT_SIZE, FACE_QUALITY_INPUT_SIZE))
    except Exception:
        return None


def _face_quality(aligned):
    """eDifFIQA(T) 质量分（0~1，越高越清晰）；模型缺失或失败返回 None。

    输入为对齐后的 112×112 BGR 脸：转 RGB → 归一化到 [-1,1] → NCHW。
    DNN 实例带内部状态且非线程安全，调用需加锁。
    """
    net = _get_quality_net()
    if not net or aligned is None:
        return None
    try:
        x = cv2.cvtColor(aligned, cv2.COLOR_BGR2RGB).astype(np.float32)
        x = ((x / 255.0) - 0.5) / 0.5
        x = np.moveaxis(x[None, ...], -1, 1).astype(np.float32)
        with _face_lock:
            net.setInput(x)
            y = net.forward()
        return float(np.squeeze(y))
    except Exception as e:
        logger.warning("人脸质量评估失败: %s", e)
        return None


def _face_over_blocks(dets, over_blocks, img_w, img_h):
    """统计「人脸周围」网格中出现过曝的格子集合（去重）。

    人脸周围 = 人脸框覆盖的网格行/列范围，向四周各扩 FACE_OVER_BLOCK_EXPAND 格
    （夹到网格边界）。返回 set[(r, c)]，供「人脸区域过曝」判定使用。
    """
    cw = img_w / float(GRID)
    ch = img_h / float(GRID)
    over_set = {(r, c) for r, c in over_blocks}
    hit = set()
    for d in dets:
        x, y, w, h = d["box"]
        c0 = max(0, int(x // cw) - FACE_OVER_BLOCK_EXPAND)
        c1 = min(GRID - 1, int((x + w) // cw) + FACE_OVER_BLOCK_EXPAND)
        r0 = max(0, int(y // ch) - FACE_OVER_BLOCK_EXPAND)
        r1 = min(GRID - 1, int((y + h) // ch) + FACE_OVER_BLOCK_EXPAND)
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                if (r, c) in over_set:
                    hit.add((r, c))
    return hit


def _crop_box(box, img_w, img_h, margin=FACE_CROP_MARGIN):
    """把人脸框外扩 margin 并夹到图内，返回 (x0, y0, x1, y1)。"""
    x, y, w, h = box
    mx, my = int(w * margin), int(h * margin)
    x0, y0 = max(0, x - mx), max(0, y - my)
    x1, y1 = min(img_w, x + w + mx), min(img_h, y + h + my)
    return x0, y0, x1, y1


def _encode_face_thumb(img, box, margin=FACE_CROP_MARGIN):
    """裁剪人脸区域（外扩 margin）并编码为 data url。box 需为 img 同尺度坐标。"""
    try:
        x0, y0, x1, y1 = _crop_box(box, img.shape[1], img.shape[0], margin)
        if x1 <= x0 or y1 <= y0:
            return None
        crop = img[y0:y1, x0:x1]
        ch, cw = crop.shape[:2]
        if cw > 240:
            k = 240.0 / cw
            crop = cv2.resize(crop, (240, max(1, int(ch * k))))
        ok, buf = cv2.imencode(".jpg", crop, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
        if not ok:
            return None
        return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode("ascii")
    except Exception:
        return None


def _process_photo(project_dir: str, rel_path: str) -> tuple[dict, dict, str | None]:
    """处理单张照片。返回 (记录, 实时载荷, 人脸缩略data-url或None)。"""
    abs_path = os.path.join(project_dir, rel_path)
    img = _imread_unicode(abs_path)
    if img is None:
        # 读不了（含 .dng 等 RAW / 无法解码）：跳过判定，保守保留
        rec = {"path": rel_path, "verdict": "pass", "reason": None,
               "skipped": True, "metrics": {"skipped": True}}
        live = {"path": rel_path, "orientation": "landscape", "skipped": True,
                "overlay": None, "metrics": {"skipped": True},
                "verdict": "pass", "reason": None}
        return rec, live, None

    h, w = img.shape[:2]
    scale = 1.0
    if max(h, w) > WORK_MAX_SIDE:
        scale = WORK_MAX_SIDE / float(max(h, w))
    if scale != 1.0:
        small = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))),
                           interpolation=cv2.INTER_AREA)
    else:
        small = img
    sh, sw = small.shape[:2]
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

    # ---- 第一级：曝光 ----
    over_blocks, under_blocks = _grid_exposure(gray)
    over_ratio = float(np.count_nonzero(gray > OVER_GRAY)) / gray.size
    under_ratio = float(np.count_nonzero(gray < UNDER_GRAY)) / gray.size

    # ---- 第二级：分块清晰度（整幅对焦）----
    tg_mean, hf_ratio = _tenengrad(gray)
    sharp_ratio, sharp_ref = _sharp_block_stats(gray)

    # ---- 第三级：画面最大的三张脸的人脸质量（eDifFIQA）----
    dets, _k = _detect_faces(small)
    to_nat = 1.0 / scale if scale != 1.0 else 1.0
    top_idx = _top_faces(dets, to_nat)
    face_scores = []   # [(人脸下标, 质量分), ...]
    for i in top_idx:
        kps = dets[i]["kps"]
        if to_nat != 1.0:  # 工作尺度 → 原图尺度（对齐用原图可保留最多细节）
            kps = [(px * to_nat, py * to_nat) for px, py in kps]
        sc = _face_quality(_align_face(img, kps))
        if sc is not None:
            face_scores.append((i, sc))
    # 三张中只要有一张 ≥ 阈值即合格；取最大值做判定
    face_quality = max((s for _i, s in face_scores), default=None)
    # 右上缩略图展示「最清晰脸」：有分取分最高者，无分退回最大脸
    if face_scores:
        prim_i = max(face_scores, key=lambda t: t[1])[0]
    else:
        prim_i = top_idx[0] if top_idx else None

    # ---- 人脸周围过曝：识别人脸后，其周围网格过曝达阈值即否决 ----
    face_over = _face_over_blocks(dets, over_blocks, sw, sh)

    # ---- 判定 ----
    verdict, reason = "pass", None
    if dets and len(face_over) >= FACE_OVER_BLOCK_COUNT:
        # 人脸区域过曝（有人脸时优先按人脸周围曝光判定）
        verdict, reason = "waste", "exposure"
    elif len(over_blocks) >= OVER_BLOCK_COUNT or len(under_blocks) >= UNDER_BLOCK_COUNT:
        verdict, reason = "waste", "exposure"
    elif sharp_ratio < SHARP_BLOCK_RATIO_THRESHOLD:
        # 画面处处缺高频细节 —— 整幅对焦不足
        verdict, reason = "waste", "focus"
    elif face_quality is not None and face_quality < FACE_QUALITY_THRESHOLD:
        # 画面最大的三张脸质量分均低于阈值 —— 人脸不清楚
        verdict, reason = "waste", "face"

    metrics = {
        "width": w, "height": h,
        "gray_mean": round(float(gray.mean()), 1),
        "over_ratio": round(over_ratio, 4), "under_ratio": round(under_ratio, 4),
        "over_blocks": len(over_blocks), "under_blocks": len(under_blocks),
        "tenengrad_mean": round(tg_mean, 4), "high_freq_ratio": round(hf_ratio, 4),
        "sharp_block_ratio": round(sharp_ratio, 4), "sharp_ref": round(sharp_ref, 4),
        "face_count": len(dets),
        "face_over_blocks": len(face_over),
        "face_quality": None if face_quality is None else round(face_quality, 4),
        "face_quality_threshold": FACE_QUALITY_THRESHOLD,
        "face_scores": [round(s, 4) for _i, s in face_scores],
    }

    # 叠加几何：归一化坐标（0~1，基于未旋转原图）
    def _nb(blocks):
        return [[round((c) / GRID, 4), round((r) / GRID, 4),
                 round(1.0 / GRID, 4), round(1.0 / GRID, 4)] for r, c in blocks]
    overlay = {
        "grid": GRID,
        "over_blocks": _nb(over_blocks),
        "under_blocks": _nb(under_blocks),
        "faces": [[round(b[0] / sw, 4), round(b[1] / sh, 4),
                   round(b[2] / sw, 4), round(b[3] / sh, 4)] for b in
                  (d["box"] for d in dets)],
        "face_primary": prim_i,
    }

    rec = {"path": rel_path, "verdict": verdict, "reason": reason, "metrics": metrics}
    live = {
        "path": rel_path,
        "orientation": "portrait" if h > w else "landscape",
        "natw": w, "nath": h,
        "overlay": overlay, "metrics": metrics,
        "verdict": verdict, "reason": reason,
    }
    if prim_i is not None:
        fx, fy, fw, fh = dets[prim_i]["box"]
        if to_nat != 1.0:  # 工作尺度 → 原图尺度（缩略图裁自未缩放的 img）
            fx, fy, fw, fh = (int(round(fx * to_nat)), int(round(fy * to_nat)),
                              int(round(fw * to_nat)), int(round(fh * to_nat)))
        thumb = _encode_face_thumb(img, [fx, fy, fw, fh])
    else:
        thumb = None
    return rec, live, thumb


# ===========================================================================
# 任务调度
# ===========================================================================

def start_quality_filter(project_dir: str, project_key: str) -> bool:
    """启动三级过滤线程；同一项目已在跑时返回 False（不重复起线程）。"""
    with _run_lock:
        if project_key in _running:
            return False
        _running.add(project_key)
    t = threading.Thread(target=_run, args=(project_dir, project_key), daemon=True)
    t.start()
    return True


def _run(project_dir: str, project_key: str):
    try:
        if not _HAVE_CV:
            raise RuntimeError("缺少 opencv-python-headless / numpy 依赖，无法运行三级过滤")

        rel_paths = list_photos(project_dir)
        total = len(rel_paths)
        if total == 0:
            _push_progress(project_key, status="done", total=0, done=0, percent=100,
                           waste=0, counts={"exposure": 0, "focus": 0, "face": 0},
                           waste_rate=0.0, message="图片素材内没有照片")
            return

        _push_progress(project_key, status="running", total=total, done=0, percent=0,
                       waste=0, counts={"exposure": 0, "focus": 0, "face": 0},
                       waste_rate=0.0)

        records: list[dict] = []
        counts = {"exposure": 0, "focus": 0, "face": 0}
        waste = 0
        last_thumb = None

        for idx, rel in enumerate(rel_paths):
            try:
                rec, live, thumb = _process_photo(project_dir, rel)
            except Exception as e:
                logger.warning("处理失败，保守判合格: %s (%s)", rel, e)
                rec = {"path": rel, "verdict": "pass", "reason": None,
                       "metrics": {"error": str(e)}}
                live = {"path": rel, "orientation": "landscape", "overlay": None,
                        "metrics": {"error": str(e)}, "verdict": "pass", "reason": None}
                thumb = None

            records.append(rec)
            if rec.get("verdict") == "waste":
                waste += 1
                r = rec.get("reason")
                if r in counts:
                    counts[r] += 1
            if thumb:
                last_thumb = thumb

            preserve_user_overrides(project_dir, records)
            save_records(project_dir, records)

            done = idx + 1
            _push_progress(project_key,
                           status="running", total=total, done=done,
                           percent=int(done / total * 100),
                           waste=waste, counts=dict(counts),
                           waste_rate=round(waste / done * 100, 1),
                           current=live, face_thumb=thumb or None)

        _push_progress(project_key,
                       status="done", total=total, done=total, percent=100,
                       waste=waste, counts=dict(counts),
                       waste_rate=round(waste / total * 100, 1),
                       face_thumb=last_thumb,
                       message=f"废片检测完成，共 {total} 张，废片 {waste} 张")
        logger.info("三级过滤完成: %s, 共 %d 张, 废片 %d", project_key, total, waste)

    except Exception as e:
        logger.exception("三级过滤失败")
        _push_progress(project_key, status="error", total=0, done=0, percent=0,
                       message=str(e))
    finally:
        with _run_lock:
            _running.discard(project_key)