"""API contract tests: shapes the SPA relies on, module manifest, deep-linking,
sentence↔time mapping, and graceful degradation (OPS-3, EXT-3/EXT-6)."""

from __future__ import annotations


def test_meta_lists_modules_and_attribution(client):
    m = client.get("/api/v1/meta").json()
    names = {mod["name"] for mod in m["modules"]}
    assert {"proceedings", "representatives"} <= names
    assert m["source_attribution"]["url"] == "https://www.parlament.hu"  # LEGAL-1
    assert m["counts"]["sessions"] == 1


def test_health_ok(client):
    assert client.get("/api/v1/health").json()["status"] == "ok"


def test_speech_viewer_payload(client):
    r = client.get("/api/v1/proceedings/speeches/43001-1")
    assert r.status_code == 200
    d = r.json()
    assert d["speech"]["speaker"]["label"] == "Kovács Béla"
    assert len(d["sentences"]) == 2
    # Sentences carry day-absolute timing for click-to-seek (VIE-3).
    assert d["sentences"][0]["time_start"] == 10.0
    assert d["session"]["video_uri"].endswith(".m3u8")     # VIE-2 HLS source
    # The VOD activation endpoint is carried through so the player can ping it.
    assert "playseq.php" in d["session"]["video_playseq"]
    assert d["speech"]["source_page"]                       # VIE-7 source link
    assert d["neighbours"]["next"] == "43001-2"             # prev/next nav


def test_speech_not_found(client):
    assert client.get("/api/v1/proceedings/speeches/99999-1").status_code == 404


def test_speech_text_returns_sentences_for_inline_spoiler(client):
    """The sitting-day spoiler pulls just the transcript, not the viewer payload."""
    d = client.get("/api/v1/proceedings/speeches/43001-1/text").json()
    assert d["uid"] == "43001-1"
    assert d["has_text"] is True
    assert len(d["sentences"]) == 2
    assert [s["ord"] for s in d["sentences"]] == [0, 1]
    assert all(s["text"] for s in d["sentences"])
    # Each sentence carries its source-paragraph index so the reader can
    # reconstruct the transcript's original paragraphs.
    assert [s["paragraph"] for s in d["sentences"]] == [0, 1]


def test_speech_text_video_only_speech_is_empty(client):
    """A video-only speech (VIE-8) reports has_text=False with no sentences."""
    d = client.get("/api/v1/proceedings/speeches/43001-2/text").json()
    assert d["has_text"] is False
    assert d["sentences"] == []


def test_speech_text_not_found(client):
    assert client.get("/api/v1/proceedings/speeches/99999-1/text").status_code == 404


# --- clip export window (VIE-10) -------------------------------------------

# A real smil-format day URI so per_speech_clip can shift its offsets (the
# conftest default URI is a bare .m3u8 with no smil offsets to crop).
_SMIL_DAY = ("https://sgis.parlament.hu:446/vod/"
             "smil:20260509.092628.2143172.24318900.smil/playlist.m3u8")


def _seed_smil_day(conn):
    conn.execute("UPDATE session SET video_uri = ?, video_playseq = ? WHERE id = '43001'",
                 (_SMIL_DAY, "https://sgis.parlament.hu/archive/playseq.php?"
                  "date1=20260509&time1=092628&offset1=003543.172"
                  "&date2=20260509&time2=092628&offset2=064518.9&type=real"))
    conn.commit()


def test_speech_clip_defaults_to_whole_speech_window(client, conn):
    """No start/end → the speech's own [video_start, video_end] window."""
    _seed_smil_day(conn)
    d = client.get("/api/v1/proceedings/speeches/43001-1/clip").json()
    # Speech 43001-1 is day-relative [10, 40]; day stream starts at 2143172 ms,
    # so the cropped smil is [2143172+10000, 2143172+40000].
    assert d["start"] == 10.0 and d["end"] == 40.0 and d["duration"] == 30.0
    assert "smil:20260509.092628.2153172.2183172.smil" in d["video_uri"]
    assert "playseq.php" in d["video_playseq"]


def test_speech_clip_crops_to_subrange(client, conn):
    _seed_smil_day(conn)
    d = client.get("/api/v1/proceedings/speeches/43001-1/clip",
                   params={"start": 20, "end": 30}).json()
    assert d["start"] == 20.0 and d["end"] == 30.0
    assert "smil:20260509.092628.2163172.2173172.smil" in d["video_uri"]


