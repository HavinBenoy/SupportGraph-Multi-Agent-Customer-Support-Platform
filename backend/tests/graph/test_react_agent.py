import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.errors import GraphRecursionError

from app.graph.builder import build_graph
from app.llm.fake import ScriptedChatModel


def _run(text, cfg):
    return build_graph().invoke({"messages": [HumanMessage(text)]}, cfg)


def test_agent_calls_tool_then_answers(alice_cfg):
    result = _run("Where is my order A1001?", alice_cfg)
    types = [m.type for m in result["messages"]]
    assert types == ["human", "ai", "tool", "ai"]  # question -> tool call -> tool result -> answer
    assert result["messages"][1].tool_calls[0]["name"] == "get_order_status"
    assert "shipped" in result["messages"][-1].content


def test_agent_routes_return_questions_to_eligibility_tool(alice_cfg):
    result = _run("Can I return order A1002?", alice_cfg)
    assert result["messages"][1].tool_calls[0]["name"] == "check_return_eligibility"
    assert "Eligible" in result["messages"][-1].content


def test_agent_asks_for_order_id_without_calling_tools(alice_cfg):
    result = _run("Where is my stuff?", alice_cfg)
    assert [m.type for m in result["messages"]] == ["human", "ai"]
    assert not result["messages"][-1].tool_calls


class _LoopingModel(ScriptedChatModel):
    """Misbehaving model that requests a tool forever -- simulates a runaway ReAct loop."""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        from langchain_core.outputs import ChatGeneration, ChatResult

        call = {"name": "get_order_status", "args": {"order_id": "A1001"}, "id": "call_x", "type": "tool_call"}
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="", tool_calls=[call]))])


def test_runaway_tool_loop_is_stopped_by_recursion_limit(alice_cfg):
    graph = build_graph(model=_LoopingModel())
    cfg = {**alice_cfg, "recursion_limit": 6}
    with pytest.raises(GraphRecursionError):
        graph.invoke({"messages": [HumanMessage("order A1001")]}, cfg)
