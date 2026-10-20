import logging
from time import perf_counter

from langgraph.graph import END, START, StateGraph

from processor.import_processor.state import ImportState

logger = logging.getLogger("zhichengapp")


def timed_node(stage, operation):
    """记录阶段耗时和任务标识，不记录文档正文、模型输入或凭据。"""

    async def execute(state):
        started = perf_counter()
        try:
            return await operation(state)
        finally:
            logger.info(
                "task_id=%s stage=%s duration_ms=%.1f",
                state["task_id"],
                stage,
                (perf_counter() - started) * 1000,
            )

    return execute


def build_graph(service):
    """绑定已注入的服务上下文；节点不会自己创建数据库或下载模型。"""
    graph = StateGraph(ImportState)
    graph.add_node("parse_and_store", timed_node("parse_and_store", service.parse_and_store))
    graph.add_node("index", timed_node("index", service.index))
    graph.add_node("activate", timed_node("activate", service.activate))
    graph.add_edge(START, "parse_and_store")
    graph.add_edge("parse_and_store", "index")
    graph.add_edge("index", "activate")
    graph.add_edge("activate", END)
    return graph.compile()
