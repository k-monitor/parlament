"""Bluesky announcements (§8.7): the haiku finder, the AT Protocol post shape and
the announcer's decisions about what to say once.

Nothing here touches the network: the client is driven through an injected opener
so the real request-building code is exercised, and the announcer is driven with a
recording fake client.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date

import pytest

from app import bluesky, loader, social
from app.config import settings
from app.haiku import find_haikus, haikus_in_session, tokenize

# An accidental haiku: 5 + 7 + 5 Hungarian syllables (= vowels), breaking on word
# boundaries. See app/haiku.py for the rules.
HAIKU_SENTENCE = "A költségvetés fontos kérdés marad ma a magyar népnek."
HAIKU_LINES = ["A költségvetés", "fontos kérdés marad ma", "a magyar népnek."]
# Also a haiku, but said by the chair while running the sitting — procedural, so
# never posted (STAT-1).
CHAIR_HAIKU_SENTENCE = "Az elnök szólal: most a szavazás jön el, kérem, üljenek."


# ---------------------------------------------------------------------------
# The finder
# ---------------------------------------------------------------------------

def test_finds_a_whole_sentence_haiku():
    words = tokenize(HAIKU_SENTENCE)
    spans = find_haikus(words, partial=False)
    assert len(spans) == 1
    assert spans[0] == (0, 2, 6, 9)          # 5 / 7 / 5 on word boundaries


def test_a_sentence_that_is_not_575_is_not_a_haiku():
    assert find_haikus(tokenize("A költségvetés fontos kérdés."), partial=False) == []


@pytest.mark.parametrize("sentence", [
    "A 2026. évi költségvetés fontos kérdés marad ma.",   # digits: spoken as words
    "A költségvetés (Taps.) kérdés marad ma a magyar népnek.",  # stage direction
    "A költségvetés fontos kérdés marad ma DR. magyar népnek.",  # vowel-less token
])
def test_unreliable_sentences_are_skipped(sentence):
    """Syllable counting would be wrong on these, so they are not judged at all."""
    words = tokenize(sentence)
    assert words is None or find_haikus(words, partial=False) == []


def test_partial_finds_a_run_inside_a_longer_sentence():
    longer = "Elnök úr, " + HAIKU_SENTENCE
    assert find_haikus(tokenize(longer), partial=False) == []
    assert find_haikus(tokenize(longer), partial=True)


# ---------------------------------------------------------------------------
# Post shape (AT Protocol)
# ---------------------------------------------------------------------------

def test_link_facets_are_byte_offsets_not_character_offsets():
    """Accented Hungarian before a URL makes the two differ — which is the bug this
    guards: a facet with character offsets links the wrong range."""
    text = "Ülésnap ötven felszólalással: https://példa.hu/x"
    url = "https://példa.hu/x"
    (facet,) = bluesky.link_facets(text)
    start, end = facet["index"]["byteStart"], facet["index"]["byteEnd"]
    assert text.encode()[start:end].decode() == url
    assert start != text.index(url)              # bytes ≠ characters here
    assert facet["features"][0]["uri"] == url
    assert facet["features"][0]["$type"] == "app.bsky.richtext.facet#link"


def test_link_facet_drops_sentence_punctuation_after_the_url():
    (facet,) = bluesky.link_facets("Lásd: https://example.org/a.")
    assert facet["features"][0]["uri"] == "https://example.org/a"


def test_clip_trims_on_a_word_boundary_within_budget():
    clipped = bluesky.clip("egy kettő három négy öt", 12)
    assert bluesky.graphemes(clipped) <= 12
    assert clipped.endswith("…") and "kettő" in clipped
    # Text that fits is returned untouched.
    assert bluesky.clip("rövid", 12) == "rövid"


class _Response:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _recording_opener(calls):
    def opener(req, timeout=None):
        calls.append(req)
        if "createSession" in req.full_url:
            return _Response({"did": "did:plc:test", "handle": "bot.example",
                              "accessJwt": "jwt-token"})
        return _Response({"uri": "at://did:plc:test/app.bsky.feed.post/abc",
                          "cid": "cid"})
    return opener


def test_client_builds_a_valid_post_record():
    calls: list = []
    client = bluesky.BlueskyClient("bot.example", "app-pass-word",
                                   opener=_recording_opener(calls))
    uri = client.post("Ülésnap: https://example.org/sessions/43002",
                      embed=bluesky.external_embed("https://example.org/x", "t", "d"))

    assert uri == "at://did:plc:test/app.bsky.feed.post/abc"
    session_call, post_call = calls
    assert session_call.full_url.endswith("/xrpc/com.atproto.server.createSession")
    assert json.loads(session_call.data) == {"identifier": "bot.example",
                                             "password": "app-pass-word"}
    assert post_call.full_url.endswith("/xrpc/com.atproto.repo.createRecord")
    assert post_call.get_header("Authorization") == "Bearer jwt-token"
    body = json.loads(post_call.data)
    assert body["repo"] == "did:plc:test"
    assert body["collection"] == "app.bsky.feed.post"
    record = body["record"]
    assert record["$type"] == "app.bsky.feed.post"
    assert record["langs"] == ["hu"]
    assert record["createdAt"].endswith("Z")
    assert record["facets"][0]["features"][0]["uri"] == "https://example.org/sessions/43002"
    assert record["embed"]["$type"] == "app.bsky.embed.external"


def test_post_over_the_grapheme_limit_is_refused_before_it_is_sent():
    calls: list = []
    client = bluesky.BlueskyClient("bot.example", "pw", opener=_recording_opener(calls))
    with pytest.raises(bluesky.BlueskyError, match="over the 300"):
        client.post("á" * 301)
    assert calls == []           # not even a login was attempted


def test_credentials_come_from_one_env_var(monkeypatch):
    monkeypatch.setattr(settings, "bluesky_auth", "bot.example:abcd-efgh-ijkl-mnop")
    assert bluesky.credentials() == ("bot.example", "abcd-efgh-ijkl-mnop")
    # The split is on the LAST colon, so a DID identifier keeps its own colons (an
    # app password never has one).
    monkeypatch.setattr(settings, "bluesky_auth", "did:plc:abc123:abcd-efgh-ijkl-mnop")
    assert bluesky.credentials() == ("did:plc:abc123", "abcd-efgh-ijkl-mnop")
    # Malformed, and the per-key alternative.
    monkeypatch.setattr(settings, "bluesky_auth", "bot.example")
    monkeypatch.setattr(settings, "bluesky_handle", "")
    monkeypatch.setattr(settings, "bluesky_password", "")
    assert bluesky.credentials() is None
    monkeypatch.setattr(settings, "bluesky_auth", "")
    monkeypatch.setattr(settings, "bluesky_handle", "bot.example")
    monkeypatch.setattr(settings, "bluesky_password", "pw")
    assert bluesky.credentials() == ("bot.example", "pw")


# ---------------------------------------------------------------------------
# The announcer
# ---------------------------------------------------------------------------

TODAY = date(2026, 5, 12)


def _complete_session_record(session="43002", sitting=2, day="2026-05-11"):
    """A sitting day published IN FULL — every speech has both a transcript and a
    per-speech video window, which is what makes it SIT-2 `complete`.

    Three speeches: an MP whose sentence is an accidental haiku, the chair saying
    one too (procedural — never posted), and an ordinary MP speech so the day's
    counts are not all haiku."""
    video = "https://example/playlist.m3u8"
    # `duration` is a PER-SPEECH clip length upstream, which is why the day post must
    # not report it as the day's length (it lands in session.video_duration).
    media = {"videoFileURI": video, "duration": 33120, "creator": "Magyar Országgyűlés",
             "license": "https://lic", "sourcePage": "https://parlament.hu/x",
             "videoStart": 10.0, "videoEnd": 40.0}

    def speech(index, label, pid, faction, sentences, speech_type=None):
        # 15 minutes per sentence, each speech starting an hour after the last, so the
        # day has a realistic speaking time to report.
        base = (index - 1) * 3600
        return {
            "originID": f"43-{sitting}-{index}", "speechIndex": index,
            "electoralPeriod": {"number": 43},
            "agendaItem": {"title": "Napirend előtt", "officialTitle": "Napirend előtt",
                           "type": "debate", "nativeType": "HU-debate"},
            "people": [{"type": "memberOfParliament", "label": label,
                        "context": "main-speaker", "personID": pid,
                        "faction": {"label": faction}}],
            "media": dict(media),
            "textContents": [{"type": "proceedings", "sourceURI": "https://parlament.hu/x",
                              "textBody": [{"speech_id": f"43-{sitting}-{index}",
                                            "sentences": [
                                                {"text": t, "timeStart": base + i * 900,
                                                 "timeEnd": base + (i + 1) * 900,
                                                 "paragraph": 0}
                                                for i, t in enumerate(sentences)]}]}],
            "debug": {"confidence": 0.9, "align-method": "forced-alignment",
                      "speechUUID": f"uuid-{session}-{index}",
                      **({"felszolalasTipusa": speech_type} if speech_type else {})},
        }

    return {
        "meta": {"session": session, "electoralPeriod": 43, "sitting": sitting,
                 "date": day, "dateStart": f"{day}T08:00:00",
                 "dateEnd": f"{day}T17:12:00", "source": "felicitas-json",
                 "status": "published", "dayVideoURI": video,
                 "timingMethod": "whisper-forced-alignment",
                 "sourceScrapedAt": "2026-05-11T20:00:00+00:00"},
        "data": [
            speech(1, "Kovács Béla", "k001", "Fidesz",
                   ["Tisztelt Elnök úr!", HAIKU_SENTENCE]),
            # The chair, running the sitting: procedural (STAT-1), so its haiku is
            # an artefact of the formula and not anybody's contribution.
            speech(2, "Nagy Anna", "n002", "TISZA", [CHAIR_HAIKU_SENTENCE],
                   speech_type="ülésvezetés"),
            speech(3, "Nagy Anna", "n002", "TISZA", ["Nem támogatjuk a javaslatot."]),
        ],
    }


@pytest.fixture
def announce_db(data_dir, tmp_path):
    """A DB holding the conftest day (43001, half-published: one speech has no
    transcript) plus a fully published day (43002) with the haiku."""
    (data_dir / "processed" / "43002-session.json").write_text(
        json.dumps(_complete_session_record(), ensure_ascii=False))
    db = tmp_path / "announce.db"
    loader.build_database(data_dir, db)
    return db


@pytest.fixture(autouse=True)
def bluesky_settings(monkeypatch):
    """Announcing switched on with a known public origin, and the shipped defaults
    for everything else."""
    monkeypatch.setattr(settings, "site_url", "https://example.org")
    monkeypatch.setattr(settings, "bluesky_base_url", None)
    monkeypatch.setattr(settings, "bluesky_announce", True)
    monkeypatch.setattr(settings, "bluesky_dry_run", False)
    monkeypatch.setattr(settings, "bluesky_haikus", True)
    monkeypatch.setattr(settings, "bluesky_haiku_per_day", 1)
    monkeypatch.setattr(settings, "bluesky_haiku_mps_only", True)
    monkeypatch.setattr(settings, "bluesky_max_posts", 4)
    monkeypatch.setattr(settings, "bluesky_max_age_days", 30)
    monkeypatch.setattr(settings, "bluesky_auth", "")
    monkeypatch.setattr(settings, "bluesky_handle", "")
    monkeypatch.setattr(settings, "bluesky_password", "")


class FakeClient:
    """Records what would go out; optionally fails like a rate-limited PDS."""

    def __init__(self, fail_after: int | None = None):
        self.posts: list[dict] = []
        self.fail_after = fail_after

    def post(self, text, *, embed=None, langs=None):
        if self.fail_after is not None and len(self.posts) >= self.fail_after:
            raise bluesky.BlueskyError("RateLimitExceeded")
        self.posts.append({"text": text, "embed": embed})
        return f"at://did:plc:test/app.bsky.feed.post/{len(self.posts)}"


def _run(db, state, client=None, **kw):
    kw.setdefault("today", TODAY)
    client = client if client is not None else FakeClient()
    result = social.announce(db, client=client, state_file=state, **kw)
    return result, client


def _seeded_state(path, **buckets):
    """An EXISTING state file, so a run is not a first run."""
    state = {"version": social.STATE_VERSION, "days": {}, "haikus": {}, "scans": {}}
    state.update(buckets)
    path.write_text(json.dumps(state), encoding="utf-8")
    return path


def test_first_run_seeds_and_posts_nothing(announce_db, tmp_path):
    """A fresh deploy (or a wiped volume) must not dump a backlog into the feed."""
    state = tmp_path / "state.json"
    result, client = _run(announce_db, state)

    assert client.posts == []
    assert result.posts == []
    assert result.seeded == 1                      # only 43002 is complete
    stored = json.loads(state.read_text())
    assert "43002" in stored["days"] and "43001" not in stored["days"]
    # Both days' haiku scans are recorded, so their existing poems stay unposted.
    assert set(stored["scans"]) == {"43001", "43002"}


def test_announces_a_fully_processed_day_and_its_haiku(announce_db, tmp_path):
    state = _seeded_state(tmp_path / "state.json")
    result, client = _run(announce_db, state)

    kinds = [p.kind for p in result.posts]
    assert kinds == ["day", "haiku"]               # the day first, then its poem
    day, haiku = (p["text"] for p in client.posts)

    assert "43. ciklus 2. ülésnapja" in day
    assert "2026. május 11." in day
    assert "3 felszólalás" in day and "2 felszólaló" in day
    # Speaking time as the day's own toplist sums it (STAT-1: the chair's procedural
    # speech is not in it), NOT the per-speech `duration` sitting in the day row.
    assert "45 perc beszédidő" in day and "9 óra" not in day
    assert "https://example.org/sessions/43002" in day

    for line in HAIKU_LINES:
        assert line in haiku
    assert "Kovács Béla (Fidesz)" in haiku
    assert "https://example.org/proceedings/43002-1?s=1" in haiku
    # The chair's procedural haiku is not somebody's contribution — never posted.
    assert not any("üljenek" in p["text"] for p in client.posts)
    # Every post is inside the server's own limit.
    assert all(bluesky.graphemes(p["text"]) <= bluesky.MAX_GRAPHEMES
               for p in client.posts)


def test_a_second_pass_says_nothing_new(announce_db, tmp_path):
    state = _seeded_state(tmp_path / "state.json")
    _run(announce_db, state)
    result, client = _run(announce_db, state)
    assert client.posts == [] and result.posts == []


def test_a_half_published_day_is_not_announced(announce_db, tmp_path):
    """43001 has a speech with no transcript: the site badges it "Részben
    feldolgozva", so the bot must not call it processed (SIT-2/SOC-2)."""
    state = _seeded_state(tmp_path / "state.json")
    result, _ = _run(announce_db, state)
    assert [p.session_id for p in result.posts if p.kind == "day"] == ["43002"]

    # It is announced once the missing transcript lands.
    conn = sqlite3.connect(announce_db)
    conn.execute("UPDATE speech SET has_text = 1 WHERE session_id = '43001'")
    conn.commit()
    conn.close()
    result, _ = _run(announce_db, state)
    assert [p.session_id for p in result.posts if p.kind == "day"] == ["43001"]


