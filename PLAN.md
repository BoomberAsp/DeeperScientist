# 方向 A：科研智能体证据链追踪模块 — 实现计划

> 基于 DeepScientist，选择方向 A（Engineering），为科研 Agent 增加证据链追踪模块。
> 小组人数：4 人。展示日期：2026.06.05，终稿日期：2026.06.08。

## 总体技术路线

基于 DeepScientist 现有架构，证据链追踪模块的改动集中在以下层面：

```
新增 Skill: evidence-track (evidence-track/SKILL.md)
    ↓ Prompt 注入证据追踪指令
Agent 在工具调用时记录 evidence entries (artifact.record)
    ↓ 结构化存储到 quest/artifacts/evidence/
报告生成时渲染 Evidence Table
    ↓ artifact.interact 输出到 QQ/WeChat
后处理验证脚本检查证据引用一致性
```

**不新增 MCP namespace**（遵循项目约束），而是：
- 在 `artifact` namespace 中使用 `artifact.record` 记录证据条目
- 新增 custom skill `evidence-track` 注入证据追踪行为指令
- 新增 `src/skills/evidence-track/SKILL.md` 定义证据追踪工作流

## 四人分工

### 成员 A：证据存储与 MCP 工具扩展（Backend）

**负责内容：**
1. 设计 evidence entry 数据 schema（JSON + Markdown 双格式）
2. 扩展 `ArtifactService`，新增 `record_evidence()` / `list_evidence()` / `get_evidence()` 方法
3. 在 MCP artifact server 中新增 `artifact.evidence_record` / `artifact.evidence_list` / `artifact.evidence_verify` 工具
4. 实现 evidence ID 自动生成（格式：`EVD-{run_id}-{seq}`）
5. 实现证据类型分类逻辑（supported / inferred / insufficient）的规则引擎

**关键文件：**
- `src/deepscientist/artifact/service.py` — 新增 evidence 方法
- `src/deepscientist/artifact/schemas.py` — 新增 evidence 相关 schema
- `src/deepscientist/mcp/server.py` — 注册 evidence MCP 工具
- `src/deepscientist/quest/layout.py` — 新增 `artifacts/evidence/` 目录

**Evidence 目录布局：**
```
quest_root/artifacts/evidence/
├── INDEX.md                # 统一索引表（Agent 自动维护）
├── EVD-001.md              # 单条证据详情（YAML frontmatter + Markdown 正文）
├── EVD-002.md
└── ...
```

**Evidence 条目 Schema（单文件 `EVD-xxx.md` frontmatter）：**
```yaml
---
evidence_id: "EVD-a1b2c3-001"
title: "BERT base GLUE results"
source_type: arxiv
source_location: "arxiv:1810.04805, section 3"
source_content_hash: "sha256:abc123..."
claim: "BERT base achieves 93.2% average accuracy on GLUE benchmark"
evidence_level: supported
tool_call_id: "call_xxx"
tool_invocation: "artifact.arxiv(paper_id='1810.04805', full_text=False)"
timestamp: "2026-05-29T10:00:00Z"
---
# EVD-a1b2c3-001: BERT base GLUE results

## Source Excerpt
> BERT base achieves 93.2% average accuracy on the GLUE benchmark...

## Claim
The model achieves 92.3% accuracy on benchmark X.

## Relationship to Claim
The source directly reports the accuracy number cited in the claim.
```

**INDEX.md 维护逻辑（由 `record_evidence()` 内部自动执行）：**
1. 写入 `artifacts/evidence/EVD-xxx.md`（详情文件）
2. 读取 `artifacts/evidence/INDEX.md`
3. 在 Evidence Records 表格中插入/更新对应行（按 evidence_id 幂等）
4. 更新 INDEX.md 顶部的 `Last updated` 时间戳

### 成员 B：Prompt 工程与 Agent 行为注入（Prompt/Skill）

**负责内容：**
1. 编写 `src/skills/evidence-track/SKILL.md`，定义证据追踪 skill 合约
2. 修改 `PromptBuilder`，在 prompt 中注入证据追踪指令块
3. 更新 `STAGE_MEMORY_PLAN`，添加 evidence 相关 memory kind
4. 编写 system prompt 补充片段：要求 agent 在每次工具调用后记录 evidence，输出时标注 evidence id
5. 调试 prompt，确保 agent 能正确区分三类证据并遵守输出格式

**关键文件：**
- `src/skills/evidence-track/SKILL.md` — 新建
- `src/deepscientist/prompts/builder.py` — 注入证据追踪 prompt block
- `src/prompts/system.md` — 可选补充 evidence 相关指令
- `src/prompts/contracts/evidence_tracking.md` — 新建证据追踪合约

**SKILL.md 核心指令要点：**
- 每次使用 `bash_exec`、`artifact.arxiv`、URL fetch 后，必须调用 `artifact.evidence_record`
- 生成结论时必须附带 evidence id
- 三类标注规则：`[EVD-xxx:supported]` / `[EVD-xxx:inferred]` / `[EVD-xxx:insufficient]`
- 无证据支持的陈述必须标记为 `[NO_EVIDENCE]`

### 成员 C：报告生成与对比分析（Report/Validation）

**负责内容：**
1. 实现 evidence table 渲染逻辑（Markdown 表格 + JSON 导出）
2. 编写后处理脚本：解析 agent 输出，提取所有 `[EVD-*]` 引用，与 evidence store 交叉验证
3. 实现扩展前后对比框架：
   - 无证据模块的 baseline 输出（5 个测试案例）
   - 有证据模块的输出（同 5 个案例）
   - 对比指标：evidence coverage、unsupported claim count、citation accuracy
4. 准备对比分析可视化（ASCII table 或简单图表）

**关键文件：**
- `src/deepscientist/artifact/evidence_table.py` — 新建，evidence table 渲染
- `scripts/verify_evidence.py` — 新建，交叉验证脚本
- `scripts/before_after_compare.py` — 新建，对比分析脚本
- `tests/test_evidence_tracking.py` — 新建，单元测试

**对比指标设计：**

| 指标 | 扩展前 | 扩展后 |
|------|--------|--------|
| 有证据支持的结论比例 | — | X% |
| 无来源引用数 | Y | Z |
| 错误引用数 | — | W |

### 成员 D：集成测试与 QQ/WeChat 演示（QA/Demo）

**负责内容：**
1. 端到端集成：确保 A、B、C 的工作产物能串联运行
2. 设计并执行 5 个测试案例（含 1 个边界/失败案例）：
   - Case 1: 论文 PDF 分析（正常案例）
   - Case 2: 网页文本总结（正常案例）
   - Case 3: 实验日志分析（正常案例）
   - Case 4: ArXiv 论文对比（正常案例）
   - Case 5: 信息不足 / 故意错误引用（边界案例）
3. QQ 真实交互部署：基于 DeepScientist 已有的 QQ connector，配置并测试完整对话流
4. 收集截图、日志、录制 screen recording
5. 撰写项目报告（PDF）+ 证据材料（PDF）

**关键配置：**
- `connectors.yaml` 中 QQ connector 配置
- Quest 创建脚本 + 测试输入材料准备
- 演示脚本（确保现场 10 分钟内走完完整流程）

