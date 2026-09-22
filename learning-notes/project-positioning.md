# 项目定位与包装

## 一句话理解

这是一个面向 AI Agent 的可插拔 RAG 知识检索服务：负责文档摄取、混合检索与结果引用，并通过 MCP stdio 暴露为标准工具。

## 源码事实

- MCP 服务入口为 `src/mcp_server/server.py::main()`，使用官方 MCP SDK 的 stdio transport。
- 默认暴露 `query_knowledge_hub`、`list_collections`、`get_document_summary` 三个工具。
- 查询主链路为：`QueryKnowledgeHubTool.execute()` → `QueryProcessor` → `HybridSearch` → Dense/BM25 检索 → RRF Fusion → 可选 Rerank → `ResponseBuilder`/Citation → `CallToolResult`。
- 当前摄取 CLI 主要处理 PDF：`PdfLoader` → `DocumentChunker` → Transform → Dense/Sparse 编码 → Chroma 与 BM25 索引。
- `src/libs/` 中的 LLM、Embedding、Reranker、Splitter、VectorStore、Evaluator 使用抽象接口与工厂，配置在 `config/settings.yaml` 中选择实现。
- `TraceContext`/`TraceCollector` 支持查询与摄取链路追踪；`src/observability/dashboard/` 提供 Streamlit 管理页面；`src/observability/evaluation/` 提供评估相关组件。
- 当前仓库有 65 个测试文件、约 1364 个测试函数定义；本环境未安装 pytest，因此没有把“测试全部通过”作为已验证结论。

## 推荐包装定位

优先包装为“面向 AI Agent 的模块化 RAG 检索底座 / MCP Knowledge Retrieval Service”，而不是泛泛的“聊天机器人”或未经验证的“生产级企业平台”。

## 可主打的三条卖点

1. 检索质量：Dense + BM25 混合召回，使用 RRF 融合，支持可选重排。
2. 工程解耦：核心 Provider 通过抽象接口、工厂和 YAML 配置替换，减少业务层与具体供应商绑定。
3. Agent 接入：用 MCP 把检索、知识库发现和文档摘要标准化为工具，结果带来源引用。

## 数据与表述边界

README 中出现的文档规模、准确率、延迟、测试数量等示例数字，不能直接视为本仓库已经验证的运行结果。简历中只有在本人用真实数据跑过并能说明评估方法时，才写入具体指标；否则使用“支持”“实现”“建立机制”等事实型表述。

## 面试主线

先讲业务问题：关键词搜索难以同时处理专有名词匹配与语义表达。

再讲设计：稀疏检索保留关键词精确性，Dense 检索补充语义召回，RRF 做稳定融合，Rerank 做候选集精排。

最后讲工程化：摄取与查询分层，统一 `Document`/`Chunk`/`ChunkRecord` 契约，Provider 可替换，MCP 负责 Agent 适配，Trace 和评估负责定位与回归。

## 还需要补证据的部分

- 用自己的垂直领域文档构建 golden set，实测 Hit@K、MRR、延迟和成本。
- 至少对比纯 Dense、纯 BM25、Hybrid、Hybrid+Rerank 四种策略。
- 补一次真实 MCP Client 调用与一次 PDF 摄取全链路演示。
- 在简历中写入的每个 Provider、Dashboard 页面和评估指标都要实际运行并能解释。

## 本次核对：本地模型横评的当前边界

- 当前已具备 Ollama 文本 Provider、配置切换模型名、`ChatResponse.usage`，并保留 Ollama 原始响应；原始响应包含 `load_duration`、`eval_count`、`eval_duration` 等可用于后续测量的字段。
- 当前 `config/settings.yaml` 实际配置是 DeepSeek 文本 LLM、Ollama Embedding，Ollama Vision 处于 disabled；默认 Reranker 是 Cross-Encoder，因此不能把“项目当前已基于 Ollama 部署文本大模型”当作现状。
- 当前没有模型横评/benchmark 脚本，也没有显存采集、固定问题集、warm-up/重复运行、p50/p95 或统一质量评分实现。
- 因此“支持 Ollama 本地模型”是当前事实；“同硬件横评 3 个模型并据此选型”属于可在现有 Provider/评测基础上实现的新增工作，不能当作当前已完成结果。
