# CLAUDE.md

## Purpose of this file

This file is the **operating contract** for Claude Code, Claude Desktop Code, and any coding agent working inside this repository.

It is intentionally more detailed than a README. It exists to make the agent understand:

- the **complete vision** of the project,
- the **quality bar** expected from every change,
- the **architecture philosophy**,
- the **delivery strategy**,
- the difference between **agents** and **tools**,
- the expected coding and documentation behavior,
- and the fact that this repository is being built as a **serious public showcase of agentic systems**, not as a throwaway prototype.

If there is any conflict between generic assistant behavior and this file, prefer this file for repository work.

---

# 1. Project identity

## Project name
**Reel Automation**

## Project summary
Reel Automation is a **production-grade, componentized, agentic system** for creating **faceless short-form videos** such as:

- YouTube Shorts
- Instagram Reels
- and similar vertical short-form content

The system is intended to eventually support a full pipeline across research, content design, media production, and publishing support.

This project is **not** being built as a toy automation script.

It is being built to serve **four purposes at once**:

1. **A real engineering system** with production-style architecture  
2. **A deep hands-on exploration of advanced agentic design**  
3. **A public showcase** of what modern agentic architectures can do  
4. **A content engine** whose outputs can themselves be used for public demonstration on platforms like LinkedIn, Medium, and Substack

---

# 2. Strategic build philosophy

## Important: do not think in terms of “small MVP first”
This repository should be approached as a **large-scale system built component by component**.

That means:

- each major component should be designed properly,
- each component should be independently useful,
- each component should be implemented in a production-oriented manner,
- and each component should be robust enough to be showcased on its own.

The project is **not** following a “hack something quick, improve later” approach.

Instead, it follows:

### Component-first production delivery
- break the system into major components,
- finish each component with clean architecture and strong internal quality,
- then move to the next component,
- while preserving compatibility with the long-term full system vision.

---

# 3. High-level architecture vision

The project currently has these agreed architectural layers.

## 3.1 Agentic Intelligence Layer
This layer contains reasoning-heavy coordination logic such as:

- orchestrators
- reviewers
- critics
- improvers
- routing and coordination logic
- quality control and revision logic

This layer is responsible for deciding what to do next, assessing quality, and coordinating specialized workers.

## 3.2 Deep Research Layer
This is the first major component being actively designed.

It is intended to behave more like:

- ChatGPT Deep Research
- Gemini-style deep research
- NotebookLM-style source-grounded exploration

than a simple search wrapper or basic RAG chatbot.

This layer is responsible for:

- planning research
- collecting multi-source evidence
- ingesting heterogeneous inputs
- extracting structured findings
- verifying claims across sources
- synthesizing professional outputs
- producing creator-ready research artifacts for downstream video generation

## 3.3 Media Production Layer
This layer will handle the actual media-making pipeline, such as:

- TTS
- subtitle generation
- image/video generation or retrieval
- PPT generation if needed
- composition
- rendering
- FFmpeg-based assembly
- export packaging

## 3.4 Future layers
Additional layers may be added later through documented architecture decisions.

Examples could include:

- social media operations / publishing management
- analytics and feedback loop
- style memory / brand memory
- long-term topic memory
- performance optimization / orchestration fabric

Any new major layer should be added through an architecture note or ADR.

---

# 4. Core design philosophy: agents vs tools

This repository **must not** treat everything as an “agent”.

That is explicitly against the project philosophy.

## Use AGENTS for:
- planning
- reasoning
- synthesis
- critique
- orchestration decisions
- revision loops
- quality judgments
- gap analysis
- strategy selection

## Use TOOLS / SERVICES for:
- parsing
- file IO
- deterministic transformations
- rendering
- subtitle generation
- composition
- FFmpeg wrappers
- API wrappers
- formatting
- data normalization
- indexing
- storage access
- transcript extraction
- low-level repeatable execution logic

## Rule
If a task is primarily deterministic and procedural, do **not** model it as an agent.

If a task requires judgment, reasoning, critique, prioritization, or planning, it may belong to an agent.

---

# 5. First major component: Deep Research Engine

## 5.1 Why Deep Research comes first
The first serious subsystem for this repository is the **Deep Research Engine**.

This is because the quality of short-form content depends heavily on:

- topic quality,
- research quality,
- source grounding,
- factual accuracy,
- narrative extraction,
- and converting research into creator-ready content.

