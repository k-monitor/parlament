"""Loader correctness: JSON -> normalized DB, idempotency, aggregates (OPS-3)."""

from __future__ import annotations

import json

from app import loader


def test_core_rows_loaded(conn):
    assert conn.execute("SELECT COUNT(*) FROM session").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM speech").fetchone()[0] == 2
    # Only the speech with a transcript contributes sentences.
    assert conn.execute("SELECT COUNT(*) FROM sentence").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM person WHERE is_mp=1").fetchone()[0] == 2


def test_speech_uid_is_session_plus_index(conn):
    uids = {r[0] for r in conn.execute("SELECT uid FROM speech")}
    assert uids == {"43001-1", "43001-2"}


def test_degraded_speech_ingested_not_dropped(conn):
    """SCR-5: a no-transcript speech is kept, flagged with low confidence."""
    row = conn.execute(
        "SELECT has_text, confidence, align_method FROM speech WHERE uid='43001-2'"
    ).fetchone()
    assert row["has_text"] == 0
    assert row["confidence"] == 0.5
    # Falls back to per-speech video offsets for its span.
    span = conn.execute("SELECT time_start, time_end FROM speech WHERE uid='43001-2'").fetchone()
    assert span["time_start"] == 50.0 and span["time_end"] == 60.0


def test_timing_provenance_marked_estimated(conn):
    """TIM-3: every text speech is stamped estimated-day-offset."""
    row = conn.execute("SELECT align_method, confidence FROM speech WHERE uid='43001-1'").fetchone()
    assert row["align_method"] == "estimated-day-offset"
    assert row["confidence"] == 0.7


def test_speech_span_derived_from_sentences(conn):
    row = conn.execute("SELECT time_start, time_end, duration FROM speech WHERE uid='43001-1'").fetchone()
    assert row["time_start"] == 10.0
    assert row["time_end"] == 40.0
    assert row["duration"] == 30.0


def test_aggregates_speaking_time(conn):
    # k001 spoke 30s in one speech.
    row = conn.execute(
        "SELECT speech_count, speaking_seconds FROM person_stats "
        "WHERE person_id='k001' AND period_number IS NULL").fetchone()
    assert row["speech_count"] == 1
    assert row["speaking_seconds"] == 30.0
    # Faction all-periods aggregate row exists (factions endpoint depends on it).
    fid = conn.execute("SELECT id FROM faction WHERE label='Fidesz'").fetchone()[0]
    frow = conn.execute(
        "SELECT mp_count, speaking_seconds FROM faction_stats "
        "WHERE faction_id=? AND period_number IS NULL", (fid,)).fetchone()
    assert frow["mp_count"] == 1 and frow["speaking_seconds"] == 30.0


def test_reingest_is_idempotent(conn, db_path, tmp_path):
    """ING-4: re-loading a session replaces, never duplicates."""
    from tests.conftest import _session_record
    c = loader.connect(db_path)
    loader.load_session(c, _session_record())
    loader.load_session(c, _session_record())   # twice
    loader.rebuild_aggregates(c)
    assert c.execute("SELECT COUNT(*) FROM speech WHERE session_id='43001'").fetchone()[0] == 2
    assert c.execute("SELECT COUNT(*) FROM sentence").fetchone()[0] == 2
    c.close()


