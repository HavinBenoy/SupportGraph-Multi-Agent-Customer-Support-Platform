import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.errors import GraphRecursionError

from app.graph.builder import build_graph
from app.llm.fake import ScriptedChatModel


def _run(text, cfg):
    return build_graph().invoke({"messages": [HumanMessage(text)]}, cfg)


def test_agent_calls_tool_then_answers(asha_cfg):
    result = _run("What is my attendance in PYF-101?", asha_cfg)
    types = [m.type for m in result["messages"]]
    assert types == ["human", "ai", "tool", "ai"]  # question -> tool call -> tool result -> answer
    assert result["messages"][1].tool_calls[0]["name"] == "get_my_attendance"
    assert "8 of 10" in result["messages"][-1].content and "80.0%" in result["messages"][-1].content


def test_agent_routes_assessment_questions_to_the_assessment_tool(asha_cfg):
    result = _run("What marks did I get in PYF-101 quiz?", asha_cfg)
    assert result["messages"][1].tool_calls[0]["name"] == "list_my_assessments"
    assert "17/20" in result["messages"][-1].content


def test_agent_routes_schedule_questions_to_the_schedule_tool(asha_cfg):
    result = _run("When is my next class in PYF-101?", asha_cfg)
    assert result["messages"][1].tool_calls[0]["name"] == "get_my_schedule"
    assert "2026-10-25" in result["messages"][-1].content


def test_agent_lists_batches_when_asked_which_batches_the_student_is_in(asha_cfg):
    result = _run("Which batch am I enrolled in?", asha_cfg)
    assert result["messages"][1].tool_calls[0]["name"] == "get_my_batches"
    assert "PYF-101" in result["messages"][-1].content


def test_agent_asks_for_batch_id_without_calling_tools(asha_cfg):
    result = _run("How am I doing?", asha_cfg)
    assert [m.type for m in result["messages"]] == ["human", "ai"]
    assert not result["messages"][-1].tool_calls and "PYF-101" in result["messages"][-1].content


class _LoopingModel(ScriptedChatModel):
    """Misbehaving model that requests a tool forever -- simulates a runaway ReAct loop."""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        from langchain_core.outputs import ChatGeneration, ChatResult

        call = {"name": "get_my_attendance", "args": {"batch_id": "PYF-101"}, "id": "call_x", "type": "tool_call"}
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="", tool_calls=[call]))])


def test_runaway_tool_loop_is_stopped_by_recursion_limit(asha_cfg):
    graph = build_graph(model=_LoopingModel())
    cfg = {**asha_cfg, "recursion_limit": 6}
    with pytest.raises(GraphRecursionError):
        graph.invoke({"messages": [HumanMessage("attendance PYF-101")]}, cfg)
