"""A deterministic, rule-based chat model that speaks the tool-calling protocol.

Why it exists: tests and CI must not depend on a real LLM (slow, flaky, costs money).
This model makes the same *kind* of moves a real one does -- emit a tool call, then read
the tool result and answer -- so the graph wiring is tested for real. It is NOT a
substitute for evals against a real model (build-order step 6).
"""

import json
import re
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

ORDER_ID_RE = re.compile(r"\b[A-Za-z]\d{4}\b")
RETURN_WORDS = re.compile(r"\b(return|refund|send (it )?back)\b", re.IGNORECASE)


class ScriptedChatModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "scripted-fake"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedChatModel":
        # A real model needs the tool schemas; this one already "knows" its two tools.
        return self

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        last = messages[-1]
        if isinstance(last, ToolMessage):
            reply = AIMessage(content=self._answer_from_tool(last))
        else:
            reply = self._decide(messages)
        return ChatResult(generations=[ChatGeneration(message=reply)])

    def _decide(self, messages: list[BaseMessage]) -> AIMessage:
        text = next((str(m.content) for m in reversed(messages) if isinstance(m, HumanMessage)), "")
        match = ORDER_ID_RE.search(text)
        if not match:
            return AIMessage(content="Could you share your order number (for example A1001)?")
        tool = "check_return_eligibility" if RETURN_WORDS.search(text) else "get_order_status"
        return AIMessage(
            content="",
            tool_calls=[{"name": tool, "args": {"order_id": match.group(0)}, "id": "call_1", "type": "tool_call"}],
        )

    @staticmethod
    def _answer_from_tool(message: ToolMessage) -> str:
        try:
            data = json.loads(str(message.content))
        except json.JSONDecodeError:
            return "Sorry, I hit a problem looking that up."
        if "error" in data:
            return f"I couldn't complete that lookup: {data['error']}"
        if "eligible" in data:
            return f"Return check for {data['order_id']}: {data['reason']}"
        return f"Order {data['order_id']} is {data['status']}."