def test_procedural_speech_excluded_from_stats_but_stored(db_path):
    """STAT-1: an ülésvezetés speech is kept and flagged `procedural`, but does
    not count toward the speaker's statistics — only toward the transcript."""
    c = loader.connect(db_path)
    rec = {
        "meta": {"session": "43009", "electoralPeriod": 43, "sitting": 9,
                 "date": "2026-05-20", "dayVideoURI": "https://example/p.m3u8",
                 "timingMethod": "estimated-day-offset"},
        "data": [
            {"originID": "43-9-1", "speechIndex": 1,
             "agendaItem": {"title": "Vita", "type": "regular"},
             "people": [{"label": "Kovács Béla", "context": "main-speaker",
                         "personID": "k001", "faction": {"label": "Fidesz", "id": 7}}],
             "media": {"videoFileURI": "https://example/p.m3u8", "duration": 7200},
             "textContents": [{"textBody": [{"sentences": [
                 {"text": "Egy érdemi mondat.", "timeStart": 0.0, "timeEnd": 30.0}]}]}],
             "debug": {"confidence": 0.7, "align-method": "estimated-day-offset",
                       "felszolalasTipusa": "napirend előtti felszólalás"}},
            {"originID": "43-9-2", "speechIndex": 2,
             "agendaItem": {"title": "Vita", "type": "regular"},
             "people": [{"label": "Kovács Béla", "context": "chair",
                         "personID": "k001", "faction": {"label": "Fidesz", "id": 7}}],
             "media": {"videoFileURI": "https://example/p.m3u8", "duration": 7200,
                       "videoStart": 100.0, "videoEnd": 130.0},
             "textContents": [{"textBody": [{"sentences": [
                 {"text": "Megadom a szót.", "timeStart": 100.0, "timeEnd": 130.0}]}]}],
             "debug": {"confidence": 0.7, "align-method": "estimated-day-offset",
                       "felszolalasTipusa": "ülésvezetés"}},
        ],
    }
    loader.load_session(c, rec)
    loader.rebuild_aggregates(c)
    # Both speeches are stored; only the ülésvezetés one is flagged procedural,
    # and the raw type is retained for the viewer label.
    flags = dict(c.execute(
        "SELECT speech_index, procedural FROM speech WHERE session_id='43009'"))
    assert flags == {1: 0, 2: 1}
    assert c.execute("SELECT felszolalas_tipus FROM speech WHERE uid='43009-2'"
                     ).fetchone()[0] == "ülésvezetés"
    # The sitting's per-person stat counts only the one non-procedural speech.
    pss = c.execute(
        "SELECT speech_count, speaking_seconds FROM person_session_stats "
        "WHERE person_id='k001' AND session_id='43009'").fetchone()
    assert pss["speech_count"] == 1 and pss["speaking_seconds"] == 30.0
    c.close()


def test_non_mp_speaker_office_stored(db_path):
    """A speaker who is not in the MP roster (a minister) is created as a non-MP
    stub, and the government office (tisztség) reported on their speech is stored
    on the speech so their profile can identify them by their post."""
    c = loader.connect(db_path)
    rec = {
        "meta": {"session": "43015", "electoralPeriod": 43, "sitting": 15,
                 "date": "2026-06-30", "dayVideoURI": "https://example/p.m3u8",
                 "timingMethod": "estimated-day-offset"},
        "data": [
            {"originID": "43-15-1", "speechIndex": 1,
             "agendaItem": {"title": "Interpelláció", "type": "regular"},
             "people": [{"label": "Törőcsikné Görög Márta", "context": "main-speaker",
                         "personID": "0052", "office": "igazságügyi miniszter"}],
             "media": {"videoFileURI": "https://example/p.m3u8", "duration": 7200},
             "textContents": [{"textBody": [{"sentences": [
                 {"text": "Tisztelt Ház!", "timeStart": 0.0, "timeEnd": 30.0}]}]}],
             "debug": {"confidence": 0.7, "align-method": "estimated-day-offset",
                       "felszolalasTipusa": "válasz"}},
        ],
    }
    loader.load_session(c, rec)
    # The office is stored on the speech...
    assert c.execute("SELECT speaker_office FROM speech WHERE uid='43015-1'"
                     ).fetchone()[0] == "igazságügyi miniszter"
    # ...and the speaker is a non-MP stub (never appeared in a roster).
    assert c.execute("SELECT is_mp FROM person WHERE person_id='0052'").fetchone()[0] == 0
    c.close()


def test_office_holder_registry_loaded_with_dates_and_categories(conn):
    """REP-2: the office-holder registry lands as dated ``person_office`` terms,
    each carrying the portal's own office category."""
    rows = conn.execute(
        "SELECT title, category, date_start, date_end, source FROM person_office "
        "WHERE person_id='k001' AND source='registry' ORDER BY date_start DESC"
    ).fetchall()
    assert [(r["title"], r["category"], r["date_end"]) for r in rows] == [
        ("az Országgyűlés jegyzője", "parliamentary", None),   # still held: open end
        ("Belügyminisztérium államtitkára", "state-secretary", "2022-05-24T12:00:00Z")]
    assert rows[0]["date_start"] == "2026-05-09T22:00:00Z"
    # The MP's own roster copy of a term has no category — the roster doesn't
    # report one — which is why the listing reads the registry rows (REP-11).
    assert conn.execute("SELECT COUNT(*) FROM person_office WHERE source='roster' "
                        "AND category IS NOT NULL").fetchone()[0] == 0


