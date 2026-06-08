Roxy: 05-30 21:28:48
代码执行环境和 shell 都限制文件写入。我直接把完整的 PLAN_append_layer2.md 内容发给你，你可以保存到工作区。

---

文件内容如下（保存为 `projects/PLAN_append_layer2.md`）：

```markdown
# 方向 A：科研智能体证据链追踪模块 — 修订版 PLAN（追加 Layer 2 外部归因验证）

> 基于 PLAN.md v1，新增 Layer 2 外部归因验证（External Attribution Verification）。
> Layer 1 保持不变（证据链记录 + 引用格式校验）。

Roxy: 05-30 21:28:49
Layer 2 独立于智能体，使用外部 NLI 模型对 (claim, source_excerpt) 做事实一致性判断。
> 展示日期：2026.06.05，终稿日期：2026.06.08。

---

## 改动总览

 层级  功能  谁判断  发现什么问题  PLAN.md 原有？ 
---------------------------------:---:
 Layer 1  证据链记录 + 引用格式校验  Agent 自评 + `verify_evidence_claims()` 检查 EVD ID 是否在 INDEX.md 中存在、level 是否一致

Roxy: 05-30 21:28:50
引用遗漏、level 标注错误、retracted 误用  ✅ 已设计 
 Layer 2  外部归因验证（事实一致性）  **独立外部 NLI 模型**，与 Agent 解耦  **幻觉**——Agent 声称 source 说 X，但 source 实际说的是 Y  🆕 **本次新增** 

**核心矛盾（PLAN.md 未解决）：**

Roxy: 05-30 21:28:52
> PLAN.md 的 `verify_evidence_claims()` 只检查 `[EVD-xxx]` 引用格式是否在 INDEX.md 中存在、level 标签是否一致，**不去 source 原文核实 Agent 的 claim 是否被真的支持**。如果 Agent 产生幻觉（声称 source 支持某个结论但实际上不支持），Layer 1 检测不到。
>
> Layer 2 的目标：对每个 (claim, source_excerpt) 对，用独立的 NLI 模型判断 entailment，输出差异报告。

---

## Layer 2：外部归因验证架构

Roxy: 05-30 21:28:53
### 两阶段流水线

Roxy: 05-30 21:28:54
```
EVD-xxx.md（含 claim + source_excerpt）
        │
        ▼
┌─────────────────────────────────────┐
│  Phase 1: 快速筛选（批量，轻量模型）    │
│  ├── MiniCheck (LLaMA-3.1-8B 微调)   │
│  │   或 Vectara HHEM                 │
│  ├── 对每对 (claim, source_excerpt)   │
│  │   输出: SUPPORTED / NOT_SUPPORTED │
│  └── 耗时: ~10ms/对                  │
└──────────────┬──────────────────────┘
               │ 可疑 case（低分/矛盾）
               ▼
