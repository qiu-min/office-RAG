# Ingestion 与 BM25 索引

## 一句话理解

同一批处理后的 Chunk 会分别产生两种检索数据：Dense 向量写入 Chroma，Sparse 词项统计构建独立 BM25 倒排索引；BM25 不是从向量数据库反推出来的。当前 `BatchProcessor` 的代码是在每个 batch 内先调用 Dense，再调用 Sparse，并不是并发执行。

## 原始文档如何进入系统

当前 CLI 入口主要支持 PDF：

```text
python scripts/ingest.py --path documents/ --collection technical_docs
→ discover_files() 找到 PDF
→ IngestionPipeline.run(file_path)
→ PdfLoader.load()：PDF → Document（图片型 PDF 先 OCR，再交给 MarkItDown）
→ DocumentChunker.split_document()：Document → List[Chunk]
→ Transform：ChunkRefiner / MetadataEnricher / ImageCaptioner
→ BatchProcessor：Dense vector + Sparse term stats
→ VectorUpserter：写入 Chroma collection
→ BM25Indexer：写入独立 BM25 index
```

因此没有一个单独的“文档转 Collection API”：

- `PdfLoader.load()` 负责把文件变成 `Document`。
- `DocumentChunker.split_document()` 负责把 `Document` 变成多个 `Chunk`。
- `VectorStoreFactory.create(..., collection_name=...)` 创建或获取命名的 Chroma Collection。
- `VectorUpserter.upsert()` 将 Chunk 和 Dense vector 写入该 Collection。

当前 `scripts/ingest.py::discover_files()` 默认只发现 `.pdf` 文件；如果要接入 Markdown、HTML、Word 或数据库，需要增加对应 Loader/同步适配，而不是直接把文件路径传给 Chroma。

## PdfLoader 如何实现 BaseLoader

`BaseLoader` 只规定一个核心契约：`load(file_path) -> Document`。`PdfLoader` 在这个契约上实现了 PDF 校验、Markdown 文本提取、图片落盘和统一 `Document` 构造。

实际调用链：

```text
IngestionPipeline.run()
→ self.loader.load(file_path)
→ BaseLoader._validate_file()
→ SHA256 计算
→ 检测 PDF 是否有文字层
→ 图片型 PDF：Tesseract OCR 生成临时可搜索 PDF
→ MarkItDown.convert()
→ 可选的 PyMuPDF 图片提取
→ Document(id, text, metadata)
```

实现细节：

1. 构造 `PdfLoader` 时必须有 `MarkItDown`；缺少该依赖会在初始化时抛出 `ImportError`。
2. `load()` 先调用 `_validate_file()`，确认路径存在且是文件，再检查扩展名是否为 `.pdf`。
3. 对 PDF 二进制内容计算 SHA256，生成 `doc_id = doc_<hash前16位>`；该 hash 也用于图片目录。
4. 先用 `pdfplumber` 检测已有文字层；如果为空且启用 OCR，则用 Tesseract 将每页转成带文字层的临时 PDF，再调用 `MarkItDown.convert()`，最终使用 `result.text_content` 作为标准化 Markdown 文本。
5. 从前 20 行 Markdown 标题中提取 `# ` 标题；没有标题时使用前 10 行的第一个非空行作为 title。
6. 默认启用图片提取：PyMuPDF 遍历每页图片，保存到 `data/images/<doc_hash>/`，在正文末尾追加 `[IMAGE: <image_id>]` 占位符，并把图片路径、页码、offset、尺寸等写入 `metadata['images']`。
7. 图片提取失败不会阻断文本加载；会记录 warning 并退化为 text-only Document。PyMuPDF 未安装时也会跳过图片提取。
8. OCR 依赖 Python 包 `pytesseract` 和系统中的 Tesseract 可执行程序；可通过 `TESSERACT_CMD` 指定可执行文件路径。OCR 生成的临时 PDF不会覆盖原始 PDF。

最终返回的 `Document` 至少包含：

```python
Document(
    id="doc_<hash>",
    text="Markdown text...",
    metadata={
        "source_path": "...pdf",
        "doc_type": "pdf",
        "doc_hash": "<sha256>",
        "title": "...",       # 如果能提取
        "images": [...],       # 如果提取到图片
    },
)
```

这里 Loader 只负责“PDF → 统一 Document”，不负责切分；后续由 `DocumentChunker` 把 `Document` 转成 `Chunk`。

对应源码：

- `src/libs/loader/base_loader.py::BaseLoader.load`
- `src/libs/loader/base_loader.py::BaseLoader._validate_file`
- `src/libs/loader/pdf_loader.py::PdfLoader.load`
- `src/libs/loader/pdf_loader.py::PdfLoader._extract_and_process_images`
- `src/ingestion/pipeline.py::IngestionPipeline.run`

