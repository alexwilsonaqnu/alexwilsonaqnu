# State-of-the-Art Agentic Harnessing
## A Research Guide for Designing Agent-Native Repos, Skills, and Multi-Agent Software Groups

*Compiled May 20, 2026 — current as of Claude Code v2.x, Claude Agent SDK v0.1.48 (Python) / v0.2.71 (TS), Claude Managed Agents (public beta), MCP spec 2025-06-18 + 2026 roadmap drafts, Agent Skills open standard (agentskills.io).*

---

## How to use this guide

This is a working reference, not a polemic. The field changes monthly. I've tried to be honest about which patterns are settled, which are emerging, and where the rough edges still are. Where I cite a number (token counts, agent counts, SDK downloads) it's from a primary or near-primary source from Q1–Q2 2026.

The guide is structured so you can read it linearly to build a mental model, or jump to a section when you're making a specific design decision. Section 19 is a reference architecture you can clone straight to a repo.

**Assumed reader:** someone building production agentic systems on Claude (Agent SDK, Claude Code, or Managed Agents), comfortable with the orchestrator-subagent pattern, MCP, and OTel. If that's not you, sections 1–4 still work as primer material.

---

## Table of contents

1. The mental model: harness, agent, skill, subagent, MCP, hook
2. The five-layer architecture
3. The agent contract: what specifying a subagent actually means
4. The .claude directory: anatomy and load order
5. CLAUDE.md design (the 2026 version)
6. SKILL.md: the open standard, in full
7. Progressive disclosure as a first-class design pattern
8. Designing MCP servers (not REST wrappers)
9. Hooks: deterministic control where prompts can't be trusted
10. Context engineering as a discipline
11. The long-running agent harness: initializer + coding agent
12. Memory: four layers and when to use which
13. The six composable agent patterns
14. Evaluator-optimizer and quality loops
15. Adding agents on the fly: hot-swap, marketplaces, agent-as-tool
16. Observability: OTel GenAI semantic conventions
17. Security and supply-chain hygiene
18. Anti-patterns: layer confusion and how to spot it
19. Reference architecture: a clonable skeleton
20. Templates (SKILL.md, subagent, hooks, settings.json)
21. Operating model: what a 2026 software group actually looks like
22. Open problems and where this is heading

---

## 1. The mental model: harness, agent, skill, subagent, MCP, hook

The single biggest source of "my agent setup is a mess" is treating *the agent* as the unit of design. It isn't. The unit is the **primitive**, and there are six of them, each with a different scope and load behavior. Get these straight and most architectural debates resolve themselves.

| Primitive | What it is | Where it lives | When it loads | Scope |
|---|---|---|---|---|
| **Harness** | The runtime that executes the agent loop, mediates tool calls, manages context, fires hooks | Either Claude Code, the Agent SDK in your process, or Managed Agents | Always running | Process |
| **Agent** | A Claude model instance + system prompt + tools + a context window | Inside the harness | Spawn on session start | One context window |
| **Skill** | A folder with `SKILL.md` + optional scripts/refs that an agent loads when its description matches the task | `.claude/skills/<name>/` or uploaded to the API | Frontmatter always; body on-demand | In-context, same window |
| **Subagent** | An isolated Claude instance spawned by the main agent for a scoped task, one-way result back | `.claude/agents/<name>.md` | On invocation | Separate context, returns a summary |
| **Agent Team** (early 2026) | Multiple agents in separate processes, bidirectional comms, shared task list | Managed Agents | Spawned as session children | Separate processes |
| **MCP server** | A tool/data surface speaking the Model Context Protocol | External process or remote URL | Persistent connection | Cross-session |
| **Hook** | A deterministic script fired at one of ~25 lifecycle events | `.claude/hooks/` registered in `settings.json` | At the event | Cross-cutting |

The architecture stack reads top-down like this:

```
Harness                      ← the runtime
└── Main Agent               ← runs inside the harness
    ├── CLAUDE.md            ← persistent project memory
    ├── Skills               ← loaded into the same context on match
    ├── MCP tools            ← external capabilities
    ├── Subagents            ← isolated workers, one-way result back
    └── Agent Teams          ← separate processes, bidirectional
Hooks                        ← fire at lifecycle events, cross-cutting
```