┌─────────────────────────────────────┐
│  Phase 2: 精细验证（对可疑 case）      │
│  ├── DeBERTa-v3-large-mnli (NLI)     │
│  │   或 GPT-4o-mini API              │
│  ├── 输出: entail / contradict /     │
│  │         neutral + 置信度分数       │
│  └── 耗时: ~100ms-1s/对              │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│  差异报告 (Layer 2 Report)            │
│  ├── Agent 说 supported 但 NLI 判定  │
│  │   contradict → 疑似幻觉           │
│  ├── Agent 说 supported 但 NLI 判定  │
│  │   neutral → 证据不足              │
│  └── 一致率统计 (agreement rate)      │
└─────────────────────────────────────┘
```

Roxy: 05-30 21:28:55
### 工具选型对比

 工具  类型  速度  精度  部署难度  推荐用途 
------------------------------------------
 **MiniCheck** (`lytang/MiniCheck-Flan-T5`)  微调模型 (T5)  ★★★★★ (~10ms/对)  ★★★★ (GPT-4 评分的 90%+)  ★★★★★ (pip install)  Phase 1 主力 
 **Vectara HHEM** (`vectara/hallucination_evaluation_model`)  微调模型 (DeBERTa)  ★★★★★

Roxy: 05-30 21:28:56
★★★☆ (偏向 summary-level)  ★★★★★  Phase 1 备选 
 **AlignScore** (`yzhan238/AlignScore`)  微调模型 (RoBERTa)  ★★★★  ★★★★  ★★★★  Phase 1/2 通用 
 **DeBERTa-v3-mnli** (`MoritzLaurer/DeBERTa-v3-large-mnli`)  NLI 模型  ★★★☆  ★★★★★  ★★★★  Phase 2 精细验证 
 **GPT-4o-mini API**  LLM  ★★☆  ★★★★★  ★★★★★ (API key)

Roxy: 05-30 21:28:57
Phase 2 备用 
 **roberta-large-mnli**  NLI 模型  ★★★  ★★★☆  ★★★★★  Phase 1 备选 

**推荐组合**（建议用于展示）：

```
Phase 1: MiniCheck-Flan-T5  → 快速筛出可疑 claim
Phase 2: DeBERTa-v3-large-mnli → 对可疑 claim 做精细 NLI 三分类
```

---

## 代码层集成：PLAN.md 之上的修改

### 新增文件清单

 文件  负责人  说明 
--------------------

Roxy: 05-30 21:28:58
`scripts/external_attribution_verify.py`  **成员 C**  Layer 2 主脚本：读取 evidence 目录，对每个 EVD 调用 NLI 模型 
 `scripts/nli_model_loader.py`  **成员 C**  NLI 模型加载器：统一接口加载 MiniCheck + DeBERTa 
 `src/deepscientist/artifact/attribution_report.py`  **成员 C**  归因验证报告生成：差异表格 + 一致率统计

Roxy: 05-30 21:28:59
`tests/test_external_attribution.py`  **成员 C**  Layer 2 单元测试 
 `requirements-layer2.txt`  **成员 C**  额外依赖：`minicheck`, `transformers`, `torch` 

### 修改文件清单

 文件  修改内容  负责人 
-----------------------
 `scripts/verify_evidence.py`  在现有 Layer 1 验证后，可选调用 Layer 2 流水线  **成员 C**

Roxy: 05-30 21:29:01
`scripts/before_after_compare.py`  对比指标增加 Layer 2 维度（幻觉检测率等）  **成员 C** 
 `src/skills/evidence-track/SKILL.md`  增加 source_excerpt 为 required 字段的强制约束  **成员 B** 

---

## 详细设计：`scripts/external_attribution_verify.py`

### 输入

Roxy: 05-30 21:29:02
```
quest_root/artifacts/evidence/
├── INDEX.md
├── EVD-001.md    ← 每个文件含 claim + source_excerpt (YAML frontmatter)
├── EVD-002.md
└── ... ```

### 输出

```
quest_root/artifacts/evidence/
├── INDEX.md
├── EVD-001.md
├── ...

Roxy: 05-30 21:29:03
└── _layer2_report/
    ├── attribution_report.md      ← 人类可读差异报告
    ├── attribution_report.json    ← 结构化数据（供 before_after_compare.py 使用）
    └── per_evidence/              ← 逐条详细分析
        ├── EVD-001.json
        ├── EVD-002.json
        └── ... ```

### 核心逻辑

Roxy: 05-30 21:29:04
```python
#!/usr/bin/env python3
"""
external_attribution_verify.py — Layer 2 外部归因验证脚本

对 evidence 目录中的每对 (claim, source_excerpt) 做两阶段 NLI 验证。
独立于 Agent，不与 Agent 共享任何推理状态。
"""

import sys
from pathlib import Path
import json
from dataclasses import dataclass, field
from typing import Optional

Roxy: 05-30 21:29:05
# ============================================================
# Phase 1: 快速筛选 (MiniCheck)
# ============================================================

