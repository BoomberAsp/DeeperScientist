# DeepScientist Architecture

This document describes the architecture of DeepScientist, a local-first autonomous research studio that manages long-horizon research workflows from baselines through experiments to paper outputs.

## System Overview

DeepScientist follows a **launcher + daemon + agent runners** architecture. The core insight is that each research quest is a Git repository, and all durable state lives in files and Git commits. The system orchestrates autonomous AI agents (called "runners") through structured MCP (Model Context Protocol) tools, with research workflow stages defined as "skills" that inject context and behavioral constraints into agent prompts.

```
User/IM → Daemon (HTTP API) → Runner → Subprocess (codex/claude/opencode) ←→ MCP Servers (stdio)
                                     ↕
                              Quest (Git Repo)
```

## Launch Chain

```
ds (npm global binary)
  └─ bin/ds.js (Node.js launcher)
       └─ uv-managed Python runtime (~/DeepScientist/runtime/python-env)
            └─ src/deepscientist/daemon/app.py (DaemonApp)
                 └─ HTTP server at http://127.0.0.1:20999
                      ├─ Web UI (React, src/ui/)
                      └─ TUI (Node.js, src/tui/)
```

**Runtime Home** (`~/DeepScientist/`):
- `runtime/` — uv-managed Python environment and tools
- `config/` — YAML configuration and baseline registry
- `memory/` — Global memory cards (shared across quests)
- `quests/` — One quest per Git repository (the core data unit)
- `logs/` — Daemon and runtime logs
- `cache/` — Reusable caches (synced skills, etc.)

## Agent Architecture (the core loop)

The agent architecture has four layers that compose together:

### 1. Runners — The Agent Executor Layer

Runners are the actual agent processes. Each runner wraps a CLI-based AI coding agent (e.g., Codex CLI, Claude Code, OpenCode, Kimi Code) and manages its lifecycle as a subprocess.

```
src/deepscientist/runners/
├── base.py          # RunRequest, RunResult dataclasses
├── simple_cli.py    # SimpleCliRunner base class
├── codex.py         # CodexRunner (primary, battle-tested)
├── claude.py        # ClaudeRunner (experimental)
├── opencode.py      # OpenCodeRunner (experimental)
├── kimi.py          # KimiRunner
├── registry.py      # RunnerFactory registry (name → factory)
├── builtins.py      # register_builtin_runners()
└── metadata.py      # RunnerMetadata (name, label, binary, config dir, etc.)
```

**Runner Interface** (defined in `base.py`):

```python
RunRequest(quest_id, quest_root, worktree_root, run_id, skill_id,
           message, model, approval_policy, sandbox_mode, ...)

RunResult(ok, run_id, model, output_text, exit_code, history_root, ...)
```

**Execution Flow** (in `SimpleCliRunner.run()`):

1. Build prompt via `PromptBuilder.build()` — this assembles the system prompt, skill instructions, memory plan, and quest context
2. Write prompt to `run_root/prompt.md`
3. Set environment variables (`DS_QUEST_ID`, `DS_QUEST_ROOT`, `DS_RUN_ID`, `DS_ACTIVE_ANCHOR`, `DS_AGENT_ROLE`, etc.)
4. Spawn runner binary as subprocess (e.g., `codex exec`, `claude -p`)
5. The runner subprocess communicates with MCP servers over stdio
6. Parse output, log events, return `RunResult`

**Runner Metadata** (in `metadata.py`):

| Runner  | Label      | Binary    | Config Dir            |
|---------|------------|-----------|-----------------------|
| codex   | Codex      | `codex`   | `~/.codex`            |
| claude  | Claude     | `claude`  | `~/.claude`           |
| opencode| OpenCode   | `opencode`| `~/.config/opencode`  |
| kimi    | Kimi Code  | `kimi`    | `~/.kimi`             |

**Approval Policy** (in `codex.py`): The Codex runner defines which MCP tools are auto-approved per namespace. Different custom profiles (e.g., `settings_issue`, `start_setup_prepare`) expose different tool subsets.

### 2. MCP Servers — The Tool Layer

The MCP (Model Context Protocol) layer exposes research-domain tools to the runner subprocesses. There are exactly **three** public MCP namespaces, each backed by a domain service:

```
src/deepscientist/mcp/
├── server.py    # build_memory_server(), build_artifact_server(), build_bash_exec_server()
└── context.py   # McpContext (environment-derived context for tool execution)
```

Each MCP server is a `FastMCP` instance that communicates over **stdio** with the runner subprocess. The server functions (`build_memory_server`, `build_artifact_server`, `build_bash_exec_server`) are called by the CLI entry point (run with `stdio` transport).

#### Memory Server (`memory`)