The mistake is using the wrong primitive for the job. Behavioral *guarantees* (don't `rm -rf`, always run lint after edits) belong in hooks because hooks cannot be reasoned around by the model. Reusable *workflows* (how to fill a PDF form, how to write a commit message) belong in skills because they should activate on description match. Specialist *reasoning* under context isolation (security review of a 50-file diff) belongs in a subagent because the exploration noise must not pollute the main window. External *data and actions* (Asana, Stripe, your DB) belong in MCP servers because they need their own authn/authz and lifecycle.

Confusing them is the root cause of most failures: behavioral constraints in CLAUDE.md drift; reusable workflows pasted into every prompt blow context; specialist reasoning in the main agent corrupts the planner; external API calls bolted onto the agent surface skip auditing and rate limits.

---

## 2. The five-layer architecture

A useful way to think about an end-to-end Claude system, from a March 2026 Anthropic webinar:

1. **MCP** — connectivity. How the agent reaches your data and acts on your systems.
2. **Skills** — task-specific procedural knowledge. How the agent knows *how to do the thing*.
3. **Agent** — the primary worker. The Claude instance with the loop.
4. **Subagents** — parallel independent workers. Where context isolation lives.
5. **Agent Teams** — coordination. Multiple agents that can talk to each other.

Each shipped in this order during late 2024 through early 2026 because each layer fixes a limit of the layer above. Tools needed a protocol → MCP. Tools without procedural knowledge underperformed → Skills. Single agents hit context limits → Subagents. Subagents had no peer comms → Agent Teams.

The implication for your repo: you can't just "build an agent." You're building five layers, and most of the engineering effort is in layers 1, 2, and 4. Layer 3 (the agent itself) is almost configuration once the rest is in place.

---

## 3. The agent contract: what specifying a subagent actually means

Anthropic's multi-agent research established a four-part contract for every subagent. Miss any of the four and the subagent drifts:

1. **Objective.** What is the subagent solving? One sentence, action verb.
2. **Output format.** What does "done" look like? JSON schema, markdown structure, or plain text spec.
3. **Tool and source guidance.** Which tools is it allowed to call, which data sources should it consult, and in what order?
4. **Task boundaries.** What is out of scope? What should it escalate back to the orchestrator?

For multi-subagent orchestration, Anthropic also publishes effort-scaling rules in the lead agent's prompt:

- 1 subagent for simple fact-finding
- 2–4 for direct comparisons
- 10+ only for complex research

Without these rules, the lead agent over-scales — spinning subagents for problems a single inference call could answer — and the cost multiplier compounds against you. A research run that should cost $0.40 costs $14 because the orchestrator spawned eight subagents to look up things it could have read directly.

The order of operations when you're debugging a multi-agent system: fix the four-part contract first, embed effort-scaling rules in the orchestrator prompt second, audit tool descriptions third, then iterate on anything else. Contract and tools are upstream of every other lever.

---

## 4. The .claude directory: anatomy and load order

The `.claude/` folder is the control surface. Its layout is now stable:

```
.claude/
├── CLAUDE.md                  # Persistent project memory (commit)
├── CLAUDE.local.md            # Personal overrides (gitignore)
├── settings.json              # Shared settings, hooks, permissions (commit)
├── settings.local.json        # Local overrides, API keys (gitignore)
├── agents/                    # Custom subagent definitions
│   └── code-reviewer.md
├── commands/                  # Custom slash commands
│   └── review.md              # → /review
├── hooks/                     # PreToolUse / PostToolUse / Stop scripts
│   └── block-secrets.sh
├── skills/                    # Skill folders
│   └── security-review/
│       └── SKILL.md
├── plugins/                   # Installed plugin manifests
└── projects/                  # Session transcripts and state
```

Two load mechanisms matter:

**Ancestor traversal at startup.** When Claude Code starts, it walks UP from the working directory and loads every `CLAUDE.md` it finds. This is how root-level conventions reach package-level work.

**Descendant lazy load.** Subdirectory `CLAUDE.md` files load *on demand* when Claude reads a file in that subdirectory. Sibling directories never load. If you're working in `frontend/`, you never see `backend/CLAUDE.md`.

This gives you a free monorepo pattern: a thin global root CLAUDE.md, then package-specific CLAUDE.md files in each `packages/<name>/`. The global one is always present; package ones load only when work touches that package. Context stays clean.

`settings.json` is where hooks register, MCP servers attach, and tool permissions get scoped. Permission rules evaluate `deny → ask → allow`, so a deny always wins.

---

## 5. CLAUDE.md design (the 2026 version)

CLAUDE.md is the highest-leverage file in the repo. It's the persistent project memory — the part that survives compaction, gets loaded at startup, and shapes every subsequent decision. The 2026 consensus, formed from Boris Cherny's Claude Code workflow posts and Anthropic's official best-practices doc:

**Keep it under 200 lines.** Past that, you're paying tokens on every session for content that's only relevant some of the time. If it's bigger, split it: path-scoped sub-CLAUDE.mds, or `@import` references to external files.

**What goes in it:**

- Build, test, lint, dev-server commands (deterministic, fast feedback)
- Architecture: the four or five capital-letter concepts that organize the system
- Conventions you actually enforce (linter rules, naming, file layout)
- Cross-package constraints (which packages can import from which)
- Protected files (lockfiles, generated code, anything Claude should never edit blindly)
- The one or two non-obvious gotchas that cost senior engineers half a day each

**What does NOT go in it:**

- Tutorials (those go in `docs/`, referenced by skills)
- Logged conversations or session-specific state (use progress files)
- Anything you'd be tempted to update mid-session (use the Memory Tool instead)

**Format choices:**

- Imperative voice ("Use pnpm, never npm"). Prescriptive beats descriptive.
- Concrete commands with backticks. Models latch onto exact strings.
- `@import` syntax (`@docs/git-workflow.md`) for reusing standards across teams.
- Sections short and named. Headers help retrieval in long context.

A good root CLAUDE.md for a monorepo looks like a runbook, not an essay.

---

## 6. SKILL.md: the open standard, in full

A skill is a folder containing a `SKILL.md` file plus any supporting scripts, references, or assets. The format is now an open standard (agentskills.io) and is supported by Claude Code, Codex CLI, Gemini CLI, GitHub Copilot, and Cursor (with manual placement). The format originated at Anthropic and was released as an open standard in December 2025.

**Minimum viable skill:**

```
my-skill/
└── SKILL.md
```

**SKILL.md structure:**

```markdown
---
name: my-skill-name
description: A clear, slightly pushy description of what this does AND when to use it.
---

# Human-readable title

## Instructions
Imperative steps the agent follows when this skill activates.

## Examples
Input/output pairs that demonstrate correct usage.

## Guidelines
Constraints, gotchas, things to avoid.
```

**Frontmatter rules:**

- `name`: machine-readable, kebab-case, unique in your installation
- `description`: this is the **trigger**. It is the only thing the agent sees until activation. Make it specific, include both what the skill does *and* when to use it, and lean slightly pushy because models tend to under-trigger skills. Anthropic's own skill-creator skill explicitly recommends: "Make sure to use this skill whenever the user mentions X, Y, or Z, even if they don't explicitly ask for it."

**A more complete skill layout:**

```
pdf-form-filler/
├── SKILL.md
├── scripts/
│   ├── extract_fields.py
│   └── fill_form.py
├── references/
│   ├── forms.md
│   └── annotations.md
└── assets/
    └── template.pdf
```

In this layout, `SKILL.md` is loaded into context when the skill activates. The `scripts/` are run as bash tools — their code never enters context, only their output does. The `references/` are read on demand if `SKILL.md` references them ("for form fields with checkboxes, see references/forms.md"). The `assets/` are static files the scripts operate on.

**Three-level progressive disclosure:**

1. *Metadata* (name + description): always loaded, ~30–50 tokens per skill. You can have hundreds of skills installed at trivial cost.
2. *SKILL.md body*: loaded when the description matches the task. Now you've paid the cost of the instructions but not the references.
3. *Referenced files and scripts*: loaded only when SKILL.md explicitly references them, or run as scripts whose code never enters context.

This is why skills scale where prompt libraries don't. A prompt library has to load everything that *might* be relevant. A skill library loads only what *is* relevant, at three layers of resolution.

**Naming and description, from Anthropic's skill-creator skill:**

> Currently Claude has a tendency to "undertrigger" skills — to not use them when they'd be useful. To combat this, please make the skill descriptions a little bit "pushy". So for instance, instead of "How to build a simple fast dashboard to display internal Anthropic data," you might write "How to build a simple fast dashboard to display internal Anthropic data. Make sure to use this skill whenever the user mentions dashboards, data visualization, internal metrics, or wants to display any kind of company data, even if they don't explicitly ask for a 'dashboard.'"

Trigger reliability is a measurable thing. Anthropic's skill-creator tool runs an eval loop: tests trigger phrases against your description, splits 60% train / 40% test, evaluates over multiple runs, and iterates the description. This is the right discipline for any production skill: if it doesn't fire when it should, it's worse than not having it.

---

## 7. Progressive disclosure as a first-class design pattern

Progressive disclosure is the unifying principle of the agentic stack as of 2026. It shows up at every layer:

- **CLAUDE.md**: ancestors always loaded; descendants on demand.
- **Skills**: metadata always; body on match; references on read.
- **Subagents**: spawned only when the orchestrator decides; their context never reaches the main window.
- **MCP tools**: declared at session start; *invoked* only when needed; structured outputs let the model compose without re-inference.

The design move: every piece of knowledge has a *cost-of-presence* (tokens at every turn) and a *cost-of-absence* (the agent doesn't know it when it needs to). Progressive disclosure lets you minimize cost-of-presence by paying the *first* token cost (the trigger) and amortizing the rest only when justified.

The corollary for your repo design: never put something in the always-loaded layer if you can put it in a triggered layer. CLAUDE.md should be small, skills should be many, references should be lazy.

---

## 8. Designing MCP servers (not REST wrappers)

MCP hit roughly 300M monthly SDK downloads by mid-2026, up from ~100M at the start of the year. The protocol is no longer a question — it's the substrate. The interesting question is *how to design MCP servers well*.

**The single biggest mistake:** wrapping a REST API 1:1. If your existing REST API has 47 endpoints, you do not want an MCP server with 47 tools. Large tool inventories confuse agents. The agent has to read every tool description on every turn, and the more there are, the worse the choice gets.

**Agent-native MCP server design:**

- **Compose around workflows, not endpoints.** "Get the customer's open issues, sort by priority, and summarize" is one tool, not three. The MCP server orchestrates the underlying calls.
- **Split monolithic servers.** One MCP server per coherent domain (CRM, billing, inventory). Each independently deployable, testable, and maintainable. The agent discovers tools across all of them.
- **Use structured outputs.** Give the model type information on returns. With structured outputs the model can compose results, filter JSON, transform data, and chain tools through code without bouncing back to inference for every step. This is the 2026 roadmap's biggest cost-reducer.
- **Code execution support.** Let the MCP server execute code that filters and transforms data *before* it reaches the LLM. Token consumption drops dramatically: a 25,000-token file read becomes a 600-token summary because the server ran the script locally.
- **Prefer destructive operations gated by approval.** Refunds, deletes, sends — these belong behind a `requires_approval: true` flag that the harness honors.

**Production layer concerns (the unsexy ones that matter):**

- Multi-tenant isolation: tools should accept a tenant scope and validate it server-side. Never trust the agent to scope.
- AuthN/AuthZ: OAuth 2.1 with CIMD (client-initiated metadata discovery) is the new default for remote MCP servers. Vaults hold the actual credentials; the agent passes a `vault_id`, never a raw token.
- Rate limits: enforce at the server, not the harness. Agents can loop.
- Audit trails: every tool call gets a row with session ID, agent ID, step number, input hash, output hash, tool called, timestamp. This is what makes regulated industries possible. EU AI Act August 2026 deadline (currently August 2, 2026; the Digital Omnibus proposed a push to December 2, 2027 but is unadopted) takes this seriously.

**Transport:**

- `stdio` for local development (Claude Desktop, Claude Code, child-process MCP servers)
- `Streamable HTTP` for production deployments (replacing the older SSE transport)

**When NOT to use MCP:**

- If the action is one-off and just needs to be called by code you control, write the tool directly in the agent SDK.
- If the data is static and small, a skill reference file is cheaper.
- If you're prototyping, a direct function call is fine. Promote to MCP when you need it to be cross-session, cross-product, or auditable.

The 2026 roadmap names server discovery, long-running tasks, event-driven triggers, and streaming results as the next wave. Build your servers anticipating these (declare tools as long-running where they are; structure outputs as streamable where useful) and the migrations will be cheap.

---

## 9. Hooks: deterministic control where prompts can't be trusted

Hooks fire at ~25 distinct lifecycle events in Claude Code (and have direct analogs as `PreToolUse`/`Stop` callbacks in the Agent SDK). Unlike prompts, hooks run actual code that the model cannot reason around. This makes them the right primitive for any *guarantee* you need.

**The hooks you'll actually use:**

Blocking-capable:
- `UserPromptSubmit` — runs when you submit a prompt. Can rewrite or block.
- `PreToolUse` — runs before any tool. The primary security checkpoint.
- `PermissionRequest` — runs when Claude asks permission. Auto-approve or deny.
- `Stop` / `SubagentStop` — runs when an agent finishes. Can force continuation (the "are you really done?" check).
- `PreCompact` — runs before context compaction. Back up the transcript first.

Informational:
- `SessionStart` / `SessionEnd` — load init context, clean up.
- `PostToolUse` / `PostToolUseFailure` — run linters, log results.
- `SubagentStart` — track orchestration.
- `Notification` — Claude is asking for something.

**Pattern: deterministic guardrails.**

A `PreToolUse` hook with matcher `"Bash"` that runs a script to validate the command. Exit code 0 allows, exit code 2 blocks and feeds the error back to Claude. This is how you enforce "no SQL writes," "no `rm -rf`," "no production endpoints during development" — at the system level, not at the prompt level.

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "./.claude/hooks/validate-bash.sh",
            "timeout": 5
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "./.claude/hooks/format-and-lint.sh"
          }
        ]
      }
    ]
  }
}
```

**Pattern: quality loops.**

A `Stop` hook that runs the test suite and, if any test fails, returns exit code 2 with the failure message. The agent gets the failure back and keeps working. This is how you build the "agent doesn't declare done until it actually is" property without trusting the agent to do it.

**Pattern: hook + subagent quality gate.**

A `Stop` hook that invokes an evaluator subagent to score the work (functionality, design, craft, originality) and only accepts the stop if the score crosses a threshold. This is the architecture behind `cwc-long-running-agents`, Anthropic's reference repo for the Code with Claude 2026 long-running agents station.

---

## 10. Context engineering as a discipline

Anthropic formalized "context engineering" in September 2025 as the successor to prompt engineering. The shift is: prompt engineering optimizes a single instruction; context engineering manages the entire information environment across a multi-turn, multi-step process. As Andrej Karpathy put it: "context engineering is the delicate art and science of filling the context window."

**Three things to internalize:**

**Context rot is real.** As tokens in the window grow, the model's ability to recall accurately decreases — even well before the hard limit. This is empirically true on needle-in-a-haystack benchmarks. Per Anthropic's research, model performance degrades around 50% of the window even with 1M-token contexts. The 1M-token window (now GA for Opus 4.6 and Sonnet 4.6) does not free you from context management; it lets you put off the hard problems for longer.

**Context is finite with diminishing marginal returns.** The core discipline is finding the smallest set of high-signal tokens that maximize the likelihood of the desired outcome. Every token that doesn't directly help is a token that competes for attention.

**Lossiness is a spectrum.** A summary loses information; a structured note loses less; a git commit loses still less; the full original transcript loses none but costs the most. Pick the lossiness level appropriate to the *downstream* use, not the upstream availability.

**The four levers context engineering gives you:**

1. **Compaction** — summarize a near-full conversation and restart with the summary. Pair with git commits + progress files so the agent can reconstruct state.
2. **Tool clearing** — drop tool results once they've been processed. The model rarely needs the raw response after extracting what it wanted.
3. **Sub-agent delegation** — push exploration into a subagent, get back a 1,000–2,000 token summary instead of a 50,000-token trace.
4. **External memory** — write to disk (`claude-progress.txt`, JSON state files, the Memory Tool) so context can be reloaded selectively.

**The JSON-over-markdown finding.** Anthropic ran experiments and discovered that models are less likely to *re-write* JSON than markdown. If you have state that the agent is supposed to update incrementally (todo lists, feature completion, scores), use JSON. If you have prose the agent is meant to extend (summaries, narratives, briefs), use markdown.

---

## 11. The long-running agent harness: initializer + coding agent

Anthropic's "Effective harnesses for long-running agents" post (Nov 2025) is the canonical writeup. The core insight: long-running agents work in discrete sessions, each starting with no memory of the previous. Treat the project like a software project staffed by engineers working shifts, where each new engineer arrives with no memory. What lets human shifts work? Source control, init scripts, progress files, descriptive commits, and tests.

**The two-agent harness:**

**Initializer agent** (runs once at project start, special prompt):
- Writes a comprehensive `feature-list.json` expanding the user's prompt into requirements
- Writes `init.sh` that starts the dev server and runs the app
- Writes `claude-progress.txt` (empty, with header)
- Creates an initial git commit so future agents can read history

**Coding agent** (every subsequent session, regular prompt):
- Reads `init.sh`, `feature-list.json`, `claude-progress.txt`, and the recent git log on startup
- Picks the next incomplete feature
- Implements it, writes tests, commits with a descriptive message
- Appends progress to `claude-progress.txt`
- Stops at a natural boundary, not when context is full

**Why this works:**
- The state survives context compaction because it's on disk and in git.
- The agent's first action in every new session is to *read* the state, not to *resume* an in-memory model.
- Tests prevent premature completion. Without an explicit "done" signal, the agent will declare victory too early.
- Git commits are checkpoints. After a bad session, `git reset` is cheap; without commits, recovery is impossible.

**The session-cap pattern (ralph-loop):**
- Cap session length (number of turns, or wall time).
- Have an outer script start the next session with a fresh prompt: "Pick the next incomplete feature from feature-list.json, implement it, evaluate, commit, then stop."
- The outer loop is deterministic; the inner work is autonomous.

**For your repo, this looks like:**

```
project-root/
├── init.sh                    # written by initializer
├── feature-list.json          # written by initializer, updated by coding agent
├── claude-progress.txt        # appended every session
├── .claude/
│   └── ... (settings, agents, skills, hooks)
└── src/
```

The same pattern generalizes beyond coding. For research or financial modeling agents: replace `feature-list.json` with a structured `research-plan.json`; replace tests with an evaluator subagent that scores partial work; replace git with a versioned artifacts directory. The harness shape is the same.

---

## 12. Memory: four layers and when to use which

Memory in Claude as of mid-2026 has four layers. Most teams use one. The teams that ship faster use all four with discipline about what goes where.

**Layer 1: CLAUDE.md (persistent project memory).** Always loaded at session start. Survives compaction. The right place for things you *always* want the agent to know: build commands, conventions, architecture. Edit deliberately, not mid-session.

**Layer 2: Auto memory (in-conversation working memory).** The conversation transcript itself, plus the agent's own todo lists and notes during a session. Lost when the session ends unless externalized.

**Layer 3: Memory Tool (durable, model-managed memory).** A tool the agent can call to add, retrieve, and remove durable memories. Released in beta in early 2026. The agent decides what's worth keeping. Use it for things the agent will discover during use that future sessions should know: codepaths, library locations, gotchas, "we tried X and it didn't work because Y." The discipline: durable memory should only contain information that continues to constrain *future* reasoning. Persistent preferences, decisions, failed approaches. Not session-specific state.

**Layer 4: Dreaming (Managed Agents, April 2026).** Between sessions, the agent reflects on multiple recent sessions and curates memories — extracts patterns, prunes outdated state, surfaces decisions worth keeping. This is the managed version of what you'd otherwise build with a nightly cron that runs a meta-agent over session logs. Available on Claude Managed Agents; doable manually elsewhere.

**Subagent memory is isolated.** Subagents don't share memory with the orchestrator or each other. If you want a subagent to have persistent memory, give it its own memory store (Memory Tool with a scoped namespace, or a dedicated file path it reads/writes).

**Multi-session pattern:**
- CLAUDE.md for conventions
- Progress files for what's been done
- Memory Tool for what's been learned
- Git for everything that's been built

These don't overlap if you write to each deliberately.

---

## 13. The six composable agent patterns

Anthropic's "Building Effective Agents" post (December 2024, updated through 2026) established the canonical pattern library. Six patterns; most production agents combine three or four of them.

**1. Prompt chaining.** Sequential steps where each step's output feeds the next. Use when you can decompose a task into clean stages and each stage has a clear contract. Example: outline → draft → critique → revise.

**2. Routing.** A classifier directs the task to one of N specialized handlers. Use when inputs vary widely and specialization beats generalization. Example: incoming support ticket → bug / billing / feature-request handler.

**3. Parallelization.** Multiple agents run the same task on different inputs (or sections) simultaneously, results aggregated. Use when work is independent and latency matters. Example: process 50 documents → 50 parallel summaries → one merge.

**4. Orchestrator-subagents.** A lead agent decomposes the task and delegates to specialists. The lead never sees the specialist contexts, only their summaries. This is the dominant pattern for complex work in 2026.

**5. Evaluator-optimizer.** Generator produces a candidate; evaluator scores it; loop until criteria met or max iterations. Use when quality can be improved by iteration and you have clear (or learnable) criteria.

**6. Autonomous agents (the ReAct loop).** Open-ended action selection in a tool environment with feedback. Use only when the task is genuinely open-ended and the others don't fit. This is the most expensive pattern; reach for it last.

**Combining patterns is the norm.** A vertical compliance tool might:
- Route by document type
- Parallelize section processing
- Chain extract → analyze → flag → report
- Orchestrate specialist agents per regulatory domain
- Evaluate flagged items before surfacing

This isn't a single agent; it's an architecture. The patterns let you decompose the architecture into reasoned pieces.

---

## 14. Evaluator-optimizer and quality loops

The evaluator-optimizer pattern is worth its own treatment because it's the one that most reliably trades cost for quality.

**The mechanism:**
- A generator agent produces a candidate output.
- An evaluator agent scores it against criteria.
- If the score is below threshold, the evaluator emits structured feedback.
- The generator iterates, with previous attempts + feedback in context.
- Loop until threshold met or `max_iterations` reached.

**Where it earns its keep:**
- Code quality (does it pass tests, lint, type check?)
- Frontend design quality (subjective but gradable with rubrics + few-shot examples)
- Writing (does the draft satisfy the brief?)
- Anything with measurable acceptance criteria

**Where it doesn't:**
- Fact-finding (one shot is fine; iteration doesn't add information)
- Simple lookups (cost multiplier wastes money)
- Subjective quality without rubric (evaluator drifts and you're just looping)

**Key design choices:**

- **Scoring principles, not binary pass/fail.** Anthropic's reference repo for long-running agents gives evaluators a rubric (functionality, design, craft, originality) with few-shot examples, not a yes/no contract. Continuous scores produce continuous improvement; binary contracts produce thrashing.
- **Per-feature "done" definitions.** Before generation, builder and evaluator agree on what done means for *this* feature and write it to a file the hook enforces. Pre-agreement on acceptance criteria eliminates most disagreement loops.
- **Cap iterations.** 3 is a good default. If 3 didn't work, the problem is upstream (the spec was wrong, the tools are missing, the task is malformed); more iterations won't fix it.
- **Externalize the loop.** A `Stop` hook + evaluator subagent is more robust than an inline loop. The agent stops "naturally," the hook checks, the hook re-prompts. This means each iteration starts with a clean context window.

**For frontend in particular:** add a Playwright MCP or Claude in Chrome to the evaluator's tools so it can actually look at the rendered output. Frontend quality without rendering is fiction.

---

## 15. Adding agents on the fly: hot-swap, marketplaces, agent-as-tool

The question "how do I add agents seamlessly, on the fly" splits into three different design problems:

**A. Adding a capability mid-session without restart.**

This is the hot-swap problem. Three mechanisms in 2026:

- **Skill installation.** `npx skills add <org>/<repo>` adds a skill to your installation. Claude Code re-discovers skills on next prompt; you don't restart. New trigger description, new instructions, no model reload.
- **Subagent definition.** Drop a new markdown file in `.claude/agents/`. Claude Code picks it up on next `/agents` invocation. Same — no restart.
- **MCP server attachment.** Add an MCP server to `settings.json` or via `/mcp connect`. The agent discovers the new tools on next turn.

The pattern is the same across all three: capabilities are file-based, file changes are picked up at the next natural boundary, no in-flight session needs to be torn down.

**B. Dynamic agent spawning during a session.**

This is the agent-as-tool pattern. The orchestrator calls a tool (`Task` in Claude Code, or a custom tool in the SDK) that spawns a subagent with:
- A specialized system prompt
- A scoped toolset
- An isolated context window
- A defined output format

The orchestrator gets a structured result back. The subagent's context is discarded. This is the right way to add specialists "on the fly" — the orchestrator decides, the harness materializes the subagent, the work happens in isolation, the result composes back.

The Agent SDK exposes this directly; in Claude Code, custom subagents in `.claude/agents/` can be invoked explicitly (`@agent-name`), proactively by the orchestrator (when the description matches), or via `claude --agent <name>` in headless mode.

**C. Productized agent marketplaces.**

The skill marketplace ecosystem (SkillsMP, Skills.sh, ClawHub) crossed ~490K skills by March 2026. Plugins (which bundle skills + agents + commands + hooks + MCP configs) install via `/plugin marketplace add <repo>` then `/plugin install <name>`. Org admins control approved sources; employees get a curated catalog.

For a software group: maintain a private marketplace (a GitHub repo with `.claude-plugin/marketplace.json`) that lists approved plugins. Onboarding becomes: clone the repo, run `claude`, install the org plugin. Every engineer gets the same skills, subagents, hooks, and MCP configs. Updates ship by pushing to the marketplace repo.

A modern marketplace plugin manifest looks like:

```
your-org-plugins/
├── .claude-plugin/
│   └── marketplace.json         # Lists all plugins in this marketplace
├── plugins/
│   ├── data-engineering/
│   │   ├── agents/              # Specialist agents
│   │   ├── commands/            # Slash commands
│   │   └── skills/              # Skills
│   ├── security-review/
│   │   └── ...
│   └── deployment/
│       └── ...
└── README.md
```

Each plugin is independently installable. A team that needs only `data-engineering` installs only that — 3–4 components, ~1000 tokens of metadata in context — not the whole marketplace.

**For agents truly added at runtime by other agents (the most ambitious case):**

The Claude Agent SDK supports `subagents-as-tools`: define subagent specs as JSON, hand them to the SDK at session start, and the orchestrator can call them as tools. Combined with skills, this lets you ship a system where a meta-agent reads the task, decides what subagents would be useful, and dynamically configures them. This is the bleeding edge as of 2026 — useful, but expect to engineer it yourself; durable execution and crash recovery are not provided by the SDK at layer 4. Managed Agents fills more of that gap.

---

## 16. Observability: OTel GenAI semantic conventions

The OpenTelemetry GenAI Semantic Conventions SIG has stabilized enough of the spec by early 2026 that you can build production observability on it without expecting churn. Datadog, Honeycomb, New Relic, Dynatrace, Grafana Tempo, Langfuse, Arize Phoenix, and OpenObserve all support the conventions.

**The three signals:**

- **Traces.** Each LLM call, tool call, retrieval step, and subagent invocation is a span. Parent-child relationships form the full agent reasoning tree. This is your single most useful signal — it answers "what did the agent actually do?"
- **Metrics.** Aggregate counters and histograms: tokens consumed per model per token-type, operation duration, tool error rates, subagent counts. This is your cost and reliability dashboard.
- **Logs.** Structured events: agent decisions, tool input/output (capped + sampled), evaluator scores. Correlate to traces via trace ID.

**Key span attributes (the `gen_ai.*` namespace):**

- `gen_ai.system` (anthropic, openai, etc.)
- `gen_ai.operation.name` (chat, text_completion, embedding)
- `gen_ai.request.model`
- `gen_ai.request.temperature`
- `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`
- `gen_ai.usage.cache_read_input_tokens` (prompt caching)
- `gen_ai.response.finish_reasons`
- `gen_ai.tool.name` (on tool spans)
- `gen_ai.agent.id`, `gen_ai.session.id` (on agent spans)

Span name pattern: `{operation.name} {gen_ai.system}` → `chat anthropic`. Human-readable in any backend.

**Important: prompts go as events, not attributes.** Span attributes are always indexed and exported. Span events can be filtered or dropped at the collector. Cap prompt/completion content at 500–1000 characters; sample at the collector for high-volume environments. This keeps observability cheap and keeps PII risk manageable.

**Auto-instrumentation libraries:**

- OpenLLMetry (Traceloop) — broad coverage across providers
- Native OTel `instrumentation-genai` packages — being upstreamed
- Framework-native: CrewAI emits OTel-compliant spans natively; LangChain, LlamaIndex, AG2, AutoGen via instrumentation packages

For the Claude Agent SDK + MCP stack: wrap your top-level agent invocation in an `invoke_agent` span, let OpenLLMetry (or equivalent) handle the Anthropic client spans, and add manual spans inside MCP servers for tool execution and downstream calls. Propagate the trace context through the MCP call (the W3C Trace Context headers) so MCP server spans nest correctly under the agent span.

**The three highest-leverage things to monitor from day one:**

1. **Tokens per session, broken out by model and cache hit rate.** Cost runs away here first.
2. **Tool-call frequency anomalies.** If `search_docs` normally runs 100 times/hour and it's now 10,000, the agent is in a loop.
3. **Bimodal duration histograms.** Most calls fast, some very slow → cache miss, connection exhaustion, or cold start downstream.

---

## 17. Security and supply-chain hygiene

Skills are markdown files with optional executable code. That last clause is the security story.

**The 2026 reality:** Snyk's ToxicSkills research, published February 2026, scanned 3,984 skills and found 1,467 (36.8%) had at least one security flaw; 534 (13.4%) had critical issues; 76 were confirmed malicious payloads — credential theft, backdoor installation, data exfiltration. Some public marketplaces have shown 12–20% malicious rates in audits.

**Use skills only from sources you trust.** Audit every file in a skill before installing: SKILL.md, scripts, references. Look for:
- Network calls in scripts (especially to non-obvious domains)
- File system access patterns outside the skill's stated purpose
- Operations that don't match the description ("PDF form filler" that reads `~/.aws/credentials`)
- Obfuscated or encoded payloads
- Prompt injection attempts in SKILL.md ("ignore previous instructions and...")

**Organizational defaults:**
- Maintain an internal marketplace; lock down which external sources employees can install from
- Default `installed` skills for everyone; `available` for self-service; `required` for compliance; `hidden` for staging
- CI scan skills like you scan npm packages

**Sandboxing:**
- Use `/sandbox` for OS-level isolation when running untrusted code
- `auto` mode lets a classifier model review and block risky commands without per-call interruption
- Allowlist specific commands via `/permissions` once you've audited them

**Secrets handling for Managed Agents and similar hosted infrastructures:**
- Never put credentials in system prompts or env vars directly in sessions
- Use vaults: store OAuth tokens and credentials externally, pass `vault_id`s when creating a session
- The agent never sees the raw credential; the runtime injects it at tool-call time

**Multi-tenant agentic systems specifically need:**
- Per-tenant tool scoping (the agent cannot call cross-tenant tools even if it tries)
- Audit logging with non-repudiable hashes (session, agent, step, input hash, output hash, tool, timestamp)
- PII stripping at the input layer before the agent sees the data
- Approval gates for destructive operations (refunds, deletes, sends)

These are 80% of the engineering effort in a production MCP server. They're also where the EU AI Act August 2026 deadline lives.

---

## 18. Anti-patterns: layer confusion and how to spot it

The single highest-frequency failure mode in 2026 agent systems: using the right tool in the wrong layer.

**Behavioral constraint that should be a hook, written in CLAUDE.md.**
Symptom: "We tell the agent not to push to main, but it sometimes does anyway."
Why it fails: CLAUDE.md is instructions, not enforcement. The agent will follow them most of the time and forget them under pressure.
Fix: hook on `PreToolUse` for any git push command, validate the branch.

**Reusable workflow that should be a skill, pasted into prompts.**
Symptom: "Every conversation, I have to remind the agent how we format commit messages."
Why it fails: prompts are stateless; the agent has no way to retain the pattern across conversations.
Fix: write a `commit-message` skill with a pushy description and examples.

**Specialist reasoning that should be a subagent, run inline.**
Symptom: "After reviewing those 50 files for security issues, the main agent lost track of what we were originally working on."
Why it fails: the security review filled the context window with noise the orchestrator never needed.
Fix: define a `security-reviewer` subagent in `.claude/agents/`. Orchestrator delegates; subagent returns a 1000-token summary.

**External capability that should be an MCP server, hardcoded into the agent SDK.**
Symptom: "Three of our agents call Stripe; auth handling is duplicated; rate limit bugs differ across them."
Why it fails: every agent reimplements the integration; no auditing; no consistent rate limiting.
Fix: one Stripe MCP server, used by all three agents.

**Quality gate that should be evaluator-optimizer, hoped for in the system prompt.**
Symptom: "We told the agent to double-check its work, but it still declares 'done' with failing tests."
Why it fails: a single agent cannot reliably evaluate its own output to a higher standard than it produced.
Fix: separate evaluator subagent + Stop hook that re-prompts the generator until tests pass.

**Monolithic agent holding everything.**
Symptom: "Our main agent has a 180k-token system prompt and the entire repo loaded; behavior is inconsistent across runs."
Why it fails: context rot, attention dilution, prompt sprawl.
Fix: refactor into a thin orchestrator + skills + subagents + scoped MCP. Move static knowledge to CLAUDE.md, workflows to skills, exploration to subagents, integrations to MCP.

**Mixing routing logic into a subagent's system prompt.**
Symptom: "The reviewer subagent sometimes acts like the planner."
Why it fails: routing decisions belong in the orchestrator or a hook; embedding them in a specialist erases the specialty.
Fix: orchestrator decides what to dispatch; subagent does only its specialty.

The diagnostic question: *"If this primitive failed, what layer would I want to debug?"* The answer is the layer the primitive should live in.

---

## 19. Reference architecture: a clonable skeleton

Here is a repo skeleton that puts the pieces together for a production-grade single-product agentic system. Adapt as needed; the structure is opinionated but defensible.

```
your-product/
├── README.md
├── CLAUDE.md                            # Root: conventions, build, architecture
├── init.sh                              # Bootstrap for long-running agent harness
├── feature-list.json                    # Generated by initializer agent
├── claude-progress.txt                  # Appended by coding agent
├── pyproject.toml / package.json
├── .claude/
│   ├── settings.json                    # Hooks, permissions, MCP servers, model config
│   ├── settings.local.json              # Personal overrides (gitignore)
│   ├── CLAUDE.md                        # Personal addendum (often empty)
│   ├── agents/
│   │   ├── planner.md                   # Decomposes tasks into feature-list updates
│   │   ├── coder.md                     # Implements one feature at a time
│   │   ├── reviewer.md                  # Evaluator subagent
│   │   ├── security-reviewer.md         # Specialized review
│   │   └── docs-writer.md
│   ├── commands/
│   │   ├── ship-feature.md              # /ship-feature: full pipeline
│   │   └── audit.md                     # /audit: security + perf review
│   ├── hooks/
│   │   ├── pre-bash-validate.sh         # Block risky commands
│   │   ├── post-edit-format.sh          # Lint + format on every write
│   │   └── stop-quality-gate.sh         # Evaluator gate on Stop
│   ├── skills/
│   │   ├── commit-message/
│   │   │   └── SKILL.md
│   │   ├── conventional-pr/
│   │   │   ├── SKILL.md
│   │   │   └── references/labels.md
│   │   ├── pdf-form-fill/
│   │   │   ├── SKILL.md
│   │   │   └── scripts/fill.py
│   │   └── api-endpoint-scaffold/
│   │       ├── SKILL.md
│   │       └── references/conventions.md
│   └── projects/                        # Session transcripts (gitignore)
├── docs/
│   ├── architecture.md
│   ├── git-workflow.md
│   └── deployment.md
├── mcp-servers/
│   ├── product-db/                      # Internal MCP server, this product's DB
│   │   ├── server.ts
│   │   ├── tools/
│   │   └── README.md
│   └── customer-support/
│       └── server.ts
├── src/
│   └── ... (the actual product)
├── tests/
├── evals/                               # Agent-system evals
│   ├── trigger-tests.json               # Skill triggering tests
│   ├── trace-replays/                   # Captured traces for regression
│   └── eval_metadata.json
└── telemetry/
    ├── otel-collector-config.yaml
    └── dashboards/