## 时间线（7 天，5.29 → 6.5 展示）

```
Day 1 (5/29 周四): 全体对齐
  - 一起确认 evidence schema 设计（A 主导）
  - 统一 evidence id 格式、输出标注规范
  - 每个人搭建本地开发环境，确认 DeepScientist 可运行

Day 2-3 (5/30-31 周五-六): 并行开发
  - A: 完成 evidence 存储层 + MCP 工具
  - B: 完成 SKILL.md + prompt 注入（用 A 的工具进行联调）
  - C: 完成 evidence table 渲染 + 验证脚本框架
  - D: 配置 QQ connector，准备测试材料

Day 4 (6/1 周日): 首次联调
  - 全体集成测试，确保 agent 能通过 QQ 接收任务、记录证据、输出 evidence table
  - 修复集成问题

Day 5 (6/2 周一): 测试案例执行
  - D 执行 5 个测试案例，收集日志和截图
  - C 执行 before/after 对比，输出对比报告
  - A/B 修复测试中发现的问题

Day 6 (6/3 周二): 报告撰写
  - D 主导项目报告撰写（问题定义、系统设计、测试案例、分析）
  - C 提供对比数据
  - A/B 提供技术方案说明
  - 制作 Poster（120cm × 80cm）

Day 7 (6/4 周三): 预演 + 终稿
  - 完整彩排一次 10 分钟展示流程
  - 打印 Poster、打印 Report Draft 5 份
  - 打包提交压缩包
  - 修正预演中发现的问题
```

## 关键风险与缓解

| 风险 | 缓解 |
|------|------|
| QQ connector 配置复杂 | D 第 1 天就开始验证 QQ 通路，用最简单的 echo 任务先跑通 |
| Agent 不遵守证据标注格式 | B 在 prompt 中使用 few-shot examples + 强制 JSON/Markdown 输出格式 |
| 时间不足完成全部代码 | 优先确保 `artifact.evidence_record` + prompt 指令 + 手动验证可跑通，evidence table 可接受半自动脚本 |
| DeepScientist 环境问题 | A 第 1 天全组统一运行 `ds doctor` 确保环境一致 |

## 提交清单

```
GroupXX_LLM_Project_Engineering.zip
├── report.pdf                              # 项目报告（6-10 页）
├── evidence_materials.pdf                  # 证据材料（截图、测试案例、日志）
└── code/                                   # 完整工程实现
    ├── src/
    │   ├── skills/evidence-track/SKILL.md
    │   ├── deepscientist/artifact/service.py      # (修改)
    │   ├── deepscientist/artifact/schemas.py       # (修改)
    │   ├── deepscientist/artifact/evidence_table.py # (新增)
    │   ├── deepscientist/mcp/server.py             # (修改)
    │   ├── deepscientist/prompts/builder.py         # (修改)
    │   └── deepscientist/quest/layout.py            # (修改)
    ├── scripts/
    │   ├── verify_evidence.py
    │   └── before_after_compare.py
    ├── tests/
    │   └── test_evidence_tracking.py
    ├── config/
    │   └── connectors.yaml                 # QQ 配置
    ├── test_cases/                         # 5 个测试案例的输入材料
    ├── logs/                               # 运行日志
    └── README.md                           # 安装与启动说明
```

## 证据链记忆管理：INDEX.md 索引表设计

### 设计动机

DeepScientist 现有的文件管理机制存在两个问题：

1. **检索靠遍历** — `memory.search()` 和 `memory.list_recent()` 逐个解析文件的 YAML frontmatter，没有统一索引。每次检索相当于实时构建索引视图，效率低且缺乏全局视野。
2. **元数据分散** — 会话工作区中的论文、图表、bash 日志、工具调用记录、evidence 条目分散在 `artifacts/`、`memory/`、`.ds/bash_exec/`、`literature/` 等多个目录，没有统一的入口可以一览所有文件。

**INDEX.md 的核心思想**：在会话工作区维护一张统一的文件索引表，由 Agent 在每次写入文件时自动更新，作为检索的"热缓存"。

### DeepScientist 现有机制 vs INDEX.md 模式

| 特性 | DeepScientist 现状 | INDEX.md 模式 |
|------|-------------------|---------------|
| 统一索引 | 无，分散在各目录 | 一张 INDEX.md，集中索引 |
| 元数据存储 | 文件内嵌 YAML frontmatter | INDEX.md 表格行 + frontmatter 双存 |
| 检索方式 | memory.search() 遍历解析 | 先读 INDEX.md 定位，再读具体文件 |
| 概览信息 | frontmatter 中的 title/tags/kind | INDEX.md 表格：标题、位置、格式、摘要 |
| 文件间关系 | 无（artifact 的 foundation_ref 有部分关联） | INDEX.md 可标注依赖关系 |
| 由 Agent 生成 | 是（memory.write / artifact.record） | 是（自动追加 INDEX.md 行） |

### INDEX.md 格式

```markdown
# Session File Index

> Auto-maintained by evidence-track skill. Last updated: 2026-05-29T10:15:00

## Evidence Records
| Evidence ID | Title | Source Type | Source Location | Evidence Level | Summary | File Path | Timestamp |
|-------------|-------|-------------|-----------------|----------------|---------|-----------|-----------|
| EVD-001 | BERT paper results | arxiv | arxiv:1810.04805 §3 | supported | BERT base 93.2% on GLUE | artifacts/evidence/EVD-001.md | 2026-05-29T10:00 |
| EVD-002 | Runtime benchmark | code_output | bash-xxx/log.txt L42-58 | supported | Inference 12ms on V100 | artifacts/evidence/EVD-002.md | 2026-05-29T10:05 |
| EVD-003 | Scaling extrapolation | inferred | — | inferred | Extrapolated from trend | artifacts/evidence/EVD-003.md | 2026-05-29T10:08 |
| EVD-004 | Missing reference | — | — | insufficient | No source found | artifacts/evidence/EVD-004.md | 2026-05-29T10:10 |

## Input Materials
| File ID | Title | Format | Source | Summary | File Path | Timestamp |
|---------|-------|--------|--------|---------|-----------|-----------|
| MAT-001 | Attention Is All You Need | PDF | arxiv:1706.03762 | Transformer architecture paper | literature/attention.pdf | 2026-05-29T09:00 |
| MAT-002 | GLUE Benchmark Results | CSV | experiment output | Accuracy across 9 tasks | experiments/main/glue_results.csv | 2026-05-29T09:30 |

## Tool Call Records
| Call ID | Tool | Mode | Summary | Exit Code | Log Path | Timestamp |
|---------|------|------|---------|-----------|----------|-----------|
| CALL-001 | bash_exec | await | pip install transformers | 0 | .ds/bash_exec/bash-xxx/log.txt | 2026-05-29T09:15 |
| CALL-002 | bash_exec | await | python run_eval.py | 0 | .ds/bash_exec/bash-yyy/log.txt | 2026-05-29T09:45 |
```

### Agent 维护 INDEX.md 的流程

