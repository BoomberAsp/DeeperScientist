# 基于 DeepScientist 的科研 Agent 证据链追踪模块

## 《大语言模型的原理与应用》期末 Project 报告

**作者：** 第四小组

**日期：** 2026-06-08

### 1. 项目题目和选择方向

项目题目：**基于 DeepScientist 的科研 Agent 证据链追踪模块**。

选择方向：**方向 A：科研智能体功能扩展，证据链追踪模块**。本项目面向科研 Agent 在论文阅读、实验总结、研究假设生成和报告撰写中的可追溯性问题，目标是让 Agent 对关键结论标注可检查的 evidence id，并区分直接支持、合理推断和证据不足的内容。

### 2. 选择的基础系统

基础系统：**DeepScientist**。

DeepScientist 本身是一个面向长周期科研任务的本地化科研工作台，支持从论文、代码仓库或自然语言研究目标启动 quest，并把任务文件、分支、产物、记忆和交互过程保存在同一个工作区中。本项目没有另起一套独立系统，而是在 DeepScientist 的既有 `artifact` 工具族和 quest 工作区结构上增加证据链能力。

### 3. 问题定义

科研 Agent 生成报告时，常见风险不是完全没有引用，而是“看似有引用，实际来源并不支撑结论”。例如，来源只说明了较弱事实，Agent 却写成更强结论；或者来源主题相关，但不能支持具体 claim。只让 Agent 自己标注 `supported / inferred / insufficient` 仍然不够，因为模型可能对证据关系做出自信但错误的判断。

因此，本项目要解决的问题是：**让科研 Agent 的关键结论变得可追踪、可检查、可复现，并在发布前暴露不可靠引用。**

本项目把证据检查拆成三层：

1. **Layer 1：引用完整性检查**。检查报告中的 `[EVD-xxx:level]` 是否存在于证据索引中，level 是否与证据记录一致，是否误用了 `retracted` 证据。Layer 1 是确定性检查，不依赖任何模型。
2. **Source Fidelity：原文忠实性检查**。针对 Agent 提供的 `source_excerpt`，系统独立获取原始来源内容（arXiv 摘要、URL 页面、本地文件），用滑动窗口模糊匹配验证摘录是否忠实出现在原文中。这一步解决了关键信任缺口：Agent 可能编造或歪曲引文，而纯 NLI 只比较 claim 与 excerpt，无法发现 excerpt 本身是伪造的。
3. **Layer 2：语义支持检查**。对 evidence record 中的 `claim` 与 `source_excerpt` 做可插拔的 NLI cascade 校验（heuristic → DeBERTa-v3 → LLM API），输出 `green / yellow / red` 风险信号。考虑到小规模 NLI 模型（如 DeBERTa-v3，基于 MNLI/FEVER 训练）在开放域科研陈述上的泛化能力有限，本项目采用可插拔后端设计，推荐在科研场景下使用 LLM API（GPT-4 类模型）作为最终仲裁——利用 Agent 自身的推理能力来独立判断证据关系。

最终发布规则是：`green` 可以保留为支持性引用；`yellow` 需要降级、拆分或谨慎改写；`red` 需要移除、修正或替换证据。对于不能作为支持的 citation，发布版报告会把 inline EVD 引用替换为 `[NO_EVIDENCE]`，避免把不可靠来源伪装成强支持。

### 4. 系统设计或实验设计

系统设计围绕“记录证据、引用证据、校验证据、修订输出”四步展开。

