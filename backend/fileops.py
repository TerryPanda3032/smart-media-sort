# -*- coding: utf-8 -*-
"""文件操作工具 — 删除、复制进度、文件夹选择对话框。"""

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading

logger = logging.getLogger(__name__)

# ===========================================================================
# 安全删除
# ===========================================================================

def force_delete(path: str) -> None:
    """删除文件夹，先尝试普通删除，失败时逐个文件处理，最后才弹 UAC。

    改进:
    1. 不再一上来就弹 UAC，先尝试 shutil + onerror 回调
    2. 路径用 os.path.realpath 规范化，防止注入
    3. UAC 脚本中的路径用 PowerShell 单引号包裹，防注入
    """
    real_path = os.path.realpath(path)
    if not os.path.exists(real_path):
        return

    # 第一次尝试: 直接删除
    try:
        shutil.rmtree(real_path)
        return
    except PermissionError:
        pass
    except Exception as e:
        logger.warning("shutil.rmtree 失败: %s", e)

    # 第二次尝试: 逐个文件 chmod 后删除
    try:
        _rmtree_with_chmod(real_path)
        return
    except Exception as e:
        logger.warning("chmod+rmtree 失败: %s", e)

    # 第三次尝试: UAC 提权
    _uac_delete(real_path)

    if os.path.exists(real_path):
        raise RuntimeError("删除失败：可能用户取消了 UAC 授权，或文件被占用")


def _rmtree_with_chmod(path: str):
    """逐个文件 chmod 后删除。"""
    def on_error(func, filepath, exc_info):
        try:
            os.chmod(filepath, 0o777)
            func(filepath)
        except Exception:
            raise
    shutil.rmtree(path, onerror=on_error)


def _uac_delete(path: str):
    """通过 UAC 提权删除文件夹。路径用 PowerShell 单引号转义防注入。"""
    ps_safe_path = path.replace("'", "''")

    fd, script_path = tempfile.mkstemp(suffix='.ps1', prefix='del_')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8-sig') as f:
            f.write(f"Remove-Item -LiteralPath '{ps_safe_path}' -Recurse -Force\n")
        ps_code = (
            'Start-Process -Verb RunAs -WindowStyle Normal -Wait '
            f"powershell.exe -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File \"{script_path}\"'"
        )
        result = subprocess.run(
            ['powershell', '-NoProfile', '-Command', ps_code],
            capture_output=True, timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            stderr_text = result.stderr.strip() if result.stderr else ""
            logger.warning("UAC 提权删除返回非零: %d, stderr: %s", result.returncode, stderr_text)
    finally:
        if os.path.exists(script_path):
            try:
                os.remove(script_path)
            except Exception:
                pass


# ===========================================================================
# 复制进度管理
# ===========================================================================

class CopyProgressManager:
    """复制进度状态管理，带 TTL 自动清理。"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._data = {}
            cls._instance._data_lock = threading.Lock()
        return cls._instance

    def get(self, key: str) -> dict | None:
        with self._data_lock:
            return self._data.get(key)

    def set(self, key: str, value: dict):
        with self._data_lock:
            self._data[key] = value

    def update(self, key: str, **kwargs):
        with self._data_lock:
            if key in self._data:
                self._data[key].update(kwargs)

    def remove(self, key: str):
        with self._data_lock:
            self._data.pop(key, None)

    def cleanup_stale(self, max_age_seconds: int = 3600):
        """清理超过 max_age_seconds 的进度记录。"""
        import time
        now = time.time()
        with self._data_lock:
            stale_keys = [
                k for k, v in self._data.items()
                if now - v.get("_created_at", now) > max_age_seconds
                and v.get("status") in ("done", "error")
            ]
            for k in stale_keys:
                del self._data[k]


# ===========================================================================
# 文件夹/文件选择对话框
# ===========================================================================

def run_folder_dialog() -> str:
    """在子进程中用 tkinter 打开 Windows 原生文件夹选择对话框。"""
    script = '''
import tkinter as tk
from tkinter import filedialog
root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)
path = filedialog.askdirectory(title="选择工作目录")
root.destroy()
print(path or "")
'''
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def run_file_dialog() -> str:
    """在子进程中用 tkinter 打开 Windows 原生文件选择对话框。"""
    script = '''
import tkinter as tk
from tkinter import filedialog
root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)
path = filedialog.askopenfilename(
    title="选择 FFmpeg 可执行文件",
    filetypes=[("可执行文件", "*.exe"), ("所有文件", "*.*")]
)
root.destroy()
print(path or "")
'''
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def count_files(source_path: str) -> int:
    total = 0
    for _, _, filenames in os.walk(source_path):
        total += len(filenames)
    return total


def get_next_number(dest_path: str) -> int:
    if not os.path.isdir(dest_path):
        return 1
    max_num = 0
    for fname in os.listdir(dest_path):
        name, _ = os.path.splitext(fname)
        if name.isdigit():
            num = int(name)
            if num > max_num:
                max_num = num
    return max_num + 1