## 当前 Splitter 的实现位置

- 抽象接口：`src/libs/splitter/base_splitter.py::BaseSplitter`
- 默认具体实现：`src/libs/splitter/recursive_splitter.py::RecursiveSplitter`
- 工厂注册与创建：`src/libs/splitter/splitter_factory.py::SplitterFactory`
- 业务适配：`src/ingestion/chunking/document_chunker.py::DocumentChunker`

当前 `config/settings.yaml` 配置为 `ingestion.splitter: "recursive"`。因此 Pipeline 实际路径是：

```text
IngestionPipeline
→ DocumentChunker.split_document()
→ SplitterFactory.create()
→ RecursiveSplitter.split_text()
→ List[str]
→ DocumentChunker 创建 List[Chunk]
```

`SplitterFactory` 本身不负责切分文本，它只负责根据配置选择并实例化 `BaseSplitter` 的具体实现。这样 `DocumentChunker` 依赖的是抽象接口，而不是直接依赖 `RecursiveSplitter`；未来增加 `SemanticSplitter` 或 `FixedLengthSplitter` 时，可以通过注册 provider 和修改配置切换，减少业务层改动。当前项目只有 `recursive` 默认实现，因此工厂的主要价值是解耦和扩展准备，而不是当前已经存在多个可选实现。

## RecursiveSplitter 如何切分

`src/libs/splitter/recursive_splitter.py::RecursiveSplitter` 实际包装 LangChain 的 `RecursiveCharacterTextSplitter`，使用 `length_function=len` 按字符数控制长度。

默认分隔符按优先级排列：

```text
\n\n → \n → ". " → "! " → "? " → "; " → ", " → 空格 → 空字符串
段落     行     句子边界                                  字符级兜底
```

算法不是直接每 N 个字符硬切，而是先尝试较粗粒度的边界：

1. 优先按段落、换行等较自然的边界拆分。
2. 如果某个片段仍超过 `chunk_size`，递归使用更细的分隔符继续拆分。
3. 使用 `_merge_splits` 将相邻小片段合并，尽量让每个 Chunk 不超过 `chunk_size`。
4. 相邻 Chunk 保留 `chunk_overlap` 的前后文；由于必须尊重分隔符，重叠量是按片段边界近似实现的，不一定恰好等于指定字符数。
5. 连空格也无法解决超长内容时，最后使用空字符串按字符级切分。

当前配置是 `chunk_size=1000`、`chunk_overlap=200`。因此它是“最多约 1000 字符、相邻块尽量保留约 200 字符上下文”的递归字符切分器，而不是真正理解标题层级或代码 AST 的 Markdown 解析器。

直观理解（没有自然分隔符时）：如果原文长度约 2500 字符，可以近似看成：

```text
Chunk 1: 字符 1    - 1000
Chunk 2: 字符 801  - 1800   ← 与 Chunk 1 重叠 801-1000，共 200 字符
Chunk 3: 字符 1601 - 2500   ← 与 Chunk 2 重叠 1601-1800，共 200 字符
```

实际 LangChain 会优先在段落、换行、句子等边界切分，所以最终边界可能略有变化；`200` 是目标重叠上下文，不保证每次严格等于 200 个字符。

“分隔符合不合适”的判断不是语义判断，而是长度判断：当前文本中找到某个分隔符后，先按它拆成 pieces；如果某个 piece 已经超过 `chunk_size`，就递归使用后面的更细分隔符；如果多个 pieces 合并后会超过 `chunk_size`，就先结束当前 Chunk，再开始下一个 Chunk。`chunk_overlap` 只在合并相邻 pieces 时用于保留前一个 Chunk 的尾部。

`chunk_size` 不是要求每个 Chunk 恰好等于该长度，也不是绝对不可超过的数据库约束：短文本、自然段落边界会产生更短的 Chunk；默认分隔符包含空字符串，普通连续文本通常会继续细分到字符级，但不可再拆的片段、保留分隔符造成的边界情况或自定义 separators 可能使结果略超出该长度。因此它应理解为“目标上限/控制参数”，不是严格 invariant。

## 第一阶段：文件完整性检查

第一阶段由 `IngestionPipeline.run()` 编排，具体实现由 `SQLiteIntegrityChecker` 提供：

```text
scripts/ingest.py
→ IngestionPipeline.run(file_path)
→ compute_sha256(file_path)
→ should_skip(file_hash)
   ├─ 已有 success 且未 force：直接跳过后续摄取
   └─ 未处理/曾失败/force=True：继续进入 Document Loading
```