| 环节       | 设计内容                                                     |
| ---------- | ------------------------------------------------------------ |
| 证据记录   | Agent 读取论文、URL、PDF、实验日志或工具输出后，调用 `artifact.evidence_record(...)` 记录 claim、source location、source excerpt 和 evidence level |
| 证据存储   | 每条证据写入 `artifacts/evidence/EVD-*.md`，并同步到 `artifacts/evidence/INDEX.md`；id 格式为 `EVD-{run_id[:8]}-{seq:03d}` |
| 证据等级   | `supported` 表示来源直接支持；`inferred` 表示合理推断；`insufficient` 表示证据不足；`retracted` 表示证据失效但保留审计线索 |
| 强制约束   | `supported` 和 `inferred` 必须提供 `source_excerpt`，否则后端校验拒绝写入 |
| Agent 输出 | 报告中的关键 claim 使用 `[EVD-xxx:supported]`、`[EVD-xxx:inferred]`、`[EVD-xxx:insufficient]` 或 `[NO_EVIDENCE]` |
| 发布前验证 | `artifact.evidence_verify(...)` 依次执行 Layer 1 引用检查、Source Fidelity 原文忠实性检查、Layer 2 语义检查 |
| 报告区分   | `annotated_report_markdown` 保留原文并追加检测表；`publishable_report_markdown` 删除 yellow/red 支持性引用并替换为 `[NO_EVIDENCE]` |

当前可证实的实现包括 evidence schema 校验、evidence record/list/get/update、MCP artifact 工具注册、evidence table 渲染、Layer 1 + Source Fidelity + Layer 2 verifier、before/after 对比脚本，以及 evidence-track skill / prompt contract。实现与文件的对应关系如下。

| 模块         | 关键实现                                                     | 作用                                                         |
| ------------ | ------------------------------------------------------------ | ------------------------------------------------------------ |
| 存储层       | `src/deepscientist/artifact/service.py`                      | 写入 `EVD-*.md`、维护 `INDEX.md`、记录事件、更新和读取证据   |
| MCP 工具层   | `src/deepscientist/mcp/server.py`                            | 注册 `evidence_record / evidence_list / evidence_get / evidence_update / evidence_verify / evidence_index_snapshot` |
| 表格渲染     | `src/deepscientist/artifact/evidence_table.py`               | 把 evidence records 渲染为 Markdown 表和 JSON，用于报告与验证输出 |
| 验证器       | `src/deepscientist/artifact/evidence_verifier.py`、`scripts/verify_evidence.py` | 执行 Layer 1 引用检查、Source Fidelity 原文忠实性检查、Layer 2 NLI/cascade 检查和风险指标计算 |
| 原文忠实性检查 | `src/deepscientist/artifact/source_fetcher.py`              | 独立获取原始来源内容（arXiv/URL/本地文件），滑动窗口模糊匹配验证 excerpt 是否忠实出现在原文中 |
| 对比脚本     | `scripts/before_after_compare.py`                            | 统计 before/after 报告中的 citation coverage 和 `[NO_EVIDENCE]` 显式暴露比例 |
| Prompt/Skill | `src/skills/evidence-track/SKILL.md`、`src/prompts/contracts/evidence_tracking.md` | 约束 Agent 在读到来源后记录 evidence，并在最终输出中引用 evidence id |

## 4.1 原系统之外新增工具的调用情况

DeepScientist 原系统已经具备 quest 管理、聊天交互、文件读写、命令执行和 artifact 产物管理等基础能力。本项目没有把这些原有能力计入新增贡献，而是在原有 `artifact` 工具族之外，额外扩展了一组 evidence chain tracking 工具。原系统工具负责“完成科研任务”，新增 evidence 工具负责“把任务中的关键结论转化为可追踪、可校验、可发布前审计的证据链”。

新增工具的调用关系如下。

