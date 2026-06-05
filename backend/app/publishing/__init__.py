"""Publishing / Social-Ops layer — uploading finished videos to platforms.

The fourth major component (CLAUDE.md §3.4 "social media operations / publishing
management"). A deterministic *tool* band (CLAUDE.md §4 — API wrappers, file I/O):
the upstream agentic layer decides *what* to publish and *when*; this band
*executes* the upload. It mirrors the search / visuals fabric point-for-point —
a provider-neutral Protocol + typed DTOs + a hermetic fake + one hardened httpx
adapter (`YouTubeShortsPublisher`) + protocol-conformant skeletons for TikTok /
Instagram Reels. See ADR 0033.
"""