def test_speech_clip_clamps_request_into_the_speech(client, conn):
    """A window reaching outside the speech is clamped to the speech bounds."""
    _seed_smil_day(conn)
    d = client.get("/api/v1/proceedings/speeches/43001-1/clip",
                   params={"start": 0, "end": 9999}).json()
    assert d["start"] == 10.0 and d["end"] == 40.0


def test_speech_clip_rejects_empty_window(client, conn):
    _seed_smil_day(conn)
    r = client.get("/api/v1/proceedings/speeches/43001-1/clip",
                   params={"start": 30, "end": 30})
    assert r.status_code == 422


def test_speech_clip_not_found(client):
    assert client.get("/api/v1/proceedings/speeches/99999-1/clip").status_code == 404


def test_session_browse_groups_by_agenda(client):
    d = client.get("/api/v1/proceedings/sessions/43001").json()
    titles = [a["title"] for a in d["agenda"]]
    assert "Napirend előtt" in titles and "Szavazás" in titles
    # Each agenda item carries its speeches in order (use case 2).
    first = d["agenda"][0]
    assert first["speeches"][0]["uid"] == "43001-1"


def test_session_neighbours_none_for_sole_sitting(client):
    """The only sitting of its cycle has no prev/next day — both null so the
    frontend renders no navigation."""
    n = client.get("/api/v1/proceedings/sessions/43001").json()["neighbours"]
    assert n == {"prev": None, "next": None}


def test_session_neighbours_are_chronological_within_cycle(client, data_dir, db_path):
    """Prev/next point to the chronologically adjacent sitting of the SAME cycle,
    ordered by (date, sitting); null at the cycle's first/last day."""
    import json
    from app import loader
    from tests.conftest import _session_record
    # A later sitting in the same cycle (43001 is 2026-05-09).
    (data_dir / "processed" / "43002-session.json").write_text(
        json.dumps(_session_record(session="43002", sitting=2, date="2026-05-12"),
                   ensure_ascii=False))
    loader.build_database(data_dir, db_path)

    earlier = client.get("/api/v1/proceedings/sessions/43001").json()["neighbours"]
    assert earlier["prev"] is None
    assert earlier["next"]["id"] == "43002" and earlier["next"]["sitting"] == 2

    later = client.get("/api/v1/proceedings/sessions/43002").json()["neighbours"]
    assert later["prev"]["id"] == "43001" and later["prev"]["date"] == "2026-05-09"
    assert later["next"] is None


def test_session_list_paginates(client):
    d = client.get("/api/v1/proceedings/sessions").json()
    assert d["total"] == 1 and d["limit"] == 50 and d["offset"] == 0
    assert len(d["sessions"]) == 1
    # A normal held sitting reports its status.
    assert d["sessions"][0]["status"] == "published"
    # Offset past the end yields an empty page, but total still reflects the count.
    d2 = client.get("/api/v1/proceedings/sessions?offset=50").json()
    assert d2["total"] == 1 and d2["sessions"] == []


def test_scheduled_upcoming_session_listed_and_flagged(client, data_dir, db_path):
    """An announced sitting with no speeches yet (status 'scheduled') is ingested
    and surfaced as an upcoming day, not dropped — so the site shows a sitting is
    coming."""
    import json
    from app import loader
    rec = {"meta": {"session": "43002", "electoralPeriod": 43, "sitting": 2,
                    "date": "2999-01-01", "status": "scheduled",
                    "dateStart": "2999-01-01T00:00:00",
                    "dateEnd": "2999-01-01T23:59:59", "source": "felicitas-json"},
           "data": []}
    (data_dir / "processed" / "43002-session.json").write_text(json.dumps(rec))
    loader.build_database(data_dir, db_path)   # atomic-swaps over the live file

    d = client.get("/api/v1/proceedings/sessions").json()
    assert d["total"] == 2
    # The future upcoming day sorts first and is flagged scheduled with no speeches.
    top = d["sessions"][0]
    assert top["id"] == "43002" and top["status"] == "scheduled" and top["speeches"] == 0
    assert any(s["id"] == "43001" and s["status"] == "published"
               for s in d["sessions"])
    # The detail endpoint carries the status and an empty agenda (renderable).
    det = client.get("/api/v1/proceedings/sessions/43002").json()
    assert det["session"]["status"] == "scheduled"
    assert det["agenda"] == []