| 新增工具                                | 所属层次       | 典型调用时机                                                 | 主要输入                                                     | 输出/效果                                                    |
| --------------------------------------- | -------------- | ------------------------------------------------------------ | ------------------------------------------------------------ | ------------------------------------------------------------ |
| `artifact.evidence_record(...)`         | 证据写入层     | Agent 读完论文、网页、实验日志或工具输出，并提取到可支撑 claim 的原文片段后 | `claim`、`source_type`、`source_location`、`source_excerpt`、`evidence_level`、可选 `tool_invocation` | 创建一条 `EVD-*.md` 证据记录，并同步更新 `artifacts/evidence/INDEX.md` |
| `artifact.evidence_list(...)`           | 证据检索层     | Agent 写最终报告前，需要查看当前 quest 中有哪些可用证据时    | 可选的证据等级、来源类型或过滤条件                           | 返回证据列表，帮助 Agent 选择合适的 evidence id              |
| `artifact.evidence_get(...)`            | 证据检索层     | Agent 或开发者需要检查某一条引用的完整内容时                 | evidence id                                                  | 返回单条证据的 claim、来源位置、原文摘录、等级和状态         |
| `artifact.evidence_update(...)`         | 证据维护层     | 发现 claim 表述过强、来源摘录不准确、证据等级需要调整或证据已经失效时 | evidence id，以及新的 claim/source/excerpt/level/status      | 更新证据记录；对失效证据标记为 `retracted`，保留审计线索     |
| `artifact.evidence_index_snapshot(...)` | 调试与后处理层 | 调试 evidence index、生成报告表格或外部脚本需要结构化读取索引时 | quest/run 上下文                                             | 返回结构化的 evidence index 快照，避免只依赖 Markdown 文本解析 |
| `artifact.evidence_verify(...)`         | 发布前验证层   | Agent 生成报告草稿后、正式输出或提交前                       | `agent_output_text`、verification mode、可选 before/after 文本 | 依次执行 Layer 1 引用检查、Source Fidelity 原文忠实性检查、Layer 2 语义支持检查，输出审计版与发布版报告 |

这些新增工具在系统中的调用顺序可以概括为：**先记录证据，再查询证据，再引用证据，最后校验证据**。Agent 读取外部材料后调用 `evidence_record` 写入证据；生成报告前通过 `evidence_list / evidence_get / evidence_index_snapshot` 查看可用证据；发现证据不准确或失效时调用 `evidence_update`；报告发布前调用 `evidence_verify` 生成审计版和发布版结果。

新增 evidence 工具与原系统工具之间是"补强关系"而不是"替代关系"：原系统工具负责完成科研 Agent 的阅读、分析和生成任务；新增工具负责把这些任务中的关键结论转化为可追踪证据，并在最终输出前检查 citation 是否真实可靠。该设计使系统从"能生成科研报告"进一步变成"能生成可审计科研报告"。

QQ 截图证据：用户通过 QQ 输入论文链接并启动 `019`，DeepScientist 通过 qqbot 正常接收任务并回复处理状态，说明系统能够通过 QQ 对话触发科研 Agent 工作流。

<img src="C:\Users\song\AppData\Roaming\Typora\typora-user-images\image-20260608131352586.png" alt="image-20260608131352586" style="zoom: 50%;" />

## 4.2 证据链存储系统架构

证据链存储系统是本项目的核心后端。它不是简单把引用写在报告末尾，而是把每一条证据拆成可独立管理的 evidence record，并通过索引、事件记录和状态字段组成一条可审计链路。同时，每条证据记录通过 `source_content_hash` 字段与独立获取的原始来源缓存关联，使 Source Fidelity 检查成为可能。其基本结构如下。

```text
Quest / Run 工作区
└── artifacts/
    └── evidence/
        ├── INDEX.md                  ← 证据总索引
        ├── EVD-{run_id[:8]}-001.md   ← 证据记录（YAML frontmatter + Markdown）
        ├── EVD-{run_id[:8]}-002.md
        ├── ...
        ├── sources/                  ← 缓存的原始来源内容
        │   ├── <sha256_1>.json       ←   key = sha256(source_location)
        │   └── <sha256_2>.json
        └── verification/             ← 生成的验证报告
            └── evidence_verify-*.md
```