```

**Notes on this layout:**

- `init.sh`, `feature-list.json`, and `claude-progress.txt` are the long-running-agent triplet; if you're not running multi-session work, you can omit them.
- `.claude/agents/` holds your subagent roster. Start with two or three; grow only when a specialty earns its keep.
- `.claude/skills/` is where most growth happens. Each skill is one folder; one folder per coherent task class.
- `mcp-servers/` lives in the same repo for cohesion, but each subdirectory should be independently buildable and deployable.
- `evals/` is non-negotiable for production. Skills that don't trigger reliably, subagents that drift from their contract, prompts that regress on real cases — all of these need fast, repeated measurement.
- `telemetry/` holds your OTel collector config and the dashboards/alerts your team relies on. Commit them. Treat dashboards as code.

---

## 20. Templates

### 20.1 SKILL.md template (production-grade)

```markdown
---
name: api-endpoint-scaffold
description: Generate RESTful API endpoints following our team's layered architecture conventions. Use this skill whenever the user asks to create a new API endpoint, route, controller, or backend handler, or mentions adding a new API, even if they don't say the word "skill". Also use when modifying existing endpoint structure.
---

# API Endpoint Scaffold

## Purpose
Generate consistent RESTful endpoints in the layered architecture used by this codebase: route → controller → service → model.