Tools: `write`, `read`, `search`, `list_recent`, `promote_to_global`

Backed by `MemoryService`, which manages markdown files with YAML frontmatter organized by:
- **Scope**: `quest` (per-quest) or `global` (shared across quests)
- **Kind**: `papers`, `ideas`, `decisions`, `episodes`, `knowledge`, `templates`

Cards are stored as files under `quest_root/memory/<kind>/` or `~/DeepScientist/memory/<kind>/`.

#### Artifact Server (`artifact`)

Tools: `record`, `science`, `checkpoint`, `submit_idea`, `confirm_baseline`, `get_quest_state`, `create_analysis_campaign`, `submit_paper_outline`, `interact`, `complete_quest`, and ~20 more.

Backed by `ArtifactService`, the richest domain service. It manages:
- **Research artifacts** as structured JSON/YAML files under `quest_root/artifacts/`
- **Git operations** for versioning (branches, commits, worktrees)
- **Science nodes** (computational runs, dataset analysis, parameter sweeps, claims)
- **Paper contract** (outlines, writing plan, evidence ledger, analysis inventory)
- **Baseline management** (import, attach, confirm, compare, publish)
- **Metrics** with contract validation

#### BashExec Server (`bash_exec`)

Tools: `bash_exec` (single tool with multiple modes: `run`, `await`, `read`, `stop`, `status`, `list`)

Backed by `BashExecService`, which provides managed shell sessions with:
- Persistent session logs under `.ds/bash_exec/`
- Timeout and lifecycle management
- Log truncation, windowing, and continuation (seq-based navigation)
- All terminal operations MUST go through this tool (native shell is forbidden)

### 3. Skills — The Workflow Layer

Skills define the research workflow as stage-gated behavioral prompts. Each skill is a markdown file with frontmatter metadata that the PromptBuilder injects into the agent's system prompt.

```
src/skills/
├── scout/SKILL.md              # Problem framing, literature scouting
├── baseline/SKILL.md           # Baseline reproduction
├── idea/SKILL.md               # Idea generation
├── experiment/SKILL.md         # Experiment execution
├── analysis-campaign/SKILL.md  # Analysis campaigns
├── write/SKILL.md              # Paper writing
├── finalize/SKILL.md           # Final deliverables
├── decision/SKILL.md           # Decision recording
├── figure-polish/SKILL.md      # Figure refinement (companion)
├── paper-plot/SKILL.md         # Paper plotting (companion)
├── review/SKILL.md             # Paper review (companion)
├── rebuttal/SKILL.md           # Rebuttal generation (companion)
└── intake-audit/SKILL.md       # Initial quest setup (companion)
```

**Skill Contract** (SKILL.md frontmatter):
```yaml
---
name: scout
description: Problem framing, literature scouting, dataset/metric clarification
skill_role: stage          # stage | companion | custom
skill_order: 60            # Controls ordering in the workflow
---
```

**Skill Roles**:
- **Stage skills** — The main research pipeline: scout → baseline → idea → experiment → analysis-campaign → write → finalize
- **Companion skills** — Auxiliary capabilities: paper-outline, figure-polish, review, rebuttal, intake-audit
- **Custom skills** — User-defined or external skills

**Skill Discovery**: `discover_skill_bundles()` in `src/deepscientist/skills/registry.py` scans `src/skills/*/SKILL.md` and builds `SkillBundle` dataclasses with parsed metadata. Skills are sorted by `skill_order`.

**Memory Plan**: Each stage skill has a memory plan in `src/deepscientist/prompts/builder.py` (`STAGE_MEMORY_PLAN`) that defines which memory kinds (papers, decisions, ideas, episodes, knowledge, templates) are visible at quest and global scope for that stage.

### 4. Prompts — The Composition Layer

`PromptBuilder` (in `src/deepscientist/prompts/builder.py`) assembles the full prompt for each agent run by composing:

1. **System prompt** — Core research instructions from `src/prompts/system.md`
2. **Skill prompt** — The skill's SKILL.md body content (stage-specific workflow instructions)
3. **Memory plan** — Which memory kinds to load and display
4. **Quest context** — `brief.md`, `plan.md`, `status.md`, `SUMMARY.md` from the quest repository
5. **Retry context** — Error recovery context from previous attempts

The prompt builder also provides:
- `classify_turn_intent()` — Classifies user messages to determine next action
- `current_standard_skills()` / `current_companion_skills()` — Returns active skill IDs

## Quest System

A quest is the fundamental unit of work — one Git repository per research project.

