# 术语表

| Term | 中文理解 | 在本项目中的作用 |
|---|---|---|
| MCP | 让外部 AI Client 按标准协议发现并调用工具的协议 | `src/mcp_server/` 对外暴露知识库能力 |
| Tool | MCP Client 可调用的一个命名操作 | `query_knowledge_hub`、`list_collections`、`get_document_summary` |
| RAG | 先从知识库检索，再把结果用于生成/回答的应用架构 | 本项目的核心业务能力，主要落在 `src/core/`、`src/libs/`、`src/ingestion/` |
| Collection | 一组相关知识记录的逻辑集合/知识域 | Chroma collection；实际保存多个文档切分后的 Chunk |
| ChromaDB | 面向向量相似度检索的数据库 | 持久化保存 Chunk 的 Dense vector、正文和 metadata |
| Vector Store | 对向量存储与相似度查询的统一抽象 | `BaseVectorStore` 定义契约，当前实现是 `ChromaStore` |
| Document | 原始完整文档 | 摄取流程中的源对象，通过 `doc_id` / `source_ref` 与多个 Chunk 关联 |
| Chunk | 文档切分后的可检索片段 | Chroma 中的主要记录单位，同时拥有 Dense vector、正文和 metadata |
| ChunkRecord | 带向量的 Chunk 存储模型 | 表达 Chunk 加 dense/sparse vector；当前代码主要由 VectorUpserter 直接构造 dict 写入 |
| ProcessedQuery | 结构化查询 | 保存原始 query、关键词和 filters，供 Dense/Sparse 检索使用 |
| RetrievalResult | 统一召回结果 | Dense、Sparse、Hybrid、Rerank 和 ResponseBuilder 之间共享的结果结构 |
| Ingestion | 把外部文档处理并写入可检索存储的过程 | `scripts/ingest.py` 与 `src/ingestion/` |
| OpsPilot Agent Service | 内部 OpsPilot 的 TypeScript / Node.js Agent 服务 | 通过 Application `ToolDefinition` 将 RAG 能力接入通用 Agent Runtime |
| Runbook | 针对某类告警或故障的标准处置手册 | 可作为本项目的知识库文档，被 MCP query tool 检索 |
| Intent Recognition | 判断用户想做什么，并决定是否调用哪个 Agent Tool | 通常属于 Agent / 对话编排层；本项目 README 将其与 RAG 的检索能力分开描述 |
| Audience / Visibility | 文档面向的角色和可见范围 | Excel 插件场景中区分最终用户文档与内部开发者文档，不能只靠 collection 名称实现安全隔离 |
| MCP Bridge | 在插件或远程服务与 stdio MCP Server 之间做协议转接的适配层 | 当前项目使用 stdio；远程 Excel 插件接入时需要 Client/Bridge 或其他 transport |
| Golden Set | 用于回归评估的一组带期望结果的问题集合 | 可按用户问题、错误码、函数名和开发者 API 分组评估检索质量 |
| Vision LLM | 能同时接收文本和图片的模型 | 摄取阶段由 `ImageCaptioner` 调用，将 PDF 图片转成描述并写回 Chunk |
| Ollama Vision Provider | 对本地 Ollama 多模态模型的适配层 | `OllamaVisionLLM` 调用 `/api/chat`，当前配置模型为 `qwen2.5vl:3b` |