- SHA256 在 `src/libs/loader/file_integrity.py::SQLiteIntegrityChecker.compute_sha256()` 中以 64KB 分块读取，避免一次性加载大文件。
- SHA256 是密码学哈希/摘要，不是可逆加密；当前输入是文件二进制内容，不是文件路径。
- 历史记录持久化在 `data/db/ingestion_history.db` 的 `ingestion_history` 表中。
- 只有 `status='success'` 的文件会被跳过；失败记录允许下次重试。
- 全部阶段成功后记录 `mark_success()`；处理失败时记录失败状态，便于后续重试。
- `--force` 会跳过“已成功处理则跳过”的判断，强制重新摄取。

当前 `should_skip()` 只按 `file_hash` 查询，不按文件路径或 collection 参与判断。因此相同内容换了路径仍可能被识别为已处理；跨 Collection 的相同文件是否需要分别摄取，是当前实现需要注意的边界。

对应源码：

- `scripts/ingest.py::main()`
- `src/ingestion/pipeline.py::IngestionPipeline.run()`
- `src/libs/loader/file_integrity.py::SQLiteIntegrityChecker`

## 对应源码

- `src/ingestion/pipeline.py`
  - `Pipeline` 初始化 `BatchProcessor`、`VectorUpserter`、`BM25Indexer`
  - Stage 5 Encoding
  - Stage 6 Storage
- `src/ingestion/embedding/batch_processor.py::BatchProcessor.process()`
- `src/ingestion/embedding/sparse_encoder.py::SparseEncoder.encode()`
- `src/ingestion/storage/bm25_indexer.py::BM25Indexer.build()`
- `src/ingestion/storage/bm25_indexer.py::BM25Indexer.add_documents()`
- `src/ingestion/storage/vector_upserter.py::VectorUpserter.upsert()`

## 实际构建链路

```text
Processed Chunks
        │
        ├─ DenseEncoder → dense_vectors → Chroma
        │
        └─ SparseEncoder → term_stats → BM25Indexer → data/db/bm25/{collection}_bm25.json
```

具体过程：

1. `BatchProcessor` 对同一批 Chunk 分别调用 `DenseEncoder` 和 `SparseEncoder`。
2. `SparseEncoder` 使用 `jieba` 分词，并为每个 Chunk 统计 `term_frequencies`、`doc_length` 和 `unique_terms`。
3. `VectorUpserter` 先把 Chunk 文本、metadata 和 Dense vector 写入 Chroma，并返回实际 vector IDs。
4. Pipeline 将返回的 vector ID 写回 sparse stats，保证 BM25 的 `chunk_id` 与 Chroma ID 对齐。
5. `BM25Indexer.add_documents()` 根据词频、文档频率、平均文档长度计算 IDF，并构建 term → postings 的倒排结构。
6. BM25 索引持久化到 `data/db/bm25/{collection}_bm25.json`。

`BM25Indexer.add_documents()` 的参数名不是指添加一个原始 `Document`：`term_stats` 是当前原始文档切出的所有 Chunk 的 Sparse 统计列表。它会加载已有索引（若存在），尝试删除该文档的旧 postings，把已有统计与当前 `term_stats` 合并，重新计算全局 DF/IDF 和平均文档长度，最后保存 BM25 索引。`doc_id` 的设计意图是支持同一文档重新摄取时先删旧数据再加新数据。

当前实现有一个需要留意的 ID 对齐问题：调用时传入的是 `document.id`，但前面已把 `sparse_stats['chunk_id']` 改成了 `VectorUpserter` 根据 `source_path` 哈希生成的 storage ID；`remove_document()` 又按 `chunk_id.startswith(document.id)` 删除。因此如果这两种 ID 没有相同前缀，重新摄取时旧 BM25 postings 可能不会被删除。

Storage 的 6c 是图片索引注册：`PdfLoader` 之前已经把图片文件保存到磁盘，6c 从 `document.metadata['images']` 读取图片 ID、路径和页码，只要文件存在就调用 `ImageStorage.register_image()` 写入 SQLite `image_index`。它不会再次复制图片，也不会生成 caption；后续组件可以通过 `image_id` 查到文件路径，用于图片展示或多模态响应。

### `BM25Indexer.build()` 如何从 sparse_stats 建索引

给定每个 Chunk 的 `term_frequencies`、`doc_length` 和 `chunk_id` 后，`build()` 会：

