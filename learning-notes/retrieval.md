# Retrieval 查询链路

## 一句话理解

`query_knowledge_hub` 负责参数校验、组件初始化和结果包装；`HybridSearch` 负责查询预处理、Dense/BM25 双路召回、失败降级和 RRF 融合；之后由 `CoreReranker` 可选重排，最后由 `ResponseBuilder` 生成带引用的 MCP 响应。

## 对应源码

- `src/mcp_server/tools/query_knowledge_hub.py`
  - `QueryKnowledgeHubTool.execute()`
  - `_ensure_initialized()`
  - `_perform_search()`
  - `_apply_rerank()`
  - `query_knowledge_hub_handler()`
- `src/core/query_engine/hybrid_search.py`
  - `HybridSearch.search()`
  - `_run_retrievals()`
  - `_run_dense_retrieval()`
  - `_run_sparse_retrieval()`
  - `_fuse_results()`
- `src/core/query_engine/query_processor.py::QueryProcessor.process()`
- `src/core/query_engine/dense_retriever.py::DenseRetriever.retrieve()`
- `src/core/query_engine/sparse_retriever.py::SparseRetriever.retrieve()`
- `src/core/query_engine/fusion.py::RRFFusion.fuse()`
- `src/core/query_engine/reranker.py::CoreReranker.rerank()`
- `src/core/response/response_builder.py::ResponseBuilder.build()`

## 实际调用链

```text
query_knowledge_hub_handler
↓
QueryKnowledgeHubTool.execute
↓
_ensure_initialized
  ├─ EmbeddingFactory
  ├─ VectorStoreFactory → DenseRetriever
  ├─ BM25Indexer → SparseRetriever
  ├─ QueryProcessor
  └─ create_hybrid_search → HybridSearch + RRFFusion
↓
_perform_search
↓
HybridSearch.search
  ├─ QueryProcessor.process
  ├─ DenseRetriever.retrieve
  │    ├─ embedding_client.embed([query])
  │    └─ vector_store.query(vector)
  ├─ SparseRetriever.retrieve
  │    ├─ BM25Indexer.query(keywords)
  │    └─ vector_store.get_by_ids(chunk_ids)
  ├─ RRFFusion.fuse
  └─ 单路失败时使用另一条召回结果
↓
_apply_rerank（可选）
↓
ResponseBuilder.build
  ├─ CitationGenerator
  └─ MultimodalAssembler
↓
MCPToolResponse.to_mcp_content
↓
types.CallToolResult
```

## 已确认的数据变化

1. `execute()` 校验查询，并应用 `top_k` 默认值 5、上限 20，以及默认集合 `default`。
2. 如果启用 Rerank，Tool 会把传入的 `top_k` 放大为 `top_k * 2`，让 Hybrid Search 的融合结果提供更大的候选池；这不是指 Dense 和 Sparse 两路都各自取这个值。两路实际召回数量由 `dense_top_k` 和 `sparse_top_k` 独立配置。
3. `QueryProcessor` 输出 `ProcessedQuery`，包含原始查询、关键词和过滤条件。
4. Dense 路径使用原始查询生成向量；Sparse 路径使用 QueryProcessor 提取出的关键词查询 BM25。
5. Dense 与 Sparse 默认可通过 `ThreadPoolExecutor` 并行执行。
6. Dense 和 Sparse 都成功时，`RRFFusion` 按排名计算 `1 / (k + rank)`，默认 `k=60`，并将 RRF 分数写入新的 `RetrievalResult`。
7. Rerank 失败时，`CoreReranker` 返回原始召回顺序，保证查询仍能返回结果。
8. `ResponseBuilder` 将结果转换成 Markdown、结构化 Citation，并按需追加 ImageContent。

## Query 如何生成 Sparse 关键词

用户输入的原始 `query` 会先进入 `QueryProcessor.process()`：

```text
原始 query
→ 清理空白
→ 提取 collection/source/type/tag 等过滤语法
→ 删除过滤语法
→ jieba 中文分词
→ 去标点、停用词、过短词
→ 去重并限制关键词数量
→ ProcessedQuery.keywords
```

随后：

- Dense 使用 `ProcessedQuery.original_query`。
- Sparse 使用 `ProcessedQuery.keywords` 调用 BM25。
- 当前实现没有调用 LLM 做同义词扩展；`expanded_terms` 字段只是预留能力。
- 如果关键词为空，HybridSearch 会跳过 Sparse 查询并返回空的 Sparse 结果，而不是把它视为异常。

## Dense 与 Sparse 是否共享存储

两路共享同一个 Chroma `VectorStore` 实例来保存/读取 Chunk 的文本和 metadata，但真正的检索入口不同：

```text
Dense:
query → Embedding → Chroma vector_store.query(vector)

Sparse:
keywords → 独立 BM25 index → chunk_ids
         → 同一个 Chroma vector_store.get_by_ids(chunk_ids)
```

因此，Sparse 不是用关键词查询 Chroma 的向量相似度；它先查 `data/db/bm25/{collection}` 下的 BM25 索引，再用 Chunk ID 从 Chroma 补齐正文和 metadata。`QueryKnowledgeHubTool._ensure_initialized()` 将同一个 `vector_store` 同时注入 DenseRetriever 和 SparseRetriever。

## 两路检索的统一结果契约

Dense 和 Sparse 的底层结果形式不同，但两者最终都返回 `List[RetrievalResult]`：

