"""Minimal Bluesky (AT Protocol) posting client — §8.7 SOC-1.

Two XRPC calls are all a bot needs, so this talks to the PDS directly over the
stdlib rather than adding an SDK for them (the same reasoning as
``app/valasztas.py``: nothing here should pull a dependency the rest of the
deployment doesn't already need):

  * ``com.atproto.server.createSession`` — exchange handle + **app password** for
    an access token;
  * ``com.atproto.repo.createRecord`` — write one ``app.bsky.feed.post``.

Credentials come from the environment and nowhere else (OPS-4): a single
``PARLAMONITOR_BLUESKY_AUTH=handle:app-password``. Use an **app password**
(Settings → Privacy and security → App passwords on bsky.app), never the account
password — it is scoped, revocable, and cannot change the account's own password.

What the caller has to know about post shape, since the server enforces it:

  * ``text`` is capped at **300 graphemes** / 3000 bytes. :func:`clip` trims to a
    word boundary within a budget; the announcer sizes its variable parts (a poem,
    a day's stats) so the link at the end always survives.
  * a URL in the text is **not** a link by itself — it needs a *facet* carrying the
    range as **UTF-8 byte** offsets (:func:`link_facets`). Hungarian text makes
    byte and character offsets differ, so this is not optional here.
  * link *cards* come from an ``app.bsky.embed.external`` embed, not from the
    facet. The announcer sends both: the card renders, and the bare URL stays
    clickable wherever it doesn't.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

from .config import settings

logger = logging.getLogger(__name__)

# The server's own limit on a post's text (app.bsky.feed.post: maxGraphemes 300).
MAX_GRAPHEMES = 300


class BlueskyError(RuntimeError):
    """A call to the PDS failed. The announcer logs it and leaves the day
    unrecorded, so the next run retries it rather than losing the post."""


def _identifier(raw: str) -> str:
    """An identifier in the shape createSession accepts: a **bare** handle, DID or
    email.

    A handle copied out of the Bluesky UI carries a leading ``@``, which the PDS
    rejects with the very same "Invalid identifier or password" as a wrong app
    password would — the two are indistinguishable in a log, so drop it here."""
    return raw.strip().lstrip("@").strip()


def implausible_identifier(identifier: str) -> str:
    """Why ``identifier`` cannot be what createSession wants, or ``""``.

    An AT Protocol handle is a **domain** (``parlamonitor.bsky.social``), so a
    single bare word is never one — and yet the PDS answers it with the very same
    "Invalid identifier or password" as a revoked app password, which sends you
    looking at the secret instead of the name. A DID is the one dotless form; an
    email has a dot of its own."""
    if identifier.startswith("did:") or "." in identifier:
        return ""
    return (f"{identifier!r} cannot be a Bluesky handle — a handle is a full domain, "
            f"so this is probably '{identifier}.bsky.social'")


def credentials() -> tuple[str, str] | None:
    """``(identifier, app_password)`` from the environment, or ``None``.

    ``PARLAMONITOR_BLUESKY_AUTH="handle:app-password"`` is the one variable to set
    — one secret, one place. The split is on the **last** colon: an app password is
    four dash-separated groups and never contains one, while an identifier may be a
    DID (``did:plc:abc123``) and does. The separate
    ``PARLAMONITOR_BLUESKY_HANDLE`` / ``PARLAMONITOR_BLUESKY_APP_PASSWORD`` pair is
    accepted as an alternative for secret stores that inject one value per key."""
    raw = (settings.bluesky_auth or "").strip()
    if raw:
        identifier, _, password = raw.rpartition(":")
        identifier, password = _identifier(identifier), password.strip()
        if identifier and password:
            if flaw := implausible_identifier(identifier):
                logger.warning("PARLAMONITOR_BLUESKY_AUTH: %s", flaw)
            return identifier, password
        logger.warning("PARLAMONITOR_BLUESKY_AUTH is not 'handle:app-password' — "
                       "ignoring it")
        return None
    if settings.bluesky_handle and settings.bluesky_password:
        return _identifier(settings.bluesky_handle), settings.bluesky_password.strip()
    return None


# --- text measuring / trimming ----------------------------------------------

def graphemes(text: str) -> int:
    """Approximate grapheme count — what the server's 300 limit counts.

    Combining marks are folded into the character they mark (NFC first, so
    Hungarian's precomposed vowels count as one either way). It does not model
    emoji ZWJ sequences, so an emoji-heavy post could measure short by a few; the
    announcer's posts use at most one leading emoji and keep a margin."""
    normalized = unicodedata.normalize("NFC", text)
    return sum(1 for ch in normalized if not unicodedata.combining(ch))


def clip(text: str, budget: int) -> str:
    """``text`` trimmed to at most ``budget`` graphemes, on a word boundary.

    Used on the *variable* part of a post so the fixed parts — above all the link —
    are never what gets cut."""
    if budget <= 0:
        return ""
    if graphemes(text) <= budget:
        return text
    # One grapheme of the budget goes to the ellipsis that discloses the cut.
    cut = text[: max(0, budget - 1)].rstrip()
    if " " in cut:
        cut = cut[: cut.rindex(" ")].rstrip(" ,;:—–")
    return cut + "…"


_URL_RE = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)
# A sentence-final character that is punctuation of the sentence, not of the URL.
_URL_TRAILING = ")]},.;:!?\"'"