def phase1_minicheck_screen(claim: str, source_excerpt: str) -> dict:
    """
    返回 {"label": "SUPPORTED"

Roxy: 05-30 21:29:06
"NOT_SUPPORTED", "score": float}
    
    MiniCheck 是 sentence-transformers 兼容模型，可以直接 score。

Roxy: 05-30 21:29:07
判定阈值：score >= 0.5 → SUPPORTED, 否则 NOT_SUPPORTED
    """
    from minicheck import MiniCheck
    scorer = MiniCheck(
        model_name="Bespoke-MiniCheck-7B",  # 或 Flan-T5-Large
        enable_prefix_caching=False,
        cache_dir="./.model_cache",
    )
    pred_label, raw_prob, _, _ = scorer.score(
        docs=[source_excerpt], 
        claims=[claim],
    )
    return {
        "phase": 1,
        "model": "MiniCheck-7B",
        "label": "SUPPORTED" if raw_prob >= 0.5 else "NOT_SUPPORTED",
        "score": float(raw_prob),
    }

Roxy: 05-30 21:29:08
# ============================================================
# Phase 2: 精细验证 (DeBERTa-v3-mnli)
# ============================================================

def phase2_nli_verify(claim: str, source_excerpt: str) -> dict:
    """
    返回 {"label": "entail""contradict"

Roxy: 05-30 21:29:09
"neutral", "scores": {...}}
    
    使用 DeBERTa-v3-large-mnli 做标准 NLI 三分类。
    premise = source_excerpt, hypothesis = claim.

Roxy: 05-30 21:29:10
"""
    from transformers import pipeline
    
    classifier = pipeline(
        "zero-shot-classification",
        model="MoritzLaurer/DeBERTa-v3-large-mnli",
        device=-1,  # CPU（服务器无 GPU）
        cache_dir="./.model_cache",
    )
    
    result = classifier(
        source_excerpt,
        candidate_labels=["entailment", "contradiction", "neutral"],
        hypothesis_template="This text is {} the claim.",
    )
    
    label_map = {
        "entailment": "entail",
        "contradiction": "contradict",
        "neutral": "neutral",
    }
    
    return {
        "phase": 2,
        "model": "DeBERTa-v3-large-mnli",
        "label": label_map.get(result["labels"][0], "neutral"),
        "scores": dict(zip(
            [label_map.get(l, l) for l in result["labels"]],
            result["scores"],
        )),
    }

Roxy: 05-30 21:29:12
# ============================================================
# Phase 2 fallback — LLM-based verification
# ============================================================

def phase2_llm_verify(claim: str, source_excerpt: str) -> dict:
    """
    使用 GPT-4o-mini / DeepSeek 做精细验证（备用方案）。

Roxy: 05-30 21:29:13
优点：可处理复杂推理（数字近似、逻辑等价性等）。
    缺点：API 调用有成本 + 延迟。
    """
    prompt = f"""You are a fact-checker. Given a source excerpt and a claim from an AI agent,
determine whether the source SUPPORTS, CONTRADICTS, or is NEUTRAL toward the claim. 
SOURCE EXCERPT:
```
{source_excerpt}
```

Roxy: 05-30 21:29:14
CLAIM:
```
{claim}
```

Respond in JSON only:
{{"label": "SUPPORTED"  "CONTRADICTED"  "NEUTRAL", "reason": "brief explanation"}}
"""
    # 实际调用由 verify 脚本通过 API 完成
    return {"phase": 2, "model": "GPT-4o-mini (LLM)", "label": "NEUTRAL"}

Roxy: 05-30 21:29:15
# ============================================================
# Main pipeline
# ============================================================

Roxy: 05-30 21:29:16
@dataclass
class AttributionResult:
    evidence_id: str
    agent_label: str          # Agent 自评: supported/inferred/insufficient
    agent_claim: str
    source_excerpt: str
    phase1_label: str = ""
    phase1_score: float = 0.0
    phase2_label: str = ""
    phase2_scores: dict = field(default_factory=dict)
    needs_phase2: bool = False
    agent_nli_agreement: bool = True
    flagged_as_hallucination: bool = False