```python
RetrievalResult(
    chunk_id: str,
    score: float,
    text: str,
    metadata: dict,
)
```

- Dense 原始结果来自 VectorStore，包含 `id`、`score`、`text`、`metadata`，再映射为 `chunk_id`。
- Sparse 的 BM25 原始结果只有 `chunk_id` 和 `score`；随后通过 `VectorStore.get_by_ids()` 补齐 `text` 和 `metadata`。
- 统一结构后，`RRFFusion`、`CoreReranker` 和 `ResponseBuilder` 不需要区分结果来自 Dense 还是 Sparse。
- 字段统一不代表分数统一：Dense 当前 Chroma 实现把 cosine distance 转成 `[0, 1]` similarity；Sparse 使用原始 BM25 score。`RetrievalResult` 的文档字符串称 score 已 normalized，但当前 Sparse 路径实际没有做归一化，因此 RRF 才不直接使用这些原始分数。

## RRF 如何选出 fusion_top_k

`RRFFusion.fuse()` 的步骤是：

1. 遍历 Dense 和 Sparse 两份已按相关性降序排列的列表。
2. 对每个结果按它在当前列表中的 1-based rank 计算贡献：`1 / (k + rank)`，默认 `k=60`。
3. 相同 `chunk_id` 在多份列表中出现时，累加多次贡献；只出现在一份列表中的结果也保留。
4. 为每个唯一 Chunk 创建新的 `RetrievalResult`，其中 `score` 改为 RRF 分数，文本和 metadata 保留首次出现的版本。
5. 按 `score` 降序排序，分数相同时按 `chunk_id` 稳定排序，最后执行 `fused_results[:top_k]`。

因此 `fusion_top_k` 是排序后的数量上限，不是原始分数阈值。例如 Dense 中排名 3、Sparse 中排名 1 的 Chunk，其 RRF 分数是 `1/(60+3) + 1/(60+1)`；它可能超过只在某一路排名 1 的 Chunk，因为它同时获得了两路证据。

## CLI 查询与 MCP 查询的关系

两者共享核心组件和主要检索链路：

```text
QueryProcessor
→ DenseRetriever + SparseRetriever
→ HybridSearch / RRF
→ CoreReranker
```

差异在入口和外围职责：

- `scripts/query.py`：加载配置、手动组装组件，直接调用 `HybridSearch.search()`，可用 `--verbose` 打印 Dense/Sparse/Fusion 中间结果，再在脚本中调用 Reranker。
- `src/mcp_server/tools/query_knowledge_hub.py`：作为 MCP Tool 入口，负责参数校验、lazy 初始化、`asyncio.to_thread()` 调用阻塞检索、ResponseBuilder 引用/图片组装和 `CallToolResult` 包装。
- 两者的默认参数也不同：CLI 默认 `top_k=10`，MCP Tool 默认 `top_k=5`。

因此可以说：`scripts/query.py` 是核心 RAG 查询的 CLI 调试壳，`query_knowledge_hub` 是面向 Agent/MCP Client 的协议适配壳。

## 为什么这条链路值得理解

调用关系本身不难；真正需要掌握的是每一层的职责和数据契约：

- Dense 分数、BM25 分数和 RRF 分数不是同一种分数，不能直接比较。
- BM25 依赖关键词，Dense 依赖原始自然语言查询。
- RRF 融合只使用排名，不要求两路分数归一化。
- Rerank 是融合后的二次排序，不负责全库召回。
- MCP Tool 负责协议适配，核心检索逻辑仍在 `src/core/query_engine/`。

## 我的理解

可以把它理解成：先用两种不同的“找答案方式”各自捞一批候选，再用 RRF 合并，Reranker 负责精排，ResponseBuilder 负责把结果包装成客服 Agent 能读、用户能信的带引用答案。

## 容易混淆

- `top_k`：Tool 最终返回数量；开启 Rerank 时，召回阶段会先取两倍候选。
- 当前 `config/settings.yaml` 中 `dense_top_k=20`、`sparse_top_k=20`、`fusion_top_k=10`、`rerank.top_k=5`；默认 Tool 查询 `top_k=5` 时，Hybrid Search 的融合候选上限为 10，最终 Rerank 返回 5 条。
- `dense_top_k` 和 `sparse_top_k` 当前默认都为 20，但它们是独立配置，不要求一致；`RRFFusion` 可以融合长度不同的两份排名列表。
- `RetrievalResult.score`：在不同阶段含义不同，融合后是 RRF 分数，重排后可能是 rerank 分数。
- `collection`：查询工具用它选择 VectorStore collection 和 BM25 索引，不等同于普通 metadata filter。
- RAG 检索结果：这里只是证据片段；最终自然语言回答还需要上层 LLM 或 Agent 生成。

## 待确认

- [x] 已确认 `settings.yaml` 中各路 `top_k` 和 rerank 配置；`parallel_retrieval` 当前不是 YAML 字段，而是在 `HybridSearch._extract_config()` 中固定为 `True`。
- [ ] 智能客服 Agent 如何把 `CallToolResult` 中的 Citation 约束到最终回答中。

## 复习问题

1. 为什么 Dense 和 BM25 的原始分数不能直接相加？
2. 为什么开启 Rerank 时要先召回两倍数量？
3. 如果 Dense 失败但 BM25 成功，HybridSearch 如何返回结果？
4. RRF 融合后 `RetrievalResult.score` 的含义发生了什么变化？
5. `ResponseBuilder` 为什么不负责生成最终客服回答？