```
1. Agent 执行工具调用（bash_exec / arxiv / URL fetch / memory.write）
2. 工具返回结果后，Agent 调用 artifact.evidence_record(...) 写入单条 evidence
3. evidence_record() 内部做两件事：
   a. 写入 evidence 详情文件（artifacts/evidence/EVD-xxx.md）
   b. 追加/更新 INDEX.md 中对应表格的一行（幂等，按 evidence_id 去重）
4. 用户上传论文/数据时，Agent 记录到 INDEX.md 的 Input Materials 表格
5. Agent 生成报告时，先读 INDEX.md 获取证据全景，再按需读具体文件
6. Agent 输出结论时标注：引用 INDEX.md 中的 Evidence ID
```

### 与 DeepScientist 现有系统的关系

```
INDEX.md（新增，统一索引层）
├── artifacts/evidence/    ← 证据详情文件（新增）
├── artifacts/             ← 现有，研究工件
├── memory/                ← 现有，长期记忆卡片（保留 frontmatter）
├── literature/            ← 现有，文献笔记
├── .ds/events.jsonl       ← 现有，完整事件日志（保留，作为 ground truth）
└── .ds/bash_exec/         ← 现有，shell 会话日志
```

**INDEX.md 不替代现有机制，而是叠加索引层**：
- `events.jsonl` 仍是完整的 ground truth 事件日志
- memory frontmatter 仍做细粒度元数据
- INDEX.md 仅做粗粒度检索入口，避免每次遍历目录

### 证据链工具

| 工具 | 所属 namespace | 功能 |
|------|---------------|------|
| `artifact.evidence_record` | artifact | 写入 evidence 条目 + 更新 INDEX.md |
| `artifact.evidence_update` | artifact | 更新已有 evidence 条目元数据（含 retracted 标记） |
| `artifact.evidence_list` | artifact | 读取 INDEX.md，返回证据列表 |
| `artifact.evidence_get` | artifact | 按 evidence_id 读取单条 evidence 详情 |
| `artifact.evidence_verify` | artifact | 交叉验证：检查 agent 输出中的 [EVD-*] 引用是否在 INDEX.md 中存在，对应的 evidence_level 是否正确 |
| `artifact.index_snapshot` | artifact | 返回 INDEX.md 的完整解析结果（JSON 格式），供验证脚本使用 |

## 成员 A 详细实现流程：证据存储与 MCP 工具扩展

### 概览

以下按文件列出所有修改步骤，每个步骤包含具体代码位置、修改内容和验证方法。实现顺序按依赖关系排列：先 schema → 再存储层 → 最后 MCP 工具注册。

涉及文件：

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/deepscientist/quest/layout.py` | 修改 | 新增 `artifacts/evidence/` 目录 |
| `src/deepscientist/artifact/schemas.py` | 修改 | 新增 `evidence` artifact kind 和相关常量 |
| `src/deepscientist/artifact/service.py` | 修改 | 新增 5 个 evidence 方法 |
| `src/deepscientist/mcp/server.py` | 修改 | 注册 5 个 MCP 工具到 artifact namespace |

---

### Step 1: 注册 Quest 目录 (`quest/layout.py`)

**文件**: `src/deepscientist/quest/layout.py`  
**位置**: `QUEST_DIRECTORIES` 元组末尾

**操作**: 在 `QUEST_DIRECTORIES` 中新增一行：

```python
# 在 "artifacts/decisions" 附近插入
"artifacts/evidence",
```

**验证**: 创建新 quest 后，确认 `quest_root/artifacts/evidence/` 目录自动生成。

---

### Step 2: 注册 Artifact Kind (`artifact/schemas.py`)

**文件**: `src/deepscientist/artifact/schemas.py`

#### 2.1 新增 evidence 到 ARTIFACT_DIRS

**位置**: `ARTIFACT_DIRS` 字典，新增：

```python
ARTIFACT_DIRS = {
    # ... 现有条目 ...
    "evidence": "evidence",     # ← 新增
}
```

#### 2.2 新增 evidence 相关常量

**位置**: 文件末尾，新增以下常量：

```python
# Evidence Chain Tracking

EVIDENCE_SOURCE_TYPES = {
    "arxiv",
    "pdf",
    "url",
    "code_output",
    "tool_call",
    "bash_log",
    "memory_card",
    "user_upload",
    "experiment_result",
    "dataset",
    "literature_review",
}

EVIDENCE_LEVELS = {
    "supported",      # 证据直接支持该结论
    "inferred",       # 模型合理推断，但未被直接证明
    "insufficient",   # 证据不足或需要进一步验证
    "retracted",      # 证据曾被记录但后续发现错误或不再有效（保留审计线索，不物理删除）
}

EVIDENCE_ACTIONS = {
    "record",
    "update",
    "verify",
}


def validate_evidence_payload(payload: dict) -> list[str]:
    """校验 evidence 条目 payload。"""
    errors: list[str] = []
    source_type = str(payload.get("source_type") or "").strip()
    if source_type and source_type not in EVIDENCE_SOURCE_TYPES:
        errors.append(
            f"Unknown evidence source_type: {source_type}. "
            f"Allowed: {', '.join(sorted(EVIDENCE_SOURCE_TYPES))}"
        )
    evidence_level = str(payload.get("evidence_level") or "").strip()
    if evidence_level and evidence_level not in EVIDENCE_LEVELS:
        errors.append(
            f"Unknown evidence_level: {evidence_level}. "
            f"Allowed: {', '.join(sorted(EVIDENCE_LEVELS))}"
        )
    claim = str(payload.get("claim") or "").strip()
    if not claim:
        errors.append("Evidence record requires `claim`.")
    content_hash = str(payload.get("source_content_hash") or "").strip()
    if content_hash and not content_hash.startswith("sha256:"):
        errors.append(
            "source_content_hash must use 'sha256:<hex>' format when provided."
        )
    return errors
```

#### 2.3 修改 `validate_artifact_payload`

**位置**: `validate_artifact_payload()` 函数中 `if kind not in ARTIFACT_DIRS` 之前，新增 evidence 分支：

```python
def validate_artifact_payload(payload: dict) -> list[str]:
    errors: list[str] = []
    kind = payload.get("kind")
    if is_science_kind(kind):
        return validate_science_payload(payload)
    # ↓ 新增
    if kind == "evidence":
        return validate_evidence_payload(payload)
    # ↑ 新增
    if kind not in ARTIFACT_DIRS:
        ...
```

#### 2.4 新增 guidance

**位置**: `guidance_for_kind()` 函数内部，新增：

```python
if kind == "evidence":
    return "Evidence recorded. Link to conclusions using [EVD-xxx:level] notation."