每条 `EVD-*.md` 是一条独立证据记录，保存该证据对应的 claim、来源类型、来源位置、原文摘录、证据等级、状态和更新时间；`INDEX.md` 是当前 quest 的证据总索引，用于快速列出所有 evidence id、证据等级和简要 claim。

| 存储对象 | 内容 | 作用 |
| -------- | ---- | ---- |
| `EVD-*.md` | 单条证据的完整记录，含 claim、source_type、source_location、source_excerpt、evidence_level、source_content_hash | 支持逐条追踪和人工复查 |
| `INDEX.md` | 当前 quest/run 的证据索引，汇总 evidence id、等级、状态和简要 claim | Agent 写报告前快速检索可用证据 |
| `sources/` | 按 `sha256(source_location)` 为 key 缓存的原始来源内容（arXiv 摘要、URL 页面、本地文件） | 支撑 Source Fidelity 独立检查，同一来源多次引用不重复获取 |
| `verification/` | 每次 `evidence_verify` 生成的三层验证报告（Layer 1 + Source Fidelity + Layer 2） | 保留验证历史，可 Git 追踪每次验证结果变化 |
| `retracted` 状态 | 标记已经失效或不应继续引用的证据 | 防止旧证据继续被当作有效支持 |

**EVD 与源文件的双向链接。** 每条 EVD 记录通过 `source_content_hash = sha256(source_location)` 字段与 `sources/` 目录中的缓存文件建立一一对应关系。当 Source Fidelity 检查执行时，系统首先查找 `sources/<hash>.json`——若缓存存在则直接使用，否则根据 `source_type` 独立获取原始来源内容（arXiv 摘要、HTTP GET、本地文件读取）并写入缓存。随后，系统用滑动窗口模糊匹配在该来源内容中搜索 `source_excerpt`，验证 Agent 提供的摘录是否忠实出现在原文中。验证完成后，报告写入 `verification/` 目录。这样，一条 evidence record 的完整链路是：**原始来源 → sources/<hash>.json → EVD-*.md → verification/evidence_verify-*.md**，每一环都可独立审计。

从数据流角度看，证据链存储架构包含四个阶段。

1. **写入阶段**：Agent 在阅读论文、网页、PDF 或实验结果后，把能够支撑 claim 的来源片段传给 `evidence_record`。后端首先检查 schema，尤其要求 `supported` 和 `inferred` 类型必须包含 `source_excerpt`；检查通过后生成新的 evidence id，并写入单独的 `EVD-*.md` 文件。
2. **索引阶段**：每次新增或更新证据后，系统都会同步维护 `INDEX.md`。Agent 后续不需要从散落的报告文本中寻找引用，而是可以通过 evidence list 或 index snapshot 直接看到当前可用证据。
3. **维护阶段**：如果发现某条证据的 claim 表述过强、证据等级错误或来源已经失效，系统通过 `evidence_update` 修改记录或标记为 `retracted`。这里选择“撤回而不是删除”，是为了保留完整的审计历史。
4. **引用阶段**：最终报告只引用 evidence id，例如 `[EVD-xxxx-003:supported]`。发布前 verifier 会把报告中的 id 与 `INDEX.md` 和对应 `EVD-*.md` 文件逐一对齐，检查 id 是否存在、level 是否一致、状态是否有效。

这种架构相当于在科研 Agent 的生成结果和外部来源之间增加了一个“证据账本”。Agent 不能只在报告里写一个看起来像引用的标记，而必须先在证据账本中留下可追踪记录；报告发布前，系统再用账本反查每个 citation 是否真实存在、是否有效、是否真的支持 claim。

## 4.3 语义查验的可插拔后端与三阶段流程

在引用完整性检查（Layer 1）和原文忠实性检查（Source Fidelity）之后，系统还会执行语义查验。语义查验的目标是检查”引用的 `source_excerpt` 是否真的支持该 claim”。本项目采用可插拔后端 + 分阶段 cascade 设计。