1. 令 `N = len(term_stats)`，并计算所有 Chunk 的平均词数 `avg_doc_length`。
2. 遍历每个 Chunk 的 `term_frequencies.keys()`，统计每个词出现在多少个 Chunk 中，得到文档频率 `df`；同一个词在同一个 Chunk 中出现多次只算一个 Chunk。
3. 对每个词计算 `idf = log((N - df + 0.5) / (df + 0.5))`。
4. 再遍历所有 Chunk，为当前词创建 posting：`{chunk_id, tf, doc_length}`，其中 `tf` 来自该 Chunk 的词频；没有该词的 Chunk 不加入 posting。
5. 保存 `term -> {idf, df, postings}` 的倒排索引，并写入 `num_docs`、`avg_doc_length`、`total_terms` 等元数据。

例如两个 Chunk 的统计为：`A: {python: 2, api: 1}, doc_length=3`，`B: {python: 1, model: 1}, doc_length=2`，索引会包含 `python` 的 postings `[{'chunk_id': 'A', 'tf': 2, 'doc_length': 3}, {'chunk_id': 'B', 'tf': 1, 'doc_length': 2}]`，而 `api` 只包含 A，`model` 只包含 B。查询时先按词找到 postings，再使用 tf、idf、doc_length 和平均长度计算 BM25 分数。

### 当前代码中的 BM25 分数公式

`BM25Indexer.query()` 会对查询中的每个词遍历 postings，调用 `_calculate_bm25_score()`，然后把同一个 Chunk 对多个查询词的贡献相加：

```text
term_score = IDF × [TF × (k1 + 1)]
             -----------------------------
             TF + k1 × (1 - b + b × (doc_length / avg_doc_length))
total_score(chunk) = Σ term_score(query_term, chunk)
```

当前默认参数是 `k1=1.5`、`b=0.75`。`k1` 控制词频增加的收益递减；`b` 控制长 Chunk 的长度归一化惩罚。项目当前的 IDF 公式是 `log((N - df + 0.5) / (df + 0.5))`。例如 `N=10`、`df=2` 时 `IDF≈1.224`；某 Chunk 中该词 `TF=2`、长度为 3、平均长度为 5，则分母为 `2 + 1.5 × (0.25 + 0.75 × 3/5)=3.05`，该词贡献约为 `1.224 × 5/3.05=2.01`。查询包含多个词时，各词贡献相加；查询词不在索引中则跳过。

### Chunk.id 与 VectorUpserter 生成的 storage ID

`DocumentChunker` 创建 `Chunk` 时已经生成了领域层的 `Chunk.id`，格式类似 `{document.id}_{index:04d}_{content_hash}`。但 `VectorUpserter` 没有直接使用它，而是根据 `source_path` 的哈希、`chunk_index` 和当前文本内容重新生成 `{source_path_hash}_{index:04d}_{content_hash}`，把这个值作为 Chroma 的 record ID，并写入 metadata 的 `chunk_id`。

从设计意图看，这是把“领域对象 ID”和“向量存储 ID”分层，并保证重复摄取时能幂等 upsert；但在当前实现中两者都已经是确定性的，因此这次重新生成在功能上有一定冗余，也可能造成 ID 认知混淆。Pipeline 后续使用 VectorUpserter 返回的新 ID 覆盖 `sparse_stats['chunk_id']`，确保 BM25 命中结果可以回查 Chroma。

这个映射依赖“顺序不变”而不是 `zip()` 做语义校验：`BatchProcessor` 对同一个 batch 先后调用两个 Encoder，两个 Encoder 都按输入顺序返回；随后 `extend()` 按 batch 顺序展开；`VectorUpserter` 又按 `zip(chunks, vectors)` 顺序生成并返回 storage IDs。因此正常成功路径下 `sparse_stats[i]` 与 `vector_ids[i]` 来自同一个 Chunk。若某个 Encoder 丢弃、重排或部分失败，当前代码主要依靠数量校验/后续 upsert 失败来暴露问题，并没有在这行主动比较原始 Chunk ID。

## Embedding 的实现与调用链

本项目的 Dense Embedding 分成“抽象接口、具体 Provider、批处理适配器”三层：

- 抽象接口：`src/libs/embedding/base_embedding.py::BaseEmbedding`，只规定 `embed(texts) -> List[List[float]]`。
- Provider 工厂：`src/libs/embedding/embedding_factory.py::EmbeddingFactory`，根据 `settings.embedding.provider` 创建具体实现。
- 当前配置的具体实现：`src/libs/embedding/ollama_embedding.py::OllamaEmbedding`。配置是 `provider: ollama`、`model: nomic-embed-text`；如果改为 `openai` 或 `azure`，工厂会创建对应的 `OpenAIEmbedding` 或 `AzureEmbedding`。
- 批处理适配器：`src/ingestion/embedding/dense_encoder.py::DenseEncoder`，负责从 Chunk 提取文本、按 `batch_size` 分批、调用 Provider，并校验向量数量和维度。

