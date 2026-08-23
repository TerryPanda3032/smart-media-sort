# -*- coding: utf-8 -*-
"""交付服务 — 把整个项目文件夹拷贝到用户指定输出目录，并对账校验。

规则: 整项目拷贝（在输出目录下生成「项目名/」子目录），
  去掉所有 JSON 文件；目标目录按文件按需创建 → 不保留空文件夹。
SSE 事件类型: deliver
流程: copying（拷贝）→ verify（校验）→ done / mismatch / error
进度字段:
  total / done          — 文件总数 / 已完成数（总进度）
  current_file_done / current_file_total — 当前文件已拷字节 / 总字节（当前文件进度）
"""

import logging
import os
import shutil
import threading
import time

from fileops import CopyProgressManager

logger = logging.getLogger(__name__)

# 全局单例（复用文件复制进度管理器）
_progress = CopyProgressManager()

# SSE 推送回调 — 由 main.py 注册
_sse_callback = None


def set_sse_callback(fn):
    """注册 SSE 推送回调函数（由 main.py 调用）。"""
    global _sse_callback
    _sse_callback = fn


def _notify_sse(name: str):
    if _sse_callback:
        try:
            data = _progress.get(name)
            if data:
                _sse_callback(name, dict(data))
        except Exception:
            pass


def get_progress(name: str) -> dict | None:
    return _progress.get(name)


def reset(name: str):
    """重置本项目交付进度记录（交付完成/取消后重置页面）。"""
    _progress.remove(name)


def start_deliver(name: str, project_dir: str, dest_dir: str):
    """启动后台交付线程：拷贝 + 校验。"""
    t = threading.Thread(
        target=_do_deliver,
        args=(name, project_dir, dest_dir),
        daemon=True,
    )
    t.start()


def _collect_source_files(project_dir: str) -> list[tuple[str, str]]:
    """收集整个项目目录内的交付文件（排除 JSON 文件与隐藏目录）。

    只收集文件，目标目录按文件按需创建 → 空文件夹不会产生。
    返回 [(源绝对路径, 相对项目根路径)]。
    """
    files: list[tuple[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(project_dir):
        # 跳过隐藏目录
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in sorted(filenames):
            # 交付不要 JSON（id.json / 分类结果.json / 分类方案.json 等）
            if fn.lower().endswith(".json"):
                continue
            abs_src = os.path.join(dirpath, fn)
            rel = os.path.relpath(abs_src, project_dir)
            files.append((abs_src, rel))
    return files


def _do_deliver(name: str, project_dir: str, dest_dir: str):
    try:
        all_files = _collect_source_files(project_dir)
        total = len(all_files)
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
            _progress.update(name, status="done", message="没有可交付的文件")
            _notify_sse(name)
            return

        # 交付 = 把整个项目文件夹放过去（去掉 JSON 与空文件夹）
        dest_root = os.path.join(dest_dir, os.path.basename(os.path.normpath(project_dir)))
        os.makedirs(dest_root, exist_ok=True)

        # ---- 拷贝阶段（单文件级防护：单个失败不中止整个交付）----
        failed_files = []
        for abs_src, rel in all_files:
            dest = os.path.join(dest_root, rel)
            # 自拷贝防护：源与目标为同一路径（如输出目录选到了项目内部），跳过避免 Invalid argument
            if os.path.abspath(dest) == os.path.abspath(abs_src):
                failed_files.append((rel, "源与目标路径相同，已跳过"))
                current = _progress.get(name)
                if current:
                    _progress.update(name, done=current["done"] + 1)
                _notify_sse(name)
                continue

            try:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
            except Exception:
                pass
            size = os.path.getsize(abs_src)
            _progress.update(name,
                current_file=os.path.basename(rel),
                current_pct=0.0,
                current_file_done=0,
                current_file_total=size)
            _notify_sse(name)

            try:
                with open(abs_src, "rb") as f_src, open(dest, "wb") as f_dst:
                    copied = 0
                    while True:
                        buf = f_src.read(1024 * 1024)
                        if not buf:
                            break
                        f_dst.write(buf)
                        copied += len(buf)
                        if size > 0:
                            _progress.update(name,
                                current_pct=copied / size,
                                current_file_done=copied)
                            # 每 1MB 推送一次，避免洪泛
                            if copied % (1024 * 1024) < len(buf):
                                _notify_sse(name)
                try:
                    shutil.copystat(abs_src, dest)
                except Exception:
                    pass
            except Exception as e:
                # 单个文件拷贝失败：记录后跳过，继续其余文件，避免整个交付中断
                logger.warning("交付拷贝失败，跳过 %s: %s", rel, e)
                failed_files.append((rel, str(e)))

            current = _progress.get(name)
            if current:
                _progress.update(name, done=current["done"] + 1)
            _notify_sse(name)

        # 有失败文件且校验会不一致 → 直接以 mismatch 结束（不再多报 done/error）
        if failed_files:
            reasons = "；".join(f"{r}[{why}]" for r, why in failed_files[:5])
            if len(failed_files) > 5:
                reasons += f" 等 {len(failed_files)} 个文件"
            _progress.update(name, status="error",
                             current_pct=1.0,
                             message=f"拷贝发现 {len(failed_files)} 个文件失败：{reasons}")
            _notify_sse(name)
            return

        # ---- 校验阶段：比对源与目标 一模一样 ----
        _progress.update(name, status="verify", message="正在校验拷贝结果…")
        _notify_sse(name)
        identical, reason = _verify_copy(all_files, dest_root)

        if identical:
            # 校验一致 → 清理项目文件夹内的全部素材，交付即清空
            _progress.update(name, status="cleanup", current_pct=1.0,
                             message="校验一致，正在清空原始素材…")
            _notify_sse(name)
            cleaned, clean_err = _clear_project(project_dir)
            final_status = "done"
            final_msg = ("交付完成，校验一致，原始素材已清空" if cleaned
                         else f"交付完成，校验一致，但素材清理失败：{clean_err}")
        else:
            final_status = "mismatch"
            final_msg = f"校验不一致：{reason}"
        _progress.update(name, status=final_status, current_pct=1.0, message=final_msg)
        _notify_sse(name)

    except Exception as e:
        logger.exception("交付失败: %s", e)
        _progress.update(name, status="error", message=str(e))
        _notify_sse(name)


def _verify_copy(pairs: list[tuple[str, str]], dest_dir: str) -> tuple[bool, str]:
    """逐一比对：目标文件必须存在，且源/目标大小一致。"""
    for abs_src, rel in pairs:
        dest = os.path.join(dest_dir, rel)
        if not os.path.isfile(dest):
            return False, f"目标缺失文件 {rel}"
        try:
            if os.path.getsize(abs_src) != os.path.getsize(dest):
                return False, f"文件大小不一致 {rel}"
        except Exception as e:
            return False, f"读取失败 {rel}（{e}）"
    return True, ""


def _clear_project(project_dir: str) -> tuple[bool, str]:
    """清空项目文件夹内的全部素材内容，仅保留 id.json 等项目管理文件。

    返回 (是否成功, 错误信息或 "")。
    """
    try:
        for entry in os.listdir(project_dir):
            p = os.path.join(project_dir, entry)
            # 保留项目管理文件（id.json 等 JSON，交付本身也排除 JSON）
            if os.path.isfile(p) and entry.lower().endswith(".json"):
                continue
            if os.path.isfile(p):
                try:
                    os.remove(p)
                except Exception as e:
                    return False, f"{entry}（{e}）"
            elif os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
                if os.path.exists(p):
                    return False, f"目录 {entry} 清理失败"
        return True, ""
    except Exception as e:
        return False, str(e)