def test_session_wordcloud(client):
    """WCLOUD-1/2: the sitting word cloud returns frequency-ranked topical words,
    stop-words and short tokens dropped."""
    d = client.get("/api/v1/proceedings/sessions/43001/wordcloud").json()
    assert d["session_id"] == "43001" and d["date"] == "2026-05-09"
    words = {w["text"]: w["count"] for w in d["words"]}
    # content words survive (folded to lowercase)
    assert "költségvetés" in words and "ágazati" in words and "fejlesztés" in words
    # stop-word ("kérdés") and short token ("ügye", < 4 chars is "az"/"a") dropped
    assert "kérdés" not in words
    assert all(len(w) >= 4 for w in words)


def test_session_wordcloud_missing_session(client):
    assert client.get("/api/v1/proceedings/sessions/99999/wordcloud").status_code == 404


def test_session_new_words(client):
    """NEW-1: the endpoint returns this day's debut words (lemmatized), each with
    its same-day count and kind, ranked by count."""
    d = client.get("/api/v1/proceedings/sessions/43001/new-words").json()
    assert d["session_id"] == "43001" and d["date"] == "2026-05-09"
    words = {w["text"]: w for w in d["words"]}
    # content lemmas from the transcript debut on the corpus's first day
    assert "költségvetés" in words
    assert all(len(w) >= 4 for w in words)                 # stop/short tokens dropped
    assert all(set(w.keys()) == {"text", "count", "kind"} for w in d["words"])
    counts = [w["count"] for w in d["words"]]
    assert counts == sorted(counts, reverse=True)          # ranked by count desc
    # Names/entities, capitalized lemmas and punctuated tokens are excluded.
    assert all(w["kind"] != "entity" for w in d["words"])
    assert all(not w["text"][:1].isupper() for w in d["words"])
    assert all(not (set("/-.'’‘‑–—") & set(w["text"])) for w in d["words"])


def test_session_new_words_excludes_entities_and_capitalized(client, db_path):
    """The endpoint drops named entities, any capitalized lemma (incl. Hungarian
    accented capitals) and punctuated tokens (`/ - . '` + typographic variants),
    keeping only clean lower-case common terms."""
    import sqlite3
    c = sqlite3.connect(db_path)
    for word, kind, count in [("zöldátállásfoo", "term", 9),   # lower-case → kept
                              ("Budapestibar", "term", 8),      # capitalized → dropped
                              ("Árvízbaz", "term", 7),          # accented capital → dropped
                              ("Teszt Entitás", "entity", 6),   # entity → dropped
                              ("rtl-es", "term", 5),            # hyphen → dropped
                              ("dr.foo", "term", 5),            # dot → dropped
                              ("elmesélte‑e", "term", 5),       # NB hyphen → dropped
                              ("van't", "term", 5)]:            # apostrophe → dropped
        c.execute("INSERT INTO session_word_count(session_id, word, count, kind) "
                  "VALUES ('43001', ?, ?, ?)", (word, count, kind))
        c.execute("INSERT INTO word_first_seen(word, session_id, date, kind) "
                  "VALUES (?, '43001', '2026-05-09', ?)", (word, kind))
    c.commit(); c.close()

    words = {w["text"] for w in
             client.get("/api/v1/proceedings/sessions/43001/new-words").json()["words"]}
    assert "zöldátállásfoo" in words
    assert {"Budapestibar", "Árvízbaz", "Teszt Entitás",
            "rtl-es", "dr.foo", "elmesélte‑e", "van't"}.isdisjoint(words)


def test_session_new_words_missing_session(client):
    assert client.get("/api/v1/proceedings/sessions/99999/new-words").status_code == 404


def test_session_top_speakers(client):
    """TOPSPK-1/2: the sitting toplist ranks known representatives by total
    speaking time, excluding procedural speeches and unattributed speakers."""
    d = client.get("/api/v1/proceedings/sessions/43001/top-speakers").json()
    assert d["session_id"] == "43001" and d["date"] == "2026-05-09"
    speakers = d["speakers"]
    # Both speeches are non-procedural and attributed (k001, n002); ranked by time.
    assert [s["person_id"] for s in speakers] == ["k001", "n002"]
    top = speakers[0]
    assert top["label"] == "Kovács Béla" and top["speeches"] == 1
    assert top["seconds"] == 30 and top["faction"]["label"] == "Fidesz"
    # Sorted by speaking time descending.
    assert speakers[0]["seconds"] >= speakers[1]["seconds"]


