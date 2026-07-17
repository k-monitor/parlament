"""Cache-Control stamping for CDN/browser caching (NFR-1, high-traffic).

The whole site is read-only and changes at most once per sync interval
(default 30 min), so nearly every response is safely cacheable. This pure-ASGI
middleware (no BaseHTTPMiddleware response buffering) stamps a Cache-Control
header on successful GET/HEAD responses that don't set their own, picked by
path:

- ``/assets/…``        — Vite content-hashed bundles: cache forever, immutable.
- ``/media/photos/…``  — MP portraits: long-lived, revalidated at the edge.
- ``/api/v1/health``   — never cached (it must reflect *this* origin, now).
- ``/api/…``           — short browser TTL + a longer shared (CDN) TTL, plus
  stale-while-revalidate so the edge refreshes in the background instead of
  stampeding the origin when an entry expires.
- everything else (the SPA shell, the server-rendered OG share cards) — no
  browser caching (a deploy must show up on reload) but edge-cacheable, since
  a stale-by-minutes share card is harmless.

Behind Cloudflare the API/HTML TTLs only take effect once a Cache Rule makes
those paths eligible for caching (see DEPLOYMENT.md); the headers then drive
the edge TTL via s-maxage. Without a CDN they still enable browser caching.
"""

from __future__ import annotations

import os

# Overridable per deployment (OPS-4): tighten when the sync cadence is fast,
# lengthen for a mostly-static archive.
API_CACHE_CONTROL = (os.environ.get("PARLAMONITOR_API_CACHE_CONTROL", "").strip()
                     or "public, max-age=60, s-maxage=300, stale-while-revalidate=600")
HTML_CACHE_CONTROL = (os.environ.get("PARLAMONITOR_HTML_CACHE_CONTROL", "").strip()
                      or "public, max-age=0, s-maxage=300, stale-while-revalidate=600")
ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"
PHOTO_CACHE_CONTROL = "public, max-age=86400, s-maxage=604800"


def _policy_for(path: str) -> str:
    if path.startswith("/assets/"):
        return ASSET_CACHE_CONTROL
    if path.startswith("/media/"):
        return PHOTO_CACHE_CONTROL
    if path == "/api/v1/health":
        return "no-store"
    if path.startswith("/api/"):
        return API_CACHE_CONTROL
    return HTML_CACHE_CONTROL


class CacheControlMiddleware:
    """Add a path-based Cache-Control to 200/304 GET/HEAD responses that lack
    one. Errors and redirects are left uncached, and any handler-set header
    wins."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("GET", "HEAD"):
            await self.app(scope, receive, send)
            return

        policy = _policy_for(scope["path"])

        async def send_with_cache_header(message):
            if (message["type"] == "http.response.start"
                    and message["status"] in (200, 304)):
                headers = message.setdefault("headers", [])
                if not any(k.lower() == b"cache-control" for k, _ in headers):
                    chosen = policy
                    # Never mark an HTML body `immutable`: only genuine hashed
                    # assets earn the year-long TTL. If an /assets/ URL ever
                    # yields the SPA shell (a fallback bug, a proxy error page),
                    # caching that for a year poisons the site — a stylesheet was
                    # cached as HTML and Firefox refused it. Cache HTML as HTML.
                    if chosen is ASSET_CACHE_CONTROL:
                        ctype = next((v for k, v in headers
                                      if k.lower() == b"content-type"), b"")
                        if ctype.lower().startswith(b"text/html"):
                            chosen = HTML_CACHE_CONTROL
                    headers.append((b"cache-control", chosen.encode("latin-1")))
            await send(message)

        await self.app(scope, receive, send_with_cache_header)
