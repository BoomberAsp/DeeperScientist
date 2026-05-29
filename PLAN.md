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
| `artifact.evidence_list` | artifact | 读取 INDEX.md，返回证据列表 |
| `artifact.evidence_get` | artifact | 按 evidence_id 读取单条 evidence 详情 |
| `artifact.evidence_verify` | artifact | 交叉验证：检查 agent 输出中的 [EVD-*] 引用是否在 INDEX.md 中存在，对应的 evidence_level 是否正确 |
| `artifact.index_snapshot` | artifact | 返回 INDEX.md 的完整解析结果（JSON 格式），供验证脚本使用 |