需要区分调用层级：`IngestionPipeline` 不直接调用 `embedding.embed()`，而是调用 `BatchProcessor.process()`；`BatchProcessor` 再调用 `DenseEncoder.encode()`，最后由 `DenseEncoder` 调用注入的 Embedding Provider。

当前 Dense Embedding 的实际调用链是：

```text
IngestionPipeline Stage 5
→ BatchProcessor.process()
→ DenseEncoder.encode(chunks)
→ [chunk.text for chunk in chunks]
→ 按 batch_size=100 分批
→ OllamaEmbedding.embed(batch_texts)
→ 对每个文本 POST 到 Ollama /api/embeddings
→ 返回 List[List[float]]
→ VectorUpserter 写入 Chroma
```

如果只问“Dense Embedding 本身”有 4 个核心步骤：

1. 从每个 `Chunk` 取出 `chunk.text` 并校验不能为空。
2. `DenseEncoder` 按 `batch_size` 分批，当前配置为 100。
3. Provider 调用模型生成向量；当前 `OllamaEmbedding` 对 batch 中每个文本调用 Ollama `/api/embeddings`，提取响应里的 `embedding` 字段。
4. 校验“一个 Chunk 对应一个向量”、顺序一致、向量维度一致，然后交给存储层。

### `OllamaEmbedding.embed()` 的正常执行步骤

从 `src/libs/embedding/ollama_embedding.py::OllamaEmbedding.embed()` 看，主流程可概括为 6 步：

1. 调用 `BaseEmbedding.validate_texts()`，检查输入列表非空，且每个文本有效。
2. 准备 Ollama 地址和结果列表：`{base_url}/api/embeddings`。
3. 遍历 `texts`，为每个文本构造 `{"model": self.model, "prompt": text}` 请求体。
4. 对当前文本发起一次 HTTP `POST` 请求。
5. 解析 JSON，检查是否存在 `embedding` 字段，并把向量追加到结果列表。
6. 所有文本处理完后，返回 `List[List[float]]`。

因此，虽然 `DenseEncoder` 传入的是一批文本，但当前 Ollama Provider 的实现是“一个文本一次请求”：例如 3 个文本会向 Ollama 发起 3 次 `/api/embeddings` 请求。HTTP 错误、连接错误、超时和响应格式错误会被转换成 `OllamaEmbeddingError`。

`OllamaEmbedding.get_dimension()` 只返回该 Provider 配置的向量维度，不执行推理；当前从 `settings.embedding.dimensions` 读取，配置为 768，缺省值也是 768。实际 `DenseEncoder` 仍会根据返回向量的 `len(vec)` 校验所有向量维度一致，并没有在编码过程中调用 `get_dimension()`。

向量数据库不需要自己知道模型名称，它接收的是已经生成好的浮点数组，并主要关心向量维度。但生成文档向量和查询向量的 Provider、模型、维度和预处理方式必须保持一致；当前 `DenseRetriever` 也通过 `EmbeddingFactory` 创建同一配置的 Provider，再对查询文本调用 `embed([query])`。如果更换模型，即使维度相同，也应重新生成并写入所有文档向量，否则向量空间不一致会导致检索结果失真。

Ollama 当前请求体只有 `model` 和 `prompt`，没有发送 `dimensions`。因此 `settings.embedding.dimensions: 768` 不是对 Ollama 的强制参数，而是项目对模型输出维度的配置约定；实际维度由 Ollama 模型本身决定。当前默认的 `nomic-embed-text` 预期输出 768 维，所以配置与模型应当匹配，但如果换了输出其他维度的模型，`get_dimension()` 返回的配置值不会自动改变实际向量，也不会对向量做截断或补零。当前代码只检查返回向量彼此维度一致，没有把实际维度和配置值做强制比较。

通常维度由模型架构固定；少数 Provider（例如 OpenAI 的部分 `text-embedding-3` 模型）支持通过 API 参数选择维度。Ollama 当前实现没有这个参数，因此要改变维度通常需要换一个原生维度不同的模型，或额外增加投影/降维层，并对文档和查询向量使用同一套处理。

如果问“Stage 5 完整的编码步骤”，还要加上 Sparse 路径：`BatchProcessor` 在同一 batch 中先调用 `DenseEncoder`，再调用 `SparseEncoder`；后者使用 `jieba` 生成词频、文档长度等 BM25 统计。Sparse 统计不是 `BaseEmbedding` 的另一种向量实现，而是独立的关键词检索数据。

### Sparse Encoding 的底层实现

