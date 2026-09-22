# 评估与 Ground Truth 学习笔记

## 一句话理解

Ground Truth 不是重新准备一份文档，而是为真实用户问题标注“哪些已摄取的 Chunk 应该被召回”。

## 当前项目中的格式

`tests/fixtures/golden_test_set.json` 的每个测试用例包含：

- `query`：用户问题
- `expected_chunk_ids`：正确答案所在的 Chunk ID，用于 Custom Evaluator 的 Hit Rate/MRR
- `expected_sources`：来源文档记录；当前 `EvalRunner` 的 Custom Evaluator 路径暂未用它计算指标
- `reference_answer`：参考答案，供后续答案质量评估使用

## 实际调用链

```text
golden_test_set.json
→ scripts/evaluate.py
→ EvalRunner
→ HybridSearch
→ retrieved_chunk_ids
→ CustomEvaluator
→ hit_rate / mrr
```

## 源码事实

- `EvalRunner._evaluate_single()` 只有在 `expected_chunk_ids` 非空时才构造 Ground Truth。
- `CustomEvaluator` 的 `hit_rate` 判断 Top-K 中是否命中任意标注 Chunk；`mrr` 使用第一个命中 Chunk 的倒数排名。
- 当前示例 Golden Set 的 `expected_chunk_ids` 为空，因此 Custom Evaluator 会得到 0，不能代表真实检索质量。
- 当前 `scripts/evaluate.py` 支持 `--test-set`、`--collection` 和 `--top-k` 参数。
- P95 延迟不是当前 Custom Evaluator 的 Ground Truth 指标，应从查询 Trace/运行时间统计中单独计算。

## 推荐制作顺序

1. 先用 `scripts/query.py --verbose` 查询真实集合，查看返回的 `id`、来源和文本。
2. 人工判断哪些返回 Chunk 真正包含答案。
3. 把这些 Chunk ID 写入 `expected_chunk_ids`。
4. 用同一份集合和 Golden Set 执行 `scripts/evaluate.py`。

## 待确认

- [ ] 是否需要为评估脚本增加按 `expected_sources` 计算的来源级指标？
- [ ] 是否需要增加“不应回答/无相关文档”测试用例的专门指标？

## 本次核对：与简历表述的边界

- 当前已有 Golden Set / `EvalRunner` / Custom Evaluator / Ragas Evaluator / `tests/e2e/test_recall.py` 的评测骨架，但 `tests/fixtures/golden_test_set.json` 只有 5 条示例问题，所有 `expected_chunk_ids` 都为空。
- `test_recall.py` 的 `HIT_RATE_THRESHOLD` 和 `MRR_THRESHOLD` 当前均为 `0.0`，无 Ground Truth 时直接 skip，因此尚不能称为有效的质量门禁。
- 仓库当前没有 `.github/workflows` 或其他 CI 配置；`scripts/evaluate.py` 能输出报告，但不会按忠实度/召回阈值返回阻断合并的失败状态。
- `RagasEvaluator` 源码支持 `faithfulness`，但当前 `settings.yaml` 是 `evaluation.enabled: false` + `provider: custom`；Ragas wrapper 当前只支持 Azure/OpenAI，不能据此声称已完成本地/DeepSeek 忠实度门禁。

## Business Golden Set v1（已验证）

- 业务 Golden Set 位于 `evals/datasets/business_golden_v1.json`，包含 40 条 case，覆盖 `ops_kb/` 下 9 个 PDF。
- 这些 case 的 `expected_chunk_ids` 来自 `ubuntu_ops_filtered` Chroma collection 的 metadata segment；该 collection 当前有 245 个 chunk，ID 与 BM25 索引共享。
- `scripts/validate_golden_set.py` 使用标准库直接读取 Chroma SQLite metadata，校验 source 文件、chunk 存在性和 chunk/source 对应关系，不初始化 Embedding、LLM 或重新 ingestion。
- 2026-09-22 验证结果：40/40 valid，40 条均已映射真实 chunk；状态为 pending 40、reviewed 0、unresolved 0。
- `EvalRunner` 未修改。现有 `load_test_set()` 已能继续读取 `query`、`expected_chunk_ids`、`expected_sources`、`reference_answer`，并忽略业务 Golden Set 的扩展字段，因此旧 fixture 保持兼容。