**可插拔后端设计。** 考虑到不同场景对成本、延迟和泛化能力的不同需求，以及小规模 NLI 模型（如 DeBERTa-v3，基于 MNLI/FEVER 训练）在开放域科研陈述上的泛化能力有限，Layer 2 支持四种后端：

| 后端 | 成本 | 延迟 | 开放域泛化能力 | 适用场景 |
|------|------|------|---------------|---------|
| `heuristic` | 免费 | <1ms | 低（仅 token 重叠） | 快速分诊 / 离线环境 |
| `transformers` | 本地 GPU/CPU | ~100ms | 中（MNLI/FEVER 训练） | 成本敏感 / 闭域场景 |
| `api` | API 费用 | ~1-2s | **高**（GPT-4 零样本） | **科研场景推荐** |
| `cascade` | 变化 | 变化 | 高（逐级回退） | **默认 — 最佳平衡** |

其中，`api` 后端直接使用 GPT-4 类 LLM 对 (premise, hypothesis) 做零样本蕴含判断，输出 `entailment / neutral / contradiction` 标签及推理理由。这一设计回应了”小规模 NLI 模型在科研陈述上泛化不足”的问题：当 Agent 本身由强 LLM 驱动时，用同类模型的独立推理能力来判断证据关系更可靠。

**Cascade 流程。** 默认的 `cascade` 后端按以下顺序逐级判断：

```text
Agent 报告中的 claim + evidence.source_excerpt
        │
        ▼
Stage 1: Heuristic 初筛
        │  明显匹配 / 明显缺失 / 需要进一步判断
        ▼
Stage 2: DeBERTa-v3 NLI 语义蕴含判断
        │  entailment / neutral / contradiction
        ▼
Stage 3: LLM/API 最终仲裁（可选，--cascade-api）
        │  对 Stage 2 不确定或低置信度结果做零样本重判
        │  输出最终 entailment / neutral / contradiction + 理由
        ▼
Annotated report + Publishable report
```

三阶段具体含义如下。

| 阶段 | 方法 | 判断重点 | 输出 | 作用 |
|------|------|---------|------|------|
| Stage 1 | Heuristic 初筛 | 字符串重叠、关键词覆盖、数字或实体是否明显不一致、是否缺少 `source_excerpt` | 明显可疑样例直接标为 yellow/red；不确定样例进入下一阶段 | 低成本过滤格式错误、空证据和明显不匹配引用 |
| Stage 2 | DeBERTa-v3 NLI 判断 | 将 `source_excerpt` 作为 premise，claim 作为 hypothesis，判断来源是否蕴含 claim | `entailment` → green 候选；`neutral` → yellow；`contradiction` → red | 用 NLI 模型进行语义检查；成本低但泛化有限 |
| Stage 3 | LLM/API 最终仲裁 | 当启用 `--cascade-api` 时，对 Stage 2 的结果进行零样本重判，输出独立判断及推理理由 | 最终 `entailment / neutral / contradiction` + reason + 处置建议 | 利用强 LLM 的开放域推理能力进行独立判断，弥补小 NLI 模型的泛化不足 |

**LLM/API 的角色转变。** 与初版设计不同，当前架构中 LLM/API 在 cascade 模式下不是被动的”解释生成器”，而是有权力覆盖 Stage 2 NLI 标签的最终仲裁者。当 `--cascade-api` 启用时，如果 LLM 与 NLI 的判断不一致，系统以 LLM 的判断为准——在开放域科研陈述上，GPT-4 类模型的推理能力优于固定的小规模 NLI 模型。

最终，系统根据验证结果将每条引用分为三类：**green**（直接支撑，发布版保留 EVD 引用）、**yellow**（相关但不充分，降级或替换为 `[NO_EVIDENCE]`）、**red**（矛盾或不可靠，必须移除引用）。这一设计对应本项目的关键思想：**Layer 1 保证引用存在且格式正确，Source Fidelity 保证摘录真实存在于原文，Layer 2 才检查引用是否真的支持语义内容**。只有通过三层检查后得到 green 的引用，才能作为发布版报告中的强支持证据。