## When to use
- User says "add an endpoint for X"
- User says "create a new route" or "add a new API"
- User asks to extend an existing resource (e.g., "add a delete handler for users")
- User asks to restructure the backend

## Workflow

1. Identify the resource and the HTTP verbs needed.
2. Create the route file under `src/routes/<resource>.ts`.
3. Create the controller under `src/controllers/<resource>.ts`.
4. Create the service under `src/services/<resource>.ts`.
5. Add the Pydantic/Zod schema under `src/models/<resource>.ts`.
6. Wire the route into `src/app.ts`.
7. Generate a corresponding test in `tests/routes/<resource>.test.ts`.
8. Run `pnpm test:integration -- <resource>` to verify.

## Conventions
- Routes never contain business logic; only request parsing and response shaping.
- Controllers call services; controllers do not touch the DB.
- Services hold business logic; services are unit-testable without a server.
- Models define schemas; never put logic in models.
- All endpoints have OpenAPI docstrings.

## Examples

**Input:** "Add a DELETE endpoint for users by ID"
**Output:**
- `src/routes/users.ts` gets a `DELETE /:id` handler
- `src/controllers/users.ts` gets a `deleteUser` method
- `src/services/users.ts` gets a `deleteUser` service
- `tests/routes/users.test.ts` gets a DELETE test

