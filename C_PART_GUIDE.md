# C 部分说明文档：报告生成与外部 NLI 验证

本文档说明 C 部分已经实现的内容、如何配置外部 API、如何运行验证脚本，以及如何把结果用于最终展示。

## 1. C 部分目标

C 部分承接 A 和 B 的结果：

- A 已经实现 evidence 存储和 MCP 工具，例如 `artifact.evidence_record(...)`、`artifact.evidence_verify(...)`。
- B 已经通过 prompt/skill 约束 agent 在关键结论后标注 `[EVD-xxx:level]`。
- C 负责把这些 evidence 记录转换成可读报告，并用外部 NLI/API 模型独立检查 `claim` 与 `source_excerpt` 是否事实一致。

核心目标是实现双层验证：

```text
Layer 1: 引用完整性检查
  检查报告中的 [EVD-xxx:level] 是否存在、level 是否匹配、是否引用 retracted evidence。

Layer 2: 外部语义验证
  对每条 evidence 的 (claim, source_excerpt) 做 NLI 判断：
  entailment / neutral / contradiction / unverifiable / skipped
```

## 2. 新增文件

```text
src/deepscientist/artifact/evidence_table.py
scripts/verify_evidence.py
scripts/before_after_compare.py
tests/test_evidence_validation_c.py
.env
```

### 2.1 `evidence_table.py`

作用：读取 quest 下的 evidence 文件，并渲染 Markdown/JSON 表格。

默认读取：

```text
<quest_root>/artifacts/evidence/EVD-*.md
```

输出包含：

- `evidence_id`
- `claim`
- `evidence_level`
- `source_type`
- `source_location`
- `source_excerpt`
- `claim_relation`
- `path`
- `timestamp`

### 2.2 `verify_evidence.py`

作用：生成双层验证报告。

支持的 NLI 后端：

| 后端 | 命令值 | 用途 |
|---|---|---|
| 启发式本地验证 | `heuristic` | 默认选项，快速、无 API、适合联调 |
| HuggingFace 本地模型 | `transformers` | 可选，下载本地模型后运行 |
| 外部 API | `api` | 推荐展示方案，使用 OpenAI-compatible chat completions |
| 跳过语义验证 | `none` | 只跑 Layer 1 |

### 2.3 `before_after_compare.py`

作用：比较引入 evidence tracking 前后的报告覆盖率。

主要指标：

- claim sentence estimate
- evidence citation count
- `[NO_EVIDENCE]` count
- citation completeness
- unsupported visible rate

### 2.4 `.env`

作用：配置外部 NLI API。

该文件已被 `.gitignore` 忽略，不会提交真实 API key。

## 3. 外部 API 配置

编辑仓库根目录 `.env`：

```bash
NLI_API_KEY=你的真实API_KEY
NLI_API_BASE_URL=https://api.openai.com/v1
NLI_API_CHAT_PATH=/chat/completions
NLI_API_MODEL=gpt-4o-mini
NLI_API_TIMEOUT_SECONDS=60
NLI_API_TEMPERATURE=0
```

只要服务兼容 OpenAI `/chat/completions` 格式，就可以替换。

常见配置示例：

```bash
# OpenAI
NLI_API_BASE_URL=https://api.openai.com/v1
NLI_API_MODEL=gpt-4o-mini

# DeepSeek
NLI_API_BASE_URL=https://api.deepseek.com
NLI_API_MODEL=deepseek-chat

# Kimi
NLI_API_BASE_URL=https://api.moonshot.cn/v1
NLI_API_MODEL=moonshot-v1-8k

# OpenRouter
NLI_API_BASE_URL=https://openrouter.ai/api/v1
NLI_API_MODEL=openai/gpt-4o-mini
```

注意：

- 不要把真实 API key 写进提交。
- 如果 `NLI_API_KEY` 仍是 `replace_with_your_api_key`，脚本会把 API 验证标为 `skipped`，不会真的请求 API。

## 4. 运行环境

建议使用 conda 环境 `agent`：

```bash
conda run -n agent python -m pip install -e . pytest
```

如果使用外部 API，项目依赖里的 `httpx` 已经足够。

如果使用 HuggingFace 本地模型，需要额外安装：

```bash
conda run -n agent python -m pip install torch transformers sentencepiece
```


## 5. 使用方法

下面的命令默认把生成结果写入仓库根目录的 `outputs/` 文件夹。运行前请确认该文件夹存在，本文档命令使用当前 demo quest：`/home/jackpot/DeepScientist/quests/evidence-demo`。如果之后换项目，再把这个路径替换成新的 quest 根目录。

### 5.0 输入/输出文件说明

C 部分脚本会读入一些报告文件，也会生成一些结果文件。`outputs/` 只是我们约定的演示目录，不是 DeepScientist 自动创建的 quest 目录。

#### 需要你准备的输入文件

| 文件 | 用在哪个命令 | 作用 | 是否自动生成 |
|---|---|---|---|
| `outputs/report.md` | `verify_evidence.py` | 待验证的研究总结/报告，里面应该包含 `[EVD-xxx:level]` 引用 | 否，需要手动准备或由 agent 输出后保存 |
| `outputs/before.md` | `before_after_compare.py` | 没有 evidence tracking 之前的报告 | 否，需要手动准备 |
| `outputs/after.md` | `before_after_compare.py` | 加入 `[EVD-xxx:level]` 和 `[NO_EVIDENCE]` 之后的报告 | 否，需要手动准备 |

