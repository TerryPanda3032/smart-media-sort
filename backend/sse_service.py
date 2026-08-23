# -*- coding: utf-8 -*-
"""SSE 进度状态基类 — 统一各服务模块的进度缓存与推送样板。

用法:
  from sse_service import ProgressState
  _progress = ProgressState()

  def set_sse_callback(fn):
      _progress.set_sse_callback(fn)

  def get_progress(project_key: str) -> dict | None:
      return _progress.get(project_key)

语义说明:
  - push():      整体覆盖该项目的进度快照并推送（filter / ai_filter / video_tagger）
  - update():    在原快照上增量更新并推送（batch 语义）
  - broadcast(): 仅推送事件，不缓存（video_tagger 逐视频事件语义）
  - reset():     整体覆盖快照，不推送（batch 启动初始化）
"""

import logging

logger = logging.getLogger(__name__)


class ProgressState:
    """按项目 key 缓存的进度快照 + SSE 回调推送。"""

    def __init__(self):
        self._data: dict[str, dict] = {}
        self._callback = None

    def set_sse_callback(self, fn):
        """注册 SSE 推送回调函数（由 main.py 调用）。"""
        self._callback = fn

    def get(self, project_key: str) -> dict | None:
        return self._data.get(project_key)

    def reset(self, project_key: str, data: dict):
        """整体覆盖快照，不推送（用于任务启动时初始化）。"""
        self._data[project_key] = data

    def push(self, project_key: str, **kwargs):
        """整体覆盖快照并推送。"""
        data = dict(kwargs)
        self._data[project_key] = data
        self._notify(project_key, data)

    def update(self, project_key: str, **kwargs):
        """在原快照上增量更新并推送；无快照时直接以本次数据为快照。"""
        if project_key in self._data:
            self._data[project_key].update(kwargs)
        else:
            self._data[project_key] = dict(kwargs)
        self._notify(project_key, dict(self._data[project_key]))

    def broadcast(self, project_key: str, **kwargs):
        """仅推送事件，不缓存快照。"""
        self._notify(project_key, dict(kwargs))

    def _notify(self, project_key: str, data: dict):
        if self._callback:
            try:
                self._callback(project_key, data)
            except Exception:
                logger.exception("SSE 推送失败: %s", project_key)