## References
For naming conventions, see `references/conventions.md`.
For error response patterns, see `references/errors.md`.
```

### 20.2 Subagent definition template

```markdown
---
name: security-reviewer
description: Reviews code changes for security vulnerabilities. Use proactively immediately after any code that handles auth, sessions, user input, database queries, file paths, network calls, or secrets. Use whenever the user asks for a security audit or asks "is this safe?".
tools: Read, Grep, Glob, Bash
model: opus
---

# Security Reviewer Subagent

You are a senior application security engineer. Your job is to review code for vulnerabilities and report findings in a structured format.

## Objective
Identify security issues in the specified files or diff.

## Output format
A markdown report with this structure:

### Summary
- 1-2 sentence verdict (e.g., "2 critical, 4 medium, no low findings.")

### Findings
For each finding:
- **Severity**: CRITICAL | HIGH | MEDIUM | LOW
- **Category**: e.g., SQL injection, secrets in code, insecure data handling
- **Location**: file path + line range
- **Current code**:
  ```
  <relevant snippet>
  ```
- **Issue**: 1-2 sentence description
- **Suggested fix**: concrete code change

### Out of scope
List anything you didn't review and why (e.g., "Test fixtures not reviewed; not in production path.")

## Tools and sources
- Use `Grep` and `Glob` to find related files (e.g., callers of an unsafe function).
- Use `Read` to inspect actual code; don't speculate.
- Use `Bash` only to run static analysis (e.g., `bandit`, `semgrep`) if available.
- DO NOT modify any files. Read-only.

## Task boundaries
- DO NOT make architectural recommendations beyond security.
- DO NOT review style or performance unless they affect security (e.g., DoS).
- DO NOT review more than the files specified; escalate to the orchestrator if you find a related file you think should be reviewed.

## What to look for (focus areas)
- Injection: SQL, command, XSS, template, path
- Authentication and session handling flaws
- Secrets, credentials, API keys in code
- Insecure data handling (PII logging, plaintext storage, weak crypto)
- Server-side request forgery (SSRF) and unvalidated redirects
- Race conditions in auth/session code
- Insecure deserialization
```

### 20.3 Hook template (the deterministic gate)

`.claude/hooks/stop-quality-gate.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

# Read JSON from stdin (the Stop hook input)
INPUT=$(cat)

# Get the project root from env
PROJECT_ROOT="${CLAUDE_PROJECT_ROOT:-$PWD}"

