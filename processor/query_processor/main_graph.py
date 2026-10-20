from langgraph.graph import END, START, StateGraph

from processor.query_processor.state import QueryState


def build_graph(service):
    """受控路由：结构化请求直接结束，其余请求只走检索和证据回答。"""
    graph = StateGraph(QueryState)
    graph.add_node("route", service.route)
    graph.add_node("retrieve", service.retrieve)
    graph.add_node("generate", service.generate)
    graph.add_edge(START, "route")
    graph.add_conditional_edges(
        "route",
        lambda state: "done" if "result" in state else "search",
        {"done": END, "search": "retrieve"},
    )
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", END)
    return graph.compile()
