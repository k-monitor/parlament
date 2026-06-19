-- Parlamonitor — normalized SQLite schema (DB-1).
--
-- The runtime DB is a regenerable cache built from the scraper's session and
-- representative records (DB-3). The loader is the only writer (ING-2); the
-- API opens this file read-only.
--
-- Identifiers mirror the pipeline's stable keys: sessions are "<cycle><sitting>"
-- (e.g. 43001), speeches are "<term>-<sitting>-<speech>" (originID), people are
-- the Felicitas kepviseloId (e.g. "r023").

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- Core entities (shared across all modules — EXT-2)
-- ---------------------------------------------------------------------------

CREATE TABLE electoral_period (
    number      INTEGER PRIMARY KEY,
    label       TEXT,
    date_start  TEXT,
    date_end    TEXT
);

CREATE TABLE faction (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ext_id      INTEGER,                 -- Felicitas frakcioId when known
    label       TEXT NOT NULL UNIQUE,    -- e.g. "Fidesz", "KDNP"
    color       TEXT                     -- hex, for charts (REP-4)
);

CREATE TABLE person (
    person_id         TEXT PRIMARY KEY,  -- Felicitas kepviseloId / speaker id
    label             TEXT NOT NULL,
    label_full        TEXT,
    firstname         TEXT,
    lastname          TEXT,
    wikidata_id       TEXT,
    photo_uri         TEXT,
    photo_file        TEXT,
    constituency      TEXT,
    seat              TEXT,
    email             TEXT,
    website           TEXT,
    highest_education TEXT,
    active            INTEGER,           -- 0/1/NULL
    is_mp             INTEGER DEFAULT 0, -- 1 when present in an MP roster
    -- Richer enrichment kept as JSON for the profile page (arrays of objects):
    education_json        TEXT,
    committees_json       TEXT,
    offices_json          TEXT,
    faction_history_json  TEXT,
    election_history_json TEXT,
    external_stats_json   TEXT           -- upstream per-cycle counts (bills, etc.)
);

-- person <-> faction <-> period: factions change over time (membership table).
CREATE TABLE membership (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id     TEXT NOT NULL REFERENCES person(person_id),
    faction_id    INTEGER REFERENCES faction(id),
    period_number INTEGER,
    position      TEXT,
    date_start    TEXT,
    date_end      TEXT
);
CREATE INDEX idx_membership_person ON membership(person_id);

-- ---------------------------------------------------------------------------
-- Proceedings module
-- ---------------------------------------------------------------------------

CREATE TABLE session (
    id            TEXT PRIMARY KEY,      -- "43001"
    period_number INTEGER REFERENCES electoral_period(number),
    sitting       INTEGER,               -- cycle-wide sitting number
    date          TEXT,
    date_start    TEXT,
    date_end      TEXT,
    source        TEXT,
    source_page   TEXT,
    scraped_at    TEXT,
    timing_method TEXT,
    -- per-day media (one whole-day stream per sitting):
    video_uri     TEXT,
    video_playseq TEXT,                  -- VOD "activation" endpoint (see viewer)
    video_duration REAL,
    video_license TEXT,
    video_creator TEXT
);

CREATE TABLE agenda_item (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT NOT NULL REFERENCES session(id),
    ord           INTEGER,
    title         TEXT,
    official_title TEXT,
    type          TEXT,                  -- normalized taxonomy (e.g. "voting")
    native_type   TEXT
);
CREATE INDEX idx_agenda_session ON agenda_item(session_id);

-- Note on keys: the upstream felszólalás id (`origin_id`, e.g. "43-3-12") is NOT
-- unique within a day — one continuous speech can be attached to several agenda
-- items, repeating the id. `speech_index` is the true per-day sequence, so the
-- stable, globally-unique key is `uid = "<session>-<speechIndex>"` (e.g.
-- "43003-12"). Deep links (VIE-5) use `uid`; `origin_id` drives the "view on
-- parlament.hu" source link.
CREATE TABLE speech (
    uid            TEXT PRIMARY KEY,     -- "43003-12" (session + speech_index)
    origin_id      TEXT,                 -- upstream felszólalás id "43-3-12"
    session_id     TEXT NOT NULL REFERENCES session(id),
    agenda_item_id INTEGER REFERENCES agenda_item(id),
    period_number  INTEGER,
    speech_index   INTEGER,
    person_id      TEXT REFERENCES person(person_id),
    speaker_label  TEXT,
    speaker_status TEXT,
    faction_id     INTEGER REFERENCES faction(id),
    -- timing (day-absolute seconds into the whole-day stream — TIM-2):
    time_start     REAL,
    time_end       REAL,
    video_start    REAL,                 -- per-speech offset (provenance only in v1)
    video_end      REAL,
    duration       REAL,                 -- time_end - time_start (for stats)
    -- provenance (SCR-5 / TRUST-1):
    confidence     REAL,
    align_method   TEXT,
    has_text       INTEGER DEFAULT 0,
    source_uri     TEXT,
    source_page    TEXT
);
CREATE INDEX idx_speech_session ON speech(session_id);
CREATE INDEX idx_speech_person ON speech(person_id);
CREATE INDEX idx_speech_agenda ON speech(agenda_item_id);
CREATE INDEX idx_speech_faction ON speech(faction_id);
CREATE INDEX idx_speech_origin ON speech(origin_id);