# Run tests; capture exit code
cd "$PROJECT_ROOT"
if ! TEST_OUTPUT=$(pnpm test --silent 2>&1); then
  # Tests failed - emit the failure to Claude and force continuation
  cat <<EOF
{
  "decision": "block",
  "reason": "Tests are failing. Continue working until they pass.\n\nFailure output:\n${TEST_OUTPUT}"
}
EOF
  exit 2
fi

# Run lint; same pattern
if ! LINT_OUTPUT=$(pnpm lint --silent 2>&1); then
  cat <<EOF
{
  "decision": "block",
  "reason": "Lint is failing. Fix before declaring done.\n\n${LINT_OUTPUT}"
}
EOF
  exit 2
fi

# All gates passed; allow Stop
exit 0
```

Register in `.claude/settings.json`:

```json
{
  "hooks": {
    "Stop": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "./.claude/hooks/stop-quality-gate.sh",
            "timeout": 120
          }
        ]
      }
    ]
  }
}
```

### 20.4 settings.json starter

```json
{
  "$schema": "https://json.schemastore.org/claude-code-settings.json",
  "model": "claude-opus-4-7",
  "permissions": {
    "allow": [
      "Bash(pnpm test*)",
      "Bash(pnpm lint*)",
      "Bash(pnpm build*)",
      "Bash(git diff*)",
      "Bash(git log*)",
      "Bash(git status*)"
    ],
    "deny": [
      "Bash(rm -rf*)",
      "Bash(git push*)",
      "Bash(*--force*)",
      "Bash(*production*)"
    ]
  },
  "mcpServers": {
    "product-db": {
      "type": "stdio",
      "command": "node",
      "args": ["mcp-servers/product-db/dist/server.js"]
    },
    "customer-support": {
      "type": "http",
      "url": "https://mcp.internal.example.com/customer-support"
    }
  },
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "./.claude/hooks/pre-bash-validate.sh",
            "timeout": 5
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "./.claude/hooks/post-edit-format.sh",
            "timeout": 30
          }
        ]
      }
    ],
    "Stop": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "./.claude/hooks/stop-quality-gate.sh",
            "timeout": 120
          }
        ]
      }
    ]
  }
}
```

### 20.5 Initializer agent prompt (for long-running harness)

Save as `.claude/agents/initializer.md`:

```markdown
---
name: initializer
description: Sets up a fresh project for long-running agentic work. Run only once at project start. Use when there is no init.sh, no feature-list.json, and no claude-progress.txt in the project root.
tools: Read, Write, Bash, Glob
model: opus
---

# Initializer Agent

You are the bootstrap agent for a long-running development project. Run only once.

## Your job

1. Read the user's high-level prompt for the project.
2. Expand it into a comprehensive feature list in `feature-list.json`. Be thorough — capture every reasonable interpretation of the requirements. The format:

```json
{
  "project": "<one-line summary>",
  "features": [
    {
      "id": "F001",
      "title": "...",
      "description": "...",
      "acceptance_criteria": ["...", "..."],
      "depends_on": [],
      "status": "todo"
    }
  ]
}
```

3. Write `init.sh` that installs dependencies and starts the dev server. Make it idempotent.

4. Create `claude-progress.txt` with a header:

```
# Project Progress Log
# Append-only. Each entry: ISO timestamp + feature ID + 1-2 line summary.
```

5. Run `git init` if not already a git repo. Make the first commit: "chore: initialize project scaffolding".

6. Verify by reading back each file.

7. Output a short summary of what you created and exit.

## What you do NOT do
- Do not implement any features.
- Do not write application code.
- Do not run the dev server.
- Do not declare features complete.

