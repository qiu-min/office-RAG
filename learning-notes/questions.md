# 学习问题

## 待解决

### Q: Excel 插件用户文档和内部开发者文档应如何做服务端权限隔离？

Context:

当前查询支持 collection 与 metadata filter，但尚未看到用户身份、角色、ACL 或租户边界。仅允许调用方传入 collection 名称不能满足企业内部文档安全要求。

Related source:

- `src/mcp_server/tools/query_knowledge_hub.py`
- `src/core/query_engine/query_processor.py`
- `src/core/query_engine/hybrid_search.py`

Status: 待确认

### Q: Excel 插件侧应通过 MCP Client、MCP Bridge 还是直接调用 RAG core？

Context:

当前服务使用 stdio transport，而插件或远程后端通常需要进程内调用、HTTP 或其他远程连接方式。需要根据真实插件架构决定接入层。

Related source:

- `src/mcp_server/server.py`
- `src/mcp_server/protocol_handler.py`

Status: 待确认

### Q: `IngestionPipeline` 是否需要改造成五个基础组件全部依赖注入？

Context:

五个抽象接口已经存在，但当前 `IngestionPipeline` 仍直接实例化 `PdfLoader`、`DocumentChunker`、三个 Transform、`BatchProcessor`、`VectorUpserter`、`BM25Indexer` 和 `ImageStorage`。因此当前架构是“部分通过 Factory/Adapter 插拔”，还不是完全由五个接口驱动的通用 Pipeline。

Related source:

- `DEV_SPEC.md::3.1.1`
- `src/ingestion/pipeline.py::IngestionPipeline.__init__`
- `src/ingestion/chunking/document_chunker.py::DocumentChunker`

Status: 待确认

### Q: `mcp-server` 命令为什么映射到 `main.py`，而实际 MCP 服务入口在 `src/mcp_server/server.py`？

Context:

`pyproject.toml` 的 console script 与实际 server 模块的启动逻辑不一致，需要后续确认是遗留入口、阶段性占位，还是打包配置遗漏。

Related source:

- `pyproject.toml::project.scripts`
- `main.py::main`
- `src/mcp_server/server.py::main`

Status: 待确认

### Q: 如何把 RAG 查询能力适配成 OpsPilot `ToolDefinition`？

Context:

OpsPilot 的 Agent Runtime 只需要业务无关的 `AgentTool`，Application 层通过 `ToolDefinition` 提供工具定义和执行边界。需要决定是让 Agent Service 通过 MCP client 调用本项目，还是把 RAG core 封装成 Node.js connector；同时要定义引用、超时、取消和错误恢复语义。

Related source:

- `src/mcp_server/tools/query_knowledge_hub.py::query_knowledge_hub_handler`
- `D:/AgentProjects/OpsPilot/agent-service/packages/application/src/tools/tool-definition.ts::ToolDefinition`
- `D:/AgentProjects/OpsPilot/agent-service/packages/application/src/tools/wrap-tool-definition.ts::wrapToolDefinition`

Status: 待确认

## 已解决

### Q: `query_knowledge_hub` 从 `QueryKnowledgeHubTool.execute()` 到最终响应的完整 RAG 调用链是什么？

Answer:

`execute()` 完成参数校验和组件初始化后，调用 `HybridSearch.search()`。HybridSearch 先用 `QueryProcessor` 提取关键词与过滤条件，再并行执行 Dense 和 BM25 召回；两路成功时通过 `RRFFusion` 融合，单路失败时使用另一条结果。Tool 随后可调用 `CoreReranker` 精排，再由 `ResponseBuilder` 生成 Markdown、Citation 和可选图片，最后转换为 MCP `CallToolResult`。

Related source:

- `src/mcp_server/tools/query_knowledge_hub.py`
- `learning-notes/retrieval.md`

Status: 已解决
