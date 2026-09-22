# MODULAR-RAG-MCP-SERVER 学习笔记

## 本轮范围

本轮只学习整体架构：README 与目录结构、程序入口、MCP/RAG 边界、主要模块职责，以及下一步最值得追踪的调用链。没有进入文档加载、分块、Embedding、向量库、召回或重排的内部实现。

本轮新增学习：已从 `query_knowledge_hub` 逐层追踪到 Hybrid Search、Dense/BM25 召回、RRF 融合、可选 Rerank、Citation 和 MCP `CallToolResult`；并完成了项目定位、简历/面试包装边界和 Excel 插件业务垂直化方案梳理，见 [project-positioning.md](project-positioning.md) 与 [excel-plugin-verticalization.md](excel-plugin-verticalization.md)。本次还补充确认了 `ImageCaptioner` → `BaseVisionLLM` → Ollama `/api/chat` 的图片描述扩展点，并实现了本地 Ollama Vision Provider。

职责边界校正：Excel 插件只是 RAG 子系统的业务背景；个人包装聚焦文档知识检索模块，不声称负责整个插件产品。

## 面向项目实战与求职的学习路线

### 推荐目标

以“能够独立解释、调试并改造一条完整 RAG 链路”为目标，不要求一次性记住所有文件。对于简历和面试，至少达到 Level 3；如果要承担 RAG/LLM 应用工程岗位，核心检索链路应达到 Level 4。

### 能力等级

- [x] Level 1：能运行项目，说明它解决“客户端软件说明文档难检索、智能客服需要私有知识”的问题。
- [~] Level 2：能画出架构，解释 MCP、Ingestion、Retrieval、Storage、Observability 的职责边界。
- [ ] Level 3：能从 MCP Tool 追踪到检索结果，解释 Chunk、Embedding、BM25、向量库、RRF、Rerank 的输入输出，并独立修改一个组件。
- [ ] Level 4：能设计评估集、定位坏检索 Case、解释关键取舍，并为新文档源或新 Provider 增加适配。
- [ ] Level 5：能面向生产场景讨论权限、多租户、增量同步、延迟、成本、可用性和服务化部署。

### 学习顺序

1. **查询主链路**：`query_knowledge_hub` → `QueryKnowledgeHubTool` → `QueryProcessor` → `HybridSearch` → `ResponseBuilder`。
2. **检索基础**：先理解 Chunk、Embedding、VectorStore，再学习 Dense Retriever、BM25、RRF 和 Reranker。
3. **数据摄取链路**：`scripts/ingest.py` → `Pipeline` → Loader → Chunking → Transform → Embedding → Storage。
4. **工程闭环**：配置与工厂、Trace、Dashboard、Ragas/Custom Evaluator、测试分层。
5. **动手改造**：替换一个 Provider，新增一个 Loader 或 Retriever，增加一个评估 Case，并补测试。
6. **面试复盘**：能够解释为什么这样设计，以及如果文档规模、延迟或数据源发生变化应如何演进。

### 当前最自然的下一步

进入 Ingestion 链路，理解说明文档如何经过 Loader、Chunking、Transform、Embedding 和 Storage，最终变成查询侧能够使用的 Chroma/BM25 数据。

## 学习地图

### Architecture

- [x] 仓库整体分层与主要目录
- [x] `src/core/types.py` 数据结构关系
- [x] 程序入口（含入口不一致问题）
- [x] MCP 与 RAG 的模块边界
- [x] 主要模块职责
- [x] 首条推荐调用链：MCP query tool → QueryKnowledgeHubTool
- [~] 配置与依赖关系的深入学习（已确认 DeepSeek 凭据读取方式，仍需梳理全量配置）
- [ ] 完整 RAG pipeline

### MCP

- [~] MCP Server：已定位 `src/mcp_server/server.py`，尚未深入 transport/生命周期细节
- [x] MCP Tools：已确认 3 个默认 tool 及各自职责
- [x] Tool execution flow：已逐层追踪 `query_knowledge_hub` 到 MCP 响应

### Ingestion

- [x] Document Loading：已确认当前入口是 PDF → `Document`，并理解 `BaseLoader` 只是可插拔契约
- [x] Chunking：已确认 `DocumentChunker` 将 `Document` 转换为带 `source_ref` 的多个 `Chunk`，并理解 `BaseSplitter` 与业务适配器的边界
- [x] Embedding：已确认 BatchProcessor 并行生成 Dense vector 与 Sparse term stats，并理解 `BaseEmbedding` 只覆盖 Dense provider
- [x] Indexing / Storage：已确认 Chroma 与 BM25 的双路构建及 ID 对齐，并理解 `BaseVectorStore` 不覆盖 BM25/图片索引
- [x] 五个抽象接口与完整摄取流水线的边界

### Storage