### 5. 测试样例

当前共有 5 个端到端样例，覆盖 before/after 对照、全部 yellow 边界、NLI 部分通过、red contradiction 失败样例，以及同一主题下“原子化 facts 成功、复合长报告失败”的对照。

| Case ID | 输入/任务 | 当前结果 | 作用 |
|---|---|---|---|
| C1 | before/after 证据链测试 | 仓库最小样例显示 citation completeness 从 0.00% 提升到 50.00%；扩展运行样例中 after 侧仍存在 yellow/red 风险项 | 展示证据链能提高引用显式性，但引用存在不等于语义充分支持 |
| C2 | 中文 paper-to-idea 测试 | 引用数从 0 到 7，但 after 侧仍为 0 green / 7 yellow / 0 red | 展示中文 claim 与英文 source excerpt 混用时，NLI 更容易给出 neutral/yellow |
| C3 | AlphaFold/RFdiffusion idea | 7 个 citation id 全部可解析；2 green / 5 yellow / 0 red；发布版只保留 2 条 green 引用 | 展示 NLI 后部分证据变绿，yellow 不再作为最终支持引用 |
| C4 | QQ 发起 scientific agent / physical feedback idea | 6 个引用全部通过 Layer 1；1 green / 3 yellow / 2 red；最终风险 83.33% | 展示 red contradiction 和 replace evidence 的价值 |
| C5 | Critical Free-energy Reflexion 跨论文 hypothesis | 精简版 4/4 green、风险 0.00%；长版 0 green / 6 yellow / 1 red、风险 100.00% | 展示原子化 claim 更容易通过校验，复合长 claim 更容易失败 |

截图证据：Agent 输出中多个 EVD citation 被标红框，后续 NLI 表确认 2 green / 5 yellow。

<img src="C:\Users\song\AppData\Roaming\Typora\typora-user-images\image-20260608131444328.png" alt="image-20260608131444328" style="zoom: 40%;" />

<img src="C:\Users\song\AppData\Roaming\Typora\typora-user-images\image-20260608131510247.png" alt="image-20260608131510247" style="zoom: 40%;" />

### 6. 失败案例

#### Failure Case 1: Quest 018 AlphaFold/RFdiffusion

- 类型：语义支撑不足 / hypothesis 外推边界。
- 截图：`failure_cases/018-failure-alphafold-rfdiffusion.png`
- 核验结果：0 绿 / 7 黄 / 0 红。

该案例中，Layer 1 引用均可解析，说明 evidence ID 和文件链路没有丢失。但 claim 将 AlphaFold2 置信度、RFdiffusion 条件生成以及后续 AlphaFold3/RFdiffusion2 等多个事实合并为较长中文表述。NLI 层因此判定为 `neutral`：证据相关，但不能直接蕴含完整 claim。

处理方式：

- 将输出标注为 hypothesis，而非 established fact。
- 将复合 claim 拆分为更短、更贴近原文的事实句。
- 对跨论文推理使用 `inferred` / `proposed` 表述。

<img src="C:\Users\song\AppData\Roaming\Typora\typora-user-images\image-20260608212405419.png" alt="image-20260608212405419" style="zoom:33%;" />

#### Failure Case 2: Quest 019 Scientific Agent / Physical Feedback

- 类型：复合 claim 与新机制外推边界。
- 截图：`failure_cases/019-failure-physical-feedback-agent.png`
- 核验结果：0 绿 / 6 黄 / 0 红。

该案例把 Nature Coscientist 和 scientific-agent workflow 两类论文合成为 `Physical Evidence Ledger + Verifier-Gated Planner`。自动 verifier 能确认来源相关，但对“把物理反馈变成下一步规划约束”的新机制外推保持保守。因此，该案例说明系统能够区分“论文事实”和“基于论文提出的新研究机制”。

