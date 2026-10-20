# 知成 RAG 30题测评报告

测评日期：2026-10-20。使用当前知识库、真实本地编码与重排模型、Milvus和配置中的生成模型。未修改业务代码或知识库。

## 结果

完成30题：24道可回答题、6道无答案题。可回答题正确完整 22/24，正确拒答 6/6。

| 核心指标 | 结果 |
|---|---:|
| 章节 Recall@5 | 95.83% |
| 章节 Hit@5 | 100.00% |
| 章节 MRR@5 | 0.972 |
| 正确完整率（AI核对） | 91.67% |
| 回答级依据一致率（AI核对，仅实质回答） | 100.00%（22/22） |
| 严格引用支持通过率（AI核对，仅实质回答） | 95.45%（21/22） |
| 正确拒答率 | 100.00% |
| 误拒答率 | 8.33% |
| 问答 P50 / P95 | 8.47s / 30.86s |
| 检索与重排 P50 | 0.68s |

## 失败分析

1. 主键与外键综合题：前五条及补充上下文缺少主键定义；模型明确表示只能解释外键，因此拒答。不是知识库没有资料，而是单次检索没有找齐子问题依据。
2. FastAPI路径参数与请求体综合题：召回集中在路径/查询参数，没有请求体模型。模型正确识别当前上下文不足，但对库内可回答问题而言构成误拒答。
3. Python列表切片题：答案正确，引用[3]支持结果；额外的父章节引用[6]只有列表与索引定义，不能支撑具体切片输出，严格引用判定未通过。

优先尝试多子问题拆解召回、按子问题分配候选名额，再合并去重重排。引用生成只保留实际支持对应结论的段落。以上为待验证改进方向，本轮未修改生产逻辑。

## 资料更新与复现范围

旧机器学习DOCX已删除，当前新版本保留LaTeX。梯度下降公式题与准确率公式题均通过。本轮重新建立章节标签，不能把与旧轮次分数的差异简单解释为模型性能变化。
运行前后，所选版本稳定：True；活动讲义集合稳定：True；所检查代码哈希稳定：True。

## 口径与限制

- 30题为AI编写并核对的开发测评集，没有独立人工审核或封闭测试集。无答案题包含合成标记，难度较低。
- Recall是对预标注章节的宏平均；部分问题有两个目标章节。同义或替代证据未穷尽标注，不能当作全库穷尽召回率。
- 依据一致率只对实际生成的22条实质回答评分，两个误拒答计入正确完整率的失败项，不用拒答抬高依据一致率。不是Ragas原子事实分数。
- 引用支持按回答级严格判定，一条仅提供相关背景的多余引用也会导致该回答未通过。
- 使用生产QueryService/LangGraph业务链路，非HTTP/API全链路；不含SSE、浏览器、多轮对话和并发测试。
- 每题单次顺序运行，本地模型已预热。P95使用nearest-rank，第29顺位；不报告未采集的token成本或首字延迟。
- 本轮未重新运行上轮22项回归测试，不能把上轮结果说成本轮新测试。

## 逐题结果

| 用例 | 类别 | Recall@5 | 业务状态 | 耗时 | AI核对 |
|---|---|---:|---|---:|---|
| tuple | 基础问答 | 1.0 | completed | 10.89s | 通过 |
| singleton | 基础问答 | 1.0 | completed | 5.55s | 通过 |
| acid | 基础问答 | 1.0 | completed | 5.30s | 通过 |
| atomicity | 基础问答 | 1.0 | completed | 11.98s | 通过 |
| autocommit | 基础问答 | 1.0 | completed | 4.87s | 通过 |
| missing | 基础问答 | 1.0 | completed | 19.71s | 通过 |
| readcsv | 基础问答 | 1.0 | completed | 8.33s | 通过 |
| gradient | 基础问答 | 1.0 | completed | 24.77s | 通过 |
| overfit | 基础问答 | 1.0 | completed | 30.86s | 通过 |
| backprop | 基础问答 | 1.0 | completed | 14.46s | 通过 |
| containers | 表格 | 1.0 | completed | 5.54s | 通过 |
| list-slice | 代码 | 1.0 | completed | 12.51s | 未通过 |
| empty-set | 代码 | 1.0 | completed | 5.31s | 通过 |
| count-null | 表格与代码 | 1.0 | completed | 9.71s | 通过 |
| keys | 跨章节 | 1.0 | completed | 6.52s | 通过 |
| foreign | 跨章节 | 1.0 | completed | 8.58s | 通过 |
| path-int | 代码 | 1.0 | completed | 8.35s | 通过 |
| body-model | 代码 | 1.0 | completed | 5.10s | 通过 |
| vectorize | 代码 | 1.0 | completed | 7.11s | 通过 |
| numpy-slice | 代码 | 1.0 | completed | 10.70s | 通过 |
| constraints | 跨章节 | 0.5 | insufficient_evidence | 24.55s | 未通过 |
| api-inputs | 跨章节 | 0.5 | insufficient_evidence | 34.87s | 未通过 |
| formula-update | 公式 | 1.0 | completed | 26.89s | 通过 |
| formula-accuracy | 公式与表格 | 1.0 | completed | 9.42s | 通过 |
| no-fee | 无答案 | — | insufficient_evidence | 6.99s | 通过 |
| no-nonce | 无答案 | — | insufficient_evidence | 5.15s | 通过 |
| no-person | 无答案 | — | insufficient_evidence | 7.33s | 通过 |
| no-benchmark | 无答案 | — | insufficient_evidence | 7.89s | 通过 |
| no-room | 无答案 | — | insufficient_evidence | 10.04s | 通过 |
| no-falsepremise | 无答案 | — | insufficient_evidence | 5.80s | 通过 |
