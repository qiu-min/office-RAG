# MCP Tools

## 三个默认工具的职责

### `list_collections`

用于发现 Chroma 中有哪些知识库集合。输入可选参数 `include_stats`，输出集合名称、集合 metadata 和 `collection.count()`。

注意：当前实现的 count 来自 Chroma collection 的记录数量，通常对应 Chunk 数，不一定是去重后的文档数。

源码：`src/mcp_server/tools/list_collections.py::ListCollectionsTool.list_collections()`

### `query_knowledge_hub`

用于执行核心 RAG 查询：QueryProcessor → Dense/BM25 → RRF → 可选 Rerank，并返回带 Citation、可选图片的 MCP 内容。

源码：`src/mcp_server/tools/query_knowledge_hub.py::QueryKnowledgeHubTool.execute()`

### `get_document_summary`

用于根据 `doc_id` 和可选 `collection` 查找文档的所有 Chunk，并返回标题、摘要、标签、源路径、Chunk 数和额外 metadata。

查找优先使用 `source_ref == doc_id`，失败后按 Chunk ID 做部分匹配。摘要优先使用 metadata 中已有的 `summary`，否则截取首个 Chunk 的文本预览；它不是重新调用 LLM 生成摘要。

源码：`src/mcp_server/tools/get_document_summary.py::GetDocumentSummaryTool.get_document_summary()`

## Agent 典型调用顺序

```text
list_collections
→ 确定可用知识集合
→ query_knowledge_hub
→ 获取相关 Chunk 与 Citation
→ get_document_summary
→ 需要了解某个来源文档时查看标题、摘要和元数据
```

`get_document_summary` 也可以直接使用已知的 `doc_id`，不要求一定先调用 `list_collections`。

## 复习问题

1. 为什么 `list_collections` 不能直接替代 `query_knowledge_hub`？
2. `get_document_summary` 的摘要在什么情况下只是首个 Chunk 的文本预览？
3. 为什么 `get_document_summary` 需要先找到一个文档的所有 Chunk？