def test_days_older_than_the_window_are_left_alone(announce_db, tmp_path):
    state = _seeded_state(tmp_path / "state.json")
    result, client = _run(announce_db, state, today=date(2026, 8, 1))
    assert client.posts == [] and result.posts == []


def test_haiku_quota_is_per_day_not_per_pass(announce_db, tmp_path):
    """A day's transcript arrives in instalments, so the same day is re-scanned;
    the quota counts poems already POSTED for it."""
    state = _seeded_state(tmp_path / "state.json")
    _, client = _run(announce_db, state)
    assert sum(1 for p in client.posts if "költségvetés" in p["text"]) == 1

    # More of the day lands (a new speech with another haiku): re-scanned, but the
    # day's quota of one is already spent.
    conn = sqlite3.connect(announce_db)
    conn.execute("INSERT INTO speech(uid, session_id, period_number, speech_index, "
                 "person_id, speaker_label, has_text, video_start, procedural) "
                 "VALUES ('43002-4','43002',43,4,'k001','Kovács Béla',1,60.0,0)")
    conn.execute("INSERT INTO sentence(speech_id, ord, text) VALUES "
                 "('43002-4', 0, ?)", (HAIKU_SENTENCE,))
    conn.commit()
    conn.close()
    result, client = _run(announce_db, state)
    assert [p.kind for p in result.posts] == [] and client.posts == []