def test_session_top_speakers_missing_session(client):
    assert client.get("/api/v1/proceedings/sessions/99999/top-speakers").status_code == 404


def test_degraded_speech_renders(client):
    """VIE-8: video-only speech still returns with metadata + no-text flag."""
    d = client.get("/api/v1/proceedings/speeches/43001-2").json()
    assert d["speech"]["has_text"] is False
    assert d["sentences"] == []
    assert d["speech"]["speaker"]["label"] == "Nagy Anna"


def test_resolve_speakers_matches_mp_by_name(client):
    """Heckle attribution: an interjection name resolves to an MP (accent/case-
    insensitive), an unknown name is omitted, and "resolve" isn't caught as an id."""
    d = client.get("/api/v1/representatives/resolve",
                   params=[("name", "kovacs bela"), ("name", "Nagy Anna"),
                           ("name", "Közbeszólás")]).json()
    assert set(d["resolved"]) == {"kovacs bela", "Nagy Anna"}
    assert d["resolved"]["kovacs bela"]["person_id"] == "k001"
    assert d["resolved"]["Nagy Anna"]["label"] == "Nagy Anna"
    assert "photo_uri" in d["resolved"]["Nagy Anna"]
    assert client.get("/api/v1/representatives/resolve").json() == {"resolved": {}}


def test_representative_profile(client):
    d = client.get("/api/v1/representatives/k001").json()
    assert d["label"] == "Kovács Béla"
    assert d["constituency"] == "Budapest 1."
    assert d["current_faction"]["label"] == "Fidesz"
    assert d["current_faction"]["color"] == "#FF6A13"
    assert d["education"]
    # Wikidata/Wikipedia links joined via P4966 (EXT-2) and surfaced on the profile.
    assert d["wikidata_id"] == "Q42"
    assert d["wikipedia_url"] == "https://hu.wikipedia.org/wiki/Kov%C3%A1cs_B%C3%A9la"
    # An MP without a Wikidata item simply has null links (no crash).
    assert client.get("/api/v1/representatives/n002").json()["wikipedia_url"] is None


def _load_minister_speech(db_path):
    """Load one sitting day whose speaker is a non-MP minister, so their profile is
    built from a speech carrying a government office (tisztség)."""
    from app import loader
    c = loader.connect(db_path)
    loader.load_session(c, {
        "meta": {"session": "43015", "electoralPeriod": 43, "sitting": 15,
                 "date": "2026-06-30", "dayVideoURI": "https://example/p.m3u8",
                 "timingMethod": "estimated-day-offset"},
        "data": [{"originID": "43-15-1", "speechIndex": 1,
                  "agendaItem": {"title": "Interpelláció", "type": "regular"},
                  "people": [{"label": "Törőcsikné Görög Márta", "context": "main-speaker",
                              "personID": "0052", "office": "igazságügyi miniszter"}],
                  "media": {"videoFileURI": "https://example/p.m3u8", "duration": 7200},
                  "textContents": [{"textBody": [{"sentences": [
                      {"text": "Tisztelt Ház!", "timeStart": 0.0, "timeEnd": 30.0}]}]}],
                  "debug": {"confidence": 0.7, "align-method": "estimated-day-offset",
                            "felszolalasTipusa": "válasz"}}]})
    return c


def test_profile_shows_office_for_non_mp_speaker(client, db_path):
    """A non-MP speaker (a minister who holds no mandate, so has no faction or
    constituency) is identified on their profile by their government office
    (tisztség), derived from their speeches. An ordinary MP has none."""
    _load_minister_speech(db_path).close()
    d = client.get("/api/v1/representatives/0052").json()
    assert d["is_mp"] is False
    assert d["office"] == "igazságügyi miniszter"
    assert d["current_faction"] is None
    # The office is dated (REP-2): with no upstream term for it, the span of the
    # speeches carrying the title bounds it from below ("held at least until").
    assert d["office_term"]["dates_from"] == "speeches"
    assert d["office_term"]["start"] == "2026-06-30"
    assert d["office_term"]["end"] == "2026-06-30"
    assert d["office_term"]["cycles"] == [43]
    # An ordinary MP (no government office) reports no office.
    mp = client.get("/api/v1/representatives/k001").json()
    assert mp["office"] is None and mp["office_term"] is None


