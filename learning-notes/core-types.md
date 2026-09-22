# Core Data Types

## 一句话理解

`src/core/types.py` 定义的是跨模块的数据契约：摄取侧把 `Document` 逐步变成 `Chunk`，存储侧可进一步表示为 `ChunkRecord`；查询侧把用户问题表示为 `ProcessedQuery`，把召回结果统一表示为 `RetrievalResult`。

## 结构关系

```text
原始文件
  ↓ Loader
Document（完整文档）
  ↓ DocumentChunker / Splitter
List[Chunk]（可检索片段）
  ↓ Embedding + metadata enrichment
ChunkRecord（带 dense/sparse vector 的存储模型）
  ↓ Dense/BM25 retrieval
RetrievalResult（查询结果视图）

用户 Query
  ↓ QueryProcessor
ProcessedQuery（关键词 + filters）
  ↓ DenseRetriever / SparseRetriever
RetrievalResult
```

## 源码事实

- `Document`、`Chunk`、`ChunkRecord`、`ProcessedQuery`、`RetrievalResult` 都是独立的 `dataclass`，没有 Python 继承关系。
- `Document` 表示完整文档；必须在 `metadata` 中包含 `source_path`。
- `Chunk` 表示文档切分后的片段，包含 `chunk_index`、`source_ref`、offset 等追踪信息；它是 Transform、Embedding 的输入。
- `DocumentChunker` 实际把 Splitter 返回的 `str` 转换为 `Chunk`，同时复制文档 metadata、生成 Chunk ID 和 `metadata['source_ref']`。
- `ChunkRecord` 在语义上是“Chunk + dense/sparse vector + 增强 metadata”，通过 `from_chunk()` 从 `Chunk` 创建；但它不是 `Chunk` 的子类。
- `ProcessedQuery` 与文档对象无直接包含关系，表示查询预处理结果：原始 query、关键词、过滤条件和预留的扩展词。
- `RetrievalResult` 是 Dense/Sparse/Hybrid Search 共用的结果契约，包含 `chunk_id`、相关性 `score`、正文和 metadata。
- 所有五个 dataclass 都支持 `to_dict()` / `from_dict()`；`Document`、`Chunk`、`ChunkRecord` 还会校验 `source_path`，`RetrievalResult` 会校验非空 `chunk_id` 和数值型 `score`。

## 当前实现中的重要边界

`ChunkRecord` 是类型层面定义的存储模型，但当前 `src/ingestion/storage/vector_upserter.py::VectorUpserter.upsert()` 接收 `Chunk + dense_vectors` 后直接构造 `dict` 记录写入 VectorStore；当前 Pipeline 没有实际调用 `ChunkRecord.from_chunk()`。因此它更像稳定的数据契约/预留模型，而不是当前写入链路中的必经对象。

## 类型别名

- `Metadata = Dict[str, Any]`：metadata 的类型提示，不会产生新运行时类型。
- `Vector = List[float]`：Dense vector 的类型提示。
- `SparseVector = Dict[str, float]`：稀疏向量的类型提示；当前 BM25 摄取实现实际使用的是 term statistics 结构，而不是直接把 `SparseVector` 作为主索引对象。

## 容易混淆

- `Document` vs `Chunk`：前者是完整源文档，后者是检索粒度；一个 Document 通常对应多个 Chunk。
- `Chunk` vs `ChunkRecord`：前者是处理中的文本片段，后者是准备存储、额外带向量的表示；代码中不是继承关系。
- `ChunkRecord` vs `RetrievalResult`：前者面向摄取/存储，后者面向查询/排序/响应；后者带 score，前者带 vector。
- `ProcessedQuery` vs `RetrievalResult`：前者描述“用户想查什么”，后者描述“系统找到了什么”。

## 对应源码

- `src/core/types.py::Document`
- `src/core/types.py::Chunk`
- `src/core/types.py::ChunkRecord`
- `src/core/types.py::ProcessedQuery`
- `src/core/types.py::RetrievalResult`
- `src/ingestion/chunking/document_chunker.py::DocumentChunker.split_document`
- `src/core/query_engine/query_processor.py::QueryProcessor.process`
- `src/core/query_engine/dense_retriever.py::DenseRetriever._transform_results`
- `src/core/query_engine/sparse_retriever.py::SparseRetriever._merge_results`

## 复习问题

1. 为什么 `DocumentChunker` 不能只返回 `List[str]`，还要创建 `Chunk`？
2. `ChunkRecord` 和 `RetrievalResult` 分别服务于哪一侧？
3. 为什么 Dense 和 Sparse 检索最终都要转换成 `RetrievalResult`？
4. `ProcessedQuery` 为什么不直接包含召回结果？