def test_only_representatives_haikus_are_posted(announce_db, tmp_path):
    """`mps_only`: the speaker must hold a mandate in the roster."""
    conn = sqlite3.connect(announce_db)
    conn.row_factory = sqlite3.Row
    assert [h.speaker for h in haikus_in_session(conn, "43002")] == ["Kovács Béla"]
    # A minister who is not in the MP roster speaks the same words: no longer "a
    # representative said a haiku", so it is out unless the scope is widened.
    conn.execute("UPDATE person SET is_mp = 0 WHERE person_id = 'k001'")
    conn.commit()
    assert haikus_in_session(conn, "43002") == []
    assert [h.speaker for h in haikus_in_session(conn, "43002", mps_only=False)] \
        == ["Kovács Béla"]
    conn.close()


def test_haikus_can_be_switched_off(announce_db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "bluesky_haikus", False)
    state = _seeded_state(tmp_path / "state.json")
    result, _ = _run(announce_db, state)
    assert [p.kind for p in result.posts] == ["day"]


def test_dry_run_sends_nothing_and_remembers_nothing(announce_db, tmp_path):
    state = _seeded_state(tmp_path / "state.json")
    before = state.read_text()
    result, client = _run(announce_db, state, dry_run=True)

    assert [p.kind for p in result.posts] == ["day", "haiku"]   # decided...
    assert client.posts == []                                   # ...but not sent
    assert state.read_text() == before                          # ...nor recorded


