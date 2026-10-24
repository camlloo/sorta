"""LLM 客户端抽象：OpenAI Chat Completions 兼容协议的轻量封装。

开发文档 §12.1 约束：只允许轻量客户端或 httpx 直连，禁止任何 Agent/编排框架；
Tool Calling 循环由 controller.py 手写实现。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolCall:
    """模型发起的一次工具调用。arguments 为原样 JSON 字符串，由调度器校验。"""

    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMClient(Protocol):
    """最小客户端协议：controller 只依赖这个接口，便于 Mock 测试与换供应商。"""

    def chat(
        self, *, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LLMResponse: ...


class OpenAICompatClient:
    """基于 openai SDK 的 OpenAI 兼容实现（base_url 可指向任意兼容服务商）。"""

    def __init__(self, *, api_key: str, base_url: str | None = None, timeout: float = 120.0):
        from openai import OpenAI  # 延迟导入：Mock 测试无需安装/初始化 SDK

        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    def chat(
        self, *, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LLMResponse:
        resp = self._client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools or None,
            tool_choice="auto" if tools else None,
        )
        message = resp.choices[0].message
        calls = [
            ToolCall(
                id=call.id or f"call_{i}",
                name=call.function.name,
                arguments=call.function.arguments or "{}",
            )
            for i, call in enumerate(message.tool_calls or [])
        ]
        return LLMResponse(content=message.content, tool_calls=calls)


def system_message(text: str) -> dict[str, Any]:
    return {"role": "system", "content": text}


def user_message(text: str) -> dict[str, Any]:
    return {"role": "user", "content": text}


def assistant_message(response: LLMResponse) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": response.content,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in response.tool_calls
        ],
    }


def tool_message(call_id: str, result_json: str) -> dict[str, Any]:
    """工具结果消息。content 恒为 JSON 字符串：文件内容被序列化为数据，
    无法伪造消息结构或指令（§6.5 Prompt Injection 防护）。"""
    return {"role": "tool", "tool_call_id": call_id, "content": result_json}


def dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False)
