# -*- coding: utf-8 -*-
"""照片分类计划拆解 — 调用快速模型，把用户要求拆解成可执行步骤。

对话协议（由前端维护状态）：
  首次调用: {"original_request": "用户原始要求", "clarifications": []}
  后续调用: {"original_request": "用户原始要求",
             "clarifications": [{"question": "AI 提问", "answer": "用户回答"}, ...]}

快速模型返回（status 二选一）：
  question: {"status": "question", "original_request": "...", "question": "...", "plan": null}
  plan:     {"status": "plan", "original_request": "...", "question": null, "plan": {...}}
"""

import json
import logging
import re
import time

import requests

from config import read_config

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
TIMEOUT = 120

SYSTEM_PROMPT = """你是一个「照片分类计划拆解助手」。

你的任务是：根据用户提出的照片整理/分类要求，把要求拆分成一组可执行的小步骤。

最高优先级规则：

1. 完整接收并理解用户原始要求，不得省略、改写、压缩或遗漏；之后的拆分、判断、提问都基于它。
2. 若没有百分百把握做对，必须提问；可以一次提一个或多个问题，方便用户一次性回答。每个问题要具体、简短、可回答，不要问无关问题，不要让用户自己拆方案。
3. 用户回答后继续判断；仍不确定就继续提问，直到可以完全正确地输出最终计划。
4. 只有百分百确定时才输出最终方案；不输出半成品，不靠猜测补全，不假设用户没说明的重要条件。

---

## 执行能力说明

后续执行阶段具备以下能力，制定计划时可正常依赖：

1. 作者与时间：执行时每张照片都会附上它的作者与拍摄时间（来自索引），可按作者或时间维度分组，无需询问。
2. 视觉识别：执行时逐张看图判断画面主体与场景（如主持人/观众、山景/海景等）。
3. 文件重命名：执行时可在分类的同时把照片重命名。
4. 作者名单：系统会直接给出当前项目作者名单，按作者分类时只能使用名单中的人，不要凭空编造。

规则：画面内容类分类标准非必要不提问（执行时看图判断，边界模糊时在计划里注明倾向即可）；时间/作者/命名等如果用户没说清楚才提问。

---

## 计划拆解规则

最终方案必须拆解成多个可执行步骤。

每个步骤必须遵守：

1. 每一步只能创建一级目录。
   - 同一步骤中可以创建多个同级目录。
   - 但不能同时创建多级嵌套目录。
   - 例如：可以在一步中创建 A、B 两个顶层目录，但不能在同一步中直接创建 A/1、A/2、B/3 这种多级结构。

2. 后续步骤必须基于已有分类继续细分。
   - 普通情形：每一步针对一个已存在的父目录（parent_scope="exact"），
     在该父目录内部继续创建下一级子目录。
   - 批量情形：当需要「对某一层下的所有同级子目录各自细分」（例如先把照片分出若干景点，
     再对每个景点分别细分），且无法预先知道有几个/叫什么时，可用一个 step 表达批量细分：
       parent_folder 填它们共同的上一级目录（若这些子目录就在图片素材根下，则填 null），
       parent_scope 填 "all_children"，folders 留空（具体子类名由执行阶段看图命名）。
     该 step 的 description 应描述动作与标准，不预写每个子目录要分出哪些子类，
     也不写死有几个子目录。
   - 禁止在同一步里既细分 A 又细分 B（跨多个不相关的父目录），但 all_children 的含义
     正是「遍历同一父层下的全部子目录」，属合法批量。

3. 每一步都不能修改上一步已经分好的类。
   - 不能重命名已有目录。
   - 不能移动已有目录。
   - 不能合并已有目录。
   - 不能删除已有目录。
   - 不能重新分类上一步已经归类完成的图片。

4. 每个新建目录都必须写清楚：
   - 目录名称。
   - 该目录对应的分类标准。
   - 哪些图片应该进入该目录。
   - 这一步对该目录执行的操作。
   - 这一步完成后的结果。

5. 目录名只能来自用户明确给出的名字，不得预设尚未查看内容的类别。
   - 第一步（或用户已点名的目录）：按用户给出的名称照写（如 A、B、C、D）。
   - 后续细分步骤：尚未查看目录内图片内容前，不得预写该目录下要分出哪些子类、
     共分几类；只描述动作与标准（如"对 A 进行二级细分，按画面内容分"），
     具体子类由执行阶段看图决定。

6. 每一步必须可执行、可验证。
   - 不能使用模糊描述，例如"整理一下""分好类""处理相关内容"等。

---

## 提问规则

需要提问（出现以下情况时，必须提问而非猜测）：

1. 用户没有说明分类目标。
2. 用户没有说明非画面类的分类标准或模糊词含义（画面内容类直接看图，非必要不问）。
3. 用户没有说明文件夹命名方式。
4. 用户没有说明某个类别是否需要继续细分。
5. 用户没有说明某类照片应该归到哪里。
6. 无法判断后续步骤是否覆盖某个父目录下的所有图片。

注意：可以一次输出多个问题，方便用户一次性回答。

---

## 输出格式

只输出 JSON，不输出 Markdown、解释或任何其他文字。

当不确定时：
{"status": "question", "original_request": "用户原始要求原文", "questions": ["问题1", "问题2"], "plan": null}

当确定时：
{"status": "plan", "original_request": "用户原始要求原文", "questions": null, "plan": {
  "summary": "一句话概括整体方案",
  "steps": [
    {"step": 1, "description": "本步要完成的事", "parent_folder": null, "parent_scope": "exact", "scope": "all_images",
     "folders": [{"name": "目录名", "criteria": "分类标准", "operation": "本步对该目录的操作"}],
     "preserves_previous_classification": true, "expected_result": "本步完成后的状态"}
  ]
}}

---

## 字段说明

1. status：question=需继续提问；plan=确定可输出。
2. original_request：原样保留用户最初要求，多轮问答后不得丢失或改写。
3. questions：question 时为问题数组（可一个或多个）；plan 时为 null。
4. plan：question 时为 null；plan 时为完整计划。
5. steps：多个步骤组成，step 编号唯一递增。
6. parent_folder：第一步为 null；后续步骤填已存在的父目录名（批量细分时填它们共同的上一级，根下则 null）。
7. parent_scope："exact"（默认，针对单一父目录）或 "all_children"（对该父层下所有子目录批量细分；folders 留空）。
8. scope：第一步通常为 all_images（用户只要求部分照片时按指定范围）；后续步骤写明针对哪个父目录。
9. folders：本步新建的同级目录，每个写清 name、criteria、operation；细分/批量步骤尚未查看内容时不预写子类名，folders 可为空数组。
10. preserves_previous_classification：必须始终为 true。
11. expected_result：描述本步完成后的明确结果。

---

最后再次强调：不确定就输出 question；可一次问多个问题；只有百分百确定才输出 plan；最终只输出 JSON。
"""