def link_facets(text: str) -> list[dict]:
    """Rich-text facets making every URL in ``text`` an actual link.

    Ranges are **UTF-8 byte** offsets into the text, per the lexicon — with
    Hungarian accents in front of a URL, character offsets would point at the wrong
    place (a plain `str.find` mistake this exists to avoid)."""
    facets = []
    for m in _URL_RE.finditer(text):
        url = m.group(0).rstrip(_URL_TRAILING)
        start = len(text[: m.start()].encode("utf-8"))
        end = start + len(url.encode("utf-8"))
        facets.append({
            "index": {"byteStart": start, "byteEnd": end},
            "features": [{"$type": "app.bsky.richtext.facet#link", "uri": url}],
        })
    return facets


# --- the client -------------------------------------------------------------

@dataclass
class Session:
    did: str
    handle: str
    access_jwt: str


class BlueskyClient:
    """One authenticated connection to a PDS, for the length of one run.

    The announcer runs as a short-lived pass after each sync, so the access token
    is created once per run and never refreshed — ``refreshJwt`` handling would
    only matter for a long-lived process."""

    def __init__(self, identifier: str, password: str, *,
                 service: str | None = None, timeout: int | None = None,
                 opener=None):
        self.identifier = identifier
        self._password = password
        self.service = (service or settings.bluesky_service).rstrip("/")
        self.timeout = timeout or settings.bluesky_timeout
        # Injectable transport: the tests drive the real request-building code
        # without a network, and a deployment behind an egress proxy can pass its
        # own opener.
        self._opener = opener or urllib.request.urlopen
        self.session: Session | None = None

    # -- transport --

    def _call(self, method: str, body: dict, *, auth: str | None = None) -> dict:
        url = f"{self.service}/xrpc/{method}"
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json",
                     "Accept": "application/json",
                     "User-Agent": settings.bluesky_user_agent,
                     **({"Authorization": f"Bearer {auth}"} if auth else {})})
        try:
            with self._opener(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            # The PDS explains itself in the body ({"error", "message"}); an
            # unexplained status is the interesting part of the message otherwise.
            detail = ""
            try:
                payload = json.loads(exc.read().decode("utf-8"))
                detail = payload.get("message") or payload.get("error") or ""
            except Exception:                                   # noqa: BLE001
                pass
            raise BlueskyError(f"{method} failed: HTTP {exc.code} {detail}".strip()) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise BlueskyError(f"{method} failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise BlueskyError(f"{method} returned malformed JSON") from exc

    # -- api --

    def login(self) -> Session:
        """Exchange the app password for an access token (idempotent per run)."""
        if self.session:
            return self.session
        try:
            data = self._call("com.atproto.server.createSession",
                              {"identifier": self.identifier, "password": self._password})
        except BlueskyError as exc:
            # A wrong handle, a revoked app password and the account password used by
            # mistake all come back as the same 401. The announcer's log line is the
            # only place anyone ever sees this, so name the account that was tried and
            # what to look at — otherwise every sync repeats an unactionable error.
            cause = exc.__cause__
            if isinstance(cause, urllib.error.HTTPError) and cause.code in (400, 401):
                raise BlueskyError(
                    f"{exc} (tried identifier {self.identifier!r} on {self.service} — "
                    "PARLAMONITOR_BLUESKY_AUTH wants a bare handle/DID/email and a "
                    "current app password, not the account password)") from exc
            raise
        if not data.get("accessJwt") or not data.get("did"):
            raise BlueskyError("createSession returned no access token")
        self.session = Session(did=data["did"],
                              handle=data.get("handle") or self.identifier,
                              access_jwt=data["accessJwt"])
        logger.info("Bluesky: authenticated as %s", self.session.handle)
        return self.session

    def post(self, text: str, *, embed: dict | None = None,
             langs: list[str] | None = None) -> str:
        """Publish one post; returns its ``at://…`` record URI.

        Facets are derived from the text, so a caller only has to write the text
        with its URL in it."""
        if graphemes(text) > MAX_GRAPHEMES:
            # A caller that sized its parts wrongly would otherwise get an opaque
            # server-side validation error.
            raise BlueskyError(
                f"post text is {graphemes(text)} graphemes, over the {MAX_GRAPHEMES} limit")
        session = self.login()
        record = {
            "$type": "app.bsky.feed.post",
            "text": text,
            "createdAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "langs": langs or [settings.bluesky_lang],
        }
        facets = link_facets(text)
        if facets:
            record["facets"] = facets
        if embed:
            record["embed"] = embed
        data = self._call("com.atproto.repo.createRecord",
                          {"repo": session.did,
                           "collection": "app.bsky.feed.post",
                           "record": record},
                          auth=session.access_jwt)
        uri = data.get("uri") or ""
        logger.info("Bluesky: posted %s", uri or "(no uri returned)")
        return uri


def external_embed(uri: str, title: str, description: str) -> dict:
    """An ``app.bsky.embed.external`` link card.

    No thumbnail: an image would have to be uploaded as a blob first, and the
    site's own OG image (app/og.py) is not reachable from a bot that must work
    before the page is even crawled. Title + description is what the card needs to
    be honest about where the link goes."""
    return {"$type": "app.bsky.embed.external",
            "external": {"uri": uri,
                         "title": clip(title, 200),
                         "description": clip(description, 300)}}


def client_from_env() -> BlueskyClient | None:
    """A client built from the environment, or ``None`` when no credentials are
    configured — the signal the announcer treats as "posting is switched off"."""
    creds = credentials()
    if not creds:
        return None
    return BlueskyClient(*creds)