- [x] Vector Store / Chroma：已确认 ChromaDB、`ChromaStore` 适配器、Collection 与 Chunk 记录的关系

### Retrieval

- [x] Query processing
- [x] Vector retrieval
- [x] Hybrid retrieval
- [ ] Metadata filtering
- [x] Reranking

### Evaluation

- [x] Ground Truth / Golden Test Set 的字段和 Custom Evaluator 调用链
- [x] 基于业务文档制作并运行真实评估集（Business Golden Set v1）

## 当前架构地图（已从源码确认的范围）

```text
MCP Client
    ↓ stdio / MCP SDK
src/mcp_server/server.py
    ↓ create_mcp_server()
src/mcp_server/protocol_handler.py
    ├── tools/list
    └── tools/call
          ↓
    src/mcp_server/tools/
      ├── query_knowledge_hub.py  ──┐
      ├── list_collections.py      ├── MCP 适配层
      └── get_document_summary.py ──┘
                 ↓
          src/core/query_engine/
          src/core/response/
          src/core/trace/
                 ↓
          src/libs/ + src/ingestion/storage/
          （可插拔 Provider、向量库、BM25 索引等）

独立入口：scripts/ingest.py
    ↓
src/ingestion/pipeline.py
    ↓ loader / chunking / transform / embedding / storage

辅助平面：src/observability/
    ├── logger
    ├── dashboard
    └── evaluation
```

## 接入内部 OpsPilot 的判断（基于两个项目源码）

### 最适合的定位

本项目适合作为内部 OpsPilot Agent 的“知识检索工具”，为当前 Excel 分析 Agent 补充业务规则、指标口径、操作手册、历史分析报告、FAQ 和数据字典等长期知识。

### 已确认的连接点

- OpsPilot 的 `agent-service/apps/api-runtime/src/runtime-module.ts::createApiRuntimeModule` 在 composition root 组装 `ToolDefinition[]`；当前默认只加入 `get_workbook_info` 与 `get_sheet_profile`。
- OpsPilot 的 `packages/application/src/tools/tool-definition.ts::ToolDefinition` 是业务 Tool 的稳定契约；`wrapToolDefinitions()` 会把它适配为 Runtime 的 `AgentTool`。
- OpsPilot 的 `packages/agent-runtime` 不关心 Tool 来源，因此 RAG 可以作为普通 Tool 注入，不需要污染 Agent Loop。
- RAG 的 `query_knowledge_hub`、`list_collections`、`get_document_summary` 已经具备 Agent 所需的查询、知识域发现和文档摘要能力。

### 推荐调用关系

```text
OpsPilot API
→ ExecuteTurn
→ createAgentSession
→ Agent Runtime
→ RAG AgentTool
→ RAG MCP Server / QueryKnowledgeHubTool
→ HybridSearch + Rerank
→ 带引用的 ToolResultMessage
→ Agent 生成最终回答
```

### 重要边界

- RAG 项目当前 MCP server 使用 stdio transport；如果由 Node.js Agent Service 作为远程服务调用，需要增加 MCP client/bridge 或直接复用 RAG 核心服务契约。
- RAG ingestion 当前主要通过 `scripts/ingest.py` 摄取 PDF；Excel、业务数据库、工单和运行时事件需要单独做文档化/同步适配。
- OpsPilot 当前的系统提示词和工具组合明显围绕 spreadsheet analysis；接入 RAG 后必须补充“何时检索、必须引用、事实与推断分离、无结果时如何回答”等 Agent 指引。
- OpsPilot 的 `ToolDefinition` 具备 `recoveryPolicy` 和 Agent Runtime 的 before/after hook，可以把 RAG 查询设为 `retry_safe`；写入知识库、删除文档等副作用操作不应默认暴露给 Agent。

## 程序入口

- `pyproject.toml::project.scripts` 将 `mcp-server` 映射到 `main:main`。
- `main.py::main()` 当前只加载 `config/settings.yaml`、初始化 logger，并输出“Phase E”日志；它没有调用 MCP server。
- 实际 MCP stdio 服务入口是 `src/mcp_server/server.py::main()` → `run_stdio_server()` → `run_stdio_server_async()`。
- `run_stdio_server_async()` 创建 `mcp.server.stdio.stdio_server()`，再调用 `create_mcp_server()` 与 `server.run(...)`。

### 谁调用 `server.py`

`server.py` 的正常调用者不是仓库内的 Python 模块，而是外部 MCP Host（例如 VS Code/Copilot、Claude Desktop 或其他 MCP Agent）。Host 根据 MCP 配置以子进程方式启动 Server；Server 启动后通过 stdin/stdout 与 Host 交换 JSON-RPC 消息。当前实现使用 stdio transport，仓库测试也以 `python -m src.mcp_server.server` 子进程方式启动它。

