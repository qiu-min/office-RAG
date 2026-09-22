# Vector Store 与 Chroma

## 一句话理解

ChromaDB 是一个向量数据库产品；项目中的 `ChromaStore` 是对 ChromaDB 的适配器，负责保存 Chunk 的 Dense vector、正文和 metadata，并提供向量相似度查询。

## 对应源码

- `src/libs/vector_store/base_vector_store.py::BaseVectorStore`
- `src/libs/vector_store/chroma_store.py::ChromaStore`
- `src/libs/vector_store/vector_store_factory.py::VectorStoreFactory`
- `src/ingestion/storage/vector_upserter.py::VectorUpserter`

## 在项目中的职责

ChromaStore 初始化 `chromadb.PersistentClient`，通过 `get_or_create_collection()` 获取或创建 Collection。每条记录通常包含：

```text
id       → Chunk ID
embedding→ Dense 向量
document → Chunk 文本
metadata → source_path、source_ref、chunk_index、title 等
```

它提供的核心操作包括：

- `upsert()`：写入或更新 Chunk 向量和内容
- `query()`：根据查询向量做相似度检索
- `get_by_ids()`：根据 Chunk ID 读取正文和 metadata，供 SparseRetriever 补齐 BM25 命中结果

当前项目使用 Chroma 的 cosine space，并将 Chroma 返回的 cosine distance 转换成 similarity score。

## 容易混淆

- ChromaDB：第三方向量数据库产品/ Python 包 `chromadb`
- `ChromaStore`：本项目对 ChromaDB 的封装实现
- Embedding Model：负责把文本转换为向量，不负责持久化
- BM25 Index：独立的关键词倒排索引，不是 Chroma 的向量索引
- Collection：Chroma 中的一组向量记录，实际保存的是多个 Chunk，而不是完整 Document

## 类比

可以把 Chroma 粗略类比为“支持向量相似度查询的数据库”：Collection 类似逻辑表，Chunk ID 类似主键，Embedding 类似向量列，metadata 类似可过滤属性。但它不是传统 SQL 表，也不是 Embedding 模型。

## 查看当前 Collection

在项目根目录执行下面的只读命令，可以查看 Chroma 中的 collection 及记录数：

```powershell
.\.venv\Scripts\python.exe -c "import chromadb; c=chromadb.PersistentClient(path='data/db/chroma'); [print(f'{x.name}: {x.count()}') for x in c.list_collections()]"
```

BM25 是独立索引，可查看其目录：

```powershell
Get-ChildItem .\data\db\bm25 -Directory | Select-Object -ExpandProperty Name
```

混合检索时，Chroma collection 名和 `data/db/bm25/<collection>` 中的 BM25 索引名应保持一致。`config/settings.yaml` 中的默认名不代表命令行通过 `--collection` 覆盖后的实际 collection。

当前环境检查结果：旧的 `ubuntu_ops` 有 15 条向量记录，来自 9 个 PDF；重新 OCR 摄取后的 `ubuntu_ops_filtered` 有 245 条向量记录，首条已包含真实英文正文。Collection 的 `count()` 统计的是 Chunk/向量记录数，不是 PDF 数量。

注意：`ImageCaptioner` 会以原始 `chunk.text` 为基础，仅将 `[IMAGE: id]` 替换为“占位符 + Description”，不会删除正文。若 Chroma 中的 Chunk 只有图片占位符，问题应回溯到旧数据的 PDF 解析/摄取阶段，而不是把图片描述本身当作正文丢失原因。
