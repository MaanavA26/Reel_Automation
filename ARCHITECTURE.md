# Reel Automation Architecture

## Purpose
Reel Automation is a production-grade agentic system for generating faceless short-form video content such as YouTube Shorts and Instagram Reels.

## High-Level Goal
The system will support research, content generation, media production, and publishing workflows through modular components delivered independently but designed to work together.

## Architecture Layers
- Agentic Intelligence Layer
  - orchestrators
  - reviewers
  - critics
  - improvers
  - routing and coordination logic

- Deep Research Layer
  - planning
  - source discovery
  - ingestion
  - summarization
  - synthesis
  - source-grounded content generation

- Media Production Layer
  - TTS
  - subtitle generation
  - image/video generation or selection
  - composition
  - rendering
  - export packaging

- Additional layers may be introduced later through ADRs.

## Core Technology
- Backend: FastAPI
- Frontend: React
- Workflow orchestration: LangGraph
- Media composition: FFmpeg
- Schemas and validation: Pydantic

## Design Principles
- Production-grade modular architecture
- Agent vs tool separation
- Minimal and reviewable diffs
- Strong typing and reusable code
- Clear boundaries between layers
- Architecture changes documented through ADRs

## Agent vs Tool Policy
### Agents
Use agents for:
- planning
- reasoning
- critique
- orchestration decisions
- synthesis

### Tools / Services
Use tools or services for:
- parsing
- file IO
- deterministic transformations
- API wrappers
- rendering
- subtitle generation
- composition

## Initial Delivery Strategy
The project will be built component by component.
Each component should be independently usable, production-oriented, and extensible.

## First Major Component
Deep Research:
A source-grounded, multi-agent research subsystem that can collect, analyze, verify, and synthesize information from heterogeneous inputs for downstream short-form content generation.