def test_office_holder_outside_the_corpus_is_loaded_as_a_stub(conn):
    """REP-11: over half the registry never spoke in the House, so an office holder
    the corpus doesn't know is inserted as a person with a name and an office
    history — and no mandate. Leaving them out would halve the all-time listing;
    marking them as anything else would invent a mandate the source doesn't give."""
    p = conn.execute("SELECT * FROM person WHERE person_id='zzz9'").fetchone()
    assert p is not None
    assert (p["label"], p["label_full"]) == ("Sosem Beszélt", "Dr. Sosem Beszélt")
    assert (p["lastname"], p["firstname"]) == ("Sosem", "Beszélt")
    assert (p["is_mp"] or 0, p["is_advocate"] or 0) == (0, 0)
    assert conn.execute("SELECT title FROM person_office WHERE person_id='zzz9'"
                        ).fetchone()["title"] == "köztársasági elnök"
    # They never spoke, so nothing may count them as a speaker.
    assert conn.execute("SELECT COUNT(*) FROM person_stats WHERE person_id='zzz9'"
                        ).fetchone()[0] == 0


def test_office_terms_are_per_source_so_a_reload_keeps_the_other(db_path):
    """The two sources of office terms (the all-time registry and the per-MP roster)
    are kept apart: reloading either replaces only its own rows, so neither ever
    drops what the other supplied."""
    c = loader.connect(db_path)
    loader._load_person_offices(c, "k001", [
        {"title": "Belügyminisztérium államtitkára",
         "start": "2018-05-21T22:00:00Z", "end": "2022-05-24T12:00:00Z"}], "roster")
    c.commit()
    by_source = dict(c.execute(
        "SELECT source, COUNT(*) FROM person_office WHERE person_id='k001' "
        "GROUP BY source").fetchall())
    assert by_source == {"registry": 2, "roster": 1}

    # Re-running the registry load leaves the roster row alone (and is idempotent).
    loader.load_office_holders(c, {"data": [
        {"personID": "k001", "offices": [
            {"title": "az Országgyűlés jegyzője",
             "start": "2026-05-09T22:00:00Z", "end": None}]}]})
    by_source = dict(c.execute(
        "SELECT source, COUNT(*) FROM person_office WHERE person_id='k001' "
        "GROUP BY source").fetchall())
    assert by_source == {"registry": 1, "roster": 1}
    c.close()


def test_wire_nonmp_photos(db_path, tmp_path):
    """A non-MP speaker's downloaded portrait (`<pid>.jpg` on disk) is wired onto
    their profile; an MP's roster photo and a non-MP with no file are untouched."""
    photos = tmp_path / "photos"
    photos.mkdir()
    (photos / "004L.jpg").write_bytes(b"jpegbytes")   # nationality advocate, has a file
    (photos / "0052.jpg").exists()                     # (minister, no file — 404 upstream)
    c = loader.connect(db_path)
    c.execute("INSERT INTO person(person_id, label, is_mp) VALUES ('004L','Gallai Gergely',0)")
    c.execute("INSERT INTO person(person_id, label, is_mp) VALUES ('0052','Egy Miniszter',0)")
    c.execute("INSERT INTO person(person_id, label, is_mp, photo_uri) "
              "VALUES ('k009','Egy Képviselő',1,'/media/photos/k009.jpg')")
    (photos / "k009.jpg").write_bytes(b"x")            # an MP file must NOT be re-wired here
    wired = loader.wire_nonmp_photos(c, photos)
    assert wired == 1
    assert c.execute("SELECT photo_uri FROM person WHERE person_id='004L'").fetchone()[0] \
        == "/media/photos/004L.jpg"
    assert c.execute("SELECT photo_uri FROM person WHERE person_id='0052'").fetchone()[0] is None
    # The MP keeps their roster photo (function only touches non-MP rows).
    assert c.execute("SELECT photo_uri FROM person WHERE person_id='k009'").fetchone()[0] \
        == "/media/photos/k009.jpg"
    c.close()


def test_faction_colors_assigned(conn):
    rows = dict(conn.execute("SELECT label, color FROM faction"))
    assert rows["Fidesz"] == "#FF6A13"
    assert rows["TISZA"] == "#00A6A6"


def test_birth_date_and_signs_stored(conn):
    """The Wikidata birth date and both derived signs land on the person row.
    Stored only — no endpoint surfaces them yet."""
    row = conn.execute("SELECT date_of_birth, zodiac_sign, chinese_zodiac_sign "
                       "FROM person WHERE person_id='k001'").fetchone()
    assert row["date_of_birth"] == "1968-08-30"
    assert row["zodiac_sign"] == "virgo" and row["chinese_zodiac_sign"] == "monkey"
    # Nobody without a date gets a sign.
    undated = conn.execute("SELECT date_of_birth, zodiac_sign, chinese_zodiac_sign "
                           "FROM person WHERE person_id='n002'").fetchone()
    assert undated["date_of_birth"] is None and undated["zodiac_sign"] is None
    assert undated["chinese_zodiac_sign"] is None


