import logging
from time import perf_counter

from langgraph.graph import END, START, StateGraph

from processor.query_processor.state import QueryState


def build_graph(service):
    """受控路由：结构化请求直接结束，其余请求只走检索和证据回答。"""
    graph = StateGraph(QueryState)

    def measured(name, operation, message):
        async def invoke(state):
            started = perf_counter()
            emit = state["run"].get("emit_status")
            if emit:
                await emit(message)
            try:
                return await operation(state)
            finally:
                logging.getLogger("zhichengapp").info(
                    "query_stage request_id=%s stage=%s seconds=%.3f",
                    state["run"]["request_id"],
                    name,
                    perf_counter() - started,
                )

        return invoke

    graph.add_node("route", measured("route", service.route, "正在确认问题和课程范围"))
    graph.add_node("retrieve", measured("retrieve", service.retrieve, "正在检索并筛选课程证据"))
    graph.add_node(
        "generate", measured("generate", service.generate, "检索完成，正在组织回答并核对引用")
    )
    graph.add_edge(START, "route")
    graph.add_conditional_edges(
        "route",
        lambda state: "done" if "result" in state else "search",
        {"done": END, "search": "retrieve"},
    )
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", END)
    return graph.compile()