def test_a_failed_send_stays_pending(announce_db, tmp_path):
    """The day post goes out, the haiku fails: the next pass retries just the
    haiku, and never repeats the day."""
    state = _seeded_state(tmp_path / "state.json")
    result, client = _run(announce_db, state, client=FakeClient(fail_after=1))
    assert result.failed and [p.kind for p in result.posts] == ["day"]
    stored = json.loads(state.read_text())
    assert "43002" in stored["days"]
    assert stored["haikus"] == {} and "43002" not in stored["scans"]

    result, client = _run(announce_db, state)
    assert [p.kind for p in result.posts] == ["haiku"]


def test_an_oversized_post_is_skipped_without_blocking_the_others(
        announce_db, tmp_path, monkeypatch):
    """A post the server would reject is a builder bug. It must not go out, must not
    be recorded (so a fix makes it postable), and must not stall the queue."""
    real = social.haiku_post
    monkeypatch.setattr(social, "haiku_post",
                        lambda h, row, base: social.Post(
                            **{**vars(real(h, row, base)), "text": "á" * 400}))
    state = _seeded_state(tmp_path / "state.json")
    result, client = _run(announce_db, state)

    assert [p.kind for p in result.posts] == ["day"]      # the day still went out
    assert len(client.posts) == 1 and not result.failed
    stored = json.loads(state.read_text())
    assert stored["haikus"] == {} and "43002" not in stored["scans"]

    # With the builder fixed, the next pass posts it.
    monkeypatch.setattr(social, "haiku_post", real)
    result, _ = _run(announce_db, state)
    assert [p.kind for p in result.posts] == ["haiku"]


