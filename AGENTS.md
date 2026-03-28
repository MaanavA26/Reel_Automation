# Reel Automation – Agent Instructions

## Purpose
This repository implements a production-grade agentic system for research, content generation, media production, and publishing workflows for faceless short-form video automation.

## Architecture Layers
- Agentic Intelligence Layer
- Deep Research Layer
- Media Production Layer
- Additional layers may be introduced later through ADRs

## Core Stack
- FastAPI backend
- React frontend
- LangGraph orchestration
- FFmpeg for media composition
- Pydantic schemas
- Modular services and tools

## General Rules
- Make minimal, reviewable changes.
- Do not edit unrelated files.
- Follow local patterns before inventing new abstractions.
- Architecture changes require an ADR in docs/adrs.
- Add tests for behavior changes.
- Prefer deterministic tools over agent nodes for execution-heavy logic.

## Agent vs Tool Policy
Agents:
- planning
- synthesis
- critique
- reasoning
- orchestration decisions

Tools/services:
- parsing
- rendering
- file IO
- subtitle generation
- API wrappers
- data transformation
- deterministic formatting

## Backend Rules
- Keep API routers thin.
- Put business logic in services.
- Use typed request/response schemas.
- Add logging and clear error handling.
- Keep modules single-responsibility.

## Frontend Rules
- Use API service abstraction.
- Keep UI components focused.
- Do not entangle view logic with backend contracts more than necessary.

## Testing Rules
- Prefer focused tests.
- Add regression tests for bugfixes.
- Validate smallest relevant scope first.

## Delivery Style
When working:
1. State files to change.
2. State intended behavior.
3. Make the smallest viable patch.
4. Summarize results and validation.