# Reel Automation

Production-grade scaffold for a modular system that supports research, content generation, media production, and publishing workflows for short-form video automation.

## Repository Layout

```text
.
├── backend/
│   ├── app/
│   │   ├── agents/
│   │   ├── api/
│   │   ├── core/
│   │   ├── schemas/
│   │   ├── services/
│   │   ├── tools/
│   │   └── workflows/
│   └── tests/
├── docs/
│   ├── adrs/
│   ├── standards/
│   └── superpowers/plans/
└── frontend/
    └── src/
        ├── components/
        ├── pages/
        ├── services/
        └── types/
```

## Backend

`backend/` contains the FastAPI application skeleton. The current scaffold includes:

- `app/main.py` as the application bootstrap
- `app/api/` for thin HTTP routing
- `app/core/` for typed configuration
- `app/schemas/` for request and response models
- `app/services/`, `app/agents/`, `app/tools/`, and `app/workflows/` for future subsystem boundaries
- `tests/` for focused backend validation

## Frontend

`frontend/` contains a minimal React and TypeScript shell with:

- `src/components/` for reusable UI primitives
- `src/pages/` for route-level views
- `src/services/` for API abstractions
- `src/types/` for shared frontend contracts

## Documentation

`docs/adrs/` is reserved for architecture decision records.

`docs/standards/` is reserved for implementation and engineering standards that apply across backend, frontend, and workflow modules.

---

## License

Licensed under the Apache License, Version 2.0 — see [`LICENSE`](./LICENSE) for the full text. Copyright 2026 Maanav Aryan.