`src/ingestion/embedding/sparse_encoder.py::SparseEncoder` 不调用 LLM 或向量模型。它使用 `jieba.lcut()` 分词、正则过滤标点、可选小写化、最小词长过滤，再用 `collections.Counter` 统计词频。每个 Chunk 的输出是：`chunk_id`、`term_frequencies`、`doc_length`、`unique_terms`；之后交给 `BM25Indexer` 构建倒排索引并计算 BM25 分数。严格来说，SparseEncoder 先产生的是 BM25 term statistics，而不是已经计算完成的稀疏向量。

`raw_tokens = jieba.lcut(text)` 默认使用精确模式（`cut_all=False`，并启用 HMM）：jieba 根据词典构造可能的词切分，再用词频/概率选择较合理的路径；词典中没有的连续字符会通过 HMM 识别。它返回的是原始 token 列表，可能仍包含空格和标点，例如 `"Hello world hello"` 大致是 `['Hello', ' ', 'world', ' ', 'hello']`。随后 `SparseEncoder._tokenize()` 才负责 `strip()`、过滤纯标点/空白、转小写和过滤长度小于 `min_term_length` 的 token。实际中文切分会受 jieba 版本、默认词典和自定义词典影响。

## Transform 阶段的职责

Transform 接收 `List[Chunk]`，返回处理后的 `List[Chunk]`，位置在 Chunking 之后、Embedding 之前。它不负责把文档切块，而是改善 Chunk 的正文质量、补充语义 metadata，并把图片内容转成可检索文本。

当前 `IngestionPipeline` 按以下顺序执行：

```text
Chunk
→ ChunkRefiner：清理噪声，可选 LLM 重写
→ MetadataEnricher：补充 title / summary / tags
→ ImageCaptioner：为 [IMAGE: id] 生成图片描述并写回 Chunk
→ Dense/Sparse Encoding
```

Transform 前后的典型变化：

```text
输入 Chunk
  text: "页眉乱码... Azure 配置步骤 [IMAGE: img_1]"
  metadata: {source_path, chunk_index, images}

经过 Transform
  text: "Azure 配置步骤 [IMAGE: img_1]\n图片描述：Azure 配置界面..."
  metadata: {
    source_path, chunk_index, images,
    title, summary, tags, image_captions,
    refined_by, enriched_by
  }
```

设计上的重要点：

- `ChunkRefiner` 先做规则清理，LLM 失败时回退到规则结果。
- `MetadataEnricher` 生成结构化语义字段，便于后续检索、过滤和引用展示。
- `ImageCaptioner` 只处理 Chunk 文本中实际引用的图片，并使用缓存减少重复 Vision API 调用；未启用 Vision LLM 时是 no-op。
- LLM/图片增强失败不会默认阻断整条摄取流水线，Transform 尽量保留原 Chunk 或降级结果。

注意：`MetadataEnricher` 的规则分支不改写 `chunk.text`，只是根据文本生成 `title`、`summary`、`tags` 并合并到 metadata；真正做规则文本清洗的是 `ChunkRefiner._rule_based_refine()`。

Transform 的最终结果会直接进入 `BatchProcessor`，因此它生成或追加的内容会影响后续 Dense embedding 和 BM25 词项统计。

### MetadataEnricher 的具体生成方法

`MetadataEnricher` 采用“规则优先计算 + 可选 LLM 覆盖 + 失败回退”的策略：

- 规则 `title`：优先匹配 Markdown 标题；否则使用短的第一行、第一句，最后退化为前 100 个字符。
- 规则 `summary`：按句号/问号/感叹号后的空白切句，取前 3 句，最多 500 字符。
- 规则 `tags`：抽取首字母大写词、camelCase/snake_case 标识符、Markdown 粗体/斜体词，去重排序后最多 10 个。
- LLM 分支：把 Chunk 前 2000 个字符填入 `config/prompts/metadata_enrichment.txt`，要求 LLM 返回 `Title:`、`Summary:`、`Tags:` 三行，再由正则解析；当前配置 `ingestion.metadata_enricher.use_llm: true`。
- LLM 没有返回结果、调用异常或解析失败时，使用规则结果；单个 Chunk 发生未处理异常时使用 `Untitled`、文本预览和空 tags，避免中断整篇文档。

因此当前的 `tags` 不是来自独立的机器学习关键词模型，而是来自规则启发式或 LLM 生成，具体由 `metadata_enricher.py::MetadataEnricher._rule_based_enrich()` 和 `_llm_enrich()` 决定。

### ImageCaptioner 的能力边界

当前 `ImageCaptioner` 的实际 caption 生成只有 Vision LLM 路径：通过 `LLMFactory.create_vision_llm(settings)` 创建 `BaseVisionLLM`，再调用 `chat_with_image(text=prompt, image=ImageInput(path=...))`。代码中没有 OCR、传统图像识别或规则描述的备用实现；Vision LLM 未启用、初始化失败、图片不存在或调用失败时，Transform 直接跳过/保留原 Chunk，不生成 caption。当前 `config/settings.yaml` 中 `vision_llm.enabled` 是 `false`，所以默认运行不会进行图片描述。

