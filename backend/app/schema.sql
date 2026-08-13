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
    wikidata_id       TEXT,              -- Wikidata QID, joined via P4966 (EXT-2)
    wikipedia_url     TEXT,              -- preferred (hu, else en) Wikipedia article
    -- Birth date from Wikidata (P569, day-precision only) and the two signs the
    -- scraper derives from it. Stored, not yet surfaced by any endpoint.
    date_of_birth     TEXT,              -- YYYY-MM-DD
    zodiac_sign       TEXT,              -- sun sign, lowercase Latin, e.g. "taurus"
    chinese_zodiac_sign TEXT,            -- lunar-year animal, e.g. "dragon"
    kmonitor_url      TEXT,              -- K-Monitor adatbázis tag page (matched by name)
    cv_url            TEXT,              -- the CV PDF they had published (REP-13)
    photo_uri         TEXT,
    photo_file        TEXT,
    constituency      TEXT,
    seat              TEXT,
    email             TEXT,
    website           TEXT,
    highest_education TEXT,
    active            INTEGER,           -- 0/1/NULL
    is_mp             INTEGER DEFAULT 0, -- 1 when present in an MP roster
    -- Nationality advocate (nemzetiségi szószóló): sits and speaks in the House
    -- but holds no representative mandate, so they are never in the MP roster and
    -- have no faction/constituency — `nationality` is their affiliation instead.
    is_advocate       INTEGER DEFAULT 0, -- 1 when present in an advocate roster
    nationality       TEXT,              -- e.g. "roma", "szerb" (advocates only)
    -- Richer enrichment kept as JSON for the profile page (arrays of objects):
    education_json        TEXT,
    committees_json       TEXT,
    offices_json          TEXT,
    faction_history_json  TEXT,
    election_history_json TEXT,
    external_stats_json   TEXT,          -- upstream per-cycle counts (bills, etc.)
    -- Asset declarations (vagyonnyilatkozatok, REP-13): one entry per filing,
    -- newest first, each linking to its PDF on parlament.hu (never mirrored).
    asset_declarations_json TEXT
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

-- person <-> mandate <-> period (REP-14): the seat someone held in ONE cycle. The
-- upstream roster is a point-in-time listing, so this is what separates a cycle's
-- roster from its actual membership — the mandates that ended before the term did
-- (a death, a resignation, an incompatibility) and the handovers that followed.
CREATE TABLE person_mandate (
    person_id      TEXT NOT NULL REFERENCES person(person_id),
    period_number  INTEGER,
    date_start     TEXT,
    date_end       TEXT,
    -- 1 when the mandate ended BEFORE the term did. Upstream's own verdict (the
    -- person is named by the composition-changes registry), never a date comparison
    -- of ours; `end_reason` is its wording, e.g. "elhunyt".
    terminated     INTEGER NOT NULL DEFAULT 0,
    end_reason     TEXT,
    constituency   TEXT,               -- the seat or list it was won on
    -- The two sides of a handover. The label is stored beside the id because the
    -- other person need not be in `person` at all — a successor seated after the
    -- last roster scrape, a predecessor from a cycle this DB was never loaded with.
    predecessor_id    TEXT,
    predecessor_label TEXT,
    successor_id      TEXT,
    successor_label   TEXT,
    PRIMARY KEY (person_id, period_number)
);

