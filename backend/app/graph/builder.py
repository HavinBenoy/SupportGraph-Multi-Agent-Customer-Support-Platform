"""Builds the minimal ReAct graph:

    START -> agent --(has tool calls?)--> tools --> agent ... --> END
                  \\--(no tool calls)--> END
"""

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from app.agents.prompts.action_agent import SYSTEM_PROMPT
from app.graph.state import TicketState
from app.config import get_settings
from app.llm.gateway import get_chat_model
from app.llm.guard import invoke_guarded
from app.tools.registry import get_tools


def build_graph(model: BaseChatModel | None = None, tools: list[BaseTool] | None = None, checkpointer=None):
    model = model or get_chat_model("action")
    tools = tools if tools is not None else get_tools()
    model_with_tools = model.bind_tools(tools)

    def agent(state: TicketState) -> dict:
        reply = invoke_guarded(model_with_tools, [SystemMessage(SYSTEM_PROMPT), *state["messages"]], get_settings())
        return {"messages": [reply]}

    graph = StateGraph(TicketState)
    graph.add_node("agent", agent)
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition)  # -> "tools" if tool calls, else END
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=checkpointer)