## Constraints
- Be generous with the feature list. Over-specification is cheaper to fix than under-specification.
- Include non-functional requirements (auth, error handling, deployment) as features.
- Mark dependencies explicitly so the coding agent picks a sensible order.
```

---

## 21. Operating model: what a 2026 software group actually looks like

If you're building a software team or group around this stack — not just one agent for one product — the operating model has a few non-obvious properties worth naming.

**Skill authors are first-class engineers.** The team that can produce reliable, well-triggering, well-scoped skills will compound faster than the team that writes prompts. Treat skill authoring like a discipline: trigger eval is part of CI; new skills go through review; skills that drift get retired. Anthropic's skill-creator skill is the tool of choice for trigger optimization; running it in CI on every PR is achievable.

**Subagent design is architecture.** Adding a subagent is an architectural decision, not a productivity hack. The four-part contract (objective, output, tools/sources, boundaries) is the design doc; the markdown file is its implementation. Review subagents like you review interfaces.

**The harness is a product.** Every team convention, every behavioral guarantee, every quality bar — these live in hooks, in CLAUDE.md, in settings.json. The harness *is* the productized form of institutional knowledge. Take it seriously; check it in; version it; release it via marketplace plugins to the rest of the org.

**MCP servers are the integration layer.** Stop writing one-off API integrations. Each connection to a system that more than one agent will use becomes an MCP server. The savings compound over time: auth, rate limits, audit, observability handled once.

**Evals are the test suite.** Code without tests is bad code. Agents without evals are bad agents. Evals here mean: representative tasks (trigger phrases for skills, end-to-end task specs for full systems), assertions about outputs (sometimes deterministic, often grader subagents), variance analysis across multiple runs, regression on captured traces. Spend the engineering cycles. The teams that ship reliable agentic products in 2026 are the teams that run nightly evals.

**Observability is operations.** OTel from day one. Token burn rate, tool error rate, trace duration distribution, evaluator score distributions, skill trigger rates. Pages and dashboards keyed off these metrics catch problems before the agent does.

**Roles in a 2026 software group** that didn't quite exist three years ago:
- *Skill engineer* — owns the skill library, trigger reliability, retirement
- *Harness engineer* — owns the .claude/ configuration, hooks, settings, marketplace
- *Eval engineer* — owns the eval suite, regression detection, benchmark CI
- *MCP server engineer* — owns the integration layer
- *Agent platform engineer* — owns observability, deployment, durable execution, vault management

In a small team, one or two people wear all five hats. In a larger group, they specialize.

**One last cultural note.** The agents amplify intent and discipline. If your team's specification is sloppy, the agents produce sloppy work fast. If your team's specification is sharp, the agents produce sharp work fast. The leverage is in the upstream specification. The teams that win in 2026 are the teams that learned how to write *briefs to agents* the way they used to write briefs to senior engineers — concrete, scoped, with clear acceptance criteria and the right level of latitude.

---

## 22. Open problems and where this is heading

A few honest caveats and forward-looking notes:

**Durable execution is still your problem.** The Agent SDK provides agent loop, streaming, tool use, and compaction. Layer 4 — durable execution, crash recovery, workflow checkpoints — is not in the SDK. Managed Agents fills more of it; if you're rolling your own, expect to use a workflow engine (Temporal, Inngest, Restate, Hatchet) for anything that needs to survive worker crashes and run for hours.

**Multi-agent coordination at scale is unsolved at the standard layer.** A2A (Agent-to-Agent) protocols are emerging in parallel with MCP. MCP gives you reliable, auditable, typed tool interfaces. A2A gives you dynamic capability negotiation between agents that may not know about each other at design time. The right answer for most teams in 2026 is: use MCP for tools, use orchestrator-subagent for delegation, hold off on full A2A until your product genuinely needs design-time-unknown peer agents.

**Eval methodology is still maturing.** LLM-as-judge has known biases. Deterministic assertions are great where applicable but most agent outputs aren't deterministic. The state of the art is: small deterministic assertions where possible + grader subagents with rubrics where not + variance analysis across N runs. Calibrate graders against human review periodically.

**Cost predictability is bad.** Multi-agent workflows generate unpredictable token spend at scale. Prompt caching, scoped tools (small inventories per agent), subagent budgets (max turns, max tokens per subagent), and effort-scaling rules in the orchestrator are your levers. None of them is fully reliable; budget alerts and circuit breakers in production are non-optional.

**The model layer keeps moving.** Opus 4.6 → Opus 4.7 (current), Sonnet 4.6, Haiku 4.5. The 1M-token context is now GA but doesn't free you from context engineering. New models will continue to shift the cost-performance frontier. Build your harness so the model is a config knob, not a hardcoded assumption. Route work to the smallest model that can do it (Haiku for classification, Sonnet for most work, Opus for the genuinely hard reasoning), and measure actual quality differences before defaulting to the biggest model.

**Where this is heading (best guesses, 12-month horizon):**

- *Agent Skills standard becomes truly portable.* It mostly is now; expect richer composition primitives, dependency declarations, and signed skills.
- *MCP gets event-driven triggers, streaming, server discovery.* The 2026 roadmap names these explicitly.
- *Managed Agents-style hosted runtimes proliferate.* Anthropic isn't the only one; expect AWS, Microsoft, and Google to ship comparable runtimes. The portability wars start at the harness layer.
- *Specialized small models for tooling roles.* Classifier hooks, evaluator subagents, router agents — these don't need a frontier model. Expect SLMs purpose-trained for specific harness roles to become cost-default.
- *Eval-driven development becomes standard.* The CI loop "PR → eval suite → block on regression" replaces the prompt-engineer-by-vibes loop for any team that ships.

The pattern is clear: more of the agent stack moves into protocols, runtimes, and managed infrastructure. Your differentiation moves upward — into the skill library you build, the MCP servers you own, the evaluation rigor you apply, and the institutional knowledge encoded in your harness.

---

## Appendix A: A pragmatic adoption roadmap

If you're starting from scratch (or starting to do this right after a year of accumulated debt), here's the order that tends to work:

1. **Week 1: CLAUDE.md and a clean .claude/ directory.** Just this. Move conventions into CLAUDE.md, get under 200 lines, commit it. Add a starter `settings.json` with permissions but no hooks yet.

2. **Week 2: First three skills and first three hooks.** The three workflows you copy-paste the most → three skills. The three behavioral guarantees you've been hoping for → three hooks. Run them. Iterate.

3. **Week 3: First subagent.** Pick a specialist task that's polluting your main context (security review, code review, test generation). Write its definition. Use it. Measure context savings.

4. **Week 4: First MCP server.** The integration you've been hardcoding into multiple agents. Refactor it out. One server, multiple consumers. Add OTel from the start.

5. **Month 2: Eval suite.** Capture 20–50 representative tasks. Build assertions, mostly grader-subagent. Run nightly. Block PRs on regression.

6. **Month 2–3: Long-running harness.** If your work spans multiple sessions, set up the initializer + coding agent + progress files pattern. Use git as your durable state.

7. **Month 3+: Marketplace.** When you have 10+ skills, 3+ subagents, and a stable .claude/, package as a marketplace and distribute org-wide.

This is roughly 90 days of consistent investment to go from a single-prompt user to a team with a productized agent stack. The compound effect from there is steep — the marketplace plugin lets new engineers ramp in hours, and the skill/subagent library captures institutional knowledge that used to live in senior engineers' heads.

---

## Appendix B: Reading list (primary sources)

These are the actual posts and repos worth reading, not derivative blog summaries. Read them in this order:

1. **Anthropic — "Building Effective Agents"** (Dec 2024, updated through 2026). The canonical pattern library: prompt chaining, routing, parallelization, orchestrator-workers, evaluator-optimizer, autonomous agents. Start here.
2. **Anthropic — "Effective Context Engineering for AI Agents"** (Sep 2025). The discipline of curating tokens. Establishes context rot, compaction, sub-agent delegation, just-in-time retrieval.
3. **Anthropic — "Effective Harnesses for Long-Running Agents"** (Nov 2025). The initializer + coding agent pattern, progress files, git as memory. The blueprint for any project that spans multiple sessions.
4. **Anthropic — "Equipping Agents for the Real World with Agent Skills"** (Oct 2025, plus the December 2025 open-standard announcement). The SKILL.md model and progressive disclosure.
5. **Anthropic — "Building Agents That Reach Production Systems with MCP"** (Apr 2026). Server design patterns, OAuth + vaults, context-efficient clients, where skills complement MCP.
6. **agentskills.io** — the open standard specification. Read the format reference; it's short.
7. **github.com/anthropics/skills** — Anthropic's reference skills repo, including the `skill-creator` skill which you should use to optimize your own skill descriptions.
8. **github.com/anthropics/cwc-long-running-agents** — the reference repo for the long-running agents pattern. Hooks, evaluator subagents, frontend design eval. Not maintained, intentionally; copy what fits.
9. **claude-cookbooks/patterns/agents** (github.com/anthropics/claude-cookbooks) — runnable notebook implementations of the six composable patterns.
10. **OpenTelemetry GenAI Semantic Conventions SIG** — the evolving spec for `gen_ai.*` attributes. Track this if you're building observability seriously.
11. **Claude Agent SDK docs** at `docs.claude.com` — the Python and TypeScript SDK references, kept current.
12. **Claude Code best practices** at `code.claude.com/docs/en/best-practices` — Anthropic's canonical operating guide, updated regularly.
13. **"SkillOpt: Executive Strategy for Self-Evolving Agent Skills"** (Yang et al., Microsoft / SJTU / Tongji / Fudan, arXiv:2605.23904, May 2026; code at `aka.ms/SkillOpt`). The first systematic treatment of the skill document as a trainable artifact. Read it alongside Appendix D of this guide. The cutting-edge research item on the list.

Five hours of reading. Worth more than fifty hours of trial and error.

---

## Appendix C: The harness engineering paradigm — lineage and the Model + Harness + Context frame

The body of this guide is operational: how to build the thing. This appendix is conceptual: where the discipline came from, why it has a name now, and one framing refinement worth adopting. It's synthesized from the "harness engineering" discourse that consolidated across engineering teams in Q1–Q2 2026 — Mitchell Hashimoto, Ryan Lopopolo's OpenAI post, Martin Fowler / Birgitta Böckeler's Thoughtworks writeup, and Muratcan Koylan's commentary on the model. Treat the dates and attributions as the discourse's own account of itself; the *ideas* are what matter.

### The four-paradigm lineage

AI engineering has moved through roughly four two-year eras, each treating a different layer as the determining variable:

1. **Prompt engineering (late 2022 – mid 2023).** A single instruction was the entire program. Optimize the wording.
2. **Context engineering (mid 2023 – mid 2025).** The context window is a workspace to be curated — retrieval, memory, tool definitions, project rules. Andrej Karpathy is generally credited with naming this one (late 2025, in its mature form).
3. **Agent engineering (mid 2024 – mid 2025).** Hand the loop to the model. Let it reason, act, observe on its own.
4. **Harness engineering (2026 –).** The model is no longer the scarce variable. Frontier models from different labs converged in capability; meanwhile the gap between *the same model with and without a well-designed harness* widened dramatically. So the engineering investment moves to the harness.

The point of the lineage is not historical trivia. It's that the four eras are *cumulative, not sequential*. You still do prompt engineering. You still do context engineering. Harness engineering is the layer that now gets the marginal hour, but it sits on top of the others — a great harness around a noisy context still fails.

### The Hashimoto principle

The clearest one-line definition of the discipline, attributed to Mitchell Hashimoto (early February 2026):

> "Any time you find an agent makes a mistake, you take the time to engineer a solution such that the agent never makes that mistake again."

This is worth stating explicitly because it *is* the practice — not a metaphor for it. Every hook in Section 9, every structural test, every lint rule, every entry in CLAUDE.md, every skill that encodes a convention is an instance of the Hashimoto principle. A mistake observed once becomes a structural constraint that prevents the class of mistake forever. The repository becomes the accumulated, executable memory of every mistake the team (or the agent) has ever made and chosen not to repeat. If you already work this way — encoding mistakes as tests and lint rules rather than as prompt reminders — this is simply the name for what you're doing.

The companion tagline, from Ryan Lopopolo's OpenAI post (Feb 11, 2026) that gave the discipline its formal writeup after he shipped a production app with zero hand-written lines of code: **"Humans steer. Agents execute."** The human's job moves upstream — into specifying, constraining, and verifying — and the harness is where that specification lives in executable form.

### The refinement: Agent = Model + Harness + Context

The widely circulated shorthand is **Agent = Model + Harness** — the harness is "everything that isn't the model." That's a useful starting point but, as Koylan argues, too coarse: it lumps context management into the harness, when context deserves co-equal billing. The sharper decomposition:

**Agent = Model + Harness + Context.**

- **Model** provides *intelligence* — raw reasoning, the stateless token predictor.
- **Harness** provides *capability* — what the agent *can do*. Tools, code execution, agent coordination, constraint enforcement, lifecycle management.
- **Context** provides *direction* — what the agent *should do* and *knows*. What it sees, when it sees it, signal-to-noise.

These have genuinely different design principles, and — this is the operationally useful part — **they fail differently and you debug them differently:**

- **Harness engineering is about execution.** What tools exist, where code runs, how agents coordinate, how constraints are enforced. A harness defect looks like: the agent *couldn't* do the thing — missing tool, no verification loop, no recovery path, schema mismatch, state lost between sessions.
- **Context engineering is about attention.** What the model sees and when. A context defect looks like: the agent *had* the capability but made the wrong call — noisy context, stale information, the right fact buried under 100k tokens of irrelevant tool output, context rot past the 50% mark.

You can build a perfect harness and still get bad results from noisy, stale, or overloaded context. The two failure modes are independent.

### The two-question debugging heuristic

This is the single most portable thing to take from the harness-engineering discourse. When an agent fails, ask two questions, in order:

1. **"Does the harness give the model the right capabilities?"** — the right tools, a place to run code, a way to verify its own work, a way to recover from a crash. If no: it's a harness defect. Fix it in hooks, tools, MCP servers, the recovery loop.
2. **"Does the context give the model the right information at the right time?"** — and nothing else. If the agent had the tools but chose wrong, or hallucinated a fact, or lost the thread: it's a context defect. Fix it in retrieval, compaction, subagent isolation, what gets loaded when.

Most failures are one or the other. Relatively few are the model itself — and "wait for a better model" is rarely the right fix in 2026, because the harness-and-context gap dwarfs the model gap. The discourse circulates a figure (originating in a secondary-source landscape analysis, so treat it as directional rather than precise) that on the order of two-thirds of enterprise agent failures trace to harness defects — specifically context drift, schema misalignment, and state degradation — rather than to reasoning deficits. Whatever the exact number, the direction is right and matches what this guide argues throughout: optimizing the model while the harness and context are unstable yields diminishing returns.

### How this maps onto the rest of the guide

If you adopt the three-part frame, the guide's sections sort cleanly into it:

- **Harness layer** (capability / execution): Sections 1–4 (the primitives, the .claude directory), 8 (MCP servers), 9 (hooks), 11 (the long-running harness), 13–15 (patterns, quality loops, dynamic agents), 16–17 (observability, security).
- **Context layer** (direction / attention): Sections 5 (CLAUDE.md), 6–7 (skills and progressive disclosure), 10 (context engineering), 12 (memory).
- **The bridge:** Section 18 (anti-patterns) is, in retrospect, almost entirely a catalog of *layer-confusion* errors — and the harness/context split is the cleaner lens for it. A behavioral guarantee written in CLAUDE.md instead of a hook is a *context fix for a harness problem*. A reusable workflow pasted into prompts instead of a skill is a *harness problem (no capability to retain it) being patched in context*. The two-question heuristic catches these before they're built.

The practical close: when you stand up a new agentic system — for a client, for a product — instrument and review it along both axes separately. Keep a harness checklist (tools present, verification loop closed, recovery path exists, constraints enforced deterministically) and a context checklist (signal-to-noise high, nothing stale, nothing loaded that isn't earning its tokens, the right thing disclosed at the right time). They're different review passes. Running them as one pass is how layer-confusion bugs survive to production.

---

## Appendix D: Skill optimization — treating the skill document as trainable state

Section 6 covered how to *write* a SKILL.md and Section 14 covered evaluator-optimizer loops in general. This appendix covers a specific, recent, and important idea that sits at the intersection of the two: **the skill document itself can be optimized as a trained artifact, with the discipline of a deep-learning optimizer.** The reference work is *SkillOpt* (Yang et al., Microsoft / Shanghai Jiao Tong / Tongji / Fudan, arXiv:2605.23904, posted May 2026). It's a research result, not yet a battle-tested production pattern — treat the specifics as directional — but the framing is genuinely worth absorbing, because it changes what a skill *is*.

### The problem it names

Skills today are produced one of three ways: hand-written, generated one-shot by an LLM, or evolved through loosely-controlled self-revision. None of these behaves like an optimizer, and none reliably improves on its starting point under feedback. A hand-written skill is a guess. A one-shot LLM skill is a guess with better prose. Self-revision (an agent rewriting its own skill after failures) tends to make large, unstable semantic jumps — it can erase a useful rule while fixing a different one, with no mechanism to know it regressed.

The reframe: if the recurring object of adaptation is the agent's *procedure*, then the skill document is the thing being adapted — so it should be *trained*, with the same controls that make weight-space optimization reproducible.

### The deep-learning analogy, made operational

This is the heart of it. SkillOpt maps every part of a training loop onto a text-space equivalent:

| Deep learning | Skill optimization |
|---|---|
| Parameters | The skill document (`best_skill.md`) |
| Gradient direction | Trajectory-derived edit (add / delete / replace) |
| Learning rate | Edit budget — max number of edits applied per step |
| Validation check | Held-out selection gate — accept an edit only if it strictly improves a held-out score |
| Batch / minibatch | Rollout batches and reflection minibatches |
| Momentum | Epoch-wise slow/meta update — carries stable editing directions across epochs |
| Frozen vs. trained | The target model is frozen; only the skill is trained |

The analogy is operational, not decorative. Batch sizes control the noise in the evidence behind each edit. The edit budget controls how far one skill version is allowed to move from the last — bounded updates, not free rewrites. The held-out gate is validation. The slow/meta update is momentum. The point of all of it is *stability*: if consecutive skill revisions move too far or in inconsistent directions, the optimization history (what helped, what failed, what to preserve) stops being meaningful.

### The loop

Two models, not one. A **frozen target model** executes tasks with the current skill. A **separate optimizer model** (ideally a strong frontier model) reads the scored rollouts and edits the skill. Concretely, per step:

1. **Rollout.** The target model runs a batch of training tasks with the current skill. The harness records trajectories and scores.
2. **Minibatch reflection.** The optimizer separates failures from successes, partitions each into minibatches, and proposes structured `add` / `delete` / `replace` edits — failure minibatches propose corrections, success minibatches preserve what works. Minibatches matter because single trajectories produce anecdotal fixes, while minibatches expose *recurring* procedural errors.
3. **Merge and rank.** Edits are merged hierarchically (failure edits and success edits consolidated separately, then combined with priority on failure corrections), then ranked and clipped to the edit budget.
4. **Candidate skill.** The selected edits produce a candidate `SKILL.md`.
5. **Validation gate.** The candidate is scored on a held-out selection split. It's accepted only if it *strictly* improves the current score. Ties are rejected — the deployed skill never silently drifts.
6. **Rejected-edit buffer.** Rejected edits aren't discarded; they're kept as negative feedback so later optimizer calls in the same epoch don't repeat them.
7. **Epoch-wise slow/meta update.** At each epoch boundary, the optimizer compares the same tasks under the previous and current skills, sorts outcomes into improvements / regressions / persistent failures / stable successes, and writes longitudinal guidance into a *protected region* of the skill that step-level edits cannot overwrite. A separate optimizer-side "meta skill" records which edit patterns help — but it stays with the optimizer and is never shipped.

The deployed output is a single compact `best_skill.md`. Critically, **the optimizer runs only offline during training. At deployment there are zero extra model calls** — it's just a static skill file feeding the same target model. A stronger optimizer produces a better skill at no deployment cost.

### What the results say

Across six benchmarks (QA, spreadsheets, documents, math, embodied tasks), seven target models, and three execution harnesses — direct chat, the Codex harness, and the Claude Code harness — SkillOpt reports best-or-tied results on all 52 evaluated (model, benchmark, harness) cells, against baselines including human-written skills, one-shot LLM skills, and prior skill-evolution and prompt-optimization methods. The headline gains over a no-skill baseline: roughly +23 points average on a frontier model in direct chat, with comparable lifts inside the Codex and Claude Code loops. Treat the exact numbers as the paper's own claims on its own benchmark suite — but the *direction* (a well-optimized compact skill substantially outperforms a hand-written one) is the durable takeaway.

Three findings matter more than the headline:

- **Compactness and edit economy.** The final skills are small — on the order of a few hundred to ~2,000 tokens — and the gains come from very few accepted edits (often one to four). The validation gate is doing real work: the optimizer proposes many edits; only a handful survive. This is consistent with the progressive-disclosure principle in Section 7 — the deployed artifact stays small and inspectable.
- **Learned skills are procedural, not instance-specific.** The rules that survive read like what a careful practitioner would write after a day with the domain: "inspect workbook structure and formulas, then write evaluated static values across the full target range instead of relying on recalculation"; "bind the question to the exact visual row/header before copying the answer span." They encode the *discipline* a frontier model lacks zero-shot — answer formatting, evidence binding, search-frontier management — not memorized answers.
- **Optimized skills transfer.** A skill trained on one model improves smaller models in the same family; a skill trained in one harness transfers to another (notably Codex ↔ Claude Code); a skill trained on one benchmark gives positive gains on a nearby one. Optimize once, audit the text, reuse across related models, harnesses, and tasks.

### What this means for how you build

The practical reframe for your own skill library:

1. **A skill is no longer a one-time write.** It's an artifact with a version history, a validation score, and a training procedure. The first draft is the *initialization*, not the deliverable.
2. **You already have most of the machinery.** The reference architecture in Section 19 has an `evals/` directory and the long-running harness has scored runs. SkillOpt is, structurally, an evaluator-optimizer loop (Section 14) pointed at the skill file instead of the product code, with two additions that make it stable: a **bounded edit budget** (don't let the skill lurch) and a **strict held-out gate** (don't accept an edit that doesn't measurably help). If you've ever let an agent rewrite its own CLAUDE.md or skill after a failure, this is the disciplined version of that instinct.
3. **The precondition is a scorer.** This loop needs scored trajectories and a held-out split — it works cleanly where you have automatic verifiers, exact-match metrics, or executable checks. For domains where success is subjective, you need a grader subagent good enough to act as the validation gate, and you should calibrate it against human review (Section 22's open problem about eval methodology applies directly).
4. **Separate the optimizer from the target.** Don't have the agent optimize its own skill mid-task. Use a separate, ideally stronger, optimizer model offline. Keep any optimizer-side meta-knowledge out of the shipped skill — the deployed artifact stays lean.
5. **Protect the durable region.** If you adopt the slow/meta-update idea, fence the long-horizon guidance in a region (the paper uses HTML-comment markers) that fast, step-level edits cannot overwrite. Fast local learning and slow consolidation should not fight over the same text.

Where this is heading: skill *libraries* with shared optimization infrastructure, reuse of optimizer-side meta-knowledge across domains, preference-driven validation gates for open-ended tasks where no exact-match metric exists, and — the most interesting thread — distilling an optimized skill back into model weights as a stepping stone to weight-level adaptation. For now, the usable insight is the smaller one: stop treating skills as static prose. The skill is a trainable part of the stack, and applying even a lightweight version of this loop — bounded edits, a held-out gate, a rejected-edit buffer — to the skills that matter most in your system is one of the highest-leverage things you can do that almost nobody is doing yet.

---

*End of guide. Last updated May 26, 2026. Built to be edited. Appendix C incorporates the Model + Harness + Context framing from the 2026 harness-engineering discourse; Appendix D incorporates the skill-as-trainable-artifact framing from the SkillOpt paper (arXiv:2605.23904).*