def test_birth_columns_added_to_a_pre_existing_db(db_path):
    """The incremental `--update` path snapshots the live DB instead of re-running
    schema.sql, so a deployment built before the columns existed must gain them
    (and get filled) from a re-loaded registry alone — no rebuild."""
    c = loader.connect(db_path)
    for col in ("date_of_birth", "zodiac_sign", "chinese_zodiac_sign"):
        c.execute(f"ALTER TABLE person DROP COLUMN {col}")
    c.commit()
    loader.load_representatives(c, {
        "meta": {"cycle": 43, "cycleStart": "2026-05-09"},
        "data": [{"personID": "k001", "label": "Kovács Béla",
                  "dateOfBirth": "1968-08-30", "zodiacSign": "virgo",
                  "chineseZodiacSign": "monkey"}]})
    row = c.execute("SELECT zodiac_sign, chinese_zodiac_sign FROM person "
                    "WHERE person_id='k001'").fetchone()
    assert row["zodiac_sign"] == "virgo" and row["chinese_zodiac_sign"] == "monkey"
    c.close()


def test_membership_kept_per_cycle_for_returning_mp(db_path):
    """Loading a later cycle's registry must NOT wipe an MP's earlier-cycle
    membership: a returning MP needs a row per cycle so the per-cycle list and
    faction scope (§4A) include them in every cycle they served."""
    c = loader.connect(db_path)
    reg42 = {"meta": {"cycle": 42, "cycleStart": "2022-05-02"},
             "data": [{"personID": "k001", "label": "Kovács Béla",
                       "faction": {"label": "Fidesz", "id": 7, "position": "tag"}}]}
    reg43 = {"meta": {"cycle": 43, "cycleStart": "2026-05-09"},
             "data": [{"personID": "k001", "label": "Kovács Béla",
                       "faction": {"label": "Fidesz", "id": 7, "position": "tag"}}]}
    loader.load_representatives(c, reg42)
    loader.load_representatives(c, reg43)   # must not delete the cycle-42 row
    periods = {r[0] for r in c.execute(
        "SELECT period_number FROM membership WHERE person_id='k001'")}
    assert periods == {42, 43}
    # Re-loading a cycle replaces only that cycle's row (idempotent, no dupes).
    loader.load_representatives(c, reg43)
    assert c.execute("SELECT COUNT(*) FROM membership WHERE person_id='k001'"
                     ).fetchone()[0] == 2
    c.close()


def test_writer_lock_blocks_a_second_process(tmp_path):
    """DB-4: two loader processes must not stage at the same ``<db>.building``.

    ./deploy.sh runs `init` (a full build) while the `sync` sidecar may be mid
    `--update`; both clear that temp path when they start, so without a lock the
    slower one's final os.replace() fails with FileNotFoundError after doing all
    the work. The lock makes the second wait instead.
    """
    import subprocess
    import sys
    import textwrap

    db = tmp_path / "parlamonitor.db"
    with loader._writer_lock(db):
        assert loader._writer_lock_path(db).exists()
        # A separate process must NOT be able to take it while we hold it.
        probe = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(f"""
                import fcntl, sys
                fh = open({str(loader._writer_lock_path(db))!r}, "a+")
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    sys.exit(7)      # held elsewhere — expected
                sys.exit(0)
            """)])
        assert probe.returncode == 7

    # Released on exit: the same probe now succeeds.
    probe = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(f"""
            import fcntl, sys
            fh = open({str(loader._writer_lock_path(db))!r}, "a+")
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        """)])
    assert probe.returncode == 0


def test_writer_lock_is_reentrant(tmp_path):
    """update_database falls back to build_database in-process; flock is per open
    file description, so a naive re-acquire would deadlock against itself."""
    db = tmp_path / "parlamonitor.db"
    with loader._writer_lock(db):
        with loader._writer_lock(db):       # must not hang
            pass
    assert not loader._writer_lock_depth


