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

BATCH_ID_RE = re.compile(r"\b[A-Za-z]{2,4}-\d{3}\b")
ATTENDANCE_WORDS = re.compile(r"\b(attendance|attended|absent|present|classes? (did|have) i (miss|attend))", re.IGNORECASE)
ASSESSMENT_WORDS = re.compile(r"\b(assessments?|quiz(zes)?|assignments?|marks?|scores?|grades?|project|test results?)\b", re.IGNORECASE)
LIST_WORDS = re.compile(r"\b(my batch(es)?|my courses?|which batch|what am i enrolled|enrolled in)\b", re.IGNORECASE)


class ScriptedChatModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "scripted-fake"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedChatModel":
        # A real model needs the tool schemas; this one already "knows" its tools.
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
        match = BATCH_ID_RE.search(text)
        if not match and LIST_WORDS.search(text):
            return AIMessage(content="", tool_calls=[{"name": "get_my_batches", "args": {}, "id": "call_1", "type": "tool_call"}])
        if not match:
            return AIMessage(content="Which batch do you mean? Please share the batch id (for example PYF-101), "
                                     "or ask me to list your batches.")
        if ATTENDANCE_WORDS.search(text):
            tool = "get_my_attendance"
        elif ASSESSMENT_WORDS.search(text):
            tool = "list_my_assessments"
        else:
            tool = "get_my_schedule"
        return AIMessage(
            content="",
            tool_calls=[{"name": tool, "args": {"batch_id": match.group(0)}, "id": "call_1", "type": "tool_call"}],
        )

    @staticmethod
    def _answer_from_tool(message: ToolMessage) -> str:
        try:
            data = json.loads(str(message.content))
        except json.JSONDecodeError:
            return "Sorry, I hit a problem looking that up."
        if "error" in data:
            return f"I couldn't complete that lookup: {data['error']}"
        if "batches" in data:
            if not data["batches"]:
                return "I don't see any batches on your account."
            return "Your batches: " + "; ".join(
                f"{b['batch_id']} ({b['course']}, {b['mode']}, {b['enrollment_status']})" for b in data["batches"]) + "."
        if "assessments" in data:
            if not data["assessments"]:
                return f"No assessments are listed for {data['batch_id']}."
            return f"Assessments for {data['batch_id']}: " + "; ".join(
                f"{a['title']} due {a['due_date']} - {a['status']}" + (f" ({a['score']}/{a['max_score']})" if a["score"] is not None else "")
                for a in data["assessments"]) + "."
        if "upcoming" in data:
            if not data["upcoming"]:
                return data.get("note", f"No upcoming classes are listed for {data['batch_id']}.")
            return f"Next classes for {data['batch_id']} ({data['timing']}): " + "; ".join(
                f"{u['date']} {u['topic']}" for u in data["upcoming"]) + "."
        if "sessions_held" in data:
            if data["sessions_held"] == 0:
                return data["note"]
            return (f"Attendance for {data['batch_id']}: {data['sessions_attended']} of {data['sessions_held']} classes "
                    f"({data['attendance_pct']}%). {data['note']}")
        return "Done."