def test_profile_office_dated_from_upstream_term(client, db_path):
    """REP-2: the shown office (tisztség) states which term it refers to. The dates
    come from the person's upstream office list when it names the same post — the
    appointment boundaries, not just the speeches — where back-to-back spells of one
    post (upstream splits them at each cycle boundary and re-appointment) read as a
    single term, while an earlier, genuinely separate spell stays separate."""
    import json
    c = _load_minister_speech(db_path)
    c.execute("UPDATE person SET offices_json = ? WHERE person_id = '0052'",
              (json.dumps([
                  {"title": "igazságügyi miniszter",
                   "start": "2026-05-20T22:00:00Z", "end": "2030-05-12T21:59:59Z"},
                  # the same post continued across the cycle boundary — one term
                  {"title": "igazságügyi miniszter",
                   "start": "2022-05-24T22:00:00Z", "end": "2026-05-16T21:59:59Z"},
                  # out of office for a whole cycle in between: a separate term, and
                  # not the one the speech falls in
                  {"title": "igazságügyi miniszter",
                   "start": "2010-06-01T22:00:00Z", "end": "2014-06-05T21:59:59Z"},
              ], ensure_ascii=False),))
    c.commit()
    c.close()
    d = client.get("/api/v1/representatives/0052").json()
    assert d["office"] == "igazságügyi miniszter"
    assert d["office_term"]["dates_from"] == "term"
    assert d["office_term"]["start"] == "2022-05-24T22:00:00Z"
    assert d["office_term"]["end"] == "2030-05-12T21:59:59Z"


def test_representative_statistics_shows_bills_when_module_enabled(client):
    """REP-3: with the Bills module live, the bills-submitted metric is shown."""
    d = client.get("/api/v1/representatives/k001/statistics").json()
    assert d["totals"]["speech_count"] == 1
    assert d["totals"]["speaking_seconds"] == 30.0
    assert d["totals"]["bills_available"] is True
    assert d["totals"]["bills_submitted"] == 3   # from upstream per-cycle counts
    assert d["methodology"]                       # REP-5 methodology note present
    assert d["over_time"]                          # trend data present


def test_representative_statistics_hides_bills_when_module_disabled(client, monkeypatch):
    """REP-3/EXT-6: with the Bills module disabled, the metric is hidden, not faked."""
    from app.config import settings
    monkeypatch.setattr(settings, "enabled_modules", ["proceedings", "representatives"])
    d = client.get("/api/v1/representatives/k001/statistics").json()
    assert d["totals"]["bills_available"] is False
    assert d["totals"]["bills_submitted"] is None


def test_representative_activity_board(client):
    """REP-8: per-day activity combines statistics-eligible speeches and submitted
    irományok. k001 has one speech on the sitting day (2026-05-09) and sponsors two
    irományok (T/100 @2026-05-10, I/5 @2026-06-01) — three active days in all."""
    d = client.get("/api/v1/representatives/k001/activity").json()
    assert d["documents_available"] is True
    assert d["totals"] == {"speeches": 1, "documents": 2, "active_days": 3}
    by_day = {x["date"]: x for x in d["days"]}
    assert by_day["2026-05-09"]["speeches"] == 1
    assert by_day["2026-05-09"]["documents"] == 0
    assert by_day["2026-05-09"]["total"] == 1
    assert by_day["2026-05-10"]["documents"] == 1   # T/100
    assert by_day["2026-06-01"]["documents"] == 1   # I/5
    # days are returned in chronological order
    assert [x["date"] for x in d["days"]] == sorted(x["date"] for x in d["days"])


def test_representative_activity_hides_documents_when_bills_disabled(client, monkeypatch):
    """REP-8/EXT-6: with the Bills module off, the documents contribution is absent
    (not faked) and the endpoint still works from speeches alone."""
    from app.config import settings
    monkeypatch.setattr(settings, "enabled_modules", ["proceedings", "representatives"])
    d = client.get("/api/v1/representatives/k001/activity").json()
    assert d["documents_available"] is False
    assert d["totals"]["documents"] == 0
    assert d["totals"]["speeches"] == 1


def test_representative_activity_scoped_to_selected_cycle(client):
    """§4A: cycle 42 (no data for k001) yields an empty board."""
    d = client.get("/api/v1/representatives/k001/activity", params={"period": 42}).json()
    assert d["days"] == []
    assert d["totals"] == {"speeches": 0, "documents": 0, "active_days": 0}