-- person <-> government/House office (tisztség) <-> term: one row per office a
-- person held, with the upstream appointment/dismissal dates (`date_end` NULL =
-- still in office). Two sources feed it, distinguished by `source` so each can be
-- reloaded on its own (REP-2):
--   'registry' — the all-time office-holder listing (tisztségviselők), the ONLY
--                source for a non-MP minister/state secretary, who is in no roster;
--   'roster'   — the per-MP office list that comes with the MP/advocate registry.
-- They report the same upstream rows, so reads dedupe on (title, date_start).
-- `category` is the registry's own office grouping ('pm', 'minister',
-- 'state-secretary', 'parliamentary', 'senior', 'other') — the scraper gets it from
-- WHICH per-category listing returned the row, since the row itself carries only a
-- free-text title. Only registry rows have one, which is why the office listing
-- (REP-11) reads that source alone.
CREATE TABLE person_office (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id     TEXT NOT NULL REFERENCES person(person_id),
    title         TEXT NOT NULL,
    category      TEXT,
    date_start    TEXT,
    date_end      TEXT,
    source        TEXT NOT NULL DEFAULT 'registry'
);
CREATE INDEX idx_person_office_person ON person_office(person_id);
CREATE INDEX idx_person_office_listing ON person_office(source, category, date_start);

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
    -- 'published' = a held sitting with speeches; 'scheduled' = an announced
    -- upcoming sitting parlament.hu lists before any recording/transcript exists
    -- (shown as "coming", has no speeches yet).
    status        TEXT DEFAULT 'published',
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
    speech_uuid    TEXT,                 -- Felicitas felszólalás UUID (joins bill events to a speech)
    session_id     TEXT NOT NULL REFERENCES session(id),
    agenda_item_id INTEGER REFERENCES agenda_item(id),
    period_number  INTEGER,
    speech_index   INTEGER,
    person_id      TEXT REFERENCES person(person_id),
    speaker_label  TEXT,
    speaker_status TEXT,
    -- the speaker's government office (tisztség) as reported per speech, e.g.
    -- "igazságügyi miniszter" / "Pénzügyminisztérium államtitkára". Present only
    -- for office-holders; surfaced on a speaker's profile so a non-MP (a minister
    -- or state secretary who is not a representative) is identifiable by their office.
    speaker_office TEXT,
    -- upstream per-speech type (felszólalás típusa, e.g. "ülésvezetés"); drives
    -- the `procedural` flag below.
    felszolalas_tipus TEXT,
    -- 1 = chairing / session-management speech, kept and shown in the viewer but
    -- excluded from all representative/faction statistics (STAT-1).
    procedural     INTEGER DEFAULT 0,
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
CREATE INDEX idx_speech_uuid ON speech(speech_uuid);