```

**验证**: 运行 `python3 -c "from src.deepscientist.artifact.schemas import *; print('ok')"` 确认无语法错误。

---

### Step 3: 实现 Evidence 存储层 (`artifact/service.py`)

**文件**: `src/deepscientist/artifact/service.py`  
**位置**: `ArtifactService` 类内部（约在 `record()` 方法之后）

#### 3.1 新增 import

在文件顶部 import 区域新增：

```python
from ..memory.frontmatter import dump_markdown_document, load_markdown_document
```

> 注：
> - `load_markdown_document` 和 `sha256_text` 可能已在现有 import 中。检查文件顶部 import 区域的现有导入语句，确认是否已导入。
> - `sha256_text` 用于 `_quest_run_id` 的 fallback 路径哈希。

#### 3.2 `record_evidence()` — 写入证据条目 + 更新 INDEX.md

```python
def record_evidence(
    self,
    quest_root: Path,
    *,
    title: str = "",
    source_type: str = "",
    source_location: str = "",
    source_content_hash: str = "",
    claim: str = "",
    evidence_level: str = "supported",
    tool_call_id: str = "",
    tool_invocation: str = "",
    source_excerpt: str = "",
    claim_relation: str = "",
    evidence_id: str | None = None,
) -> dict:
    """
    写入一条 evidence 详情文件（Markdown + YAML frontmatter），并同步更新 INDEX.md。

    写入顺序（保证一致性）：
    1. 先写 evidence 详情文件
    2. 再追加 events.jsonl
    3. 最后更新 INDEX.md（带文件锁 fcntl.LOCK_EX）

    如果 INDEX.md 更新失败，记录 evidence.dirty_index 事件供后续修复，
    但 evidence 文件本身已经持久化，不会丢失。

    返回 {"ok": True/False, "evidence_id": ..., "path": ...}
    """
    evidence_root = ensure_dir(quest_root / "artifacts" / "evidence")
    
    # 生成 evidence_id
    if not evidence_id:
        run_id = _quest_run_id(quest_root)
        existing = sorted(evidence_root.glob("EVD-*.md"))
        seq = len(existing) + 1
        evidence_id = f"EVD-{run_id[:8]}-{seq:03d}"
    
    # 构建 frontmatter 元数据
    timestamp = utc_now()
    metadata = {
        "evidence_id": evidence_id,
        "title": title,
        "source_type": source_type,
        "source_location": source_location,
        "source_content_hash": source_content_hash,
        "claim": claim,
        "evidence_level": evidence_level,
        "tool_call_id": tool_call_id,
        "tool_invocation": tool_invocation,
        "timestamp": timestamp,
    }
    
    # 构建 Markdown 正文
    body_parts = [f"# {evidence_id}: {title or claim[:80]}", ""]
    if source_excerpt:
        body_parts.append("## Source Excerpt")
        body_parts.append(f"> {source_excerpt}")
        body_parts.append("")
    if claim:
        body_parts.append("## Claim")
        body_parts.append(claim)
        body_parts.append("")
    if claim_relation:
        body_parts.append("## Relationship to Claim")
        body_parts.append(claim_relation)
        body_parts.append("")
    
    body = "\n".join(body_parts)
    
    # Step 1: 写入 evidence 详情文件
    evidence_path = evidence_root / f"{evidence_id}.md"
    evidence_path.write_text(dump_markdown_document(metadata, body), encoding="utf-8")

    # Step 2: 记录到 events.jsonl（在 INDEX.md 更新之前，确保事件日志完整）
    append_jsonl(
        quest_root / ".ds" / "events.jsonl",
        {
            "type": "evidence.recorded",
            "evidence_id": evidence_id,
            "source_type": source_type,
            "evidence_level": evidence_level,
            "claim": claim[:200],
            "timestamp": timestamp,
        },
    )

    # Step 3: 更新 INDEX.md（_upsert_index_row 内部处理 fcntl 锁和 dirty_index 事件）
    _upsert_index_row(
        evidence_root,
        quest_root=quest_root,
        evidence_id=evidence_id,
        title=title,
        source_type=source_type,
        source_location=source_location,
        evidence_level=evidence_level,
        summary=(claim or title)[:120],
        file_path=str(evidence_path.relative_to(quest_root)),
        timestamp=timestamp,
    )
    
    return {
        "ok": True,
        "evidence_id": evidence_id,
        "path": str(evidence_path.relative_to(quest_root)),
        "evidence_level": evidence_level,
        "guidance": _format_evidence_guidance(evidence_id, evidence_level),
    }
```
#### 3.2b `update_evidence()` — 更新已有 evidence 条目元数据

当新信息补充或修正已有证据时使用（如提升 evidence_level、补充 source_location）。
更新后自动同步 INDEX.md。

```python
def update_evidence(
    self,
    quest_root: Path,
    *,
    evidence_id: str,
    title: str | None = None,
    source_type: str | None = None,
    source_location: str | None = None,
    source_content_hash: str | None = None,
    claim: str | None = None,
    evidence_level: str | None = None,
    source_excerpt: str | None = None,
    claim_relation: str | None = None,
) -> dict:
    """
    更新已有 evidence 条目。仅更新传入的非 None 字段。

    返回 {"ok": True/False, "evidence_id": ..., "path": ...}
    """
    evidence_root = quest_root / "artifacts" / "evidence"
    evidence_path = evidence_root / f"{evidence_id}.md"

    if not evidence_path.exists():
        return {"ok": False, "error": f"Evidence not found: {evidence_id}"}

    metadata, body = load_markdown_document(evidence_path)

    # 仅更新传入的非 None 字段
    updatable = {
        "title", "source_type", "source_location",
        "source_content_hash", "claim", "evidence_level",
    }
    for key in updatable:
        value = locals().get(key)
        if value is not None:
            metadata[key] = value

    metadata["updated_at"] = utc_now()

    # 重写正文（如果 source_excerpt 或 claim_relation 有更新）
    if source_excerpt is not None or claim_relation is not None:
        body_parts = [f"# {evidence_id}: {metadata.get('title') or metadata.get('claim', '')[:80]}", ""]
        excerpt = source_excerpt if source_excerpt is not None else metadata.get("source_excerpt", "")
        if excerpt:
            body_parts.append("## Source Excerpt")
            body_parts.append(f"> {excerpt}")
            body_parts.append("")
        claim_text = claim if claim is not None else metadata.get("claim", "")
        if claim_text:
            body_parts.append("## Claim")
            body_parts.append(claim_text)
            body_parts.append("")
        relation = claim_relation if claim_relation is not None else metadata.get("claim_relation", "")
        if relation:
            body_parts.append("## Relationship to Claim")
            body_parts.append(relation)
            body_parts.append("")
        body = "\n".join(body_parts)
        evidence_path.write_text(dump_markdown_document(metadata, body), encoding="utf-8")

    # 同步更新 INDEX.md
    _upsert_index_row(
        evidence_root,
        quest_root=quest_root,
        evidence_id=evidence_id,
        title=metadata.get("title", ""),
        source_type=metadata.get("source_type", ""),
        source_location=metadata.get("source_location", ""),
        evidence_level=metadata.get("evidence_level", ""),
        summary=(metadata.get("claim") or metadata.get("title", ""))[:120],
        file_path=str(evidence_path.relative_to(quest_root)),
        timestamp=metadata.get("updated_at", utc_now()),
    )

    # 记录更新事件
    append_jsonl(
        quest_root / ".ds" / "events.jsonl",
        {
            "type": "evidence.updated",
            "evidence_id": evidence_id,
            "updated_fields": [
                k for k in updatable if locals().get(k) is not None
            ],
            "timestamp": utc_now(),
        },
    )

    return {
        "ok": True,
        "evidence_id": evidence_id,
        "path": str(evidence_path.relative_to(quest_root)),
        "evidence_level": metadata.get("evidence_level", ""),
        "guidance": _format_evidence_guidance(evidence_id, metadata.get("evidence_level", "")),
    }