## 五个自定义抽象接口的边界

`DEV_SPEC.md` 3.1.1 中的五个接口是可插拔组件的契约，不是完整摄取流水线本身：

| 接口 | 当前职责 | 当前代码中的连接方式 |
|---|---|---|
| `BaseLoader` | 文件 → `Document` | `PdfLoader` 实现；`Pipeline` 当前直接持有 `PdfLoader` |
| `BaseSplitter` | 文本 → `List[str]` | `RecursiveSplitter` 实现；由 `DocumentChunker` 转成带 ID/metadata 的 `Chunk` |
| `BaseTransform` | `List[Chunk]` → `List[Chunk]` | `ChunkRefiner`、`MetadataEnricher`、`ImageCaptioner` 顺序执行 |
| `BaseEmbedding` | 文本批次 → Dense vectors | 由 `DenseEncoder` 包装，并与 `SparseEncoder` 一起交给 `BatchProcessor` |
| `BaseVectorStore` | 向量记录的 upsert/query | `VectorUpserter` 负责把 `Chunk + vector` 转成稳定 ID 的存储记录 |

因此需要区分两个目标：

- **实现扩展点**：为这五个接口各提供一个合法实现，可以让某个 Loader、Splitter、Transform、Embedding 或 VectorStore 可替换。
- **实现可运行的完整摄取链路**：还必须有 `IngestionPipeline` 编排、`DocumentChunker`、`FileIntegrityChecker`、`BatchProcessor`、`SparseEncoder`、`BM25Indexer`、`ImageStorage`、配置/Factory 以及错误与追踪处理。

当前代码还有两个重要边界：

1. `BaseEmbedding` 只描述 Dense embedding；Sparse/BM25 不是这五个接口中的一个，而是由 `SparseEncoder` 和 `BM25Indexer` 独立完成。
2. `BaseVectorStore` 只覆盖向量存储；完整索引状态还包括 BM25 和图片索引，不能只实现 VectorStore 就得到完整的混合检索数据。

如果按 `DEV_SPEC.md` 3.1.1 的“完整目标”验收，还要把 `DocumentManager` 的跨存储删除/统计、进度回调以及存储层删除接口纳入范围；它们不是五个基础抽象接口能够自动提供的能力。

对应源码：

- `src/libs/loader/base_loader.py::BaseLoader`
- `src/libs/splitter/base_splitter.py::BaseSplitter`
- `src/ingestion/transform/base_transform.py::BaseTransform`
- `src/libs/embedding/base_embedding.py::BaseEmbedding`
- `src/libs/vector_store/base_vector_store.py::BaseVectorStore`
- `src/ingestion/pipeline.py::IngestionPipeline`
- `src/ingestion/chunking/document_chunker.py::DocumentChunker`
- `src/ingestion/embedding/batch_processor.py::BatchProcessor`
- `src/ingestion/storage/vector_upserter.py::VectorUpserter`
- `src/ingestion/storage/bm25_indexer.py::BM25Indexer`
- `src/libs/loader/file_integrity.py::FileIntegrityChecker`

## BM25 索引结构

```json
{
  "metadata": {
    "num_docs": 100,
    "avg_doc_length": 42.5
  },
  "index": {
    "代理": {
      "idf": 1.2,
      "df": 8,
      "postings": [
        {"chunk_id": "chunk_001", "tf": 2, "doc_length": 35}
      ]
    }
  }
}
```

查询时，关键词先查倒排索引，再结合 TF、IDF、文档长度计算 BM25 分数；得到 Chunk ID 后，SparseRetriever 再从 Chroma 补回正文和 metadata。

## 关键区别

- Dense 的索引对象是向量，查询需要把 query 转成向量。
- BM25 的索引对象是词项、TF、DF 和 posting list，查询使用关键词。
- 两者共享 Chunk ID 和正文来源，但索引数据结构、评分方式和查询入口不同。

## Ollama Vision Provider 与图片描述

当前 `ImageCaptioner` 的视觉调用链是：

```text
IngestionPipeline Stage 4c
→ ImageCaptioner.transform()
→ LLMFactory.create_vision_llm(settings)
→ OllamaVisionLLM.chat_with_image()
→ Ollama POST /api/chat
→ caption 写回 Chunk.text 和 metadata.image_captions
→ Stage 5 重新生成 Dense Embedding
```

