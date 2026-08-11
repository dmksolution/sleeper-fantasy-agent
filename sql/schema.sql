-- Local cache for the Sleeper fantasy agent.
-- Everything here is derived from the public read-only Sleeper API and can be
-- rebuilt at any time with: python cli.py sync --full

PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS players (
    player_id        TEXT PRIMARY KEY,
    full_name        TEXT,
    search_name      TEXT,
    position         TEXT,
    fantasy_positions TEXT,          -- JSON array
    team             TEXT,
    status           TEXT,
    injury_status    TEXT,
    injury_body_part TEXT,
    injury_notes     TEXT,
    news_updated     INTEGER,
    age              INTEGER,
    years_exp        INTEGER,
    depth_chart_pos  TEXT,
    depth_chart_order INTEGER,
    number           INTEGER,
    active           INTEGER,
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_players_pos  ON players(position);
CREATE INDEX IF NOT EXISTS idx_players_team ON players(team);
CREATE INDEX IF NOT EXISTS idx_players_name ON players(search_name);

-- One row per player per week. week = 0 means the full-season aggregate.
CREATE TABLE IF NOT EXISTS projections (
    season      TEXT NOT NULL,
    week        INTEGER NOT NULL,
    player_id   TEXT NOT NULL,
    team        TEXT,
    opponent    TEXT,
    stats       TEXT NOT NULL,       -- JSON object of raw projected stats
    pts_ppr     REAL,
    pts_half_ppr REAL,
    pts_std     REAL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (season, week, player_id)
);
CREATE INDEX IF NOT EXISTS idx_proj_week ON projections(season, week);

-- Actual scored results, same shape as projections.
CREATE TABLE IF NOT EXISTS actuals (
    season      TEXT NOT NULL,
    week        INTEGER NOT NULL,
    player_id   TEXT NOT NULL,
    stats       TEXT NOT NULL,
    pts_ppr     REAL,
    pts_half_ppr REAL,
    pts_std     REAL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (season, week, player_id)
);

-- Average draft position by format, pulled from the season aggregate.
CREATE TABLE IF NOT EXISTS adp (
    season      TEXT NOT NULL,
    player_id   TEXT NOT NULL,
    format      TEXT NOT NULL,       -- ppr | half_ppr | std | 2qb | dynasty_ppr
    adp         REAL,
    pos_adp     REAL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (season, player_id, format)
);
CREATE INDEX IF NOT EXISTS idx_adp_lookup ON adp(season, format, adp);

-- Generic JSON blob cache with TTL, used for league, rosters, matchups, drafts.
CREATE TABLE IF NOT EXISTS kv_cache (
    key         TEXT PRIMARY KEY,
    payload     TEXT NOT NULL,
    fetched_at  TEXT NOT NULL
);

-- Append-only log so week over week changes can be diffed.
CREATE TABLE IF NOT EXISTS roster_history (
    league_id   TEXT NOT NULL,
    season      TEXT NOT NULL,
    week        INTEGER NOT NULL,
    roster_id   INTEGER NOT NULL,
    owner_id    TEXT,
    players     TEXT NOT NULL,       -- JSON array of player_id
    starters    TEXT,                -- JSON array of player_id
    captured_at TEXT NOT NULL,
    PRIMARY KEY (league_id, season, week, roster_id, captured_at)
);

-- Every recommendation the agent makes, so you can grade it later.
CREATE TABLE IF NOT EXISTS recommendations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    league_id   TEXT NOT NULL,
    season      TEXT NOT NULL,
    week        INTEGER NOT NULL,
    kind        TEXT NOT NULL,       -- start_sit | waiver | trade | draft
    player_id   TEXT,
    detail      TEXT NOT NULL,       -- JSON
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_recs ON recommendations(league_id, season, week, kind);
