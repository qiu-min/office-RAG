# Business Golden Sets

Golden Set 是稳定的业务评测数据资产，用来记录真实用户问题、文档支撑的标准答案以及可追溯的 evidence 和 chunk 映射。它不同于 `tests/fixtures/` 下服务单元测试的普通 fixture；测试 fixture 可以服务于代码契约，而 Golden Set 需要经过业务核对并长期保持稳定。

## Business Golden Set v1

`business_golden_v1` 来自项目根目录 `ops_kb/` 中已经摄取的真实业务 PDF。数据文件位于 [`datasets/business_golden_v1.json`](datasets/business_golden_v1.json)。`expected_chunk_ids` 使用当前 `ubuntu_ops_filtered` Chroma collection 中真实存在的稳定 chunk ID。

校验命令：

```bash
python scripts/validate_golden_set.py --test-set evals/datasets/business_golden_v1.json
```

## 字段说明

- `id`：稳定且唯一的 case ID，例如 `biz_001`。
- `query`：接近真实用户表达的问题，而不是文档标题或章节编号。
- `reference_answer`：完全由业务文档事实支撑的标准答案。
- `evidence`：直接支持答案的原文或关键原文片段，每项包含 `source` 和 `text`。
- `expected_sources`：应被检索到的真实 PDF 文件名。
- `expected_chunk_ids`：evidence 所在的真实 ingestion chunk ID，可用于后续 Retrieval Evaluation。
- `category`：问题类型，如 `feature`、`configuration`、`procedure`、`troubleshooting`、`limitation`、`concept`、`comparison`、`cross_section`。
- `difficulty`：`easy`、`medium` 或 `hard`，表示回答需要的文档推理范围。
- `review_status`：`pending`、`reviewed` 或 `unresolved`。

## Review Workflow

AI 只能生成 candidate，所有自动生成的 case 初始状态必须是 `pending`。人工审核时需要核对：

- query 是否真实、明确、合理；
- reference_answer 是否被业务文档完整支持；
- evidence 是否足以证明答案；
- source 是否正确；
- chunk mapping 是否对应真实、包含 evidence 的 chunk。

审核确认后，将 `pending` 改为 `reviewed`。如果答案无法确定、evidence 不充分、chunk 无法可靠映射或问题存在歧义，则改为 `unresolved`。不要为了让数据集全部通过而篡改业务事实。

## 后续用途

后续 PR 可以基于该 Golden Set 做 Retrieval Evaluation，例如 HitRate@K、MRR 和 Recall@K；也可以做 Generation Evaluation，例如 Faithfulness、Answer Relevancy 和 Context Precision。本 PR 只建立数据资产、追溯关系和校验工具，不实现这些指标、Ragas 改造或 CI Quality Gate。