CREATE TABLE sentence (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    speech_id   TEXT NOT NULL REFERENCES speech(uid),
    ord         INTEGER,
    text        TEXT NOT NULL,
    time_start  REAL,
    time_end    REAL
);
CREATE INDEX idx_sentence_speech ON sentence(speech_id);

-- FTS5 over sentence text (DB-2). External-content index mirrors `sentence`.
-- unicode61 + remove_diacritics=2 gives accent-folding and case-insensitive,
-- Unicode-aware tokenization suitable for Hungarian (SEA-2).
CREATE VIRTUAL TABLE sentence_fts USING fts5(
    text,
    content = 'sentence',
    content_rowid = 'id',
    tokenize = "unicode61 remove_diacritics 2"
);

-- Keep the FTS index in sync with the base table.
CREATE TRIGGER sentence_ai AFTER INSERT ON sentence BEGIN
    INSERT INTO sentence_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER sentence_ad AFTER DELETE ON sentence BEGIN
    INSERT INTO sentence_fts(sentence_fts, rowid, text) VALUES('delete', old.id, old.text);
END;
CREATE TRIGGER sentence_au AFTER UPDATE ON sentence BEGIN
    INSERT INTO sentence_fts(sentence_fts, rowid, text) VALUES('delete', old.id, old.text);
    INSERT INTO sentence_fts(rowid, text) VALUES (new.id, new.text);
END;

-- ---------------------------------------------------------------------------
-- Bills module (irományok) — a self-contained vertical slice (EXT-1). It owns
-- these two tables and references the shared person/faction/electoral_period
-- core entities for sponsorship rather than duplicating them (EXT-2).
-- ---------------------------------------------------------------------------

CREATE TABLE bill (
    id             TEXT PRIMARY KEY,     -- Felicitas iromanyId (UUID)
    bill_number    TEXT,                 -- "T/229"
    number_sort    INTEGER,              -- numeric sort key within a cycle
    period_number  INTEGER REFERENCES electoral_period(number),
    title          TEXT,
    type           TEXT,                 -- "törvényjavaslat"
    main_type      TEXT,                 -- Felicitas fotipus, e.g. "T"
    status         TEXT,                 -- "tárgysorozatban"
    submitted_date TEXT,
    text_url       TEXT,                 -- bill text PDF on parlament.hu (LEGAL-1)
    text_caption   TEXT,
    source_url     TEXT,                 -- most specific resolvable original
    no_text        INTEGER DEFAULT 0,
    -- legislative-stage diagram for the bill timeline: ordered
    -- [{key,label,done}] (done = stage happened — past vs future event).
    stages_json    TEXT
);
CREATE INDEX idx_bill_period ON bill(period_number);
CREATE INDEX idx_bill_number_sort ON bill(number_sort);

CREATE TABLE bill_sponsor (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id    TEXT NOT NULL REFERENCES bill(id),
    person_id  TEXT REFERENCES person(person_id),  -- NULL for govt/committee
    faction_id INTEGER REFERENCES faction(id),
    label      TEXT,                                -- "Dr. X (TISZA)" / "kormány (…)"
    ord        INTEGER
);
CREATE INDEX idx_bill_sponsor_bill ON bill_sponsor(bill_id);
CREATE INDEX idx_bill_sponsor_person ON bill_sponsor(person_id);

-- ---------------------------------------------------------------------------
-- Reserved for the deferred NER/NEL stage (§10) — kept so the shape has room.
-- ---------------------------------------------------------------------------
CREATE TABLE entity (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sentence_id INTEGER REFERENCES sentence(id),
    label       TEXT,
    wikidata_id TEXT,
    char_start  INTEGER,
    char_end    INTEGER
);

-- ---------------------------------------------------------------------------
-- Derived / statistics tables (§4.2) — rebuilt by the loader each ingest (REP-7)
-- ---------------------------------------------------------------------------

CREATE TABLE person_stats (
    person_id       TEXT NOT NULL REFERENCES person(person_id),
    period_number   INTEGER,             -- NULL row = all periods combined
    speech_count    INTEGER DEFAULT 0,
    speaking_seconds REAL DEFAULT 0,
    sentence_count  INTEGER DEFAULT 0,
    PRIMARY KEY (person_id, period_number)
);

CREATE TABLE faction_stats (
    faction_id      INTEGER NOT NULL REFERENCES faction(id),
    period_number   INTEGER,
    speech_count    INTEGER DEFAULT 0,
    speaking_seconds REAL DEFAULT 0,
    mp_count        INTEGER DEFAULT 0,
    PRIMARY KEY (faction_id, period_number)
);

-- Speeches per sitting, per person (REP-3 trend chart over time).
CREATE TABLE person_session_stats (
    person_id     TEXT NOT NULL REFERENCES person(person_id),
    session_id    TEXT NOT NULL REFERENCES session(id),
    date          TEXT,
    speech_count  INTEGER DEFAULT 0,
    speaking_seconds REAL DEFAULT 0,
    PRIMARY KEY (person_id, session_id)
);

-- Free-form key/value provenance + build metadata for operators (SCR-3).
CREATE TABLE build_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
