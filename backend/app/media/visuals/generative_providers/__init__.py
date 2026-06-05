"""Concrete `GenerativeVisualProvider` adapters, one per AI-video vendor.

Each module speaks a single vendor's **documented** request + async job-poll
contract (Veo / Runway / Luma / Pika-via-fal / Kling), implemented as wire-shape
hooks on `app.media.visuals.generative._PollingGenerativeProvider`. The shared
submit -> poll -> fetch loop, error boundary, and bounded-budget polling live in
that base; these modules only translate each vendor's schema.

CRITICAL HONESTY: none of these is validated against a live endpoint in this
offline sandbox. Each is built to the vendor's *documented* contract and carries
the same not-live-validated caveat the repo already carries for NVIDIA-TTS
(ADR 0047) and YouTube-upload (ADR 0033); confirm the wire shape on the first
live call (a one-method edit on the isolated hooks, not a rewrite). See ADR 0053.
"""
