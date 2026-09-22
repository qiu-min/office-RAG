# Excel 插件业务垂直化与个人职责边界

## 一句话理解

把通用 RAG 底座包装成 Excel 插件产品中的“知识检索子系统”：不代替整个 Excel 插件，只负责把用户手册、FAQ、版本说明和内部技术文档变成可被 AI 助手检索的知识服务。

## 个人职责边界

简历中应明确写“负责 RAG 子系统”或“负责知识检索模块”，不要写成独立负责整个 Excel 插件产品。可归入个人职责的内容包括：

- 文档摄取、清洗、分块、metadata 增强和索引构建。
- Dense + BM25 混合召回、RRF 融合、可选 Rerank。
- MCP Tool 设计与 RAG 能力封装，返回带引用的检索结果。
- 检索链路 Trace、评估集和效果回归。

不应默认归入个人职责的内容包括：Excel 函数实现、工作簿读写、插件 UI、Office 兼容层、账号系统和整个产品发布流程，除非确实参与过。

## 业务场景

Excel 插件包含多个数据处理功能，每个功能都有使用说明、参数解释、示例、限制和故障排查文档；同时还存在面向开发者的 API、架构、编码规范、发布说明和内部技术文档。用户和开发者都需要检索，但可见范围不同。

## 与当前仓库的映射

```text
用户 / 开发者 / IDE 或插件侧边栏
    ↓ MCP Client 或 MCP Bridge
src/mcp_server/server.py
    ↓
query_knowledge_hub / list_collections / get_document_summary
    ↓
QueryProcessor
    ↓
Dense Retriever + BM25 Retriever
    ↓
RRF Fusion → 可选 Rerank → Citation
    ↓
带来源的知识结果
```

实际查询核心已经存在于 `src/core/query_engine/`；MCP 适配位于 `src/mcp_server/`；摄取、分块、增强和双路索引位于 `src/ingestion/`。

## 推荐知识域

- `excel_user_manual`：面向最终用户的功能说明、操作步骤、参数和示例。
- `excel_faq`：常见问题、错误码、兼容性和排障方案。
- `excel_developer_docs`：API、模块设计、扩展方式和编码规范。
- `excel_release_notes`：版本变化、弃用项和迁移指南。

集合只是第一层隔离，还应在 metadata 中记录 `audience`、`product`、`module`、`function_name`、`version`、`platform`、`visibility`、`updated_at` 和 `source_url`。

## 最小两域方案：用户文档与内部文档

可以从两个知识域起步：`excel_public_docs` 存放用户手册、FAQ 和公开版本说明；`excel_internal_docs` 存放开发手册、架构和内部排障资料。源码中的准确术语是同一 Chroma 存储中的两个 collection，以及各自独立的 BM25 倒排索引，不必称为“两份向量数据库”。

工程方案：现有后端验证登录态，依据已认证身份计算允许访问的 collection；普通用户只查 `excel_public_docs`，员工可查公开与内部知识。若员工同时查两域，当前 `query_knowledge_hub` 一次只接收一个 `collection`，需要调用两次并在上层整合，或扩展跨 collection 检索。`list_collections` 与 `get_document_summary` 同样必须受权限约束。

源码边界：`src/mcp_server/tools/query_knowledge_hub.py::QueryKnowledgeHubTool.execute` 接收调用方传入的 `collection` 并据此初始化 Chroma collection 与 BM25 index；`src/mcp_server/tools/list_collections.py::ListCollectionsTool.list_collections` 会列出 Chroma 中的集合。当前 MCP Tool 没有身份验证或 allowed collection 校验。因此“后端识别身份并路由知识库”是可行的集成设计，不是仓库已实现的能力；若不允许外部调用绕过后端，MCP 服务应只由可信后端访问。

## 为什么混合检索适合 Excel 插件

- Dense 检索适合“怎么把多个工作表合并成一个结果”这类自然语言表达。
- BM25 适合精确匹配函数名、参数名、错误码、版本号和 API 类名。
- RRF 不要求 Dense 与 BM25 的原始分数可直接比较，按排名融合两路证据。
- Rerank 可在候选结果上进一步区分同一功能的不同版本或不同使用场景。

## 必须补齐的企业能力

1. **权限过滤**：当前项目有 collection 和 metadata filter，但没有完整的用户身份、角色和 ACL 体系。最终用户不能仅靠传入 collection 名称访问内部开发文档；服务端必须根据用户身份计算 allowed scopes，并在召回阶段执行过滤。
2. **文档格式**：当前 CLI 主要摄取 PDF。Excel 插件文档若来自 Markdown、HTML、Word、Wiki 或代码仓库，需要新增 Loader 或同步适配，不应直接声称当前已支持所有格式。
3. **版本感知**：检索需要结合插件版本、Office 平台和发布日期，优先返回适用版本，并标记过时文档。
4. **服务接入**：当前 MCP Server 使用 stdio。若插件或远程后端要调用，需要增加 MCP Client/Bridge 或 HTTP/远程 transport；不能把当前 stdio 进程直接描述成远程 SaaS API。
5. **质量评估**：按用户问题、错误码、函数名、开发者 API 四类建立 golden set，分别评估 Hit@K、MRR、引用正确性和权限泄露情况。

## 包装时的真实性边界

如果该系统只是基于前公司真实业务场景完成的个人项目或 PoC，应表述为“基于 Excel 插件业务场景设计并实现”，不要写成“前公司已上线”。只有确实完成公司内部接入、数据规模和效果验证，才能写“上线”“覆盖多少用户”或具体提升比例。

## 待确认

- [ ] 前公司文档的真实来源和格式：PDF、Word、Markdown、Wiki、代码仓库还是数据库？
- [ ] 用户端是否已有 Agent、侧边栏或后端服务可以作为 MCP Client？
- [ ] 用户文档与内部技术文档当前是否存在登录身份、部门或角色权限？
- [ ] 项目定位是个人 PoC、公司内部原型，还是实际生产系统？