```
quest_id/                            # Git repository
├── quest.yaml                       # Metadata (status, active_anchor, runner, baseline_gate)
├── brief.md                         # Research brief
├── plan.md                          # Implementation plan
├── status.md                        # Current status
├── SUMMARY.md                       # Quest summary
├── artifacts/                       # Structured research artifacts
│   ├── baselines/                   # Baseline entries
│   ├── ideas/                       # Research ideas
│   ├── decisions/                   # Decision records
│   ├── experiments/main/            # Main experiment results
│   ├── experiments/analysis/        # Analysis campaign results
│   ├── milestones/                  # Milestone records
│   ├── progress/                    # Progress records
│   ├── runs/                        # Run records
│   ├── reports/                     # Report artifacts
│   ├── approvals/                   # Approval records
│   └── graphs/                      # Graph records
├── baselines/
│   ├── imported/                    # Imported baseline packages
│   └── local/                       # Locally created baselines
├── literature/                      # Literature survey notes
├── memory/                          # Quest-scoped memory
│   ├── papers/
│   ├── ideas/
│   ├── decisions/
│   ├── episodes/
│   └── knowledge/
├── paper/                           # Paper drafts and LaTeX
├── userfiles/                       # User-uploaded files
├── handoffs/                        # Handoff artifacts
├── tmp/                             # Temporary files
└── .ds/                             # Runtime state (not committed)
    ├── runtime_state.json           # Current quest state
    ├── user_message_queue.json      # Queued user messages
    ├── events.jsonl                 # Event log (all tool calls, runs, errors)
    ├── interaction_journal.jsonl    # User-visible interactions
    ├── bash_exec/                   # Bash session logs
    ├── conversations/               # Conversation state per connector
    ├── codex_history/               # Runner-specific history
    ├── runs/                        # Per-run prompt and output
    └── worktrees/                   # Git worktrees for parallel branches
```

**Quest Lifecycle**:
1. `ds init` / `POST /api/quests` — Creates quest repo with initial files
2. Quest enters the active stage loop (scout → baseline → idea → ...)
3. `QuestService.reconcile_runtime_state()` — Recovers crashed quests on startup
4. Quest status progresses: `idle` → `active` → `paused` → `completed`

**Stage Anchors**: The `active_anchor` field in `quest.yaml` tracks the current stage. Stages advance when their exit criteria are met (e.g., scout → baseline when a baseline direction is justified).

## Daemon Architecture

The `DaemonApp` is the central orchestrator — a multi-threaded HTTP server (`ThreadingHTTPServer`) that:

- Manages service lifecycle (initialization, startup, shutdown)
- Routes HTTP requests via regex-based router (`match_route()`)
- Coordinates the quest execution loop (user message → runner → MCP tools → state updates)
- Manages connector channels for multi-platform chat integration
- Handles authentication, sessions, and admin operations

**Service Initialization** (in `DaemonApp.__init__()`):
```
ConfigManager → RuntimeConfig, RunnersConfig, ConnectorsConfig
SkillInstaller → Sync skills to home
QuestService → Reconcile runtime state (recover crashes)
MemoryService, AnnotationService, ArtifactService
BenchStoreService, BashExecService
SingleTeamService, CloudLinkService
PromptBuilder → Build prompts from repo_root and home
CodexRunner, ClaudeRunner, KimiRunner, OpenCodeRunner → Register via factory
ConnectorBridges → Register (QQ, WeChat, Telegram, Discord, Slack, Feishu, WhatsApp)
Channels → Register (local, QQ relay, WeChat relay, generic relay)
ApiHandlers → HTTP request dispatching
```

**API Routes** (in `src/deepscientist/daemon/api/router.py`): The REST API exposes:
- `/api/health` — Health check
- `/api/quests` — CRUD for quests
- `/api/quests/:id/chat` — Send messages to quest agents
- `/api/quests/:id/commands` — Send commands
- `/api/quests/:id/control` — Pause, resume, stop quests
- `/api/quests/:id/events` — Stream quest events (SSE)
- `/api/quests/:id/runs` — Create and list runs
- `/api/quests/:id/terminal/*` — Terminal session management
- `/api/quests/:id/git/*` — Git operations
- `/api/quests/:id/memory` — Memory access
- `/api/connectors` — Connector management
- `/api/system/*` — System diagnostics, logs, hardware
- `/api/admin/*` — Admin operations
- `/api/config/*` — Configuration management

**Turn Execution**: The daemon's `_run_quest_turn()` method executes one turn of the quest loop:
1. Dequeue user message from `user_message_queue.json`
2. Build `RunRequest` with current skill, message, model, etc.
3. Call `runner.run(request)` → spawns subprocess
4. Parse results, update quest state
5. If the agent requests continuation, queue auto-continue

## Git Operations Layer