Roxy: 05-30 21:29:17
def run_layer2_pipeline(quest_root: Path) -> list[AttributionResult]:
    """
    主流水线：
    1. 遍历 evidence 目录，读取所有 EVD-*.md
    2. Phase 1: MiniCheck 快速筛选
    3. 如果 Phase 1 判定 NOT_SUPPORTED 但 Agent 说 supported → Phase 2 精细验证
    4. 汇总差异报告
    """
    import yaml
    
    evidence_root = quest_root / "artifacts" / "evidence"
    results = []
    
    for evd_file in sorted(evidence_root.glob("EVD-*.md")):
        content = evd_file.read_text(encoding="utf-8")
        if content.startswith("---"):
            parts = content.split("---", 2)
            metadata = yaml.safe_load(parts[1]) if len(parts) >= 3 else {}
        else:
            metadata = {}
        
        claim = metadata.get("claim", "")
        source_excerpt = metadata.get("source_excerpt", "")
        evidence_id = metadata.get("evidence_id", evd_file.stem)
        agent_label = metadata.get("evidence_level", "")
        
        if not claim or not source_excerpt:
            r = AttributionResult(
                evidence_id=evidence_id,
                agent_label=agent_label,
                agent_claim=claim,
                source_excerpt="[MISSING — cannot verify externally]",
                phase1_label="UNVERIFIABLE",
                needs_phase2=False,
                flagged_as_hallucination=False,
            )
            results.append(r)
            continue
        
        # Phase 1
        p1 = phase1_minicheck_screen(claim, source_excerpt)
        r = AttributionResult(
            evidence_id=evidence_id,
            agent_label=agent_label,
            agent_claim=claim,
            source_excerpt=source_excerpt,
            phase1_label=p1["label"],
            phase1_score=p1["score"],
        )
        
        # 判断是否需要 Phase 2
        agent_says_supported = agent_label == "supported"
        p1_says_unsupported = p1["label"] == "NOT_SUPPORTED"
        
        if agent_says_supported and p1_says_unsupported:
            r.needs_phase2 = True
            p2 = phase2_nli_verify(claim, source_excerpt)
            r.phase2_label = p2["label"]
            r.phase2_scores = p2["scores"]
            
            if p2["label"] in ("contradict", "neutral"):
                r.flagged_as_hallucination = True
                r.agent_nli_agreement = False
        
        results.append(r)
    
    return results

Roxy: 05-30 21:29:18
def generate_attribution_report(results: list[AttributionResult], output_dir: Path):
    """生成 Layer 2 归因验证报告（Markdown + JSON）"""
    output_dir.mkdir(parents=True, exist_ok=True)
    
    total = len(results)
    supported_by_both = sum(1 for r in results 
                            if r.agent_label == "supported" and r.phase1_label == "SUPPORTED")
    hallucination_suspected = sum(1 for r in results if r.flagged_as_hallucination)
    unverifiable = sum(1 for r in results if r.phase1_label == "UNVERIFIABLE")
    agent_only_supported = sum(1 for r in results if r.agent_label == "supported")
    
    valid = max(total - unverifiable, 1)
    agreement_rate = (total - hallucination_suspected - unverifiable) / valid * 100
    
    md = f"""# Layer 2 External Attribution Verification Report

Roxy: 05-30 21:29:19
> Generated by `external_attribution_verify.py`
> Phase 1: MiniCheck-7B  Phase 2: DeBERTa-v3-large-mnli

## Summary

 Metric  Value 
---------------
 Total evidence entries  {total} 
 Agent claims "supported"  {agent_only_supported} 
 Both Agent & NLI agree "supported"  {supported_by_both}

Roxy: 05-30 21:29:20
⚠️ Suspected hallucinations  {hallucination_suspected} 
 ⚠️ Unverifiable (missing source_excerpt)  {unverifiable} 
 Agent-NLI agreement rate  {agreement_rate:.1f}% 

## Suspected Hallucinations

 Evidence ID  Agent Label  Phase 1  Phase 2  Claim (truncated) 
-----------------------------------

Roxy: 05-30 21:29:21
----------------------------
"""
    for r in results:
        if r.flagged_as_hallucination:
            md += f" {r.evidence_id}  {r.agent_label}  {r.phase1_label}  {r.phase2_label}  {r.agent_claim[:100]}... \n"
    
    if hallucination_suspected == 0:
        md += " —  —  —  —