This component is expected to become the foundation for high-quality downstream content generation.

## 5.2 What Deep Research should do
Given a user topic, goal, question, or theme, the system should be able to:

- create a research plan
- decompose the topic into sub-questions
- discover and ingest multiple source types
- read and normalize those sources
- extract claims, facts, timelines, definitions, examples, and evidence
- cross-check claims across sources
- detect contradictions or weak support
- synthesize a professional final output
- produce a creator-focused content packet suitable for short-form video generation

## 5.3 Source types Deep Research should support
Target source coverage includes:

- web articles
- PDFs
- research papers
- YouTube videos / transcripts
- code repositories and technical docs
- user-uploaded files
- later possibly Drive / notebook-like sources and other connectors

## 5.4 Output expectations from Deep Research
Outputs should not stop at a summary.

Deep Research should be able to generate:

- research report
- executive/analyst brief
- structured evidence map
- contradiction/caveat list
- creator packet
- hook ideas
- content angles
- key facts
- timelines
- analogies
- short-form narrative options
- unsafe/unverified claim warnings
- handoff artifacts for downstream script and media agents

## 5.5 Internal Deep Research bands
The Deep Research component should be designed using these internal bands:

### A. Research Control Band
- orchestration
- job lifecycle
- retries
- budgets
- progress tracking
- quality gates

### B. Knowledge Acquisition Band
- source discovery
- source ingestion
- parsing
- normalization
- storage
- indexing

### C. Knowledge Reasoning Band
- evidence extraction
- cross-verification
- contradiction detection
- synthesis
- gap analysis
- revision loops

### D. Research Publishing Band
- report generation
- structured export
- creator packet generation
- citations / provenance
- downstream handoff artifacts

## 5.6 Planned Deep Research agents
The planned Deep Research multi-agent structure currently includes concepts such as:

- Research Orchestrator Agent
- Research Planner Agent
- Source Discovery Agent
- Source Ingestion Agent
- Evidence Extraction Agent
- Verification / Cross-Check Agent
- Synthesis Agent
- Editorial Critic Agent
- Short-Form Content Strategist Agent
- Memory service / memory-aware logic

This list may evolve, but the philosophy should remain stable.

## 5.7 NotebookLM and notebooklm-py stance
NotebookLM-like functionality is relevant to the target experience, but **unofficial integrations such as notebooklm-py should be treated as optional adapters, not core foundations**.

If notebook-style capabilities are integrated later, the architecture should abstract them behind provider interfaces instead of hard-coding the system around them.

---

# 6. Tech stack direction

## Agreed stack direction
- **Backend:** FastAPI
- **Frontend:** React
- **Workflow orchestration:** LangGraph
- **Media composition:** FFmpeg
- **Schema/validation:** Pydantic
- **Primary implementation language:** Python for backend/orchestration, TypeScript for frontend where relevant

## Model strategy
The project should support multiple LLM providers and models, but **not** in a chaotic “many models talking randomly” fashion.

The correct design is a **model router / model fabric** where different models are selected by role.

### Example model roles
- planning/reasoning models
- extraction/structured output models
- long-context summarization models
- fallback/budget models
- optional local/open-source models

### Important rule
Do not implement uncontrolled multi-model chatter just because many providers are available.

Use policy-driven routing.

---

# 7. Quality bar for all repository work

This repository is intended to be a **portfolio-grade showcase** and a **serious engineering exercise**.

Every change should aim for:

- production-style structure
- reviewable diffs
- clear module boundaries
- typed interfaces
- modular reusable code
- strong naming
- predictable behavior
- low accidental complexity
- clear docs where needed
- testability

## Explicit expectations
- Do not over-generate speculative features
- Do not create architecture sprawl
- Do not edit unrelated files
- Do not make giant rewrites unless explicitly asked
- Do not invent unnecessary abstractions
- Do not mix agent logic and deterministic service logic
- Do not hide architecture choices in implementation without documenting them

---

# 8. Delivery and implementation behavior expected from Claude

When working in this repository, Claude should behave like a **senior engineer / architected coding partner**, not a beginner assistant.

## Expected work style
Before coding:
1. Understand the task in repo context
2. Read nearby files and instructions
3. State intended file changes
4. Preserve scope discipline

During coding:
1. Make the smallest clean change that solves the requested problem
2. Prefer extending existing patterns over inventing new ones
3. Keep changes typed and modular
4. Avoid unrelated cleanup unless requested