def test_procedural_type_list_covers_the_chairing_families():
    """STAT-1: the excluded set is enumerated explicitly, so it needs a guard that
    all three families stay covered and that no substantive type creeps in."""
    from app.config import DEFAULT_PROCEDURAL_SPEECH_TYPES as TYPES
    from app.config import settings

    folded = [t.strip().casefold() for t in TYPES]
    assert len(folded) == len(set(folded)), "duplicate entry in the procedural list"

    # One representative per family: running the sitting, debate markers, and
    # vote-outcome announcements (the last is the one that carried whole voting
    # blocks' worth of phantom speaking time into the chair's totals).
    for t in ("ülésvezetés", "Az ülésnap megnyitása", "általános vita lezárva",
              "Országgyűlés határozatképes", "önálló indítvány elfogadva",
              "mentelmi jog felfüggesztve"):
        assert settings.is_procedural_type(t), t
    # Case/whitespace-insensitive, and the double space in this one is verbatim
    # upstream — it must be matched as-is, not normalised away.
    assert settings.is_procedural_type("  ÜLÉSVEZETÉS  ")
    assert settings.is_procedural_type(
        "Országgyűlés a képviselő tiszteletdíjának csökkentését  fenntartotta")

    # Real contributions — including MP-initiated points of order — must survive.
    for t in ("felszólalás", "vezérszónoki felszólalás", "előterjesztő nyitóbeszéde",
              "napirend előtti felszólalás", "kétperces felszólalás", "Expozé",
              "ügyrendi kérdés", "ügyrendi javaslat", "jegyzői ismertetés", None):
        assert not settings.is_procedural_type(t), t


def test_untyped_chair_turns_fall_back_to_the_transcript():
    """STAT-1's fallback for the speeches upstream gives no type at all.

    Cycle 34 ships `felszolalasTipusa` unset on 99.8% of its speeches, so the
    type list alone leaves its chair turns counting as substantive — which puts
    that period's Speaker and deputies atop every speech ranking. The transcript
    still tags them, so an untyped speech opening "ELNÖK:" is treated as chairing.
    """
    from app.config import settings

    # Untyped + the transcript's chair tag → excluded. Folded and left-stripped,
    # since the tag is non-ASCII and the text may carry leading whitespace.
    for text in ("ELNÖK: Köszönöm szépen.",
                 "Elnök ELNÖK: Megköszönöm Horn Gyula képviselőtársunknak...",
                 "  ELNÖK: Tisztelt Országgyűlés!",
                 "elnök: köszönöm"):
        assert settings.is_procedural(None, text), text

    # Anchored at the start, so merely addressing or naming the chair is still a
    # real contribution — as is a speech with no text to judge.
    for text in ("DR. TORGYÁN JÓZSEF (FKGP): Tisztelt Elnök Úr!",
                 "Tisztelt Elnök Úr! Köszönöm a szót.",
                 "SZABAD GYÖRGY elnök: Tapsuk egyetértést jelez...",
                 "", None):
        assert not settings.is_procedural(None, text), text

    # An upstream type is authoritative in BOTH directions — the fallback may
    # only ever speak where upstream said nothing.
    assert not settings.is_procedural("felszólalás", "ELNÖK: Köszönöm.")
    assert settings.is_procedural("ülésvezetés", "Tisztelt Ház!")
    # And with no sentence supplied it collapses to the type-only rule.
    assert settings.is_procedural("ülésvezetés") and not settings.is_procedural(None)


def test_swap_clears_the_destinations_orphaned_wal(tmp_path, data_dir, db_path):
    """A stale `<db>-wal` next to the LIVE file must not survive an atomic swap.

    `os.replace` moves the main file only, so a WAL orphaned by an earlier crashed
    or killed writer stays behind — and SQLite then tries to recover it over a
    database whose pages it knows nothing about. The failure mode is nasty because
    it looks like corruption while the file underneath is perfectly intact: opened
    `immutable=1` (which ignores side-files) the DB passes `integrity_check`, but
    every ordinary open dies with "malformed database schema … invalid rootpage".
    Hit for real on a dev DB whose WAL had been orphaned hours earlier.

    Pinned as an invariant — *no side-files next to the destination after a swap* —
    rather than by reproducing the corruption, since whether SQLite rejects a
    foreign WAL outright or tries to replay it depends on its salt matching.
    """
    import sqlite3

    def _touch_a_source():
        """Make `--update` actually reload something, so it reaches its swap — with
        nothing stale it returns early and (rightly) leaves the file alone."""
        p = data_dir / "processed" / "43001-session.json"
        p.write_text(p.read_text())

    for entry_point in (
        lambda: loader.build_database(data_dir, db_path),
        lambda: (_touch_a_source(), loader.update_database(data_dir, db_path)),
        lambda: loader.remeasure_speeches(db_path),
    ):
        # An orphan of each kind, as a killed writer would leave them.
        for suffix in ("-wal", "-shm"):
            db_path.with_suffix(db_path.suffix + suffix).write_bytes(b"\x00" * 64)
        entry_point()
        for suffix in ("-wal", "-shm"):
            side = db_path.with_suffix(db_path.suffix + suffix)
            assert not side.exists(), f"{side.name} survived the swap"
        # And the swapped-in DB opens normally, not just under immutable=1.
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM speech").fetchone()[0] == 2
        conn.close()