```

#### 3.3 `list_evidence()` — 读取 INDEX.md 返回证据列表

```python
def list_evidence(
    self,
    quest_root: Path,
    *,
    evidence_level: str | None = None,
    source_type: str | None = None,
) -> dict:
    """
    解析 INDEX.md，返回 evidence 列表。支持按 evidence_level 和 source_type 过滤。
    """
    index_path = quest_root / "artifacts" / "evidence" / "INDEX.md"
    if not index_path.exists():
        return {"ok": True, "evidence_records": [], "total": 0, "index_exists": False}
    
    rows = _parse_index_table(index_path, section="Evidence Records")
    
    # 全局分布（过滤前），保留完整统计信息
    all_rows = rows
    by_level_global = {
        level: len([r for r in all_rows if r.get("evidence_level") == level])
        for level in ("supported", "inferred", "insufficient", "retracted")
    }
    
    if evidence_level:
        rows = [r for r in rows if r.get("evidence_level") == evidence_level]
    if source_type:
        rows = [r for r in rows if r.get("source_type") == source_type]
    
    return {
        "ok": True,
        "evidence_records": rows,
        "total": len(rows),
        "total_overall": len(all_rows),
        "index_exists": True,
        "by_level": by_level_global,
    }
```

#### 3.4 `get_evidence()` — 按 ID 读取单条证据详情

```python
def get_evidence(self, quest_root: Path, evidence_id: str) -> dict:
    """
    读取单条 evidence 详情文件，返回 frontmatter + body。
    """
    evidence_path = quest_root / "artifacts" / "evidence" / f"{evidence_id}.md"
    if not evidence_path.exists():
        return {"ok": False, "error": f"Evidence not found: {evidence_id}"}
    
    metadata, body = load_markdown_document(evidence_path)
    return {
        "ok": True,
        "evidence_id": evidence_id,
        "metadata": metadata,
        "body": body,
        "path": str(evidence_path.relative_to(quest_root)),
    }
```

#### 3.5 `verify_evidence_claims()` — 交叉验证 agent 输出中的证据引用

```python
def verify_evidence_claims(
    self,
    quest_root: Path,
    *,
    agent_output_text: str,
) -> dict:
    """
    解析 agent 输出文本中的 [EVD-xxx:level] 引用，与 INDEX.md 交叉验证。
    
    返回：verified（引用存在且 level 一致）、mismatched（引用存在但 level 不一致）、
          missing（引用不存在于 INDEX.md）、unreferenced（INDEX.md 中有但未被引用的证据）
    """
    import re
    
    index_path = quest_root / "artifacts" / "evidence" / "INDEX.md"
    if not index_path.exists():
        return {"ok": False, "error": "INDEX.md not found"}
    
    # 提取 agent 输出中的所有 evidence 引用（跳过 markdown 代码块内的匹配，防止误报）
    ref_pattern = re.compile(r'\[(EVD-[^\]:]+)(?::([^\]]+))?\]')
    code_block_pattern = re.compile(r'```')
    agent_refs: dict[str, str | None] = {}

    in_code_block = False
    for raw_line in agent_output_text.split("\n"):
        if code_block_pattern.search(raw_line):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        for match in ref_pattern.finditer(raw_line):
            evd_id = match.group(1)
            level = match.group(2) if match.group(2) else None
            agent_refs[evd_id] = level
    
    # 读取 INDEX.md 中的 evidence 条目
    index_rows = _parse_index_table(index_path, section="Evidence Records")
    index_ids: dict[str, str] = {r.get("evidence_id", ""): r.get("evidence_level", "") 
                                   for r in index_rows if r.get("evidence_id")}
    
    # 交叉验证
    verified = []
    mismatched = []
    missing = []
    retracted_but_cited = []
    for evd_id, claimed_level in agent_refs.items():
        if evd_id in index_ids:
            actual_level = index_ids[evd_id]
            if actual_level == "retracted":
                retracted_but_cited.append({
                    "evidence_id": evd_id,
                    "claimed_level": claimed_level,
                    "warning": "This evidence has been retracted and should not be cited.",
                })
            elif claimed_level and claimed_level != actual_level:
                mismatched.append({
                    "evidence_id": evd_id,
                    "claimed_level": claimed_level,
                    "actual_level": actual_level,
                })
            else:
                verified.append(evd_id)
        else:
            missing.append(evd_id)
    
    unreferenced = [eid for eid in index_ids if eid not in agent_refs]
    
    total = len(agent_refs)
    return {
        "ok": True,
        "total_references": total,
        "verified": verified,
        "verified_count": len(verified),
        "mismatched": mismatched,
        "mismatched_count": len(mismatched),
        "missing": missing,
        "missing_count": len(missing),
        "retracted_but_cited": retracted_but_cited,
        "retracted_but_cited_count": len(retracted_but_cited),
        "unreferenced": unreferenced,
        "unreferenced_count": len(unreferenced),
        "verification_rate": f"{len(verified) / total * 100:.1f}%" if total else "N/A",
    }
```

#### 3.5b `index_snapshot()` — 返回 INDEX.md 的结构化快照

```python
def index_snapshot(self, quest_root: Path) -> dict:
    """
    解析 INDEX.md 全部三个表格，返回结构化 JSON 快照。

    供 evidence_index_snapshot MCP 工具和验证脚本使用。
    """
    index_path = quest_root / "artifacts" / "evidence" / "INDEX.md"
    if not index_path.exists():
        return {"ok": False, "error": "INDEX.md not found"}

    evidence_records = _parse_index_table(index_path, section="Evidence Records")
    input_materials = _parse_index_table(index_path, section="Input Materials")
    tool_calls = _parse_index_table(index_path, section="Tool Call Records")

    return {
        "ok": True,
        "evidence_records": evidence_records,
        "evidence_total": len(evidence_records),
        "input_materials": input_materials,
        "input_materials_total": len(input_materials),
        "tool_calls": tool_calls,
        "tool_calls_total": len(tool_calls),
    }
```

#### 3.6 辅助函数（放在 `ArtifactService` 类外部，文件底部）

```python
# ========== Evidence INDEX.md 辅助函数 ==========

def _ensure_index_md(evidence_root: Path) -> Path:
    """确保 INDEX.md 存在，不存在则创建骨架。"""
    index_path = evidence_root / "INDEX.md"
    if not index_path.exists():
        index_path.write_text(
            "# Session File Index\n\n"
            "> Auto-maintained by evidence-track skill. Last updated: \n\n"
            "## Evidence Records\n"
            "| Evidence ID | Title | Source Type | Source Location | Evidence Level | Summary | File Path | Timestamp |\n"
            "|-------------|-------|-------------|-----------------|----------------|---------|-----------|-----------|\n\n"
            "## Input Materials\n"
            "| File ID | Title | Format | Source | Summary | File Path | Timestamp |\n"
            "|---------|-------|--------|--------|---------|-----------|-----------|\n\n"
            "## Tool Call Records\n"
            "| Call ID | Tool | Mode | Summary | Exit Code | Log Path | Timestamp |\n"
            "|---------|------|------|---------|-----------|----------|-----------|\n",
            encoding="utf-8",
        )
    return index_path


