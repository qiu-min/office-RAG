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

此前的数据资产 PR 已为后续 Retrieval Evaluation 和 Generation Evaluation 保留了
`expected_chunk_ids`、`reference_answer` 和 `evidence` 等字段；本 PR2 的检索基线说明见下节。

## Retrieval Baseline（PR2）

PR2 使用 Business Golden Set 中 `review_status == "reviewed"` 的 cases，调用生产环境的
HybridSearch 以及 settings 中启用的 optional Reranker，只评估检索结果，不生成答案。

运行命令：

```bash
python scripts/evaluate_retrieval.py \
  --test-set evals/datasets/business_golden_v1.json \
  --collection ubuntu_ops_filtered \
  --top-k 10 \
  --output evals/reports/retrieval_baseline_v1.json
```

Baseline 计算 `HitRate@K`、`Recall@K` 和 `MRR`，默认 K 为 1、3、5、10，并将每个 case
的 expected/retrieved chunk IDs 保存到 `evals/reports/retrieval_baseline_v1.json`。该文件是
后续修改 chunking、embedding、BM25、RRF 或 reranker 前的基准成绩；修改这些检索策略后，
应使用相同 Golden Set 重新运行并进行对比。

PR2 不包含 Generation Evaluation，不使用 `reference_answer` / `evidence` 评分，不调用
Ragas，也不会根据 baseline 分数自动调优检索系统。