CREATE TABLE sentence (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    speech_id   TEXT NOT NULL REFERENCES speech(uid),
    ord         INTEGER,
    text        TEXT NOT NULL,
    time_start  REAL,
    time_end    REAL,
    -- 0-based index of the source paragraph this sentence belongs to, so a
    -- speech can be re-grouped into its original paragraphs for reading
    -- (NULL on pre-migration rows → render as one block).
    paragraph   INTEGER
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
    stages_json    TEXT,
    -- extra header fields from the per-bill detail sheet (adatlap):
    subtype           TEXT,              -- tipus, e.g. "törvényjavaslat nemzetközi szerződésről"
    character         TEXT,              -- jelleg ("új" / "módosító")
    negotiation_mode  TEXT,              -- targyalasiMod ("kivételes tárgyalásban")
    status_type       TEXT,              -- allapottipus ("lezárt" / "folyamatban")
    current_event     TEXT,              -- aktualisIromanyEsemeny
    promulgation_number TEXT,            -- kihirdetesSzama
    mk_number         INTEGER,           -- Magyar Közlöny szám
    promulgation_date TEXT,              -- kihirdetesDatuma
    remark            TEXT,              -- megjegyzes
    last_modifier     TEXT,              -- utolsoModositoIromanySzam
    -- Magyar Közlöny (gazette) links for a promulgated bill, resolved from
    -- magyarkozlony.hu: the issue-listing page and the direct PDF-viewer URL.
    kozlony_url       TEXT,              -- issue listing page (fallback)
    kozlony_doc_url   TEXT               -- direct gazette PDF viewer (preferred)
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

-- Per-bill detail sub-tables (the adatlap sections). Each is a flat child of
-- bill, replaced wholesale when a bill's cycle is re-ingested (ING-4). Events
-- that reference an MP resolve person_id through the shared person entity
-- (EXT-2); unknown persons keep only their label.

CREATE TABLE bill_event (             -- "Iromány események" (legislative history)
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id      TEXT NOT NULL REFERENCES bill(id),
    ord          INTEGER,
    event_date   TEXT,
    name         TEXT,                 -- esemenyfajtaNev
    person_id    TEXT REFERENCES person(person_id),
    related_label TEXT,                -- related person / committee name
    committee_id TEXT,
    speech_number TEXT,                -- felszolalasSzam (speech attached to the event)
    speech_id    TEXT,                 -- felszolalasId (joins to speech.speech_uuid for an in-site link)
    vote_id      TEXT,                 -- joins to bill_vote.vote_id
    remark       TEXT
);
CREATE INDEX idx_bill_event_bill ON bill_event(bill_id);

CREATE TABLE bill_committee_event (   -- "Bizottsági események"
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id      TEXT NOT NULL REFERENCES bill(id),
    ord          INTEGER,
    event_date   TEXT,
    name         TEXT,
    committee    TEXT,
    committee_id TEXT,
    person_id    TEXT REFERENCES person(person_id),
    person_label TEXT,
    amendment    TEXT,                 -- módosító jav.
    overreaching_amendment TEXT,       -- túlterjeszkedő mód. jav.
    report       TEXT                  -- jelentés
);
CREATE INDEX idx_bill_committee_event_bill ON bill_committee_event(bill_id);

CREATE TABLE bill_vote (              -- "Szavazások"
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id   TEXT NOT NULL REFERENCES bill(id),
    ord       INTEGER,
    vote_id   TEXT,                    -- upstream szavazasId
    vote_date TEXT,
    subject   TEXT,                    -- oka
    yes       INTEGER,
    no        INTEGER,
    abstain   INTEGER,
    result    TEXT
);
CREATE INDEX idx_bill_vote_bill ON bill_vote(bill_id);

CREATE TABLE bill_deadline (         -- "Határidők"
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id   TEXT NOT NULL REFERENCES bill(id),
    ord       INTEGER,
    name      TEXT,
    deadline  TEXT,
    reference TEXT,                    -- jogszabályi hivatkozás
    remark    TEXT
);
CREATE INDEX idx_bill_deadline_bill ON bill_deadline(bill_id);

CREATE TABLE bill_committee (        -- "Tárgyaló bizottság" (negotiating committees)
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id      TEXT NOT NULL REFERENCES bill(id),
    ord          INTEGER,
    committee    TEXT,
    committee_id TEXT,
    role         TEXT,                 -- tárgyalási szerepkör
    reference    TEXT,                 -- jogszabályi hivatkozás
    parts        TEXT                  -- tárgyalandó részek
);
CREATE INDEX idx_bill_committee_bill ON bill_committee(bill_id);

CREATE TABLE bill_document (          -- "Indokolások" + "Háttéranyagok"
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id   TEXT NOT NULL REFERENCES bill(id),
    ord       INTEGER,
    kind      TEXT,                    -- 'justification' | 'background'
    title     TEXT,
    url       TEXT,
    doc_date  TEXT,
    published TEXT                     -- közzététel (Magyar Közlöny szám)
);
CREATE INDEX idx_bill_document_bill ON bill_document(bill_id);

CREATE TABLE bill_motion_summary (   -- "Nem önálló irományok" összesítő (counts)
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id   TEXT NOT NULL REFERENCES bill(id),
    ord       INTEGER,
    type      TEXT,
    valid     INTEGER,
    withdrawn INTEGER,
    total     INTEGER
);
CREATE INDEX idx_bill_motion_summary_bill ON bill_motion_summary(bill_id);

CREATE TABLE bill_motion (           -- "Nem önálló irományok" — the individual
                                     -- dependent motions (each its own iromány)
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id      TEXT NOT NULL REFERENCES bill(id),
    ord          INTEGER,
    iromany_id   TEXT,               -- the motion's own iromanyId (modositoId)
    bill_number  TEXT,               -- iromanyszam, e.g. "53/3"
    number_sort  INTEGER,
    main_type    TEXT,               -- fotipus, e.g. "egyéb"
    type         TEXT,               -- tipus, e.g. "Módosító javaslat"
    submitted_date TEXT,
    text_url     TEXT,               -- downloadable PDF/text (LEGAL-1)
    text_caption TEXT,
    no_text      INTEGER DEFAULT 0,
    has_vote     INTEGER DEFAULT 0,
    note         TEXT
);
CREATE INDEX idx_bill_motion_bill ON bill_motion(bill_id);

CREATE TABLE bill_motion_sponsor (   -- submitters of a non-self-standing motion
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    motion_id  INTEGER NOT NULL REFERENCES bill_motion(id),
    person_id  TEXT REFERENCES person(person_id),   -- NULL for committee/Speaker
    faction_id INTEGER REFERENCES faction(id),
    label      TEXT,
    ord        INTEGER
);
CREATE INDEX idx_bill_motion_sponsor_motion ON bill_motion_sponsor(motion_id);

-- ---------------------------------------------------------------------------
-- Votes module (szavazások) — a self-contained vertical slice (EXT-1). It owns
-- these tables and references the shared person/faction/bill core entities for
-- the roll call, faction breakdown and the bill(s) decided, rather than
-- duplicating them (EXT-2). A vote's `id` is the upstream szavazasId — the same
-- key a bill's vote tally (`bill_vote.vote_id`) already references, so the two
-- modules link both ways without any new join key.
-- ---------------------------------------------------------------------------

CREATE TABLE vote (
    id             TEXT PRIMARY KEY,     -- Felicitas szavazasId (UUID)
    period_number  INTEGER REFERENCES electoral_period(number),
    vote_datetime  TEXT,                 -- idopont (ISO)
    voting_mode    TEXT,                 -- szavazasiMod ("Listás a jelenlevők 2/3-ával")
    subject        TEXT,                 -- szavazasOka ("sürgősségi javaslat elfogadva")
    result         TEXT,                 -- eredmeny ("Elfogadva")
    yes            INTEGER,              -- igen
    no             INTEGER,              -- nem
    abstain        INTEGER,              -- tartózkodás
    total_votes    INTEGER,              -- osszesSzavazat (from the detail sheet)
    has_per_mp     INTEGER DEFAULT 0,    -- hasKepviselo: a per-MP roll call exists
    remark         TEXT                  -- megjegyzes
);
CREATE INDEX idx_vote_period ON vote(period_number);
CREATE INDEX idx_vote_datetime ON vote(vote_datetime);

-- The subject(s) a vote decided — one row per iromány (a vote can decide a bill
-- plus its motions). `iromany_id` is the upstream iromanyId; it is resolved to a
-- held bill at query time (LEFT JOIN bill ON bill.id = iromany_id) rather than by
-- a hard FK, so the two modules stay decoupled (EXT-1: re-ingesting bills must
-- not break votes). For iromány types we don't hold (resolutions, motions) the
-- join yields no bill and the number/title are kept as a label (SCR-5).
CREATE TABLE vote_subject (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    vote_id     TEXT NOT NULL REFERENCES vote(id),
    ord         INTEGER,
    iromany_id  TEXT,                    -- upstream iromanyId (joins to bill.id when held)
    bill_number TEXT,                    -- "T/174"
    title       TEXT
);
CREATE INDEX idx_vote_subject_vote ON vote_subject(vote_id);
CREATE INDEX idx_vote_subject_iromany ON vote_subject(iromany_id);

-- The per-MP roll call: every representative's individual vote. person_id joins
-- to the shared person entity (EXT-2); a kepviseloId not in the roster keeps its
-- name label only (no FK). value is the raw Hungarian vote type; value_code
-- normalizes it (yes/no/abstain/absent/novote) for counting and colouring.
CREATE TABLE vote_record (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    vote_id      TEXT NOT NULL REFERENCES vote(id),
    person_id    TEXT REFERENCES person(person_id),
    name         TEXT,                   -- nev (as recorded on the vote)
    faction_name TEXT,                   -- frakcioNev (as recorded on the vote)
    value        TEXT,                   -- szavazatTipus ("Igen", "Nem", …)
    value_code   TEXT                    -- yes|no|abstain|absent|novote
);
CREATE INDEX idx_vote_record_vote ON vote_record(vote_id);
CREATE INDEX idx_vote_record_person ON vote_record(person_id);

-- Per-faction breakdown of a vote (igen/nem/tartózkodás per faction, plus
-- against-faction defections). faction_id resolves to the shared faction entity
-- (EXT-2) where the Felicitas frakcioId is known.
CREATE TABLE vote_faction_stat (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    vote_id       TEXT NOT NULL REFERENCES vote(id),
    ord           INTEGER,
    faction_id    INTEGER REFERENCES faction(id),
    faction_name  TEXT,
    total         INTEGER,
    yes           INTEGER,
    no            INTEGER,
    abstain       INTEGER,
    absent        INTEGER,
    not_voting    INTEGER,
    against_faction TEXT                 -- frakcioElleniSzavazat (defections, e.g. "0 fő")
);
CREATE INDEX idx_vote_faction_stat_vote ON vote_faction_stat(vote_id);

-- ---------------------------------------------------------------------------
-- NER/NEL stage (§10): named entities recognized in transcript sentences, and the
-- subset of them linked out. `entity` is one row per mention (its char span in the
-- sentence, for inline linking), tagged `kind` with the NER label — every label the
-- model emits is stored, so the corpus keeps a complete entity layer, but only
-- PER/ORG are ever resolved and rendered. `entity_link` resolves each distinct
-- normalized PER/ORG name to an ordered set of destinations, once (loader-derived).
-- ---------------------------------------------------------------------------
CREATE TABLE entity (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sentence_id INTEGER NOT NULL REFERENCES sentence(id),
    entity_key  TEXT NOT NULL,     -- normalized (lemma-joined) name; joins entity_link
    surface     TEXT NOT NULL,     -- exact text as it appears in the sentence
    char_start  INTEGER,
    char_end    INTEGER,
    -- NER label: 'PER' (person) | 'ORG' (institution) | 'LOC' (place) | 'MISC'.
    -- Only PER/ORG join entity_link; LOC/MISC are stored for analysis and never
    -- shown — read paths filter on app.nlp.LINKABLE_LABELS.
    kind        TEXT NOT NULL DEFAULT 'PER'
);
CREATE INDEX idx_entity_sentence ON entity(sentence_id);
CREATE INDEX idx_entity_key ON entity(entity_key);

-- One resolved link-set per distinct name (normalized key). Written by the
-- K-Monitor resolver (app/kmonitor.py) after gathering Wikidata candidates
-- (app/wikidata.py). `links_json` is an ORDERED list of destinations —
-- `[{type, url|person_id, label, description, wikidata_id}]` with
-- `type ∈ {profile, kmonitor, wikipedia}` — K-Monitor first (the primary target),
-- Wikipedia only as a fallback when no K-Monitor tag matched, and the internal MP
-- profile first of all when the name is a known representative. `ambiguous` marks a
-- name that resolved to more than one candidate in its chosen source (the UI shows
-- the alternatives as separate badges, ordered by probability). A key with no
-- destination at all is simply not written (so it isn't re-linked).
CREATE TABLE entity_link (
    entity_key      TEXT PRIMARY KEY,
    kind            TEXT,          -- 'PER' | 'ORG'
    ambiguous       INTEGER DEFAULT 0,
    links_json      TEXT,          -- ordered JSON list of destinations (see above)
    resolved_at     TEXT
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

-- Per-file load bookkeeping for the *incremental* update path (`--update`).
-- Records the (mtime, size) of every processed JSON the DB was last built/updated
-- from, keyed by basename, so an update reloads only the sittings/registries whose
-- source file actually changed since — instead of a full rebuild (SCR-2). A full
-- `build_database` reseeds every row; a DB built before this table existed simply
-- triggers one more full rebuild to seed the baseline.
CREATE TABLE load_state (
    name  TEXT PRIMARY KEY,   -- processed-file basename, e.g. "43007-session.json"
    mtime REAL NOT NULL,
    size  INTEGER NOT NULL
);

-- Per-sitting topical term frequencies for the word cloud (WCLOUD-2), precomputed
-- at load time so the expensive lemmatization / entity extraction never runs at
-- request time. A `word` is a HuSpaCy lemma (inflected forms collapsed) or a
-- multi-word named entity; `kind` is 'entity' for recognized named entities, else
-- 'term'. The endpoint reads these rows as the per-day term frequency, and the
-- corpus-side `word_doc_freq`/`word_doc_total` below are derived from this table.
CREATE TABLE session_word_count (
    session_id TEXT NOT NULL REFERENCES session(id),
    word       TEXT NOT NULL,
    count      INTEGER NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'term',
    PRIMARY KEY (session_id, word)
) WITHOUT ROWID;

-- Per-cycle word document-frequency for the sitting-day word cloud (WCLOUD-2):
-- how many sitting days in a period contain a given (lemmatized, stop-word-
-- filtered) word. Lets the word cloud rank by TF·IDF — words frequent on one day
-- but rare across the cycle — rather than raw frequency, so each day's cloud
-- surfaces what is *distinctive* about that day. Derived from session_word_count
-- and rebuilt with the other aggregates (REP-7).
CREATE TABLE word_doc_freq (
    period_number INTEGER NOT NULL,
    word          TEXT NOT NULL,
    doc_count     INTEGER NOT NULL,   -- # of sitting days in the period with the word
    PRIMARY KEY (period_number, word)
) WITHOUT ROWID;

-- N for the IDF: number of sitting days per period that contributed any words.
CREATE TABLE word_doc_total (
    period_number INTEGER PRIMARY KEY,
    n_docs        INTEGER NOT NULL
);

-- The first sitting day, chronologically and across ALL electoral cycles, on
-- which each lemmatized word / named entity was ever spoken in the chamber
-- (NEW-1). Derived purely from session_word_count + session dates: one row per
-- distinct word, pointing at the earliest session (by date, then id for a
-- same-day tie) that contains it. Lets each sitting-day page surface the words
-- that *debuted* there — never said before in parliament, previous cycles
-- included. No FK to session (like word_doc_freq): it is rebuilt wholesale, so
-- it never blocks an idempotent per-session re-ingest.
CREATE TABLE word_first_seen (
    word       TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    date       TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'term'
) WITHOUT ROWID;
CREATE INDEX idx_word_first_seen_session ON word_first_seen(session_id);

-- Per-speech readability + lexical diversity (READ-1..7), precomputed at load
-- time (app/readability.py, saphes) so nothing is measured at request time.
-- One row per *measurable* speech: procedural/chairing speeches (STAT-1) are
-- excluded, as is anything under the minimum-length floor, so a missing row means
-- "not measurable", never "zero".
--
-- The readability half (LIX/RIX and its A/B/C counts) needs no model — it is
-- computed from the transcript's own sentences, with the speaker attribution and
-- the stenographer's stage directions stripped. The diversity half (TTR/MATTR)
-- needs LEMMAS, so it is filled only when a HuSpaCy lemmatizer was reachable;
-- `lemma_model` records which one (NULL = readability only). `mattr` is NULL for
-- a speech shorter than the sliding window, because plain TTR is not comparable
-- across lengths and must not masquerade as MATTR.
CREATE TABLE speech_metrics (
    speech_id    TEXT PRIMARY KEY REFERENCES speech(uid),
    session_id   TEXT NOT NULL REFERENCES session(id),
    -- readability (surface forms — word length is the signal)
    lix          REAL,      -- A/B + 100*C/A
    rix          REAL,      -- C/B
    words        INTEGER,   -- A
    sentences    INTEGER,   -- B
    long_words   INTEGER,   -- C (longer than the calibrated threshold)
    avg_sentence REAL,      -- A/B, words per sentence
    long_share   REAL,      -- C/A, the share of long words
    -- lexical diversity (lemmas — surface variation is morphology, not vocabulary)
    ttr          REAL,
    mattr        REAL,      -- NULL when the speech is shorter than the window
    types        INTEGER,
    tokens       INTEGER,
    lemma_model  TEXT       -- HuSpaCy model behind ttr/mattr; NULL = none reachable
);
CREATE INDEX idx_speech_metrics_session ON speech_metrics(session_id);

-- Corpus-relative cut points for the metrics above (READ-6). Björnsson's LIX
-- difficulty labels are calibrated for Swedish prose at long-word threshold 6 and
-- are meaningless at the Hungarian threshold, so a speech is banded against the
-- distribution of every measured speech instead: "nehéz" means harder to read
-- than 80% of what is said in this House. Storing the cut points (rather than a
-- per-speech percentile) keeps an incremental update cheap — nothing has to be
-- re-ranked to answer a request, and the API derives the band on read.
CREATE TABLE metric_distribution (
    metric TEXT NOT NULL,      -- 'lix' | 'mattr'
    q      INTEGER NOT NULL,   -- percentile (10, 20, … 90)
    value  REAL NOT NULL,      -- the score at that percentile
    n      INTEGER,            -- speeches behind the distribution
    PRIMARY KEY (metric, q)
) WITHOUT ROWID;

-- ---------------------------------------------------------------------------
-- Portfolios (tárcák) — the government side of the corpus (§6C)
-- ---------------------------------------------------------------------------
-- Derived tables, rebuilt wholesale by the loader from rows the other modules
-- already wrote (EXT-2): no new scraping and no source of their own. The tárca a
-- label names comes from `app/portfolios.py`, the one place that says which of
-- the corpus's four label spaces means which tárca (MIN-3).

CREATE TABLE portfolio (
    slug TEXT PRIMARY KEY,     -- stable id, used in URLs
    name TEXT NOT NULL,        -- canonical Hungarian name
    kind TEXT NOT NULL,        -- ministry | pm | no-portfolio | other | body
    ord  INTEGER               -- display order (by corpus weight) within a kind
);

-- Every corpus label that resolved to a tárca, materialised so the mapping is
-- auditable in the database itself and not only in the code (TRUST-1).
CREATE TABLE portfolio_alias (
    label          TEXT PRIMARY KEY,   -- the label as the corpus writes it
    portfolio_slug TEXT NOT NULL REFERENCES portfolio(slug)
);

-- An iromány's link to a tárca. `role` says in which direction:
--   'answered'  — the tárca answered this kérdés/interpelláció (bill_event)
--   'submitted' — the government laid it before the House through this tárca
-- (MIN-4's 'addressed' role is not populated yet — see MIN-10a.)
CREATE TABLE portfolio_bill (
    portfolio_slug TEXT NOT NULL REFERENCES portfolio(slug),
    bill_id        TEXT NOT NULL REFERENCES bill(id),
    role           TEXT NOT NULL,
    label          TEXT,               -- the label this link was resolved from
    event_date     TEXT,               -- when the tárca acted: answered / submitted
    -- The cycle this link *happened in*, derived from `event_date` — NOT the cycle
    -- the parent iromány is filed under. parlament.hu re-lists an iromány that is
    -- still in progress under the new cycle, so a bill the government submitted in
    -- 2024 comes back as a cycle-43 row; counted by the parent's cycle, a ministry
    -- abolished in 2026 reappears in the 2026 listing on the strength of a document
    -- it filed two years earlier. Falls back to the parent's cycle when the link
    -- carries no date of its own.
    period_number  INTEGER,
    PRIMARY KEY (portfolio_slug, bill_id, role)
) WITHOUT ROWID;
CREATE INDEX idx_portfolio_bill_bill ON portfolio_bill(bill_id);
CREATE INDEX idx_portfolio_bill_period
    ON portfolio_bill(portfolio_slug, role, period_number);

-- A plenary speech given in one of the tárca's offices (speech.speaker_office).
CREATE TABLE portfolio_speech (
    portfolio_slug TEXT NOT NULL REFERENCES portfolio(slug),
    speech_uid     TEXT NOT NULL REFERENCES speech(uid),
    PRIMARY KEY (portfolio_slug, speech_uid)
) WITHOUT ROWID;
CREATE INDEX idx_portfolio_speech_speech ON portfolio_speech(speech_uid);

-- Who held the tárca's offices and when — the office-holder registry's terms
-- (REP-2a) filed under the tárca they belong to.
CREATE TABLE portfolio_office (
    portfolio_slug TEXT NOT NULL REFERENCES portfolio(slug),
    person_id      TEXT NOT NULL REFERENCES person(person_id),
    title          TEXT NOT NULL,
    category       TEXT,
    date_start     TEXT,
    date_end       TEXT
);
CREATE INDEX idx_portfolio_office_slug ON portfolio_office(portfolio_slug);
CREATE INDEX idx_portfolio_office_person ON portfolio_office(person_id);