`outputs/report.md` 示例：

```md
# Demo Report

The model reaches 93.2% accuracy [EVD-xxxxxxxx-001:supported].
```

`outputs/before.md` 示例：

```md
# Before Report

The model reaches 93.2% accuracy.
The model is faster than the baseline.
```

`outputs/after.md` 示例：

```md
# After Report

The model reaches 93.2% accuracy [EVD-xxxxxxxx-001:supported].
The model is faster than the baseline [NO_EVIDENCE].
```

注意：上面的 `EVD-xxxxxxxx-001` 要替换成真实 evidence id。可以从 `outputs/evidence_table.md` 或 `outputs/evidence_table.json` 中查到。

#### 脚本生成的输出文件

| 文件 | 由哪个命令生成 | 内容 |
|---|---|---|
| `outputs/evidence_table.md` | `evidence_table.py` | Markdown 版 evidence table |
| `outputs/evidence_table.json` | `evidence_table.py` | JSON 版 evidence table，可供程序读取 |
| `outputs/verify.md` | `verify_evidence.py` | Markdown 版双层验证报告 |
| `outputs/verify.json` | `verify_evidence.py` | JSON 版双层验证结果和指标 |
| `outputs/compare.md` | `before_after_compare.py` | Markdown 版 before/after 覆盖率对比 |
| `outputs/compare.json` | `before_after_compare.py` | JSON 版 before/after 指标对比 |

### 5.1 生成 Evidence Table

```bash
PYTHONPATH=src:scripts conda run -n agent python -m deepscientist.artifact.evidence_table \
  --quest-root /home/jackpot/DeepScientist/quests/evidence-demo \
  --md-out outputs/evidence_table.md \
  --json-out outputs/evidence_table.json
```

### 5.2 使用外部 API 做双层验证

```bash
PYTHONPATH=src:scripts conda run -n agent python scripts/verify_evidence.py \
  --quest-root /home/jackpot/DeepScientist/quests/evidence-demo \
  --report outputs/report.md \
  --nli-backend api \
  --env-file .env \
  --json-out outputs/verify.json \
  --md-out outputs/verify.md
```

### 5.3 使用本地 heuristic 快速验证

```bash
PYTHONPATH=src:scripts conda run -n agent python scripts/verify_evidence.py \
  --quest-root /home/jackpot/DeepScientist/quests/evidence-demo \
  --report outputs/report.md \
  --nli-backend heuristic \
  --json-out outputs/verify.json \
  --md-out outputs/verify.md
```

### 5.4 做 before/after 对比

```bash
PYTHONPATH=src:scripts conda run -n agent python scripts/before_after_compare.py \
  --before-report outputs/before.md \
  --after-report outputs/after.md \
  --json-out outputs/compare.json \
  --md-out outputs/compare.md
```

## 6. 输出指标解释

`verify_evidence.py` 输出的主要指标：

| 指标 | 含义 |
|---|---|
| `agent_nli_agreement_rate` | Agent 自评 level 与外部 NLI 判断一致的比例 |
| `hallucination_rate` | Agent 标为 `supported`，但 NLI 判为 `neutral` / `contradiction` / `unverifiable` 的比例 |
| `unverifiable_rate` | 因缺少摘录、跳过后端或证据不足而无法验证的比例 |
| `citation_completeness` | 报告中带 `[EVD-xxx:level]` 标注的 claim 占比估计 |

Layer 2 标签：

| 标签 | 含义 |
|---|---|
| `entailment` | source excerpt 支持 claim |
| `neutral` | source excerpt 相关但不能直接支持 claim |
| `contradiction` | source excerpt 与 claim 冲突 |
| `unverifiable` | 缺少摘录或该 evidence 本来就是 insufficient/retracted |
| `skipped` | 后端被关闭、配置缺失或 API 调用失败 |

## 7. 测试命令

运行 C 部分测试：

```bash
conda run -n agent python -m pytest tests/test_evidence_validation_c.py -q
```

运行 A 的 evidence 存储测试，确认没有破坏已有契约：

```bash
conda run -n agent python -m pytest tests/test_evidence_tracking.py -q
```

一次性运行：

```bash
conda run -n agent python -m pytest tests/test_evidence_validation_c.py tests/test_evidence_tracking.py -q
```

当前验证结果：

```text
26 passed
```

## 8. 推荐展示流程

1. 让 agent 生成带 `[EVD-xxx:level]` 的研究总结。
2. 运行 `evidence_table.py` 生成 evidence table。
3. 运行 `verify_evidence.py --nli-backend api` 生成双层验证报告。
4. 准备一份没有 evidence tracking 的 before report。
5. 准备一份带 evidence tracking 的 after report。
6. 运行 `before_after_compare.py` 展示 citation completeness 提升。
7. 重点展示 hallucination case：Agent 标 `supported`，但外部 API 判 `neutral` 或 `contradiction`。

## 9. 当前边界

- C 部分不会重新实现 `artifact.evidence_record(...)`，只读取 A 已经生成的 evidence 文件。
- API 后端默认假设服务兼容 OpenAI chat completions。
- 外部 API 的判断质量取决于所选模型和 prompt。
- `citation_completeness` 是基于报告句子的启发式估计，不是严格自然语言 claim parser。
- 如果 `source_excerpt` 不是来源原文逐字摘录，外部 NLI 的判断会失真；这也是 B 部分强调 `source_excerpt` 必须逐字引用的原因。
