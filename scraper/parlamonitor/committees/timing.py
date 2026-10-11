"""Sentence↔video timing for committee sittings (BIZ-30).

The plenary viewer's karaoke (VIE-3/VIE-4) rests on the sentence timings the
alignment stage stamps onto every speech (TIM-1). A committee sitting had none:
the jegyzőkönyv carries **no timings at all** (BIZ-25), and its recording is a
YouTube video rather than a stream whose per-speech offsets parlament.hu
publishes. This stage makes them the way the plenary's are made — Whisper
transcribes the recording, and the transcription is aligned to the
*authoritative* text, which is what is shown — with the two differences the
source forces:

* **The audio is fetched on the host.** YouTube's media URLs are signed for the
  address that asked for them, so the GPU container cannot fetch one the way it
  fetches a plenary HLS playlist. The host downloads the cheapest audio-only
  track (Opus at ~45 kbit/s, ~20 MB an hour — ample for speech recognition) and
  ships the bytes (``whisper_modal.transcribe_audio``). The file is deleted as
  soon as its words are cached.
* **The whole sitting is aligned at once** (``whisper_align.align_sitting``):
  there are no per-speech windows to cut the recording by, so every speech has
  to find its own place in it.

A sitting can be timed only when it has **both** records — a parsed jegyzőkönyv
with speeches, and a recording — so the stage works on the intersection of the
minutes and the video registry. That is a small set by construction: the
channel starts in February 2024 (BIZ-23) and the minutes follow the sitting by
weeks (BIZ-22). The pairing is made here by the rule the loader links a video
to a meeting by (BIZ-22: the folded committee name and the date in the title),
applied to the minutes records, which carry both.

The cost is kept where the plenary keeps it:

* the Whisper words are cached **per video**, keyed by the video id and the
  model, so a recording is transcribed once, ever;
* each sitting's alignment is fingerprinted by its videos, its text and the
  alignment version, so a pass over unchanged inputs aligns nothing;
* on Modal, the transcription is scoped by ``PARLAMONITOR_MODAL_CYCLES`` exactly
  as the plenary's is — a spend guard does not get a side door.

Output: ``committee-timing.json``, cycle-less and rewritten whole (the loader
replaces its tables from it)::

    {"meta": {...},
     "data": [{"meetingId", "cycle", "method", "model", "coverage",
               "fingerprint", "alignedAt",
               "videos": [{"videoId", "durationS"}],
               "speeches": [{"ord", "sentences": [
                   {"para", "chars": [start, end], "text",
                    "videoId", "timeStart", "timeEnd"}]}]}]}

``chars`` are offsets into the speech's text as the minutes file holds it, and
``text`` is that slice; the API serves a speech's sentences only while the two
still agree, so a re-parsed jegyzőkönyv can never be shown cut at stale offsets.
A sentence that could not be placed keeps ``timeStart``/``timeEnd`` null: it is
shown, not playable.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .. import config, whisper_align
from ..config import Paths
from ..segment import split_sentences

logger = logging.getLogger(__name__)

SOURCE = "committee-timing"
METHOD = "whisper-sitting-alignment"
# Bumped whenever the alignment or the sentence split changes what it would
# produce, so the fingerprint forces a re-align of every sitting (the words stay
# cached — only the cheap half re-runs).
ALIGN_VERSION = 1
# YouTube's audio-only tracks, cheapest first: Opus ~45/~60 kbit/s, then AAC
# ~48 kbit/s. Speech recognition gains nothing from more.
AUDIO_FORMAT = "249/250/139/worstaudio"
# A live or not-yet-processed stream has no fixed audio to fetch: asked for one,
# yt-dlp would record the live stream for as long as it runs. The sitting is
# timed on a later pass, once the video is a video.
_LIVE_FILTER = ("live_status!=is_live & live_status!=is_upcoming "
                "& live_status!=post_live")
# How many recordings are held on disk at once: downloaded, transcribed (in
# parallel on Modal), their words cached, deleted, then the next batch.
_BATCH = 4
# The silence left between two videos of one sitting on their joint timeline,
# so a word at the end of one can never be read as the start of the next.
_VIDEO_GAP = 30.0
_WATCH_URL = "https://www.youtube.com/watch?v=%s"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- sentences --------------------------------------------------------------

_PARA_BREAK_RE = re.compile(r"\n\s*\n")


def sentence_spans(text: str) -> list[dict]:
    """A speech's text cut into sentences, each with its paragraph and its
    ``[start, end)`` character offsets into ``text``.

    The paragraphs are the parser's own (a blank line between them, BIZ-15), so
    ``para`` counts the non-empty ones exactly as the viewer's paragraph split
    does. Sentences come from the same splitter the plenary uses
    (``segment.split_sentences``) and are located back in the paragraph by
    position, which is what makes the offsets exact rather than a re-join. A
    sentence that cannot be found as written — the splitter normalises
    whitespace the text does not always share — ends the paragraph as one piece
    rather than shifting every offset after it."""
    out: list[dict] = []
    bounds: list[tuple[int, int]] = []
    pos = 0
    for m in _PARA_BREAK_RE.finditer(text or ""):
        bounds.append((pos, m.start()))
        pos = m.end()
    bounds.append((pos, len(text or "")))
    para = 0
    for ps, pe in bounds:
        chunk = text[ps:pe]
        if not chunk.strip():
            continue
        cursor = 0
        pieces: list[tuple[int, int]] = []
        for sent in split_sentences(chunk):
            found = chunk.find(sent["text"], cursor)
            if found < 0:
                pieces.append((cursor, len(chunk)))
                cursor = len(chunk)
                break
            pieces.append((found, found + len(sent["text"])))
            cursor = found + len(sent["text"])
        # Trailing text the splitter dropped (it strips) stays attached to the
        # last sentence instead of vanishing from the speech.
        if pieces and chunk[cursor:].strip():
            pieces[-1] = (pieces[-1][0], len(chunk))
        # The splitter breaks "Kérem, szavazzanak! (Szavazás.)" after the "!",
        # leaving the bracket on the spoken sentence and the stage direction
        # without its opening: the bracket belongs to what it opens.
        for k in range(len(pieces) - 1):
            a, b = pieces[k]
            body = chunk[a:b].rstrip()
            if body.endswith("("):
                cut = a + len(body) - 1
                pieces[k] = (a, cut)
                pieces[k + 1] = (cut, pieces[k + 1][1])
        for a, b in pieces:
            while a < b and chunk[a].isspace():
                a += 1
            while b > a and chunk[b - 1].isspace():
                b -= 1
            if a < b:
                out.append({"para": para, "chars": [ps + a, ps + b],
                            "text": chunk[a:b]})
        para += 1
    return out


# --- pairing a recording with its minutes -----------------------------------

_ARTICLE_RE = re.compile(r"^(?:a|az)\s+")


def fold_committee(name: str | None) -> str:
    """The key a committee name and a video title's body are matched on — case,
    accents and a leading article folded, nothing else (BIZ-22). Mirrors the
    loader's ``_fold_committee``, which links the same videos to meetings: the
    two must agree, or the site would show a video on one sitting and its
    karaoke on another."""
    flat = re.sub(r"\s+", " ", name or "").strip()
    decomposed = unicodedata.normalize("NFKD", flat)
    folded = "".join(c for c in decomposed
                     if not unicodedata.combining(c)).casefold()
    return _ARTICLE_RE.sub("", folded).strip()


def _record_date(rec: dict) -> str | None:
    """The Budapest date a sitting was held on: the cover's, which is the
    document's own; the registry's UTC timestamp only where the cover has none."""
    cover = rec.get("cover") or {}
    return cover.get("date") or (rec.get("datetime") or "")[:10] or None


def _shift(day: str, days: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=days)).isoformat()


def recording_groups(videos: list[dict]) -> list[list[dict]]:
    """The committee videos grouped into sittings: a video, followed by the
    "Folytatás" videos that continue it (same body, same day). A continuation
    with nothing to continue stands as a group of its own rather than being
    dropped — it is still a recording of *a* sitting."""
    rows = sorted((v for v in videos
                   if v.get("kind") == "committee" and v.get("date")
                   and v.get("videoId")),
                  key=lambda v: (v["date"], v.get("publishedAt") or "",
                                 v["videoId"]))
    groups: list[list[dict]] = []
    open_: dict[tuple[str, str], list[dict]] = {}
    for v in rows:
        key = (fold_committee(v.get("committeeLabel")), v["date"])
        if v.get("continued") and key in open_:
            open_[key].append(v)
            continue
        group = [v]
        groups.append(group)
        open_[key] = group
    return groups


def candidates(group: list[dict], by_key: dict) -> list[dict]:
    """The minutes records a recording group may belong to: the same body on
    the same day, or — only where that finds none — the day either side (the
    loader's UTC allowance, BIZ-16)."""
    head = group[0]
    name = fold_committee(head.get("committeeLabel"))
    exact = by_key.get((name, head["date"]), [])
    if exact:
        return exact
    return (by_key.get((name, _shift(head["date"], -1)), [])
            + by_key.get((name, _shift(head["date"], 1)), []))


# --- inputs -------------------------------------------------------------------

def _cycles_on_disk(paths: Paths) -> list[int]:
    out = []
    for p in paths.processed.glob("committee-minutes-*.json"):
        tail = p.stem[len("committee-minutes-"):]
        if tail.isdigit():
            out.append(int(tail))
    return sorted(out)


def _cycle_window(paths: Paths, cycle: int) -> tuple[str, str] | None:
    """The date range a cycle's committee registry covers, read from its small
    registry file so the large minutes file is only opened when a recording
    can fall inside it."""
    try:
        meta = json.loads(paths.committees_file(cycle).read_text(
            encoding="utf-8")).get("meta") or {}
    except (OSError, ValueError, AttributeError):
        return None
    if meta.get("dateFrom") and meta.get("dateTo"):
        return meta["dateFrom"][:10], meta["dateTo"][:10]
    return None


def load_minutes_for(paths: Paths, dates: set[str]) -> list[dict]:
    """The minutes records (with speeches) held on or around any of ``dates``,
    across every cycle on disk. Each cycle's file is opened only when its
    window can hold one of the dates, and only the matching records are kept —
    a closed cycle's file is ~100 MB and none of it is needed here."""
    if not dates:
        return []
    lo = _shift(min(dates), -1)
    hi = _shift(max(dates), 1)
    wanted = set()
    for d in dates:
        wanted.update({_shift(d, -1), d, _shift(d, 1)})
    out: list[dict] = []
    for cycle in _cycles_on_disk(paths):
        window = _cycle_window(paths, cycle)
        if window and (window[1] < lo or window[0] > hi):
            continue
        try:
            registry = json.loads(paths.committee_minutes_file(cycle).read_text(
                encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.warning("Could not read the cycle %s minutes (%s)", cycle, e)
            continue
        for rec in registry.get("data") or []:
            if _record_date(rec) in wanted and rec.get("speeches"):
                out.append({**rec, "cycle": cycle})
        del registry
    return out


# --- audio and words ----------------------------------------------------------

def _yt_dlp_cmd() -> list[str] | None:
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    if importlib.util.find_spec("yt_dlp") is not None:
        return [sys.executable, "-m", "yt_dlp"]
    return None


class YouTubeBlocked(RuntimeError):
    """YouTube refused this host as a bot ("Sign in to confirm you're not a
    bot") or rate-limited it. That is a verdict on the **address**, not on the
    video: every other recording would be refused the same way, and each refused
    attempt deepens the flag — so the pass stops asking rather than moving on to
    the next one (the parlament.hu CAPTCHA wall teaches the same thing)."""


# What yt-dlp prints when the refusal is about the host rather than the video.
# The apostrophe is a curly one in the live message, hence the wildcard.
_BOT_WALL_RE = re.compile(
    r"confirm you.{1,3}re not a bot|sign in to confirm|rate-limited by youtube",
    re.I)


@contextmanager
def youtube_egress(workdir: Path):
    """The extra yt-dlp arguments that take a download past a bot wall, set up
    for the duration of a batch and torn down after it (BIZ-30).

    * ``PARLAMONITOR_YOUTUBE_PROXY`` — a proxy URL, or ``ssh`` for the scraper's
      own SSH tunnel (the same ``PARLAMONITOR_SSH_*`` host parlament.hu can be
      reached through; its local CONNECT proxy is one yt-dlp speaks too);
    * ``PARLAMONITOR_YOUTUBE_COOKIES`` — a signed-in session's cookies file,
      handed over as a private copy, because yt-dlp writes the jar back.

    Neither is set by default, and a host YouTube does not flag needs neither."""
    args: list[str] = []
    tunnel = None
    copy = workdir / "cookies.txt"
    try:
        cookies = config.youtube_cookies()
        if cookies:
            src = Path(cookies)
            if src.is_file() and src.stat().st_size:
                workdir.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, copy)
                copy.chmod(0o600)
                args += ["--cookies", str(copy)]
            elif not src.exists():
                logger.warning("PARLAMONITOR_YOUTUBE_COOKIES is %s, which does "
                               "not exist — fetching without cookies", src)
            # Otherwise it is the compose file's /dev/null placeholder (no
            # cookies file configured on the host): nothing to say.
        proxy = config.youtube_proxy()
        if proxy and proxy.lower() == "ssh":
            from ..ssh_proxy import SSHProxy
            tunnel = SSHProxy.from_config(config.RuntimeConfig.from_env())
            if tunnel is None:
                logger.warning("PARLAMONITOR_YOUTUBE_PROXY=ssh, but no SSH tunnel "
                               "is configured (PARLAMONITOR_SSH_HOST …) — fetching "
                               "directly")
            else:
                tunnel.start()
                args += ["--proxy", tunnel.requests_proxies()["https"]]
        elif proxy:
            args += ["--proxy", proxy]
        yield args
    finally:
        if tunnel is not None:
            tunnel.close()
        copy.unlink(missing_ok=True)


def download_audio(video_id: str, dest: Path, *, timeout: float = 3600.0,
                   attempts: int = 3, extra_args: list[str] | None = None
                   ) -> Path | None:
    """Download one recording's cheapest audio-only track into ``dest``.

    Returns the file, or None when there is nothing to fetch yet (a stream still
    live or still being processed) or the download failed — either way the
    sitting simply waits for a later pass (SCR-5). Raises
    :class:`YouTubeBlocked` when YouTube refuses the host itself.

    Retried a couple of times: YouTube now and then refuses a freshly signed
    media URL with a 403 that the very next extraction does not get (seen
    2026-09-29 on a video that downloaded a minute before and after), and a
    whole sync pass is too long to wait out a coin toss. The bot wall is never
    retried — it is not a coin toss."""
    cmd = _yt_dlp_cmd()
    if cmd is None:
        logger.warning("Committee timing needs yt-dlp (on PATH or importable) "
                       "to fetch a recording's audio")
        return None
    dest.mkdir(parents=True, exist_ok=True)
    cmd = cmd + list(extra_args or []) + [
        "-f", AUDIO_FORMAT, "--no-playlist", "--no-progress",
        "--no-warnings", "--match-filter", _LIVE_FILTER,
        "--no-simulate", "--print", "after_move:filepath",
        "-o", str(dest / f"{video_id}.%(ext)s"), _WATCH_URL % video_id]
    for attempt in range(1, attempts + 1):
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=timeout)
        except (subprocess.SubprocessError, OSError) as e:
            logger.warning("Audio download failed for %s (%s)", video_id, e)
            return None
        lines = [ln.strip() for ln in
                 p.stdout.decode("utf-8", "replace").splitlines() if ln.strip()]
        path = Path(lines[-1]) if lines else None
        if p.returncode == 0 and path is not None and path.exists():
            return path
        stderr = p.stderr.decode("utf-8", "replace").strip()
        detail = stderr[:300]
        if _BOT_WALL_RE.search(stderr):
            raise YouTubeBlocked(detail)
        if p.returncode == 0:
            # Exit 0 with nothing written is the live filter at work.
            logger.info("No audio for %s yet — still live or processing",
                        video_id)
            return None
        logger.warning("No audio for %s (exit %s, attempt %d/%d)%s", video_id,
                       p.returncode, attempt, attempts,
                       f": {detail}" if detail else "")
        if attempt < attempts:
            time.sleep(5.0 * attempt)
    return None


def _identity(video_id: str) -> str:
    return f"youtube:{video_id}"


def load_words(paths: Paths, video_id: str, tag: str) -> dict | None:
    """The cached ``{words, durationS}`` of one recording, or None on a miss —
    including a cache made by another model, which must not be aligned."""
    try:
        blob = json.loads(paths.committee_whisper_cache(video_id).read_text())
    except (OSError, ValueError):
        return None
    if blob.get("fingerprint") != whisper_align.cache_fingerprint(
            _identity(video_id), tag):
        return None
    if not isinstance(blob.get("words"), list) or not blob.get("durationS"):
        return None
    return {"words": blob["words"], "durationS": float(blob["durationS"])}


def save_words(paths: Paths, video_id: str, tag: str, result: dict) -> None:
    path = paths.committee_whisper_cache(video_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = {"model": tag, "videoId": video_id,
            "fingerprint": whisper_align.cache_fingerprint(_identity(video_id), tag),
            "durationS": result.get("durationS"),
            "wordCount": len(result.get("words") or []),
            "words": result.get("words") or []}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(blob, ensure_ascii=False))
    tmp.replace(path)


def _transcribe(resolved: str, batch: list[tuple[str, Path]], *, model: str,
                language: str):
    """Yield ``(videoId, {words, durationS})`` for each downloaded recording."""
    if resolved == "whisper-modal":
        from .. import whisper_modal
        yield from whisper_modal.transcribe_audio(batch, language=language)
        return
    if resolved == "whisper-local":
        for video_id, path in batch:
            try:
                yield video_id, whisper_align.transcribe_local_audio(
                    path, model=model, language=language)
            except Exception as e:                          # noqa: BLE001 (SCR-5)
                logger.warning("Local transcription failed for %s (%s)",
                               video_id, e)
        return
    raise ValueError(f"unknown timing backend: {resolved!r}")


@dataclass
class Words:
    """What :func:`ensure_words` could get.

    ``got`` maps a video id to its ``{words, durationS}``. ``failed`` are misses
    that were tried and produced nothing (a download or transcription error —
    worth trying again next pass); ``deferred`` are misses **not tried** because
    YouTube has walled this host, and ``blocked`` is its refusal when it came
    on this pass."""
    got: dict[str, dict] = field(default_factory=dict)
    failed: list[str] = field(default_factory=list)
    deferred: list[str] = field(default_factory=list)
    blocked: str | None = None


def ensure_words(paths: Paths, wanted: list[tuple[str, int | None]], *,
                 backend: str, model: str, language: str,
                 force: bool = False, download: bool = True) -> Words:
    """The words of the ``(videoId, cycle)`` recordings asked for, transcribing
    the cache misses (see :class:`Words`).

    Cached recordings cost nothing and are always returned — including words
    made on another machine and copied into the cache, which is how a host
    YouTube walls gets its sittings timed at all. A miss is sent to the resolved
    backend — on Modal only when its sitting's cycle is inside
    ``PARLAMONITOR_MODAL_CYCLES`` (the same spend guard the plenary obeys).
    ``download=False`` fetches nothing (YouTube is known to be walling this
    host) and reports every miss as deferred."""
    tag = whisper_align.method_tag(model)
    res = Words()
    out = res.got
    misses: list[tuple[str, int | None]] = []
    for video_id, cycle in wanted:
        if video_id in out:
            continue
        cached = None if force else load_words(paths, video_id, tag)
        if cached is not None:
            out[video_id] = cached
        else:
            misses.append((video_id, cycle))
    misses = list(dict.fromkeys(misses))
    resolved = whisper_align.resolve_backend(backend)
    if not misses or resolved == "character":
        if misses:
            logger.info("No Whisper backend: %d committee recording(s) left "
                        "untimed", len(misses))
        return res

    if resolved == "whisper-modal":
        spec = config.modal_cycles()
        if spec != "all":
            cycles = _cycles_on_disk(paths)
            allowed = spec if isinstance(spec, frozenset) else (
                frozenset({max(cycles)}) if cycles else frozenset())
            skipped = {v for v, c in misses if c is not None and c not in allowed}
            if skipped:
                logger.info("Skipping Whisper on Modal for %d committee "
                            "recording(s) outside the Modal cycle scope (%s): %s",
                            len(skipped), spec, ", ".join(sorted(skipped)[:10]))
            misses = [(v, c) for v, c in misses if v not in skipped]

    if misses and not download:
        res.deferred = [v for v, _c in misses]
        return res

    failed = res.failed
    audio_dir = paths.committee_audio_dir()
    with youtube_egress(audio_dir) as egress:
        for i in range(0, len(misses), _BATCH):
            if res.blocked:
                break
            _transcribe_batch(paths, res, misses[i:i + _BATCH], audio_dir,
                              egress, resolved=resolved, model=model,
                              language=language, tag=tag)
    if res.blocked:
        res.deferred = [v for v, _c in misses
                        if v not in out and v not in failed]
    return res


def _transcribe_batch(paths: Paths, res: Words, chunk, audio_dir: Path,
                      egress: list[str], *, resolved: str, model: str,
                      language: str, tag: str) -> None:
    """Download one batch of recordings, transcribe what arrived, cache the
    words and delete the audio. Stops downloading at the first bot wall; what
    was already downloaded is still transcribed."""
    out, failed = res.got, res.failed
    batch: list[tuple[str, Path]] = []
    try:
        for video_id, _cycle in chunk:
            try:
                path = download_audio(video_id, audio_dir, extra_args=egress)
            except YouTubeBlocked as e:
                res.blocked = str(e)
                logger.warning(
                    "YouTube refused this host as a bot (%s) — no more "
                    "recordings are asked for this pass. Route around it with "
                    "PARLAMONITOR_YOUTUBE_PROXY or PARLAMONITOR_YOUTUBE_COOKIES, "
                    "or transcribe on another machine and copy "
                    "committees/whisper/ over", e)
                break
            if path is None:
                failed.append(video_id)
            else:
                batch.append((video_id, path))
        if not batch:
            return
        logger.info("Transcribing %d committee recording(s) with %s",
                    len(batch), resolved)
        got = set()
        try:
            for video_id, result in _transcribe(resolved, batch, model=model,
                                                language=language):
                if result.get("words") and result.get("durationS"):
                    save_words(paths, video_id, tag, result)
                    out[video_id] = {"words": result["words"],
                                     "durationS": float(result["durationS"])}
                    got.add(video_id)
        except Exception as e:                              # noqa: BLE001 (SCR-5)
            # A backend that fails as a whole — most likely a Whisper app
            # deployed before `transcribe_audio` existed — fails this batch,
            # not the stage: what is already timed is still written.
            logger.warning("Transcribing committee recordings failed (%s)%s", e,
                           " — redeploy scraper/whisper_modal_app.py if it "
                           "predates transcribe_audio"
                           if resolved == "whisper-modal" else "")
        failed.extend(v for v, _p in batch if v not in got)
    finally:
        for _video_id, path in batch:
            path.unlink(missing_ok=True)


# --- alignment ----------------------------------------------------------------

def _fingerprint(rec: dict, videos: list[str], tag: str) -> str:
    h = hashlib.sha256()
    h.update(json.dumps([ALIGN_VERSION, tag, videos], ensure_ascii=False)
             .encode("utf-8"))
    for sp in rec.get("speeches") or []:
        h.update(b"\x00")
        h.update(str(sp.get("ord")).encode("utf-8"))
        h.update(b"\x01")
        h.update((sp.get("text") or "").encode("utf-8"))
    return h.hexdigest()[:16]


def align_record(rec: dict, recordings: list[tuple[str, dict]]) -> dict | None:
    """Time one sitting's sentences against its recordings, in order.

    ``recordings`` is ``[(videoId, {words, durationS}), ...]``. Several videos
    (a sitting that overran into a second stream) are laid end to end on one
    timeline with a gap between them, aligned as one, and every sentence is
    mapped back to the video it fell in, in that video's own seconds — which
    is what a player seeks by. Returns None when the alignment is not trusted.
    """
    offsets: list[float] = []
    words: list[list] = []
    cursor = 0.0
    for _video_id, got in recordings:
        offsets.append(cursor)
        for w in got["words"]:
            if len(w) >= 3:
                words.append([float(w[0]) + cursor, float(w[1]) + cursor, w[2]])
        cursor += float(got["durationS"]) + _VIDEO_GAP
    end = cursor - _VIDEO_GAP

    speeches = rec.get("speeches") or []
    split = [sentence_spans(sp.get("text") or "") for sp in speeches]
    result = whisper_align.align_sitting(split, words, end)
    if result is None:
        return None
    spans, coverage = result

    def local(t: float) -> tuple[int, float]:
        k = 0
        while k + 1 < len(offsets) and t >= offsets[k + 1]:
            k += 1
        return k, t - offsets[k]

    out_speeches = []
    timed = total = 0
    for sp, sents, times in zip(speeches, split, spans):
        rows = []
        for sent, span in zip(sents, times):
            total += 1
            row = {**sent, "videoId": None, "timeStart": None, "timeEnd": None}
            if span is not None:
                k, t0 = local(span[0])
                duration = float(recordings[k][1]["durationS"])
                t0 = min(max(t0, 0.0), duration)
                t1 = min(max(span[1] - offsets[k], t0), duration)
                row.update(videoId=recordings[k][0], timeStart=round(t0, 2),
                           timeEnd=round(t1, 2))
                timed += 1
            rows.append(row)
        out_speeches.append({"ord": sp.get("ord"), "sentences": rows})
    return {"coverage": round(coverage, 3), "sentences": total, "timed": timed,
            "speeches": out_speeches}


def build_timing(paths: Paths, *, backend: str | None = None,
                 model: str | None = None, language: str | None = None,
                 force: bool = False, meetings: set[str] | None = None,
                 previous: dict | None = None) -> dict:
    """Time every sitting that has both a parsed jegyzőkönyv and a recording.

    ``previous`` (the last ``committee-timing.json``) is what makes a repeat
    pass free: a sitting whose fingerprint is unchanged is carried over as it
    was. ``force`` re-transcribes and re-aligns everything asked for;
    ``meetings`` narrows the pass to those meeting ids (the rest of the previous
    file is kept as it was)."""
    backend = backend or config.timing_backend()
    model = model or config.whisper_model()
    language = language or config.whisper_language()
    tag = whisper_align.method_tag(model)
    held = {r["meetingId"]: r for r in (previous or {}).get("data") or []
            if r.get("meetingId")}

    try:
        registry = json.loads(paths.committee_videos_file().read_text(
            encoding="utf-8"))
    except (OSError, ValueError):
        registry = {}
    groups = recording_groups(registry.get("data") or [])
    records = load_minutes_for(paths, {g[0]["date"] for g in groups})
    by_key: dict[tuple[str, str], list[dict]] = {}
    for rec in records:
        if meetings and rec.get("meetingId") not in meetings:
            continue
        by_key.setdefault((fold_committee(rec.get("committeeName")),
                           _record_date(rec)), []).append(rec)

    paired = [(g, candidates(g, by_key)) for g in groups]
    paired = [(g, c) for g, c in paired if c]
    wanted = [(v["videoId"], c[0].get("cycle")) for g, c in paired for v in g]
    # While YouTube is walling this host, nothing is downloaded — but cached
    # words (made here earlier, or on another machine and copied in) are still
    # aligned, so the sittings they cover are timed all the same.
    block = block_active(previous) if not force else None
    if block:
        logger.info("YouTube refused this host at %s; not asking again until %s "
                    "(PARLAMONITOR_YOUTUBE_BLOCK_HOURS) — aligning cached "
                    "recordings only", block.get("at"), block.get("until"))
    # A cached recording costs a file read; only a miss is downloaded and
    # transcribed, so this is cheap on every pass after the first.
    words = ensure_words(paths, wanted, backend=backend, model=model,
                         language=language, force=force, download=not block)
    got, failed = words.got, words.failed
    if words.blocked:
        now = datetime.now(timezone.utc)
        block = {"at": now.isoformat(timespec="seconds"),
                 "until": (now + timedelta(hours=config.youtube_block_hours()))
                 .isoformat(timespec="seconds"),
                 "error": words.blocked}

    # A group with several candidate sittings (a committee that met twice on
    # one day) goes to the one its recording actually aligns with.
    by_meeting: dict[str, tuple[dict, list[dict]]] = {}
    waiting: set[str] = set()
    for group, cands in paired:
        if not all(v["videoId"] in got for v in group):
            # No words for it this pass (a failed download, a recording outside
            # the Modal scope): whatever it was timed as before still stands.
            waiting.update(c["meetingId"] for c in cands)
            continue
        rec = cands[0]
        if len(cands) > 1:
            scored = []
            for cand in cands:
                res = align_record(cand, [(v["videoId"], got[v["videoId"]])
                                          for v in group])
                scored.append(((res or {}).get("coverage") or 0.0, cand))
            rec = max(scored, key=lambda s: s[0])[1]
        mid = rec["meetingId"]
        if mid in by_meeting:
            by_meeting[mid][1].extend(group)
        else:
            by_meeting[mid] = (rec, list(group))

    data: dict[str, dict] = dict(held) if meetings else {
        mid: row for mid, row in held.items()
        if mid in waiting and mid not in by_meeting}
    counts = {"sittings": 0, "reused": 0, "aligned": 0, "untrusted": 0,
              "failed": len(failed), "deferred": len(words.deferred),
              "sentences": 0, "timed": 0}
    for mid, (rec, group) in by_meeting.items():
        group.sort(key=lambda v: (bool(v.get("continued")),
                                  v.get("publishedAt") or "", v["videoId"]))
        video_ids = [v["videoId"] for v in group]
        fp = _fingerprint(rec, video_ids, tag)
        prior = held.get(mid)
        if prior and prior.get("fingerprint") == fp and not force:
            data[mid] = prior
            counts["reused"] += 1
        else:
            res = align_record(rec, [(v, got[v]) for v in video_ids])
            if res is None:
                logger.info("Committee timing: %s (%s, %s) did not align — "
                            "served without karaoke", mid,
                            rec.get("committeeName"), _record_date(rec))
                counts["untrusted"] += 1
                data.pop(mid, None)
                continue
            data[mid] = {
                "meetingId": mid, "cycle": rec.get("cycle"),
                "committeeName": rec.get("committeeName"),
                "heldOn": _record_date(rec),
                "method": METHOD, "model": tag, "fingerprint": fp,
                "alignedAt": _now_iso(), "coverage": res["coverage"],
                "sentences": res["sentences"], "timed": res["timed"],
                "videos": [{"videoId": v,
                            "durationS": round(got[v]["durationS"], 2)}
                           for v in video_ids],
                "speeches": res["speeches"],
            }
            counts["aligned"] += 1
        counts["sittings"] += 1
        counts["sentences"] += data[mid].get("sentences") or 0
        counts["timed"] += data[mid].get("timed") or 0

    rows = sorted(data.values(), key=lambda r: (r.get("heldOn") or "",
                                                r["meetingId"]), reverse=True)
    logger.info("Committee timing: %d sitting(s) timed (%d aligned, %d reused, "
                "%d untrusted, %d recording(s) failed, %d waiting on YouTube) — "
                "%d of %d sentences placed", counts["sittings"],
                counts["aligned"], counts["reused"], counts["untrusted"],
                counts["failed"], counts["deferred"], counts["timed"],
                counts["sentences"])
    return {
        "meta": {"source": SOURCE, "method": METHOD, "model": tag,
                 "alignVersion": ALIGN_VERSION, "scrapedAt": _now_iso(),
                 "count": len(rows), "counts": counts, "failed": failed,
                 "deferred": words.deferred,
                 # Set while YouTube is walling this host (see block_active);
                 # cleared by the first pass after it that can ask again.
                 "youtubeBlock": block or None},
        "data": rows,
    }


def block_active(registry: dict | None) -> dict | None:
    """The YouTube bot wall a previous pass recorded, while it still stands —
    ``{at, until, error}`` — or None once ``until`` has passed (or never set)."""
    block = ((registry or {}).get("meta") or {}).get("youtubeBlock")
    if not block or not block.get("until"):
        return None
    try:
        until = datetime.fromisoformat(block["until"])
    except (TypeError, ValueError):
        return None
    return block if until > datetime.now(timezone.utc) else None


def load_previous(paths: Paths) -> dict | None:
    f = paths.committee_timing_file()
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("Could not read %s (%s) — treating as a first run", f, e)
        return None


def save_timing(paths: Paths, registry: dict) -> None:
    out = paths.committee_timing_file()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(registry, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out)
    logger.info("Wrote %s (%d sittings)", out, registry["meta"]["count"])