本次新增的 `OllamaVisionLLM` 实现 `BaseVisionLLM`，读取 `ImageInput.path/data/base64`，并将图片转换为 Ollama 要求的纯 Base64，放入 `messages[-1].images`。它使用 `vision_llm.model` 和 `vision_llm.base_url`，temperature/max_tokens 复用主 `llm` 配置；HTTP 错误、连接失败、超时和异常响应统一转换为 `OllamaVisionLLMError`。

这意味着视觉模型是在摄取阶段工作，不是查询时的 Embedding 模型：启用 Provider 后必须重新摄取已有 PDF，图片描述才会进入 Chunk 并参与向量化。未启用或调用失败时，`ImageCaptioner` 会保留原 Chunk，退化为没有图片描述的文本检索。

对应源码：

- `src/libs/llm/base_vision_llm.py::BaseVisionLLM`
- `src/libs/llm/ollama_vision_llm.py::OllamaVisionLLM`
- `src/libs/llm/llm_factory.py::_register_vision_providers`
- `src/ingestion/transform/image_captioner.py::ImageCaptioner`
- `config/settings.yaml::vision_llm`

## 待确认

### PDF 摄取异常排查

- 运行时如果某个 PDF 的 `load` 阶段长时间没有结束，应先看 `logs/traces.jsonl`。
- 本次 `docker-compose-networking.pdf` 的 `load` 阶段约耗时 8 分 15 秒，但 `text_length=0` 且 `image_count=0`，说明异常发生在 MarkItDown/PDF 解析阶段，而不是 Embedding、Vision LLM 或向量写入阶段。
- 排查时先中断批量任务，单独摄取一个较小 PDF；必要时暂时关闭 `vision_llm`、`chunk_refiner.use_llm` 和 `metadata_enricher.use_llm`，避免把解析问题和后续模型调用混在一起。

### 当前修复

- `src/libs/loader/pdf_loader.py::PdfLoader._extract_text()` 现在默认使用 `pdfplumber` 按页提取文本，不再把 MarkItDown 作为主解析路径。
- `pymupdf` 已加入项目依赖，用于 PDF 图片提取；图片仍按原有流程写入 `data/images/{collection}`，供 `ImageCaptioner` 使用。
- 之前卡住的 `docker-compose-networking.pdf` 已验证可以在约 6 秒内提取文本和图片。
- PDF 图片提取现在默认过滤宽或高小于 100 像素的图片，并按 XRef 去重，避免把打印 PDF 中的字形碎片、装饰图标送入 Vision LLM。

- [ ] `VectorUpserter` 生成稳定 vector ID 的具体规则，以及它与增量幂等的关系。
- [ ] 文档删除时 Chroma、BM25 和图片索引的协调删除顺序。

## 复习问题

1. 为什么 BM25 不需要 Embedding 模型？
2. `term_frequencies`、`df` 和 `doc_length` 分别用于什么？
3. 为什么 Pipeline 要把 Chroma 返回的 vector ID 写回 sparse stats？
4. 如果 Chroma 有记录但 BM25 没有对应 posting，会发生什么？

## 面试重点

面试时优先讲清以下五件事：

1. **完整链路**：SHA256 增量检查 → PDF Loader → Document → Recursive Chunking → LLM/规则 Transform → Dense/Sparse Encoding → Chroma + BM25 Storage。
2. **Chunk 设计**：当前配置为 `chunk_size=1000`、`chunk_overlap=200`；说明 Chunk 是检索粒度，参数需要在召回精度、上下文完整性、Token 成本和延迟之间平衡。
3. **双路索引**：同一批 Chunk 并行生成 Dense vector 和 BM25 term stats；Dense 处理语义表达，BM25 保障客户端功能名、版本号、错误码和配置项等精确匹配。
4. **幂等与一致性**：文件级 SHA256 避免重复摄取；VectorUpserter 生成稳定 Chunk ID；BM25 的 `chunk_id` 会与 Chroma 返回的 vector ID 对齐，避免两套索引无法互相定位。
5. **可扩展性与故障处理**：当前入口是 PDF；新增 Markdown/HTML/数据库时扩展 Loader/同步适配。LLM 增强失败应能回退到规则处理，向量写入与 BM25 更新需要关注部分成功后的修复策略。

## 面试中的项目叙述

“针对客户端软件说明文档难检索的问题，我设计了六阶段摄取链路。首先通过 SHA256 判断文件是否需要增量处理，再将 PDF 解析成 Document 并按递归分割策略切成带 `source_ref` 和稳定 ID 的 Chunk。随后对 Chunk 做重组、元数据增强和图片描述，分别生成 Dense 向量与 BM25 词项统计，最终将向量写入 Chroma、倒排索引写入独立 BM25 存储，并保持两者的 Chunk ID 一致，为后续混合检索和引用返回提供基础。”