def _upsert_index_row(
    evidence_root: Path,
    *,
    quest_root: Path,
    evidence_id: str,
    title: str,
    source_type: str,
    source_location: str,
    evidence_level: str,
    summary: str,
    file_path: str,
    timestamp: str,
) -> None:
    """在 INDEX.md 的 Evidence Records 表格中原子插入或更新一行（按 evidence_id 去重）。

    使用 fcntl.LOCK_EX 保护「读取 → 内存 upsert → 写回」全过程，
    防止并发写入导致丢失更新。

    非 POSIX 平台（Windows）降级为无锁 write_text，依赖 CPython GIL 的单线程写保护。
    """
    index_path = _ensure_index_md(evidence_root)

    new_row = {
        "Evidence ID": evidence_id,
        "Title": title[:80],
        "Source Type": source_type,
        "Source Location": source_location or "—",
        "Evidence Level": evidence_level,
        "Summary": summary[:120],
        "File Path": file_path,
        "Timestamp": timestamp,
    }

    import io

    try:
        import fcntl

        with open(index_path, "r+", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                existing_text = f.read()
                rows = _parse_index_table_text(existing_text, section="Evidence Records")

                # 内存 upsert
                replaced = False
                for i, row in enumerate(rows):
                    if row.get("Evidence ID") == evidence_id:
                        rows[i] = new_row
                        replaced = True
                        break
                if not replaced:
                    rows.append(new_row)

                # 渲染并写回
                new_text = _render_index_md_text(existing_text, evidence_records=rows, timestamp=timestamp)
                f.seek(0)
                f.truncate()
                f.write(new_text)
                f.flush()
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except (ImportError, OSError, io.UnsupportedOperation):
        # 非 POSIX 平台或文件不可 seek 时的降级路径
        existing_text = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
        rows = _parse_index_table_text(existing_text, section="Evidence Records")

        replaced = False
        for i, row in enumerate(rows):
            if row.get("Evidence ID") == evidence_id:
                rows[i] = new_row
                replaced = True
                break
        if not replaced:
            rows.append(new_row)

        new_text = _render_index_md_text(existing_text, evidence_records=rows, timestamp=timestamp)
        index_path.write_text(new_text, encoding="utf-8")

        # 降级路径仍记录 dirty_index 便于事后审计
        try:
            append_jsonl(
                quest_root / ".ds" / "events.jsonl",
                {
                    "type": "evidence.dirty_index",
                    "evidence_id": evidence_id,
                    "reason": "INDEX.md updated without fcntl lock — concurrent writes possible",
                    "timestamp": utc_now(),
                },
            )
        except Exception:
            pass


def _render_index_md_text(
    existing_text: str,
    *,
    evidence_records: list[dict[str, str]],
    timestamp: str,
) -> str:
    """基于现有文本重新渲染 INDEX.md，替换 Evidence Records 表格区域。

    保留 Input Materials、Tool Call Records 等其他 section 不变。
    返回完整的 INDEX.md 文本，不直接写文件（由调用方负责写入）。
    """
    lines = existing_text.split("\n")

    headers = [
        "Evidence ID", "Title", "Source Type", "Source Location",
        "Evidence Level", "Summary", "File Path", "Timestamp",
    ]

    # 构建 Evidence Records 表格
    table_lines = ["## Evidence Records"]
    table_lines.append("| " + " | ".join(headers) + " |")
    table_lines.append("|" + "|".join("---" for _ in headers) + "|")
    for row in evidence_records:
        values = [str(row.get(h, "")) for h in headers]
        table_lines.append("| " + " | ".join(values) + " |")
    table_lines.append("")

    # 替换或追加 Evidence Records section
    skip_until_next_section = False
    result_lines = []
    replaced = False

    for line in lines:
        if line.startswith("## Evidence Records"):
            skip_until_next_section = True
            if not replaced:
                result_lines.extend(table_lines)
                replaced = True
            continue
        if skip_until_next_section and line.startswith("## "):
            skip_until_next_section = False
            result_lines.append(line)
            continue
        if skip_until_next_section:
            continue
        result_lines.append(line)

    if not replaced:
        result_lines.extend(table_lines)

    # 更新 Last updated 时间戳（正则替换，容忍模板格式微调）
    import re
    final_text = "\n".join(result_lines)
    final_text = re.sub(
        r"(> Auto-maintained by evidence-track skill\. Last updated:).*",
        rf"\1 {timestamp}",
        final_text,
    )

    return final_text


def _parse_index_table_text(text: str, *, section: str) -> list[dict[str, str]]:
    """解析 INDEX.md 文本中指定 section 的表格，返回 dict 列表。

    这是 _parse_index_table 的纯文本版本，不依赖文件 I/O。
    _upsert_index_row() 在 fcntl 锁内直接调用此函数以避免死锁。
    """
    lines = text.split("\n")

    in_target_section = False
    headers: list[str] = []
    rows: list[dict[str, str]] = []

    for line in lines:
        if line.startswith(f"## {section}"):
            in_target_section = True
            continue
        if in_target_section and line.startswith("## "):
            break
        if in_target_section and line.startswith("| ") and not headers:
            headers = [h.strip() for h in line.split("|")[1:-1]]
            continue
        if in_target_section and line.startswith("|-"):
            continue
        if in_target_section and line.startswith("| ") and headers:
            values = [v.strip() for v in line.split("|")[1:-1]]
            if len(values) == len(headers):
                rows.append(dict(zip(headers, values)))

    return rows


def _parse_index_table(index_path: Path, *, section: str) -> list[dict[str, str]]:
    """解析 INDEX.md 中指定 section 的表格（文件 I/O 便捷封装）。

    内部委托给 _parse_index_table_text() 完成实际解析。
    """
    if not index_path.exists():
        return []
    return _parse_index_table_text(index_path.read_text(encoding="utf-8"), section=section)


def _format_evidence_guidance(evidence_id: str, evidence_level: str) -> str:
    """生成 evidence record 后的指导信息。"""
    guidance_map = {
        "supported": (
            f"Evidence {evidence_id} recorded as [supported]. "
            f"Reference in conclusions as [{evidence_id}:supported]."
        ),
        "inferred": (
            f"Evidence {evidence_id} recorded as [inferred]. "
            f"This means the conclusion is a reasonable extrapolation but not directly proven by the source. "
            f"Reference as [{evidence_id}:inferred] and consider adding corroborating evidence."
        ),
        "insufficient": (
            f"Evidence {evidence_id} recorded as [insufficient]. "
            f"The source does not provide adequate support. "
            f"Reference as [{evidence_id}:insufficient] and flag this gap to the user."
        ),
        "retracted": (
            f"Evidence {evidence_id} has been retracted. "
            f"The original record was found to be incorrect or misleading. "
            f"Do NOT cite this evidence. Use [{evidence_id}:retracted] only to document why it was withdrawn."
        ),
    }
    return guidance_map.get(evidence_level, f"Evidence {evidence_id} recorded.")


def _quest_run_id(quest_root: Path) -> str:
    """从 quest 的 runtime_state 中获取当前 run_id。

    若无法获取，使用 quest_root 路径的短哈希作为 fallback，
    确保 evidence ID 至少可追溯到具体 quest。
    """
    try:
        state = read_json(quest_root / ".ds" / "runtime_state.json", {})
        run_id = str(state.get("last_run_id") or "").strip()
        if run_id:
            return run_id
    except Exception:
        pass
    # fallback: 使用 quest_root 路径哈希，保证跨 run 可追溯
    return sha256_text(str(quest_root.resolve()))[:8]
```

**验证**: 在 Python 交互环境中手动调用 `record_evidence()` 和 `list_evidence()`，确认 INDEX.md 正确生成和更新。

---

### Step 4: 注册 MCP 工具 (`mcp/server.py`)

**文件**: `src/deepscientist/mcp/server.py`  
**位置**: `build_artifact_server()` 函数内部，在现有 `evidence_record` 注册之后。

#### 4.1 `artifact.evidence_record`

```python
    @server.tool(
        name="evidence_record",
        description=(
            "Record an evidence entry for the evidence chain tracking module. "
            "Call this EVERY TIME you complete a tool invocation (bash_exec, arxiv, URL fetch, memory.read, etc.) "
            "that yields factual claims. "
            "Each entry links a claim to its source and assigns an evidence level: "
            "supported (source directly proves the claim), "
            "inferred (reasonable extrapolation but not directly proven), "
            "insufficient (source is inadequate to support the claim)."
        ),
    )
    def evidence_record(
        title: str = "",
        source_type: str = "",
        source_location: str = "",
        source_content_hash: str = "",
        claim: str = "",
        evidence_level: str = "supported",
        tool_call_id: str = "",
        tool_invocation: str = "",
        source_excerpt: str = "",
        claim_relation: str = "",
    ) -> dict[str, Any]:
        return service.record_evidence(
            context.require_quest_root(),
            title=title,
            source_type=source_type,
            source_location=source_location,
            source_content_hash=source_content_hash,
            claim=claim,
            evidence_level=evidence_level,
            tool_call_id=tool_call_id,
            tool_invocation=tool_invocation,
            source_excerpt=source_excerpt,
            claim_relation=claim_relation,
        )
```

#### 4.2 `artifact.evidence_list`

```python
    @server.tool(
        name="evidence_list",
        description=(
            "List all evidence records from INDEX.md. "
            "Use before generating reports to get an overview of available evidence. "
            "Can filter by evidence_level (supported/inferred/insufficient) or source_type."
        ),
        annotations=_read_only_tool_annotations(title="List evidence records"),
    )
    def evidence_list(
        evidence_level: str | None = None,
        source_type: str | None = None,
    ) -> dict[str, Any]:
        return service.list_evidence(
            context.require_quest_root(),
            evidence_level=evidence_level,
            source_type=source_type,
        )
```

#### 4.3 `artifact.evidence_get`

```python
    @server.tool(
        name="evidence_get",
        description=(
            "Read a single evidence record by evidence_id (e.g., 'EVD-a1b2c3-001'). "
            "Returns the full frontmatter metadata and body."
        ),
        annotations=_read_only_tool_annotations(title="Get evidence detail"),
    )
    def evidence_get(evidence_id: str = "") -> dict[str, Any]:
        if not evidence_id.strip():
            return {"ok": False, "error": "evidence_id is required"}
        return service.get_evidence(
            context.require_quest_root(),
            evidence_id=evidence_id.strip(),
        )
```

#### 4.3b `artifact.evidence_update`

```python
    @server.tool(
        name="evidence_update",
        description=(
            "Update an existing evidence entry's metadata. "
            "Use this when new information supplements or corrects a previously recorded evidence entry "
            "(e.g., a more precise source location is found, or the evidence_level needs to be changed). "
            "Only the non-None fields will be updated. "
            "To mark evidence as invalid, set evidence_level='retracted' rather than deleting."
        ),
    )
    def evidence_update(
        evidence_id: str = "",
        title: str | None = None,
        source_type: str | None = None,
        source_location: str | None = None,
        source_content_hash: str | None = None,
        claim: str | None = None,
        evidence_level: str | None = None,
        source_excerpt: str | None = None,
        claim_relation: str | None = None,
    ) -> dict[str, Any]:
        if not evidence_id.strip():
            return {"ok": False, "error": "evidence_id is required"}
        return service.update_evidence(
            context.require_quest_root(),
            evidence_id=evidence_id.strip(),
            title=title,
            source_type=source_type,
            source_location=source_location,
            source_content_hash=source_content_hash,
            claim=claim,
            evidence_level=evidence_level,
            source_excerpt=source_excerpt,
            claim_relation=claim_relation,
        )
```

#### 4.4 `artifact.evidence_verify`

```python
    @server.tool(
        name="evidence_verify",
        description=(
            "Verify evidence references in agent-generated output text. "
            "Parses all [EVD-xxx:level] annotations and cross-checks them against INDEX.md. "
            "Returns verified, mismatched (level mismatch), missing (ID not in INDEX.md), "
            "and unreferenced (in INDEX.md but not cited) evidence."
        ),
    )
    def evidence_verify(agent_output_text: str = "") -> dict[str, Any]:
        if not agent_output_text.strip():
            return {"ok": False, "error": "agent_output_text is required"}
        return service.verify_evidence_claims(
            context.require_quest_root(),
            agent_output_text=agent_output_text,
        )
```

#### 4.5 `artifact.evidence_index_snapshot`

```python
    @server.tool(
        name="evidence_index_snapshot",
        description=(
            "Return the full INDEX.md content parsed as structured JSON. "
            "Use for debugging or programmatic evidence table generation."
        ),
        annotations=_read_only_tool_annotations(title="Snapshot evidence index"),
    )
    def evidence_index_snapshot() -> dict[str, Any]:
        return service.index_snapshot(context.require_quest_root())
```

#### 4.7 更新 approval policy（Codex Runner）

**文件**: `src/deepscientist/runners/codex.py`  
**位置**: `_BUILTIN_MCP_TOOL_APPROVALS["artifact"]` 元组末尾

在 artifact 的 auto-approved tools 列表末尾新增：

```python
"evidence_record",
"evidence_update",
"evidence_list",
"evidence_get",
"evidence_verify",
"evidence_index_snapshot",
```

**验证**: 启动 `ds --here`，在 quest 中通过 agent 调用 `artifact.evidence_record`，确认工具可用且不被拦截。

---

### Step 5: 单元测试（优先于端到端验证）

在端到端验证之前，先用 mock quest_root 目录编写自动化测试，确认核心逻辑正确。
测试文件与成员 C 协调，放在 `tests/test_evidence_tracking.py` 中。

```python
# tests/test_evidence_tracking.py (成员 A 部分)

import pytest
from pathlib import Path
from deepscientist.artifact.service import ArtifactService
from deepscientist.artifact.schemas import validate_evidence_payload

class TestEvidenceSchemas:
    def test_validate_valid_payload(self):
        errors = validate_evidence_payload({
            "source_type": "arxiv",
            "evidence_level": "supported",
            "claim": "BERT achieves 93.2% on GLUE",
        })
        assert errors == []

    def test_validate_invalid_source_type(self):
        errors = validate_evidence_payload({
            "source_type": "imagination",
            "claim": "Something",
        })
        assert len(errors) >= 1

    def test_validate_missing_claim(self):
        errors = validate_evidence_payload({})
        assert any("claim" in e.lower() for e in errors)

    def test_validate_bad_hash_format(self):
        errors = validate_evidence_payload({
            "source_content_hash": "not-a-sha256-prefix",
            "claim": "Something",
        })
        assert len(errors) >= 1

    def test_valid_hash_accepted(self):
        errors = validate_evidence_payload({
            "source_content_hash": "sha256:abc123def456",
            "claim": "Something",
        })
        assert errors == []

class TestEvidenceRecord:
    @pytest.fixture
    def quest_dirs(self, tmp_path):
        """创建最小 quest 目录结构，供 evidence 方法使用。"""
        quest_root = tmp_path / "quest"
        (quest_root / "artifacts" / "evidence").mkdir(parents=True)
        (quest_root / ".ds").mkdir(parents=True)
        return tmp_path, quest_root

    def test_record_creates_file_and_updates_index(self, quest_dirs):
        tmp_path, quest_root = quest_dirs
        service = ArtifactService(tmp_path)

        result = service.record_evidence(
            quest_root,
            title="Test evidence",
            source_type="arxiv",
            claim="Test claim",
            evidence_level="supported",
        )
        assert result["ok"] is True
        assert result["evidence_id"].startswith("EVD-")
        evidence_file = quest_root / "artifacts" / "evidence" / f"{result['evidence_id']}.md"
        assert evidence_file.exists()

    def test_list_evidence_returns_records(self, quest_dirs):
        tmp_path, quest_root = quest_dirs
        service = ArtifactService(tmp_path)

        service.record_evidence(quest_root, title="E1", claim="C1", evidence_level="supported")
        service.record_evidence(quest_root, title="E2", claim="C2", evidence_level="inferred")

        result = service.list_evidence(quest_root)
        assert result["ok"] is True
        assert result["total"] == 2

    def test_verify_detects_missing_reference(self, quest_dirs):
        tmp_path, quest_root = quest_dirs
        service = ArtifactService(tmp_path)

        service.record_evidence(quest_root, title="E1", claim="C1", evidence_level="supported")
        result = service.verify_evidence_claims(
            quest_root,
            agent_output_text="According to the data [EVD-nonexistent:supported]",
        )
        assert len(result["missing"]) == 1

    def test_verify_detects_retracted_citation(self, quest_dirs):
        tmp_path, quest_root = quest_dirs
        service = ArtifactService(tmp_path)

        record = service.record_evidence(
            quest_root, title="Bad evidence", claim="Wrong claim", evidence_level="supported"
        )
        evd_id = record["evidence_id"]
        # 标记为 retracted
        service.update_evidence(quest_root, evidence_id=evd_id, evidence_level="retracted")

        result = service.verify_evidence_claims(
            quest_root,
            agent_output_text=f"The data confirms this [{evd_id}:supported]",
        )
        assert len(result["retracted_but_cited"]) == 1
        assert result["retracted_but_cited"][0]["evidence_id"] == evd_id

    def test_update_evidence_changes_level(self, quest_dirs):
        tmp_path, quest_root = quest_dirs
        service = ArtifactService(tmp_path)

        record = service.record_evidence(quest_root, title="E1", claim="C1", evidence_level="inferred")
        evd_id = record["evidence_id"]

        update_result = service.update_evidence(quest_root, evidence_id=evd_id, evidence_level="supported")
        assert update_result["ok"] is True
        assert update_result["evidence_level"] == "supported"
```

运行：

```bash
pytest tests/test_evidence_tracking.py -v
```

### Step 6: 端到端验证

完成单元测试后，进行端到端验证：

```bash
# 1. 确认 Python 语法无错误
python3 -c "from deepscientist.artifact.schemas import EVIDENCE_LEVELS, validate_evidence_payload; print('schemas OK')"
python3 -c "from deepscientist.artifact.service import ArtifactService; print('service OK')"
python3 -c "from deepscientist.mcp.server import build_artifact_server; print('mcp OK')"

# 2. 启动 daemon
ds --here

# 3. 手动测试：创建 quest → 通过 chat 发送 "Please search for paper 'Attention Is All You Need' 
#    and record evidence for any factual claims you make"
#    确认: quest_root/artifacts/evidence/ 下生成 EVD-*.md 文件
#    确认: quest_root/artifacts/evidence/INDEX.md 被正确更新

# 4. 检查 MCP 工具可用性
#    在 agent 对话中尝试: "list all evidence records"
#    确认 agent 调用了 artifact.evidence_list 并返回了正确的索引内容

# 5. 检查 evidence_verify
#    让 agent 输出包含 [EVD-xxx:supported] 引用的文本
#    然后调用 artifact.evidence_verify 交叉验证

# 6. 检查 evidence_update
#    让 agent 更新已有 evidence 的 evidence_level
#    确认 INDEX.md 中对应行被正确更新
```

### Step 7: 代码改动总结

| 文件 | 改动量 | 改动类型 |
|------|--------|----------|
| `quest/layout.py` | +1 行 | `QUEST_DIRECTORIES` 新增 `"artifacts/evidence"` |
| `artifact/schemas.py` | +45 行 | 新增 `EVIDENCE_SOURCE_TYPES`, `EVIDENCE_LEVELS`, `validate_evidence_payload()` |
| `artifact/service.py` | +400 行 | 新增 6 个 evidence 方法 + 8 个辅助函数 |
| `mcp/server.py` | +165 行 | 新增 6 个 `@server.tool()` 注册 |
| `runners/codex.py` | +6 行 | approval policy 追加 evidence 工具 |

### INDEX.md 跨表格维护说明

INDEX.md 包含三个表格：**Evidence Records**、**Input Materials**、**Tool Call Records**。

- **Evidence Records** 表格由 `record_evidence()` / `update_evidence()` 自动维护（后端）。
- **Input Materials** 和 **Tool Call Records** 表格由 Agent 通过 Prompt/Skill 指令（成员 B）驱动手动维护：
  - Agent 上传/下载论文、CSV、脚本等材料时，调用 `artifact.evidence_index_snapshot` 并更新 Input Materials 表格
  - Agent 在 `bash_exec`、`artifact.arxiv` 等工具调用后，更新 Tool Call Records 表格
  - 这两个表格的前端数据填充在 `src/skills/evidence-track/SKILL.md` 中定义合约

> 成员 A 提供 `evidence_index_snapshot` 作为结构化读取入口，成员 B 在 Prompt 中约束 agent 更新这两个表格。