def test_representative_speeches_list(client):
    d = client.get("/api/v1/representatives/k001/speeches").json()
    assert d["total"] == 1
    assert d["speeches"][0]["uid"] == "43001-1"
    assert d["speeches"][0]["excerpt"]


def test_representative_statistics_scoped_to_selected_cycle(client):
    """§4A: stats are scoped to `period`. The test DB only has cycle 43, so the
    cycle-43 totals match the all-cycles totals, while cycle 42 (no data) is
    zeroed — proving previous-cycle data never leaks into another cycle."""
    cyc43 = client.get("/api/v1/representatives/k001/statistics",
                       params={"period": 43}).json()
    assert cyc43["scope"]["period"] == 43
    assert "43" in cyc43["scope"]["description"]
    assert cyc43["totals"]["speech_count"] == 1
    assert cyc43["totals"]["bills_submitted"] == 3      # own bills in cycle 43

    cyc42 = client.get("/api/v1/representatives/k001/statistics",
                       params={"period": 42}).json()
    assert cyc42["totals"]["speech_count"] == 0
    assert cyc42["totals"]["speaking_seconds"] == 0
    assert cyc42["over_time"] == []
    assert cyc42["scope"]["sessions_covered"] == 0
    assert cyc42["totals"]["bills_submitted"] is None   # no cycle-42 bills


def test_representative_speeches_scoped_to_selected_cycle(client):
    """§4A: an MP's speech list only covers the selected cycle's sittings."""
    assert client.get("/api/v1/representatives/k001/speeches",
                      params={"period": 43}).json()["total"] == 1
    assert client.get("/api/v1/representatives/k001/speeches",
                      params={"period": 42}).json()["total"] == 0


def test_representative_speech_days(client):
    """The grouped-by-day view: one row per sitting, with a per-day count whose
    sum equals the flat speech total."""
    d = client.get("/api/v1/representatives/k001/speeches").json()
    days = client.get("/api/v1/representatives/k001/speech-days").json()
    assert days["total"] == d["total"]
    assert sum(x["count"] for x in days["days"]) == days["total"]
    assert all({"session_id", "date", "sitting", "count"} <= x.keys()
               for x in days["days"])
    # cycle scoping (§4A): cycle 42 has no sittings for this MP
    assert client.get("/api/v1/representatives/k001/speech-days",
                      params={"period": 42}).json()["days"] == []


def test_representative_speeches_filtered_by_session(client):
    """The lazy-loaded day spoiler fetches only that day's speeches."""
    days = client.get("/api/v1/representatives/k001/speech-days").json()["days"]
    sid = days[0]["session_id"]
    d = client.get("/api/v1/representatives/k001/speeches",
                   params={"session_id": sid}).json()
    assert d["total"] == days[0]["count"]
    assert all(s["session_id"] == sid for s in d["speeches"])
    # a session the MP didn't speak in yields nothing
    assert client.get("/api/v1/representatives/k001/speeches",
                      params={"session_id": "99999"}).json()["total"] == 0


def test_representatives_list_scoped_to_selected_cycle(client):
    """§4A: the list shows only MPs serving in the cycle, with that cycle's stats."""
    cyc43 = client.get("/api/v1/representatives", params={"period": 43}).json()
    assert cyc43["total"] == 2
    kovacs = next(r for r in cyc43["representatives"] if r["person_id"] == "k001")
    assert kovacs["speech_count"] == 1
    # No MPs served in the (empty) cycle 42, so the list is empty there.
    assert client.get("/api/v1/representatives", params={"period": 42}).json()["total"] == 0


def test_representatives_list_filters_and_sorts(client):
    everyone = client.get("/api/v1/representatives").json()
    assert everyone["total"] == 2
    by_time = client.get("/api/v1/representatives",
                         params={"sort": "speaking_time"}).json()
    # Kovács (30s) ranks above Nagy (0s, no transcript).
    assert by_time["representatives"][0]["person_id"] == "k001"


def test_representatives_name_search_is_accent_insensitive(client):
    """§4B FOLD-1: an unaccented query matches accented names (`kovacs` → Kovács)."""
    # Accent-stripped query matches.
    r = client.get("/api/v1/representatives", params={"q": "kovacs"}).json()
    assert [x["person_id"] for x in r["representatives"]] == ["k001"]
    # Case-insensitive too, and the accented form still matches itself.
    assert client.get("/api/v1/representatives",
                      params={"q": "KOVÁCS"}).json()["total"] == 1
    # Display text keeps its original accents (FOLD-5).
    assert r["representatives"][0]["label"] == "Kovács Béla"


