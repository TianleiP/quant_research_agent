from __future__ import annotations

from langgraph.graph import END, StateGraph

from quant_agent.agent.nodes import (
    approval_node,
    decide_node,
    evaluate_node,
    execute_node,
    observe_node,
    plan_node,
    route_after_approval,
    route_after_decide,
    route_after_evaluate,
    route_after_execute,
)
from quant_agent.agent.state import AgentState


def build_agent_graph(checkpointer=None):
    graph = StateGraph(AgentState)
    graph.add_node("observe", observe_node)
    graph.add_node("plan", plan_node)
    graph.add_node("decide", decide_node)
    graph.add_node("approval", approval_node)
    graph.add_node("execute", execute_node)
    graph.add_node("evaluate", evaluate_node)
    graph.set_entry_point("observe")
    graph.add_edge("observe", "plan")
    graph.add_edge("plan", "decide")
    graph.add_conditional_edges(
        "decide",
        route_after_decide,
        {"approval": "approval", "execute": "execute", "end": END},
    )
    graph.add_conditional_edges("approval", route_after_approval, {"execute": "execute", "end": END})
    graph.add_conditional_edges("execute", route_after_execute, {"evaluate": "evaluate", "end": END})
    graph.add_conditional_edges("evaluate", route_after_evaluate, {"decide": "decide", "end": END})
    return graph.compile(checkpointer=checkpointer)
