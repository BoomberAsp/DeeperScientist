# DeeperScientist — Evidence Chain Tracking for Autonomous Research Agents

DeeperScientist 是 [DeepScientist](https://github.com/ResearAI/DeepScientist) 的增强版本，为自主科研 Agent 新增**证据链追踪与语义验证系统**。

**核心问题**：LLM Agent 在科研过程中生成声明（如 "模型准确率达 93.2%"），但原始系统无法追溯这些声明的来源，也无法独立验证其可信度。

**DeeperScientist 的回答**：每个声明必须绑定可审计的证据记录，由外部 NLI 模型独立判定来源是否真的支持声明。

---

## 相比原版的三大贡献

### 贡献一：结构化证据存储 — 可追溯

原版 Agent 输出是自由文本，声明与来源之间没有硬链接。DeeperScientist 新增文件原生、Git 版本化的证据存储层：

```
quest/
└── artifacts/
    └── evidence/
        ├── INDEX.md                  ← fcntl 锁定，快速查表
        ├── EVD-0c8841da-001.md       ← YAML 前置元数据 + Markdown 正文
        └── EVD-a1b2c3d4-002.md
```

每条证据记录包含 claim、evidence_level（supported / inferred / insufficient / retracted）、source_type（11 种）、source_excerpt（supported / inferred 级别强制必填）、claim_relation 等字段。文件即数据库，`git log` 即审计追踪。

**设计原则**：
- **File-Native**：无数据库依赖，YAML frontmatter = 结构化
- **Lock-Safe**：fcntl.LOCK_EX 保证 INDEX.md 并发写入安全
- **Git-Versioned**：每次证据变更可追溯，git log = 完整审计轨迹
- **INDEX.md Fast Path**：无需扫描目录，O(1) 查表

### 贡献二：双层引用完整性校验 — 可审计

原版 DeepScientist 仅检查引用格式。DeeperScientist 实现真正的三层架构：

| 层 | 检查内容 | 方式 |
|----|---------|------|
| **Layer 1** | EVD ID 是否注册、等级是否匹配、是否引用了已撤销证据 | INDEX.md 确定性交叉比对 |
| **Layer 1.5** | source_excerpt 是否在原始来源中真实存在 | 独立获取来源（arXiv / URL / 本地），滑动窗口模糊匹配（≥85%），检测引文伪造与摘录失真 |
| **Layer 2** | source_excerpt 是否在语义上支持 claim | 外部 NLI 三级级联验证 |

### 贡献三：语义可验证 — 降低幻觉

尽管 LLM 幻觉多出现在虚假引用上，且该点已经在上一层被校验，但我们仍然处理了 Agent 自评 evidence_level 不可靠的问题（Agent 可能乐观地把弱相关证据标为 supported）。DeeperScientist 引入外部 NLI 模型、LLM模型的级联验证：

```
source_excerpt + claim → [Heuristic] → [DeBERTa-v3 NLI] → [LLM API]
                              ↓              ↓                ↓
                        entailment/    entailment/       entailment/
                        neutral/       neutral/          neutral/
                        contradiction  contradiction     contradiction
```

三个后端互补——启发式零成本粗筛（token 重叠 + 否定检测），Transformers 语义推理（DeBERTa-v3），LLM API 深度判断 + 自然语言理由。需要注意的是，轻量 NLI 模型的逻辑推理能力很可能不如 Agent（LLM） 本身，因此将证据 schema 与对应源文件摘录交给一个只做证据语义查验（避免幻觉上下文污染）的 LLM 是有必要的。

---

## 关键指标

| 指标 | 说明 |
|------|------|
| `agent_nli_agreement_rate` | Agent 自评与外部 NLI 的一致性 |
| `hallucination_rate` | 标注为 supported 但 NLI 判定为 contradiction 的比例 |
| `unverifiable_rate` | 无 source_excerpt 导致无法验证的声明比例 |
| `citation_completeness` | 有证据引用的声明数 / 总声明数 |

---

## 新增文件

```
├── src/deepscientist/artifact/
│   ├── evidence_table.py          # EvidenceRecord 数据类、加载、渲染
│   ├── evidence_verifier.py       # 双层验证引擎 (Layer 1 + 1.5 + 2)
│   ├── source_fetcher.py          # 来源保真度验证
│   └── evidence_packets.py        # 大体积工具结果压缩侧载
├── src/skills/evidence-track/SKILL.md          # 证据追踪伴侣技能
├── src/prompts/contracts/evidence_tracking.md  # 注入 Agent prompt 的契约
├── scripts/
│   ├── verify_evidence.py         # 证据验证 CLI
│   └── before_after_compare.py    # 追踪前后对比
├── tests/
│   ├── test_evidence_tracking.py  # 存储层测试 (20 个)
│   ├── test_evidence_validation_c.py  # 验证引擎测试 (30+ 个)
│   └── evidence_chain_test/
│       ├── test_evidence.md       # 测试用研究提示
│       └── run_comparison.py      # 自动前后对比编排
└── outputs/                       # 海报素材与演示数据
```

### 修改的原始文件

| 文件 | 变更 |
|------|------|
| `src/deepscientist/artifact/schemas.py` | 新增 `validate_evidence_payload()`，条件强制 `source_excerpt` |
| `src/deepscientist/artifact/service.py` | 新增 7 个证据 CRUD 方法 + INDEX.md 维护 + fcntl 锁 |
| `src/deepscientist/mcp/server.py` | 新增 6 个证据 MCP 工具 |
| `src/deepscientist/runners/codex.py` | 证据工具加入自动批准列表 |
| `src/deepscientist/prompts/builder.py` | 注入证据合约；`DEEPSCIENTIST_SKIP_EVIDENCE_TRACKING` 开关 |
| `src/deepscientist/quest/layout.py` | 新增 `artifacts/evidence` 和 `memory/evidence` 目录 |
| `src/deepscientist/memory/service.py` | 新增 `evidence` 到 MEMORY_KINDS |

---

## 快速开始

### 安装

```bash
git clone https://github.com/BoomberAsp/DeeperScientist.git
cd DeeperScientist
bash install.sh
```

### 运行 Before/After 对比测试

```bash
# 启动 daemon
ds

# 完整对比测试
cd tests/evidence_chain_test
python run_comparison.py all

# 输出在 outputs/ 目录下
```

也可以分步运行：

```bash
python run_comparison.py setup      # 创建 quest
python run_comparison.py before     # 无证据链运行
python run_comparison.py after      # 有证据链运行
python run_comparison.py compare    # 对比分析（不再运行 Agent）
```

### 手动运行验证脚本

```bash
python scripts/verify_evidence.py \
  --quest-root ~/DeepScientist/quests/<quest-id> \
  --report path/to/agent_output.md \
  --nli-backend cascade \
  --json-out outputs/verify.json \
  --md-out outputs/verify.md

python scripts/before_after_compare.py \
  --before-report outputs/before.md \
  --after-report outputs/after.md \
  --json-out outputs/compare.json \
  --md-out outputs/compare.md
```

### 运行测试

```bash
pytest tests/test_evidence_tracking.py tests/test_evidence_validation_c.py -v
```

---

## 兼容性

完全向后兼容。设置 `DEEPSCIENTIST_SKIP_EVIDENCE_TRACKING=1` 即可恢复原始 DeepScientist 行为。所有证据 MCP 工具是新增的，不影响现有 `memory`、`artifact`、`bash_exec` 命名空间。

---

## 引用

本项目基于 DeepScientist。如果使用本工作，请同时引用：

```bibtex
@inproceedings{
weng2026deepscientist,
title={DeepScientist: Advancing Frontier-Pushing Scientific Findings Progressively},
author={Yixuan Weng and Minjun Zhu and Qiujie Xie and QiYao Sun and Zhen Lin and Sifan Liu and Yue Zhang},
booktitle={The Fourteenth International Conference on Learning Representations},
year={2026},
url={https://openreview.net/forum?id=cZFgsLq8Gs}
}
```