def test_factions_endpoint_has_averages(client):
    d = client.get("/api/v1/representatives/factions").json()
    fidesz = next(f for f in d["factions"] if f["label"] == "Fidesz")
    assert fidesz["mp_count"] == 1
    assert fidesz["avg_speaking_seconds"] == 30.0
    assert fidesz["color"] == "#FF6A13"


def test_disabled_module_not_mounted(monkeypatch, db_path):
    """EXT-6: a module absent from config is never mounted (routes 404)."""
    import importlib
    from app import config as config_module
    monkeypatch.setenv("PARLAMONITOR_MODULES", "proceedings")
    monkeypatch.setenv("PARLAMONITOR_DB", str(db_path))
    importlib.reload(config_module)
    import app.main as main_module
    importlib.reload(main_module)
    from fastapi.testclient import TestClient
    c = TestClient(main_module.app)
    assert c.get("/api/v1/proceedings/sessions").status_code == 200
    assert c.get("/api/v1/representatives").status_code == 404
    # restore for other tests
    monkeypatch.delenv("PARLAMONITOR_MODULES")
    importlib.reload(config_module)
    importlib.reload(main_module)


def test_cache_control_headers(client):
    """High-traffic hardening: path-based Cache-Control lets the CDN/browser
    absorb repeat traffic (backend/app/caching.py)."""
    api = client.get("/api/v1/meta")
    assert api.status_code == 200
    assert "s-maxage" in api.headers["cache-control"]
    # Health reflects this origin right now — never cache it.
    assert client.get("/api/v1/health").headers["cache-control"] == "no-store"
    # Errors are not stamped (a cached 404 would mask later-added data).
    missing = client.get("/api/v1/proceedings/speeches/nope")
    assert missing.status_code == 404
    assert "cache-control" not in missing.headers


def test_spa_shell_fallback_only_for_client_routes():
    """A missing *asset*/file must 404, never the HTML shell — else the edge
    caches HTML under an immutable .css/.js URL and poisons the site (Firefox
    strict-MIME refuses it). Client routes still resolve to the shell."""
    from app.main import _should_serve_shell
    # Client-side routes (deep links / refresh) → shell.
    assert _should_serve_shell("proceedings/43001-1")
    assert _should_serve_shell("search")
    assert _should_serve_shell("representatives/factions")
    # Files / namespaced paths → real 404, no shell.
    assert not _should_serve_shell("assets/index-DJ02fr_5.css")
    assert not _should_serve_shell("assets/index-639Umro7.js")
    assert not _should_serve_shell("favicon.ico")
    assert not _should_serve_shell("api/v1/nope")
    assert not _should_serve_shell("media/photos/1.jpg")


def test_html_never_cached_immutable():
    """Defense-in-depth: the cache middleware must never stamp an HTML body
    `immutable`, even under /assets/ — a year-long HTML-as-CSS entry is exactly
    the poisoning this guards against (backend/app/caching.py)."""
    from app import caching
    # An /assets/ path normally earns the immutable, year-long policy...
    assert caching._policy_for("/assets/x.css") == caching.ASSET_CACHE_CONTROL

    async def run(content_type):
        captured = {}

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", content_type)]})

        async def send(message):
            if message["type"] == "http.response.start":
                captured["headers"] = dict(message["headers"])

        mw = caching.CacheControlMiddleware(app)
        await mw({"type": "http", "method": "GET", "path": "/assets/x.css"},
                 None, send)
        return captured["headers"][b"cache-control"]

    import asyncio
    # A real hashed asset keeps the immutable, year-long TTL.
    assert asyncio.run(run(b"text/css")) == caching.ASSET_CACHE_CONTROL.encode()
    # The SPA shell leaking through under /assets/ is cached as HTML instead.
    assert asyncio.run(run(b"text/html; charset=utf-8")) == \
        caching.HTML_CACHE_CONTROL.encode()


def test_gzip_compression(client):
    """Large JSON responses are gzip-compressed toward clients/CDN."""
    r = client.get("/api/v1/proceedings/sessions",
                   headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    # httpx transparently decompresses; the header records the encoding.
    small_or_encoded = (r.headers.get("content-encoding") == "gzip"
                        or int(r.headers.get("content-length", "0")) < 1024)
    assert small_or_encoded