处理方式：

- 保留黄项作为边界提示。
- 将论文事实与新 hypothesis 分开写。
- 后续若要提高 green 比例，需要补充更直接的 source excerpt。

<img src="C:\Users\song\AppData\Roaming\Typora\typora-user-images\image-20260608212431801.png" alt="image-20260608212431801" style="zoom:33%;" />

### 7. 局限性与未来改进

当前局限性主要有三点。

1. **小规模 NLI 模型的泛化能力有限**：DeBERTa-v3 基于 MNLI/FEVER 训练，在高度专业化的科研陈述上泛化能力不足。本项目的缓解方案是采用可插拔后端设计，推荐在科研场景下使用 `api` 后端或 `cascade --cascade-api`，利用 GPT-4 类 LLM 的开放域推理能力作为最终仲裁。
2. **复合 claim 难验证**：一句话同时包含多个事实时，NLI 可能只支持其中一部分，但整句被标为 yellow 或 red。未来应增加 claim splitter，把 claim 拆成更原子的事实单元。
3. **Source Fidelity 覆盖范围有限**：当前 Source Fidelity 检查支持 arXiv（摘要）、URL 和本地文件，但对于 `tool_call`、`memory_card`、`user_upload` 等来源类型尚无自动获取策略，仍需信任 Agent 提供的 excerpt。未来应扩展获取覆盖范围（全文 PDF 解析、付费墙论文访问）。

未来改进包括：引入跨语言 NLI 或受控翻译；增加 claim splitter 与 citation rewriter；扩展 Source Fidelity 的获取后端；统一 risk metric；在 UI 中明确区分 annotated audit view 与 publishable revised view。

### 8. 个人或小组贡献说明

| 成员 | 计划职责                                | 当前可证实贡献 |
|---|-------------------------------------|---|
| 石一凡 | 证据存储、验证与 MCP 工具扩展（Backend）          | evidence schema、record/list/get/update/index_snapshot；证据文件、events、INDEX 写入流程；MCP artifact 工具注册 |
| 宋子墨 | Prompt 工程与 Agent 行为注入（Prompt/Skill） | `evidence-track` skill；prompt contract；supported/inferred/insufficient 标注规则；报告前 verifier 调用规则 |
| 邵之航 | 报告生成与外部 NLI 验证（Report/Validation）   | evidence table；Layer 1 + Layer 2 verifier；before/after comparison；NLI cascade；annotated report 与 publishable report 区分 |
| 张岩 | 集成测试与 QQ/WeChat 演示（QA/Demo）         | 5 个输出样例、QQ 交互截图和证据截图可证实；录屏、后台截图和证据材料 PDF 待补充 |

### 9. 总结

本项目的核心贡献不是简单地”给报告加引用”，而是把科研 Agent 的结论生成过程改造成一个可审计流程：先记录证据，再在输出中引用证据，最后用外部 verifier 进行三层检查——引用完整性、原文忠实性、语义支持关系。其中，Source Fidelity 原文忠实性检查是本项目针对”Agent 可能编造或歪曲引文”这一关键信任缺口所做的专门补强：系统独立获取原始来源，用滑动窗口模糊匹配验证 excerpt 是否真实存在于原文中，而非盲目信任 Agent 提供的摘录。在 Layer 2 语义校验中，考虑到小规模 NLI 模型的泛化局限，本项目采用可插拔后端设计，推荐在科研场景下使用 LLM API 作为最终仲裁，利用 Agent 自身的推理能力来独立判断证据关系。测试结果表明，EVD 引用能够提高结论的可追踪性，但也暴露出一个重要事实：**有引用并不等于有支持**。因此，三层校验和 publishable report 改写机制是本项目中最关键的安全补强。