def _sanitize_quotes(text: str) -> str:
    """将英文直双引号替换为中文弯引号，避免破坏 JSON 结构。"""
    result = []
    open_quote = True
    for ch in text:
        if ch == '"':
            result.append("\u201c" if open_quote else "\u201d")
            open_quote = not open_quote
        else:
            result.append(ch)
    return "".join(result)


def build_messages(original_request: str, clarifications: list, authors: list | None = None) -> list[dict]:
    """构造发给快速模型的消息：系统提示词 + 作者名单 + 多轮真实问答上下文。

    历史以真实的 user/assistant 交替轮次传入（而非拍平成一整段文字），
    保证 AI 的每一次提问和用户的每一次回答都完整进入上下文，避免重复提问。
    """
    original_request = _sanitize_quotes(original_request)
    first = "用户原始要求（不得省略、改写、压缩或遗漏）：\n" + original_request
    first += "\n\n请根据规则判断：需要澄清就输出一个或多个问题；已确定就输出完整计划。"

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if authors:
        messages.append({"role": "system", "content": "当前项目作者名单：\n作者有：" + "、".join(authors)})
    messages.append({"role": "user", "content": first})

    for item in clarifications:
        qs = item.get("questions", [item.get("question", "")])
        if isinstance(qs, str):
            qs = [qs]
        ans = (item.get("answer") or "").strip()
        # AI 上一轮提出的问题 → 以 assistant 轮次回放
        messages.append({
            "role": "assistant",
            "content": json.dumps(
                {"status": "question", "original_request": original_request, "questions": qs, "plan": None},
                ensure_ascii=False,
            ),
        })
        # 用户本轮的回答 → 以 user 轮次回放
        messages.append({
            "role": "user",
            "content": ans if ans else "（未回答）",
        })

    # 最后补充防重复指令
    if clarifications:
        messages[-1]["content"] += "\n\n（以上问答均已知悉，不要重复提问已确认的问题；只需输出剩余问题或最终计划。）"
    return messages


