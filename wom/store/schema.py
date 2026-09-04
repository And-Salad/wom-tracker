"""The tables, as they are today.

Every CREATE here is IF NOT EXISTS and is executed on every open, so this
is what a new database is built from and what an older one is brought up
to. How it gets there from an older shape is migrations.py.
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    id              INTEGER PRIMARY KEY,          -- Wise Old Man player id
    username        TEXT NOT NULL UNIQUE,         -- lowercase, the API's key
    display_name    TEXT NOT NULL,
    type            TEXT,
    build           TEXT,
    status          TEXT,
    country         TEXT,
    combat_level    INTEGER,
    exp             INTEGER,
    ehp             REAL,
    ehb             REAL,
    ttm             REAL,
    registered_at   TEXT,
    updated_at      TEXT,
    last_changed_at TEXT,
    last_fetched_at TEXT,
    backfilled_at   TEXT                          -- when history was imported
);

CREATE TABLE IF NOT EXISTS snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id   INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    captured_at TEXT NOT NULL,                    -- snapshot createdAt, ISO UTC
    fetched_at  TEXT NOT NULL,
    origin      TEXT,                             -- see save_snapshot
    payload     TEXT NOT NULL,                    -- raw snapshot data as JSON
    UNIQUE (player_id, captured_at)
);

-- Only what changed. A reading repeats the previous one for 91 of every 100
-- metrics - a boss sitting at zero was being written again on every update,
-- forever - so a row is stored only when a value actually moves, and every
-- read carries the last one forward. See state_at().
--
-- WITHOUT ROWID with this key makes the table its own index, and the key is
-- ordered for the only question anyone asks of it: what was this metric worth
-- at or before some moment. That folds away both indexes the old shape needed.
CREATE TABLE IF NOT EXISTS metrics (
    player_id   INTEGER NOT NULL,
    kind        TEXT NOT NULL,                    -- skill | boss | activity | computed
    metric      TEXT NOT NULL,                    -- e.g. overall, zulrah, ehp
    captured_at TEXT NOT NULL,
    value       REAL,                             -- experience | kills | score | value
    rank        INTEGER,
    level       INTEGER,                          -- skills only
    efficiency  REAL,                             -- ehp for skills, ehb for bosses
    -- NULL for anything Wise Old Man told us. Otherwise the same words the
    -- snapshots table uses: `derived` where we worked the value out, and
    -- `reported` where a plugin said it outright. A derived value may now sit
    -- at the same moment as a real reading - a session's ramp is written at
    -- the readings it runs past - so the snapshot beside it can no longer say
    -- which this is, and recomputing has to withdraw exactly what it wrote.
    origin      TEXT,
    PRIMARY KEY (player_id, kind, metric, captured_at)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS achievements (
    player_id   INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,                    -- e.g. "99 Attack"
    metric      TEXT,                             -- attack, zulrah, overall...
    measure     TEXT,                             -- experience | kills | levels | score
    threshold   REAL,
    achieved_at TEXT,                             -- ISO UTC, may be approximate
    accuracy    INTEGER,                          -- +/- milliseconds, -1 if unknown
    first_seen  TEXT NOT NULL,                    -- when this app first stored it
    PRIMARY KEY (player_id, name)
);

CREATE INDEX IF NOT EXISTS idx_achievements_when
    ON achievements (achieved_at DESC);

CREATE TABLE IF NOT EXISTS summaries (
    player_id     INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    period        TEXT NOT NULL,                  -- day | week | month
    window_key    TEXT NOT NULL,                  -- the window's start date
    period_start  TEXT NOT NULL,                  -- ISO UTC, inclusive
    period_end    TEXT NOT NULL,                  -- ISO UTC, exclusive
    label         TEXT NOT NULL,                  -- "Sunday 30 August 2026"
    text          TEXT NOT NULL,
    digest_hash   TEXT NOT NULL,                  -- skip regenerating unchanged data
    model         TEXT,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    generated_at  TEXT NOT NULL,
    PRIMARY KEY (player_id, period, window_key)
);

-- The group verdict for a window. Its own table because it belongs to no
-- single player, and a nullable half of a primary key is a trap.
CREATE TABLE IF NOT EXISTS group_summaries (
    board         TEXT NOT NULL DEFAULT 'maxing',  -- which competition
    period        TEXT NOT NULL,
    window_key    TEXT NOT NULL,
    winner        TEXT,                           -- username the round-up named
    period_start  TEXT NOT NULL,
    period_end    TEXT NOT NULL,
    label         TEXT NOT NULL,
    text          TEXT NOT NULL,
    digest_hash   TEXT NOT NULL,
    model         TEXT,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    generated_at  TEXT NOT NULL,
    PRIMARY KEY (board, period, window_key)
);

-- Dink's metadata webhook, which reports both ends of a session as they
-- happen: a login six seconds in, carrying that account's own reading of its
-- experience, and a logout, which carries only the fact and the moment.
--
-- Between them this is the only measurement of a session we can get. Wise Old
-- Man infers an ending from the hiscores moving and cannot see a beginning at
-- all, so a three hour session arrives as a single jump and we attribute it to
-- the ten minutes we happened to notice in.
--
-- Keyed by username rather than player id: an event can arrive before the
-- account is tracked, and it stays interesting after one is pruned. player_id
-- is a convenience, filled in when we know it.
CREATE TABLE IF NOT EXISTS session_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT NOT NULL,                    -- lowercase, the token's owner
    player_id   INTEGER,                          -- NULL until the account is known
    kind        TEXT NOT NULL,                    -- login | logout
    received_at TEXT NOT NULL,                    -- when the POST reached us, ISO UTC
    happened_at TEXT NOT NULL,                    -- when the client says it happened
    world       INTEGER,                          -- login only
    total_exp   REAL,                             -- login only: totalExperience, live
    total_level INTEGER,                          -- login only
    collections INTEGER,                          -- login only: collectionLog.completed
    payload     TEXT NOT NULL                     -- what we chose to keep of the body
);

CREATE INDEX IF NOT EXISTS idx_session_events_who
    ON session_events (username, happened_at DESC);

-- What Dink reports while somebody is playing, rather than at the ends of a
-- session: a collection log slot filled, a level gained, a boss count passed.
-- Opt-in per player, so this is sparse and always will be.
--
-- Kept whole as well as flattened, because the interesting part is the detail
-- - which item, from which drop, at which rank - and none of that fits the
-- metrics table. A collection log feed wants the item; the charts want the
-- count. Both are here.
CREATE TABLE IF NOT EXISTS game_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT NOT NULL,                    -- lowercase, the token's owner
    player_id   INTEGER,
    kind        TEXT NOT NULL,                    -- collection | level | kill_count
    happened_at TEXT NOT NULL,                    -- when the client says it happened
    received_at TEXT NOT NULL,
    subject     TEXT,                             -- the item, skill or boss
    quantity    REAL,                             -- slots filled, new level, kills
    payload     TEXT NOT NULL,
    UNIQUE (username, kind, subject, happened_at)
);

CREATE INDEX IF NOT EXISTS idx_game_events_when
    ON game_events (happened_at DESC);

-- Screenshots that came with a death or a pet drop. Only the bytes' digest
-- and where they sit live here; the file itself is on the volume, because a
-- few hundred megabytes of PNG inside the database would ride along on every
-- backup pull for something decorative.
--
-- The digest is the file name, so the same screenshot delivered twice is one
-- file, and nothing a client sends is ever used as a path.
CREATE TABLE IF NOT EXISTS images (
    digest      TEXT PRIMARY KEY,                 -- sha256 of the bytes
    username    TEXT NOT NULL,
    player_id   INTEGER,
    kind        TEXT NOT NULL,                    -- death | pet
    format      TEXT NOT NULL,                    -- png | jpeg, from the bytes
    bytes       INTEGER NOT NULL,
    happened_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    event_id    INTEGER,                          -- the game_events row, if any
    caption     TEXT
);

CREATE INDEX IF NOT EXISTS idx_images_when
    ON images (kind, happened_at DESC);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    ok_count    INTEGER DEFAULT 0,
    fail_count  INTEGER DEFAULT 0,
    roster      INTEGER,                          -- players tracked at the time
    trigger     TEXT,                             -- scheduled | manual | startup
    notes       TEXT
);
"""

# A reading Wise Old Man made this recently was made because we asked. Beyond
# it, the reading already existed and we are only now collecting it - which is
# a moment we could never have observed ourselves. It is what `origin` above
# means, so it lives beside the table rather than beside either of the two
# places that apply it - see snapshots.save_snapshot and migrations.py.
FRESH_SECONDS = 60
