# B To C Handoff: Evidence Tracking

这份文档给后面完成 C 部分的同学看。目标是先用最直白的方式说明 A、B 两部分分别做了什么，再说明现在 agent 如何给科研总结里的关键结论加上可检查的证据来源，最后交代 B 部分的具体实现位置。

## 1. 先用一句话理解 A 和 B

A 部分做的是“证据记录工具本身”。

也就是说，A 让系统真的拥有了这些可调用工具：

- `artifact.evidence_record(...)`：记录一条证据，生成 `EVD-xxx` 证据编号。
- `artifact.evidence_list(...)`：列出现有证据。
- `artifact.evidence_get(...)`：读取某条证据详情。
- `artifact.evidence_update(...)`：更新证据，例如把错误证据标为 `retracted`。
- `artifact.evidence_verify(...)`：检查回答里写的 `[EVD-xxx:level]` 是否存在、level 是否匹配。
- `artifact.evidence_index_snapshot(...)`：查看证据索引快照。

A 的结果主要是后端能力：能存、能查、能更新、能验证 evidence id。证据记录会落到 `artifacts/evidence/`，并维护 `artifacts/evidence/INDEX.md`。

B 部分做的是“教 agent 正确使用这些工具”。

也就是说，B 没有重新实现 `artifact.evidence_record(...)` 这些工具，而是通过 prompt 和 skill 告诉 agent：

- 看到论文、网页、命令输出、实验日志、上传文件等来源后，如果要基于它们写结论，先调用 `artifact.evidence_record(...)`。
- 写科研总结或报告时，关键结论后面要附上证据编号。
- 证据不足就明确标出来，不要装作已经被证明。
- 没有证据的陈述要标成 `[NO_EVIDENCE]`。

可以把它理解为：

- A 做了“证据数据库和工具 API”。
- B 做了“使用证据数据库的工作规范和 prompt 接线”。

## 2. 现在 agent 如何给科研总结标注证据来源

假设 agent 要生成一份科研总结，正常流程应该是这样：

1. 先读取来源材料。

   例如调用 `artifact.arxiv(...)` 看论文摘要，调用 `bash_exec(...)` 跑实验脚本，或者读取某个日志、PDF、网页、数据文件。

2. 从来源材料里提取准备写进总结的关键结论。

   例如：“实验 A 的准确率是 93.2%”，“某篇论文提出了某种方法”，“当前日志不足以证明速度提升”。

3. 对每个重要结论调用 `artifact.evidence_record(...)`。

   一条 evidence record 里应该包含：

   - `claim`：要支持的结论是什么。
   - `source_type`：来源类型，例如 `arxiv`、`bash_log`、`experiment_result`、`url`、`pdf`。
   - `source_location`：具体来源位置，例如论文号、文件路径、日志行号、URL。
   - `evidence_level`：证据等级。
   - `source_excerpt`：来源原文摘录。`supported` 和 `inferred` 必须提供。
   - `claim_relation`：解释来源和结论之间的关系。

4. `artifact.evidence_record(...)` 返回一个证据编号。

   例如：`EVD-a1b2c3-001`。

5. agent 在最终科研总结中给关键结论加标注。

   常见格式是：

   ```text
   该实验报告的平均准确率为 93.2% [EVD-a1b2c3-001:supported]。
   这个趋势可能也适用于更小模型 [EVD-a1b2c3-002:inferred]。
   当前日志不足以证明训练速度提升 [EVD-a1b2c3-003:insufficient]。
   该方法可能改善泛化能力，但本轮尚未测量 [NO_EVIDENCE]。
   ```

6. 如果是重要总结、交付报告或 handoff，agent 应该先检查证据。

   它可以调用：

   - `artifact.evidence_list(...)`：确认有哪些证据。
   - `artifact.evidence_verify(agent_output_text=...)`：检查输出中的 evidence id 是否真实存在、标注等级是否匹配。

## 3. 这样为什么能降低幻觉率

这套机制不能保证模型永远不犯错，但能明显降低“没有来源就乱说”的概率。

原因是：

- 结论必须绑定到具体来源，而不是只靠模型记忆。
- `supported`、`inferred`、`insufficient` 三类证据会强迫 agent 区分“来源直接支持”“合理推断”和“证据不足”。
- `[NO_EVIDENCE]` 让没有证据的说法暴露出来，而不是混进已验证结论里。
- `artifact.evidence_verify(...)` 可以发现伪造的 `EVD` 编号、引用不存在的证据、证据等级写错等问题。
- evidence 文件和 `INDEX.md` 会留下可追溯记录，后续同学可以回头检查来源。

需要注意：`artifact.evidence_verify(...)` 主要检查 evidence id 和 level 是否一致，它不是自动事实审稿人。真正的语义判断仍然依赖 agent 是否正确摘录来源、正确选择 evidence level。

## 4. B 部分具体实现了什么

### 4.1 新增 evidence-track skill

文件：

```text
src/skills/evidence-track/SKILL.md
```

作用：

- 把 `evidence-track` 注册为 companion skill。
- 详细说明什么时候要记录证据。
- 给出 `artifact.evidence_record(...)` 的调用字段示例。
- 解释四种证据等级：
  - `supported`
  - `inferred`
  - `insufficient`
  - `retracted`
- 规定输出格式：
  - `[EVD-xxx:supported]`
  - `[EVD-xxx:inferred]`
  - `[EVD-xxx:insufficient]`
  - `[NO_EVIDENCE]`
- 说明 `artifacts/evidence/` 和 `memory/evidence` 的边界。

一句话说，这个文件是“证据追踪的详细操作手册”。

### 4.2 新增 evidence tracking contract

文件：

```text
src/prompts/contracts/evidence_tracking.md
```

作用：

