# ADR 0043: Fetch and render trust-boundary hardening

- **Status:** Accepted
- **Date:** 2026-06-05
- **Deciders:** Tech Lead, Council (advisor)
- **Supersedes:** none
- **Superseded by:** none

## Context

Two trust boundaries in the engine touch attacker-influenceable data:

1. **The fetch boundary** (`app.services.ingestion.httpx_fetch`) makes the first
   outbound request to URLs that originate from search results — i.e. URLs an
   adversary can plant. The pre-existing hardening (timeout, size cap,
   content-type allowlist, no credentials) left three gaps a security audit
   flagged:
   - **SSRF (HIGH):** nothing stopped a fetch of `http://127.0.0.1/…` or the
     cloud-metadata endpoint `http://169.254.169.254/…`, and because redirects
     were auto-followed (`follow_redirects=True`) a *benign* public URL could
     302-redirect us into the internal network on a hop we never inspected.
   - **DoS (MED):** the size cap ran *after* `response.content` had already
     buffered the entire body into memory, and a declared oversized
     `Content-Length` was not pre-rejected.
   - **Content-type bypass (MED):** the allowlist check was
     `if content_type and not allowed` — a **missing** `Content-Type` header fell
     through as *allowed* (fail-open).

2. **The render boundary** (`app.services.publishing.html`) interpolates a
   citation's `source_url` into an `href`. `html.escape(..., quote=True)`
   prevents attribute breakout but does **not** neutralize a dangerous *scheme*:
   `href="javascript:alert(1)"` survives escaping and executes on click
   (**XSS, HIGH**).

A related low-severity finding: adapter error messages interpolate the full
upstream response body (`{data!r}`), which then lands in `ResearchState.error`
and logs (**info-leak, LOW**).

The fix had to stay **contained at the boundaries**. Retyping `Source.url` /
`Citation.source_url` to `HttpUrl` was explicitly rejected — that change ripples
through the schema, every constructor, and every fixture, for a validation that
belongs at the point of egress/render, not in the data model.

## Decision

**Fetch (`httpx_fetch.py`) — validate every hop, stream the body.**

- Follow redirects **manually** (`follow_redirects=False`, set per-request so the
  client default is irrelevant). The provider owns its own `max_redirects` hop
  counter and re-runs the full guard on each `Location` target.
- `_validate_url` enforces an `http`/`https` **scheme allowlist** and an **SSRF
  IP guard**: IP-literal hosts are parsed directly with `ipaddress`; hostnames go
  through an **injectable resolver seam** (`resolver: Callable[[str], list[str]]`,
  default a thin `socket.getaddrinfo` wrapper). A host is blocked if **any**
  resolved address is private / loopback / link-local / reserved / multicast /
  unspecified. `169.254.169.254` is caught by `is_link_local`. IPv4-mapped IPv6
  (`::ffff:127.0.0.1`) is unwrapped before the check.
- Body is read via `client.stream(...)`: status → content-type → `Content-Length`
  pre-reject → `aiter_bytes()` accumulation aborted the moment the running total
  exceeds `max_bytes`. The `async with` closes the connection on early abort.
- Content-type is now **fail-closed**: `None` (missing header) is rejected.

**Render (`html.py`) — scheme allowlist before linking.**

- `_is_linkable(url)` lower-cases the `urlsplit` scheme and checks it against
  `{http, https, mailto}`, after stripping leading whitespace/control characters
  (so `"\tjavascript:…"` cannot smuggle a disallowed scheme past the check). A
  non-allowlisted scheme renders the (escaped) citation label as **plain text,
  no `<a>`**. Allowed schemes keep the existing `quote=True` href escaping.

**Adapter errors — bounded body excerpt.**

- The five enumerated adapters (`openai_compatible`, `gemini`, `brave_search`,
  Tavily `live`, stock `visuals`) clip the interpolated body to
  `repr(data)[:500]` via a module-local `_ERR_BODY_MAX`. An inline clip beats a
  new shared util that would couple these otherwise-independent packages
  (CLAUDE.md §7).

## Consequences

### Positive

- The engine can no longer be steered into the internal network or the
  cloud-metadata service, on the first request **or any redirect hop**.
- Oversized responses are aborted mid-stream and never fully buffered.
- A scheme-confused citation can never produce a clickable `javascript:`/`data:`
  link in the rendered report.
- Full upstream bodies no longer leak into persisted state or logs.

### Negative / Neutral

- The resolver seam adds one constructor parameter to `HttpxFetchProvider`
  (defaulted; existing call sites unchanged). Tests inject a public-IP resolver
  so hostname validation stays hermetic/offline.
- A hostname is resolved once for validation; httpx resolves again for the
  connection (a small, accepted double-lookup — TOCTOU is out of scope for v1).

## Alternatives considered

- **Retype `source_url`/`Source.url` to `HttpUrl`.** Rejected: schema-wide ripple
  for a boundary concern; also wouldn't block private-IP hosts at fetch time.
- **Keep `follow_redirects=True` + a custom transport.** A transport hook can
  re-validate, but manual per-hop following is simpler to read and audit and
  keeps the hop budget owned by the provider.
- **A shared `clip_body()` util for fix #5.** Rejected per CLAUDE.md §7 — it would
  couple three packages for a one-line guard.

## Out of scope / follow-ups

- `app/publishing/youtube.py`, `app/analytics/youtube.py`, and `app/topics/live.py`
  share the same `{data!r}` pattern but are outside this audit's enumerated set;
  applying the same clip there is a documented follow-up (CLAUDE.md §9 scope
  discipline).
- DNS-rebinding / TOCTOU between the validation lookup and the connection lookup.

## References

- ADR 0008 (HTML ingestion), ADR 0014 (PDF ingestion), ADR 0017 (renderers),
  ADR 0021 (httpx adapter hardening idiom).
- CLAUDE.md §7 (quality bar), §9 (scope discipline), §11 (provenance boundary).