```text
MCP Host
→ 启动 Python 子进程
→ src/mcp_server/server.py::main()
→ stdio_server()
↔ stdin/stdout 上的 MCP JSON-RPC
```

注意：`pyproject.toml` 的 `mcp-server = "main:main"` 是一个独立的打包命令映射，且当前 `main.py` 没有启动 MCP stdio 服务；实际启动命令与打包映射之间仍是待确认问题。

## MCP 与 RAG 的边界

### MCP 层

源码位置：`src/mcp_server/`

- `server.py`：负责 MCP stdio transport、日志重定向、服务启动。
- `protocol_handler.py`：负责 tool 注册、schema 暴露、tool 调用分发、错误包装。
- `tools/*.py`：把 MCP 输入参数转换成项目内部调用，并把内部结果包装成 `CallToolResult`。

### RAG / 业务核心层

源码位置：`src/core/`、`src/libs/`、`src/ingestion/`

- `src/core/query_engine/`：查询预处理、混合检索、召回与核心重排编排。
- `src/core/response/`：结果格式化、引用与多模态响应组装。
- `src/core/types.py`：跨模块共享的数据类型。
- `src/libs/`：可插拔的 loader、embedding、LLM、reranker、splitter、vector store、evaluator 等实现/工厂。
- `src/ingestion/`：把外部文档变成可检索数据，并写入存储；本轮只确认边界，未学习内部流程。

### 支撑平面

- `src/core/settings.py`：配置读取与校验。
- `src/core/trace/`：查询与摄取链路追踪。
- `src/observability/`：日志、Dashboard、评估。

## 最值得首先学习的调用链

选择查询链，因为它能最短地连接 MCP 边界与 RAG 核心，同时暂时不要求先掌握 ingestion 的全部细节：

```text
MCP Client
→ src/mcp_server/server.py::run_stdio_server_async
→ src/mcp_server/protocol_handler.py::create_mcp_server
→ _register_default_tools
→ src/mcp_server/tools/query_knowledge_hub.py::register_tool
→ ProtocolHandler.execute_tool
→ QueryKnowledgeHubTool.execute
→ QueryKnowledgeHubTool._ensure_initialized
→ src/core/query_engine/::HybridSearch 等组件
→ ResponseBuilder / TraceCollector
→ MCP CallToolResult
```

注意：上图后半段只记录“下一步应追踪的入口关系”，不代表本轮已经学习了 HybridSearch、Retriever、Reranker 或 VectorStore 的内部实现。

## 本轮理解校正：QueryKnowledgeHubTool

- `register_tool()` 注册的是 `query_knowledge_hub_handler`，不是立即创建并执行一次查询。
- `get_tool_instance()` 使用模块级 `_tool_instance` 做 lazy singleton：第一次 MCP 调用时创建 `QueryKnowledgeHubTool`，后续调用复用它。
- `QueryKnowledgeHubTool.__init__()` 只保存配置/可注入组件，并创建 `ResponseBuilder`；查询组件不会在这里全部构造。
- 第一次执行时，`execute()` 调用 `_ensure_initialized()`，再通过 `asyncio.to_thread()` 创建 Embedding、VectorStore、Dense/Sparse Retriever、QueryProcessor、HybridSearch 等组件。
- 检索结果先由 `ResponseBuilder.build()` 生成 `MCPToolResponse`，再由 handler 调用 `response.to_mcp_content()` 转成 MCP content blocks，最后包装成 `types.CallToolResult`。

## 源码事实与解释边界

- README 描述了完整能力面：Ingestion、Hybrid Search、MCP、Dashboard、Evaluation、Observability；目录结构与源码也能找到对应区域。
- 本轮仅确认模块位置、连接点和职责，不把 README 中的能力描述等同于已理解的实现细节。
- “MCP 是适配层，RAG 是业务核心层”是基于当前 import 与调用方向做出的架构解释；源码没有用一个显式接口文件声明这一边界。

## 待确认

- [ ] `pyproject.toml` 的 `mcp-server` 命令是否在当前版本中仍应指向 `main.py`，还是应改为 `src.mcp_server.server:main`？
- [ ] `QueryKnowledgeHubTool.execute()` 调用 `HybridSearch` 后，结果如何经过 rerank、response builder 和 citation 组装？
- [ ] `scripts/ingest.py` 与 MCP 查询使用的存储/索引之间，具体共享哪些数据结构？

## 复习问题

1. 为什么不能只根据 `pyproject.toml` 就断言 MCP 服务已经由 `main.py` 启动？
2. `ProtocolHandler` 与 `query_knowledge_hub.py` 的职责边界是什么？
3. 为什么下一步优先追踪 query tool，而不是立即从 PDF loader 开始？
4. `src/core/`、`src/libs/`、`src/ingestion/` 三者在当前架构中的关系是什么？