def _extract_content(result: dict) -> str:
    """防御式提取 AI 响应中的 content。"""
    if not isinstance(result, dict):
        raise ValueError(f"AI 返回非 JSON 对象: {str(result)[:300]}")
    choices = result.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError(f"AI 返回异常结构 (choices 缺失或为空): {str(result)[:300]}")
    first = choices[0]
    msg = first.get("message") if isinstance(first, dict) else None
    content = msg.get("content", "") if isinstance(msg, dict) else ""
    finish_reason = first.get("finish_reason") if isinstance(first, dict) else None
    if finish_reason and finish_reason != "stop":
        logger.warning("AI 响应被截断: finish_reason=%s, content repr 前 300: %s", finish_reason, repr(str(content)[:300]))
    if not isinstance(content, str) or not content.strip():
        logger.warning("AI 返回 content 非字符串或为空: type=%s, value repr=%s", type(content).__name__, repr(str(content)[:200]))
        raise ValueError(f"AI 返回空内容: {str(result)[:300]}")
    logger.debug("AI content 类型=%s, 长度=%d, repr 前 200: %s", type(content).__name__, len(content), repr(content[:200]))
    return content


def _repair_json_quotes(text: str) -> str:
    """修复 AI 输出 JSON 中字符串值内未转义直双引号导致的畸形结构。

    典型场景：AI 回显用户原文时未对字符串值内的直双引号做转义，例如：
      "original_request": "先把所有图片分成"节目表演"和"观众与主持人"。"
    这里 节目表演 两侧的 " 会被解析器误认为字符串结束符，导致 json.loads 失败。

    思路：逐字符遍历并跟踪"当前是否处于字符串值内"。在字符串值内遇到 " 时，
    若其后（可含空白）紧跟 , } ] : 之一，则视为字符串结束符；否则视为值内部
    未转义的引号，替换为中文弯引号（成对开闭）。
    """
    result = []
    i = 0
    n = len(text)
    in_string = False
    open_quote = True
    while i < n:
        ch = text[i]
        if ch == '"':
            if not in_string:
                in_string = True
                result.append(ch)
            else:
                j = i + 1
                while j < n and text[j] in " \t\n\r":
                    j += 1
                if j < n and text[j] in ",}]:":
                    in_string = False
                    result.append(ch)
                else:
                    result.append("\u201c" if open_quote else "\u201d")
                    open_quote = not open_quote
            i += 1
            continue
        if ch == "\\" and in_string:
            # 转义序列整体保留，避免误判转义引号
            result.append(ch)
            if i + 1 < n:
                result.append(text[i + 1])
                i += 2
            else:
                i += 1
            continue
        result.append(ch)
        i += 1
    return "".join(result)


def _extract_json(text: str) -> dict | None:
    """从 AI 回复中提取 JSON 对象，必要时修复畸形的未转义引号。"""
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
    # 尝试 1: 直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 尝试 2: 正则提取最外层对象
    candidates = [text]
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        candidates.append(m.group())
    # 尝试 3: 修复未转义引号后再解析
    for cand in candidates:
        repaired = _repair_json_quotes(cand)
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            continue
    logger.warning("AI 返回内容无法解析为 JSON，repr 前 500 字符: %s", repr(text[:500]))
    return None


def call_plan(original_request: str, clarifications: list, authors: list | None = None) -> dict:
    """调用快速模型拆解分类计划，返回结构化结果。

    失败（配置缺失 / 网络 / 非 JSON / 未知 status）时抛出 RuntimeError。
    """
    cfg = read_config()
    if not cfg:
        raise RuntimeError("无法读取 config.json")
    api_key = (cfg.get("api_key") or "").strip()
    if not api_key:
        raise RuntimeError("未配置 API 密钥")
    api_url = cfg.get("api_url") or ""
    model = cfg.get("model") or "Qwen/Qwen3.5-397B-A17B"

    messages = build_messages(original_request, clarifications, authors)

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 4096,
    }

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(api_url, json=payload, headers=headers, timeout=TIMEOUT)
            resp.raise_for_status()
            content = _extract_content(resp.json())
            parsed = _extract_json(content)
            if parsed is None:
                raise ValueError("AI 返回非 JSON 格式")
            status = parsed.get("status")
            if status not in ("question", "plan"):
                raise ValueError(f"AI 返回未知 status: {status}")
            if status == "question":
                qs = parsed.get("questions")
                if not isinstance(qs, list) or not qs or not all((q or "").strip() for q in qs):
                    raise ValueError("AI 返回 question 但未提供有效问题列表")
            return parsed
        except Exception as e:
            last_err = e
            logger.warning("照片分类计划请求失败 (尝试 %d/%d): %s", attempt, MAX_RETRIES, e)
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"AI 调用失败: {last_err}")