def test_the_per_run_cap_defers_the_rest(announce_db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "bluesky_max_posts", 1)
    state = _seeded_state(tmp_path / "state.json")
    result, client = _run(announce_db, state)
    assert [p.kind for p in result.posts] == ["day"] and result.skipped

    result, _ = _run(announce_db, state)
    assert [p.kind for p in result.posts] == ["haiku"]


def test_nothing_happens_without_credentials(announce_db, tmp_path):
    """The configured account IS the switch: no auth, no posts, no state file."""
    state = tmp_path / "state.json"
    result = social.announce(announce_db, state_file=state, today=TODAY)
    assert result.posts == [] and not state.exists()


def test_the_master_switch_stops_the_bot(announce_db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "bluesky_announce", False)
    state = _seeded_state(tmp_path / "state.json")
    result, client = _run(announce_db, state)
    assert result.posts == [] and client.posts == []


def test_no_public_base_url_means_no_unusable_links(announce_db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "site_url", None)
    state = _seeded_state(tmp_path / "state.json")
    result, client = _run(announce_db, state)
    assert result.posts == [] and client.posts == []


def test_a_corrupt_state_file_is_treated_as_a_first_run(announce_db, tmp_path):
    """Rather than crashing the sync pass — the cost is one silent seeding, not a
    duplicate flood."""
    state = tmp_path / "state.json"
    state.write_text("{not json", encoding="utf-8")
    result, client = _run(announce_db, state)
    assert client.posts == [] and result.seeded == 1
    assert json.loads(state.read_text())["days"]


def test_backfill_posts_the_window_on_a_first_run(announce_db, tmp_path):
    state = tmp_path / "state.json"
    result, client = _run(announce_db, state, backfill=True)
    assert [p.kind for p in result.posts] == ["day", "haiku"]
    assert len(client.posts) == 2