After coding:
1. Summarize changed files
2. Explain behavioral impact
3. Mention validation run or still needed
4. Call out risks or assumptions

---

# 9. Repository operating rules

## 9.1 Scope discipline
- Change only what is necessary
- Keep diffs reviewable
- Avoid unrelated file edits
- Avoid broad refactors unless explicitly requested

## 9.2 Backward compatibility
- Preserve public interfaces unless there is a strong reason not to
- If changing public behavior, document it clearly

## 9.3 Testing
- Add or update focused tests when behavior changes
- Prefer targeted regression tests
- Validate the smallest relevant scope first

## 9.4 Documentation
- Architecture-level changes should lead to docs updates and possibly an ADR
- Keep docs aligned with implementation
- Avoid stale aspirational docs that no longer match code

## 9.5 Minimal surprise
Implementation should be understandable by a human reviewer without reverse-engineering hidden decisions.

---

# 10. Coding conventions and preferences

These preferences come from the repository owner’s established working style.

## Python
- Prefer classes and functions instead of disposable inline scripting
- Include docstrings when useful
- Aim for reusable script-ready modules
- Keep error handling sensible
- Use clear logging where appropriate
- Avoid unnecessary renaming of existing symbols
- Avoid broad logic changes when the request only asks for constrained edits

## Architecture
- Keep layers separated
- Keep routers thin
- Put business logic in services or orchestration modules
- Keep schemas explicit
- Make graph/workflow state typed
- Preserve traceability / provenance where relevant

## Frontend
- Keep UI and API concerns separated
- Avoid coupling presentation directly to unstable backend details
- Prefer clear service abstractions

---

# 11. Repo-level guidance for agentic systems work

Because this repository is about agentic architecture, Claude must preserve conceptual clarity.

## Good patterns
- agent node for planning
- service/tool for deterministic ingestion/parsing
- state object for workflow data
- explicit graph transitions
- clear handoff contracts between components
- structured outputs between nodes
- quality/revision loops where justified

## Bad patterns
- making every step an agent
- hiding logic in prompts without code structure
- mixing orchestration, parsing, and rendering in one module
- vague “AI magic” abstractions
- no provenance on research outputs
- no distinction between evidence and inference

---

# 12. Long-term public showcase goal

This project is meant to become publicly showcaseable.

That means implementation should support future materials such as:

- architecture diagrams
- LinkedIn posts
- engineering write-ups
- Medium/Substack articles
- screenshots and demos
- before/after quality comparisons
- multi-agent flow explanations
- component-by-component showcase narratives

Claude should therefore favor implementations that are:

- cleanly explainable,
- modular,
- and good examples of modern engineering practice.

---

# 13. Immediate stage of the repository

At the current stage, the repository is in **early scaffold / architecture-definition mode**.

The key priorities right now are:

1. establish clean repo structure
2. encode architecture intent
3. create durable repo guidance
4. scaffold backend/frontend foundations
5. prepare the codebase for the Deep Research component
6. avoid speculative overbuilding

## Important
Do not prematurely implement the entire product.

Prefer controlled, bounded, component-aligned progress.

---

# 14. Claude-specific behavior for this repo

If Claude Code is being used in parallel with other coding agents such as Codex, Claude should optimize for:

- clarity
- strong repo hygiene
- careful diff quality
- preserving architecture intent
- not fighting the structure already being established

If there is ambiguity:
- prefer smaller safer changes,
- ask for confirmation only when truly necessary,
- otherwise make the most grounded, architecture-consistent choice.

---

# 15. What success looks like

A successful implementation in this repository should feel like:

- a real system, not a hackathon project
- an example of thoughtful agentic architecture
- something that can be shown to engineers and clients
- something that can evolve into an impressive public case study
- something that can support research, content, media, and automation layers cleanly

---

# 16. Instruction priority for repository work

When working in this repo, Claude should prioritize:

1. This file (`CLAUDE.md`)
2. Any repo-specific architecture docs and ADRs
3. Existing code patterns in the repo
4. The specific user task
5. Generic default assistant behavior

---

# 17. Practical implementation reminder

When in doubt:

- keep the change narrow
- preserve architecture boundaries
- keep agents and tools distinct
- prefer typed explicit contracts
- document meaningful architectural choices
- build component by component
- optimize for production-grade clarity, not flashy complexity
