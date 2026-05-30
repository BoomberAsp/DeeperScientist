# 科研智能体证据链追踪模块 — 实现计划

> 基于 DeepScientist，为科研 Agent 增加**双层证据链追踪模块**。
> 小组 4 人。展示：2026.06.05，终稿：2026.06.08。

## 核心设计：双层验证架构

现有方案的核心缺陷：Agent 自评证据置信度（supported/inferred/insufficient），但幻觉会让 Agent 声称「source X 支持结论 Y」而实际并不支持。需要**独立于 Agent 的外部验证层**来解决这个问题。

```
Layer 1: Agent 自评 + 引用格式校验（原 PLAN.md）
  Agent 调用 evidence_record(self_label=supported)
  → verify_evidence_claims() 检查 [EVD-xxx] 引用是否在 INDEX.md 中存在
  → 解决：引用遗漏、格式错误

Layer 2: 外部 NLI 归因验证（新增）
  对每条 (claim, source_excerpt) 用独立 NLI 模型做事实一致性判断
  → 解决：Agent 幻觉（声称支持但 source 实际不支持）
```

## 技术路线

```
Agent 工具调用（bash_exec / arxiv / URL fetch）
  → evidence_record(claim, source_excerpt, evidence_level)  [Layer 1: Agent 自评]
  → EVD-xxx.md + INDEX.md                                   [成员 A: 存储层]
  → verify_evidence.py 交叉验证引用格式                       [Layer 1: 格式校验]
  → external_fact_check() 用 NLI 模型验证 (claim, excerpt)   [Layer 2: 事实校验]
  → 差异报告：Agent 自评 vs 外部 NLI 判断                     [成员 C: 对比分析]
```

**不新增 MCP namespace**，所有工具挂在 `artifact` 下。

## 四人分工

### 成员 A：证据存储与 MCP 工具扩展（Backend）✅ 已实现

1. 设计 evidence entry schema（`source_excerpt` 条件必填：level 为 supported/inferred 时必填，否则拒绝写入）
2. 在 `ArtifactService` 中新增 `record_evidence()`, `list_evidence()`, `get_evidence()`, `update_evidence()`, `verify_evidence_claims()`, `index_snapshot()`
3. `record_evidence()` 内置入参校验门：写入前调用 `validate_evidence_payload()`，不合法直接拒绝
4. 在 MCP artifact server 注册 6 个工具（`evidence_record` 描述强调 source_excerpt 必填约束）
5. Evidence ID 格式：`EVD-{run_id[:8]}-{seq:03d}`
6. INDEX.md 自动维护（fcntl 文件锁保证并发安全）
7. 在 `runners/codex.py` 中将 6 个 evidence 工具加入 auto-approval 白名单

**关键文件：** `artifact/service.py`, `artifact/schemas.py`, `mcp/server.py`, `quest/layout.py`, `runners/codex.py` | **测试：** `tests/test_evidence_tracking.py`（20 个用例全部通过）

### 成员 B：Prompt 工程与 Agent 行为注入（Prompt/Skill）

1. 编写 `src/skills/evidence-track/SKILL.md`
2. 在 `PromptBuilder` 中注入证据追踪指令块
3. **关键约束**：Agent 必须在 source_excerpt 中**逐字引用 source 原文**，不得自行概括
4. 三类标注规则：`[EVD-xxx:supported]` / `[EVD-xxx:inferred]` / `[EVD-xxx:insufficient]`

**关键文件：** `skills/evidence-track/SKILL.md`, `prompts/builder.py`, `prompts/contracts/evidence_tracking.md`

### 成员 C：报告生成与外部 NLI 验证（Report/Validation）

1. 实现 evidence table 渲染（Markdown + JSON）
2. `verify_evidence.py`：**双层验证**
   - Layer 1：解析 `[EVD-*]` 引用，与 INDEX.md 交叉校验（格式完整性）
   - Layer 2：对每条 evidence 的 `(claim, source_excerpt)` 用外部 NLI 模型做事实一致性判断
3. NLI 工具选型：MiniCheck（快速筛选，比 LLM 快 400x） + DeBERTa-v3-mnli（精细验证）
4. 对比分析：Agent 自评 vs 外部 NLI 判断

**关键文件：** `artifact/evidence_table.py`, `scripts/verify_evidence.py`, `scripts/before_after_compare.py`, `tests/test_evidence_tracking.py`

**对比指标：**

| 指标 | 含义 |
|------|------|
| Agent-NLI Agreement Rate | Agent 自评与外部 NLI 一致的比例 |
| Hallucination Rate | Agent 标 supported 但 NLI 判 neutral/contradict 的比例 |
| Unverifiable Rate | source_excerpt 缺失导致无法验证的比例 |
| Citation Completeness | 有 [EVD-*] 标注的结论占比 |

### 成员 D：集成测试与 QQ/WeChat 演示（QA/Demo）

1. 端到端集成，确保 A、B、C 产物串联运行
2. 5 个测试案例（含 1 个故意注入幻觉的边界案例）
3. QQ connector 真实交互部署
4. 收集截图、日志、录制 screen recording
5. 项目报告 + 证据材料（PDF）

## 时间线（7 天）

```
Day 1 (5/29): 全体对齐 — 确认 schema、ID 格式、标注规范、NLI 工具选型
Day 2-3 (5/30-31): 并行开发 — A 存储层 + B SKILL.md + C 验证脚本框架
Day 4 (6/1): 首次联调 — 确保 Agent 能记录证据、输出 evidence table
Day 5 (6/2): 测试执行 — 5 个案例 + before/after 对比 + NLI 验证结果
Day 6 (6/3): 报告撰写 — 项目报告 + Poster (120cm×80cm)
Day 7 (6/4): 预演 + 终稿 — 10 分钟展示彩排 + 打印 + 打包提交
```

## 提交清单

```
GroupXX_LLM_Project_Engineering.zip
├── report.pdf
├── evidence_materials.pdf
└── code/
    ├── src/
    │   ├── skills/evidence-track/SKILL.md
    │   ├── deepscientist/artifact/service.py        (修改)
    │   ├── deepscientist/artifact/schemas.py         (修改)
    │   ├── deepscientist/artifact/evidence_table.py  (新增)
    │   ├── deepscientist/mcp/server.py               (修改)
    │   └── deepscientist/quest/layout.py              (修改)
    ├── scripts/
    │   ├── verify_evidence.py       (Layer 1 + Layer 2)
    │   └── before_after_compare.py
    ├── tests/
    │   └── test_evidence_tracking.py
    └── README.md
```