Roxy: 05-30 21:29:22
No hallucinations detected ✅ \n"
    
    md += f"""
## All Results

 Evidence ID  Agent Label  Phase 1  Score  Phase 2  ⚠️? 
---------------------------------------------------:---:

Roxy: 05-30 21:29:24
"""
    for r in results:
        flag = "⚠️" if r.flagged_as_hallucination else ""
        p2 = r.phase2_label if r.needs_phase2 else "—"
        md += f" {r.evidence_id}  {r.agent_label}  {r.phase1_label}  {r.phase1_score:.3f}  {p2}  {flag}

Roxy: 05-30 21:29:25
\n"
    
    (output_dir / "attribution_report.md").write_text(md, encoding="utf-8")
    
    json_report = {
        "summary": {
            "total": total,
            "supported_by_both": supported_by_both,
            "hallucination_suspected": hallucination_suspected,
            "unverifiable": unverifiable,
            "agreement_rate": round(agreement_rate, 1),
        },
        "results": [
            {
                "evidence_id": r.evidence_id,
                "agent_label": r.agent_label,
                "phase1_label": r.phase1_label,
                "phase1_score": r.phase1_score,
                "phase2_label": r.phase2_label,
                "flagged_as_hallucination": r.flagged_as_hallucination,
            }
            for r in results
        ],
    }
    (output_dir / "attribution_report.json").write_text(
        json.dumps(json_report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    
    return output_dir

Roxy: 05-30 21:29:26
# ============================================================
# CLI entry point
# ============================================================

Roxy: 05-30 21:29:27
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python external_attribution_verify.py <quest_root>")
        sys.exit(1)
    
    quest_root = Path(sys.argv[1])
    results = run_layer2_pipeline(quest_root)
    report_dir = generate_attribution_report(
        results, quest_root / "artifacts" / "evidence" / "_layer2_report"
    )
    print(f"Layer 2 report generated at: {report_dir}")
    print(f"Total evidence: {len(results)}")
    print(f"Suspected hallucinations: {sum(1 for r in results if r.flagged_as_hallucination)}")
```

Roxy: 05-30 21:29:28
---

## 对比指标扩展（增强 `before_after_compare.py`）

在 PLAN.md 原有对比指标基础上，增加 Layer 2 维度：

 指标  扩展前  Layer 1 only  Layer 1 + 2 
------:---::---::---:
 有证据支持的结论比例  —  X%  X% 
 无来源引用数  Y  Z  Z 
 错误引用数（格式）  —  W  W 
 🆕 幻觉检测数（Agent supported, NLI contradict）  —  —  H 
 🆕 证据不足标记数（Agent supported, NLI neutral）  —

Roxy: 05-30 21:29:29
—  N 
 🆕 Agent-NLI 一致率  —  —  A% 
 🆕 无法验证条目数（source_excerpt 缺失）  —  —  U 

**展示亮点**：对比「Layer 1 only」vs「Layer 1 + 2」，展示有多少 Agent 自评"高置信"的证据经不起外部验证，直接证明独立外部验证层的必要性。

---

 

---

## 成员分工调整

 成员  原 PLAN.md 职责  Layer 2 新增职责 
---------------------------------------
 **A (Backend)**  evidence 存储层 + MCP 工具  **无变化**。Layer 2 是独立脚本，不修改 evidence 存储层 
 **B (Prompt)**  SKILL.md + prompt 注入


**关键修改**：`source_excerpt` 从 optional → **required** 
 **C (Report/Validation)**  evidence table 渲染 + verify 脚本  **主要新增**：`external_attribution_verify.py`、`nli_model_loader.py`、`attribution_report.py`、单元测试、对比框架增加幻觉检测维度 
 **D (QA/Demo)**  集成测试 + QQ/WeChat 演示


测试案例中增加 1 个"故意错误引用"case；准备服务器环境（安装 minicheck + transformers） 

---

## 时间线调整

```
Day 2-3 (5/30-31): 并行开发（不变）
  - A: evidence 存储层 + MCP 工具
  - B: SKILL.md（⚠️ source_excerpt → required）
  - C: evidence table + verify 框架 + 🆕 调研 MiniCheck/DeBERTa
  - D: QQ connector

Roxy: 05-30 21:29:35
Day 4 (6/1): 首次联调
  - 全体集成，确保 Agent 记录 evidence（含 source_excerpt）
  - 晚间: 成员 C 完成 Phase 1 初版

Day 5 (6/2): 测试案例执行（含 Layer 2）
  - D 执行 5 个测试案例（含 1 个故意错误引用）
  - C 运行完整对比: baseline → Layer 1 → Layer 1+2

Day 6 (6/3): 报告撰写
  - 新增一节: "外部归因验证：排除智能体幻觉的实验分析"

Day 7 (6/4): 预演 + 终稿（不变）
```

---

## 更新后的提交清单


```
GroupXX_LLM_Project_Engineering.zip
├── report.pdf
├── evidence_materials.pdf
├── code/
│   ├── src/
│   │   ├── skills/evidence-track/SKILL.md       # ⚠️ source_excerpt → required
│   │   ├── deepscientist/artifact/service.py
│   │   ├── deepscientist/artifact/schemas.py
│   │   ├── deepscientist/artifact/evidence_table.py
│   │   ├── deepscientist/artifact/attribution_report.py  # 🆕
│   │   ├── deepscientist/mcp/server.py
│   │   ├── deepscientist/prompts/builder.py
│   │   └── deepscientist/quest/layout.py
│   ├── scripts/
│   │   ├── verify_evidence.py                    # ⚠️ 增加 Layer 2 调用入口
│   │   ├── before_after_compare.py               # ⚠️ 增加 Layer 2 维度指标
│   │   ├── external_attribution_verify.py        # 🆕
│   │   └── nli_model_loader.py                   # 🆕
│   ├── tests/
│   │   ├── test_evidence_tracking.py
│   │   └── test_external_attribution.py          # 🆕
│   ├── config/connectors.yaml
│   ├── test_cases/
│   ├── logs/
│   ├── requirements.txt
│   ├── requirements-layer2.txt                   # 🆕
│   └── README.md
```


---

## Layer 2 安装依赖 (`requirements-layer2.txt`)

```
# Phase 1: MiniCheck (推荐) 或 Vectara HHEM
minicheck>=0.1.0

# Phase 2: NLI 模型
transformers>=4.40.0
torch>=2.0.0  # CPU-only 即可

# 报告生成
pyyaml>=6.0
```

---

## 总结：PLAN.md → PLAN_append_layer2.md 的 delta

 方面  PLAN.md (Layer 1)  本修订版 (Layer 1 + 2)

----------------------------------------------
 | 验证深度   | 格式校验（EVD ID 存在 + level 匹配） | 格式校验 + 事实一致性（NLI 验证 claim ↔ source） |
 |--------|----------------------------|-------------------------------------|
 | 能否检测幻觉 | ❌ 不能                       | ✅ 能（Agent 说 supported 但 source 不支持） | 
 | 依赖智能体  | 是（Agent 自评 level）          | 否（独立于 Agent 的外部模型）                  |
 | 新增代码量  | —                          | ~400 行 Python + 测试                  |
 对成员 B 的影响  source_excerpt optional

Roxy: 05-30 21:29:39
source_excerpt **required** 
 对成员 C 的影响  —  新增 ~30% 工作量 
 服务器负载  低  Phase 1 在 CPU 上可运行（MiniCheck-Flan-T5, 780M） 
 报告亮点  证据覆盖率对比  证据覆盖率 + **幻觉检测率** + Agent-NLI 一致率 
```

---

以上就是完整的 PLAN_append_layer2.md 内容。你可以直接复制保存到 `projects/PLAN_append_layer2.md`。

核心要点回顾：

Roxy: 05-30 21:29:40
1. **Layer 1（PLAN.md 现有）** 只做引用格式校验，不验证事实
2. **Layer 2（新增）** 用 MiniCheck（Phase 1 快速筛选）+ DeBERTa-v3-mnli（Phase 2 精细验证）做独立的 claim↔source 事实一致性判断
3. **关键前置条件**：成员 B 必须把 `source_excerpt` 改成 required，否则 Layer 2 无米之炊
4. **展示亮点**：对比 Layer 1 only vs Layer 1+2，用数据证明独立外部验证层的必要性

Roxy 的回答并非总是准确无误，请理性判断。