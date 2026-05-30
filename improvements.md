# Evidence Chain Tracking — 改进点

## 核心问题

PLAN.md 现有方案的 `verify_evidence_claims()` 只做**引用格式校验**（EVD ID 是否存在、level 标注是否一致），不做**事实一致性校验**。Agent 自评 `evidence_level` 可能包含幻觉——Agent 声称 source 支持某个结论，但 source 原文实际说的是另一回事。当前验证层检测不到这类错误。

```
现有: Agent 自评 evidence_level → verify_evidence_claims() 检查格式一致性
缺失: 外部独立工具去 source 原文验证 "claim 是否真的被 source 支持"
```

## 改进点

### 1. 强化 `source_excerpt` 约束（成员 A，schema 层）✅ 已实现

`source_excerpt`（source 原文摘录）是外部验证的前提。当 `evidence_level` 为 `supported` 或 `inferred` 时强制必填，不填则拒绝写入。

**实现细节：**
- `validate_evidence_payload()`：新增条件校验，`supported`/`inferred` 时 `source_excerpt` 为空则报错
- `record_evidence()`：写入文件前先调用 `validate_evidence_payload()`，校验失败返回 `{"ok": False, "errors": [...]}`
- `evidence_record` MCP 工具描述：明确告知 Agent 不填 source_excerpt 会被拒绝
- `insufficient` 和 `retracted` 不受此限制

**涉及文件：** `artifact/schemas.py`, `artifact/service.py`, `mcp/server.py`, `tests/test_evidence_tracking.py`

### 2. 外部 NLI 归因验证（成员 C，验证脚本层）

在 `scripts/verify_evidence.py` 中集成外部 NLI 模型，对每条 evidence 的 `(claim, source_excerpt)` 对做事实一致性判断，产出 Agent 自评 vs 外部判断的差异报告。

推荐工具栈：

| 层 | 工具 | 作用 |
|----|------|------|
| 快速筛选 | MiniCheck / Vectara HHEM | 批量跑全量，比 LLM 快 100-400x |
| 精细验证 | DeBERTa-v3-mnli / GPT-4 | 对可疑 case 做深度 NLI 判断 |

核心逻辑：

```python
def external_fact_check(evidence_path):
    metadata, _ = load_markdown_document(evidence_path)
    claim = metadata["claim"]
    excerpt = metadata.get("source_excerpt", "")

    if not excerpt:
        return {"status": "UNVERIFIABLE"}

    nli_result = nli_model(premise=excerpt, hypothesis=claim)
    # entail → supported, neutral → insufficient, contradict → refuted

    return {
        "evidence_id": metadata["evidence_id"],
        "agent_label": metadata["evidence_level"],
        "external_label": nli_to_level(nli_result),
        "agreement": metadata["evidence_level"] == nli_to_level(nli_result),
    }
```

### 3. 对比指标升级

PLAN.md 原有指标（引用完整性）：

| 指标 | 含义 |
|------|------|
| 有证据支持的结论比例 | Agent 自评的 supported 占比 |
| 无来源引用数 | 缺少 evidence 标注的声明数 |
| 错误引用数 | EVD ID 在 INDEX.md 中不存在的引用数 |

建议新增指标（事实一致性）：

| 指标 | 含义 |
|------|------|
| Agent-NLI Agreement Rate | Agent 自评与外部 NLI 判断一致的比例 |
| Hallucination Rate | Agent 标 supported 但 NLI 判 neutral/contradict 的比例 |
| Unverifiable Rate | source_excerpt 缺失导致无法验证的比例 |

### 4. 报告呈现升级

在最终报告（成员 D）中，可以呈现这样一张对比表：

```
EVD-001: agent=supported, NLI=entail       ✓
EVD-002: agent=supported, NLI=neutral      ⚠ 可能幻觉
EVD-003: agent=inferred,  NLI=contradict   ✗ source 实际说了相反内容
EVD-004: agent=supported, NLI=UNVERIFIABLE — source_excerpt 缺失
```

这种 Agent 自评 vs 外部独立判断的对比本身是很好的分析亮点。