The git layer (`src/deepscientist/gitops.py`) provides:
- Repository initialization (`init_repo`)
- Branch management (`ensure_branch`, `branch_exists`, `current_branch`)
- Worktree management (`create_worktree`, `canonical_worktree_root`)
- Commit inspection (`commit_detail`, `head_commit`, `compare_refs`, `diff_file_between_refs`)
- Graph visualization (`export_git_graph`, `list_branch_canvas`, `list_commit_canvas`)

This enables the artifact system to create research branches per idea, run experiments in isolated worktrees, and track the full research history.

## Multi-Platform Integration (Connectors/Channels/Bridges)

DeepScientist integrates with multiple chat platforms through a three-layer architecture:

```
External Platform (QQ, WeChat, Telegram, etc.)
        ↕
Transport Layer (channels/)     — Long-lived connections, polling, webhooks
        ↕
Protocol Layer (bridges/)       — Platform-specific message parsing and sending
        ↕
Conversation Layer (daemon)     — Quest binding, message routing, turn execution
```

**Bridges** (`src/deepscientist/bridges/`) — Platform-specific message parsing:
- `BaseConnectorBridge` → `parse_webhook()`, `send_message()`
- Implementations: `QQConnectorBridge`, `WeixinConnectorBridge`, `TelegramConnectorBridge`, `DiscordConnectorBridge`, `SlackConnectorBridge`, `FeishuConnectorBridge`, `WhatsAppConnectorBridge`
- Registry: `register_connector_bridge(name, factory)`

**Channels** (`src/deepscientist/channels/`) — Transport-level connections:
- `LocalChannel` — Direct local API access
- `QQRelayChannel` — QQ relay (HTTP relay to QQ bot)
- `WeixinRelayChannel` — WeChat relay (HTTP relay to WeChat Official Account)
- `GenericRelayChannel` — Generic relay for Telegram, Discord, Slack, Feishu, WhatsApp
- Gateway services: `QQGatewayService`, `DiscordGatewayService`, `FeishuLongConnectionService`, `SlackSocketModeService`, `TelegramPollingService`, `WhatsAppLocalSessionService`, `WeixinIlinkService`

**Conversation Identity** (`src/deepscientist/connector_runtime.py`):
- `format_conversation_id(connector, chat_type, chat_id)` → `"telegram:direct:123456"`
- This binds each chat to a specific quest

## Configuration System

`ConfigManager` (`src/deepscientist/config/`) manages YAML configuration files under `~/DeepScientist/config/`:

| File | Content |
|------|---------|
| `config.yaml` | Runtime settings (logging, UI, skills, bootstrap, connectors) |
| `runners.yaml` | Runner configuration (binary path, model, env, timeouts) |
| `connectors.yaml` | Connector settings (API keys, webhook secrets, chat bindings) |
| `baselines.yaml` | Baseline registry |

Runners can be configured per-model, with approval policies, sandbox modes, and environment variables.

## Data Flow: A Complete Turn

```
1. User sends message (via Web UI, TUI, or IM connector)
2. Daemon receives message → enqueues to user_message_queue.json
3. Daemon decides to execute a turn:
   a. Reads quest state (quest.yaml, active_anchor)
   b. Builds RunRequest with current skill, message, model
   c. runner.run(request):
      - PromptBuilder builds full prompt:
        * system.md + skill SKILL.md + memory plan + quest context
      - Writes prompt to .ds/runs/<run_id>/prompt.md
      - Sets environment variables
      - Spawns runner binary subprocess (e.g., codex exec)
      - Runner binary connects to MCP servers over stdio
      - Agent executes tool calls via MCP:
        * memory.read/search → retrieves relevant knowledge
        * artifact.get_quest_state → reads current state
        * bash_exec → runs shell commands, scripts, experiments
        * artifact.record/checkpoint → writes results
        * artifact.interact → sends user-visible updates
      - Runner binary exits with output
   d. Daemon parses output, logs events to events.jsonl
   e. Updates quest state, advances anchor if needed
   f. Sends response to user
4. If agent requests, auto-continue next turn (with backoff)
```

## Key Design Principles

1. **One quest = one Git repository** — All durable state in files and Git. No hidden database.
2. **File-based memory** — Memory cards are markdown files with YAML frontmatter.
3. **MCP as tool boundary** — Only three namespaces: `memory`, `artifact`, `bash_exec`. No new namespaces.
4. **Skills as prompts** — Workflow behavior comes from prompts and skill instructions, not rigid schedulers.
5. **Runners as subprocesses** — Each runner is an external CLI process, isolating the agent runtime from the daemon.
6. **Connectors are transport-agnostic** — The bridge/channel/connector three-layer model separates protocol from transport.
7. **Managed shell execution** — All bash must go through `bash_exec` for logging, lifecycle, and safety.
8. **Local-first** — Everything runs locally. No cloud dependency for core functionality.