- 给每次 prompt 注入一段简洁的全局规则。
- 要求 source-bearing tool call 或材料检查后，记录准备用于结论的事实。
- 要求 `supported` 和 `inferred` 必须有 `source_excerpt`。
- 要求不能伪造 evidence id。
- 要求重要报告前使用 `artifact.evidence_list(...)`，必要时使用 `artifact.evidence_verify(...)`。

一句话说，这个文件是“每一轮 agent 都能看到的证据追踪硬提醒”。

### 4.3 修改 PromptBuilder 注入证据追踪规则

文件：

```text
src/deepscientist/prompts/builder.py
```

关键变化：

- 读取 `src/prompts/contracts/evidence_tracking.md`。
- 把 evidence tracking block 拼进最终 prompt。
- 让 agent 在每次运行时都能看到证据追踪规则。

相关逻辑大致是：

```python
evidence_tracking_block = self._prompt_fragment(
    Path("contracts") / "evidence_tracking.md",
    quest_root=quest_root,
)
```

然后加入 `sections`。

### 4.4 更新 STAGE_MEMORY_PLAN

文件：

```text
src/deepscientist/prompts/builder.py
```

变化：

- 在 scout、baseline、idea、experiment、write、finalize 等阶段的 memory plan 中加入 `evidence`。
- 这样 agent 做不同阶段任务时，会更容易优先查找证据相关 memory。

注意这里的 `evidence` memory 不是主要证据记录库。真正的 evidence record 仍然在 `artifacts/evidence/`。

### 4.5 注册 evidence memory kind

文件：

```text
src/deepscientist/memory/service.py
```

变化：

```python
MEMORY_KINDS = ("papers", "ideas", "decisions", "episodes", "evidence", "knowledge", "templates")
```

作用：

- 允许系统写入 `kind="evidence"` 的 memory card。
- 主要用于保存可复用的证据追踪经验、claim map、citation audit pattern。

### 4.6 新 quest 创建 memory/evidence 目录

文件：

```text
src/deepscientist/quest/layout.py
```

变化：

```text
memory/evidence
```

作用：

- 新建 quest 时自动创建 `memory/evidence` 目录。
- 保证 evidence memory kind 有对应目录。

### 4.7 补充测试

文件：

```text
tests/test_prompt_builder.py
tests/test_stage_skills.py
```

覆盖点：

- prompt 里确实包含 `# Evidence Tracking Contract`。
- prompt 里能看到 `artifact.evidence_record`、`artifact.evidence_list`、`artifact.evidence_verify`。
- prompt 里包含 `[EVD-xxx:supported]` 和 `[NO_EVIDENCE]`。
- `evidence-track` 能被识别为 companion skill。
- `evidence` 已加入 `MEMORY_KINDS`。
- `memory/evidence` 已加入 `QUEST_DIRECTORIES`。

## 5. 已跑过的验证命令

B 部分完成后，已经跑过这些测试：

```powershell
python -m pytest tests\test_prompt_builder.py::test_prompt_builder_includes_layered_runtime_context tests\test_prompt_builder.py::test_prompt_builder_includes_evidence_tracking_contract tests\test_prompt_builder.py::test_prompt_builder_stays_compact_and_avoids_redundant_stage_sop -q
```

结果：`3 passed`

```powershell
python -m pytest tests\test_stage_skills.py -q
```

结果：`31 passed`

```powershell
python -m pytest tests\test_evidence_tracking.py -q
```

结果：`20 passed`

还跑过：

```powershell
git diff --check
```

结果：通过。只有 Windows 下 LF/CRLF 的换行提示，不是代码错误。

## 6. 给 C 部分同学的注意事项

1. 不要重复实现 A 的 evidence 工具。

   A 已经提供了 `artifact.evidence_record(...)`、`artifact.evidence_verify(...)` 等工具。C 如果需要更高层的工作流，应优先复用这些工具。

2. 不要手写或伪造 `EVD-xxx`。

   evidence id 应该来自 `artifact.evidence_record(...)` 或 `artifact.evidence_list(...)`。

3. 不要直接编辑 `artifacts/evidence/INDEX.md` 来冒充记录证据。

   正确方式是调用 `artifact.evidence_record(...)`，让系统维护 evidence 文件和 index。

4. 注意 `memory/evidence` 和 `artifacts/evidence/` 的区别。

   - `artifacts/evidence/`：具体证据记录，权威来源。
   - `memory/evidence`：可复用经验、claim map、citation audit 习惯，不是证据主库。

5. 当前 B 是 prompt/skill 约束，不是运行时强制 hook。

   也就是说，agent 会被明确要求在使用来源后记录证据，但系统不会自动拦截每一次 `bash_exec(...)` 后强制调用 `artifact.evidence_record(...)`。

   如果 C 的任务要求“硬性自动化”，比如每次工具调用后都由中间件强制生成 evidence draft，那么 C 需要在运行时层面继续加 hook 或 validator。

6. 写科研总结时，建议 C 部分继续沿用这个输出习惯：

   ```text
   关键结论 [EVD-xxx:supported]
   合理推断 [EVD-xxx:inferred]
   证据不足 [EVD-xxx:insufficient]
   无来源支持 [NO_EVIDENCE]
   ```

## 7. 当前 B 分支状态

当前开发分支：

```text
feature/evidence-track-b
```

B 的改动集中在：

```text
src/skills/evidence-track/SKILL.md
src/prompts/contracts/evidence_tracking.md
src/deepscientist/prompts/builder.py
src/deepscientist/memory/service.py
src/deepscientist/quest/layout.py
tests/test_prompt_builder.py
tests/test_stage_skills.py
```

C 部分可以从这些文件开始读，尤其是 `src/skills/evidence-track/SKILL.md` 和 `src/prompts/contracts/evidence_tracking.md`。
