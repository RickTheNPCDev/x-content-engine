-- Content engine. One SQLite file, no ORM.

CREATE TABLE IF NOT EXISTS substance (
    id                   INTEGER PRIMARY KEY,
    created_at           TEXT NOT NULL,
    source               TEXT NOT NULL,          -- text | voice | document | forward | interview
    raw_text             TEXT,
    transcript           TEXT,
    audio_path           TEXT,
    question_id          INTEGER REFERENCES question(id),
    shareable            INTEGER NOT NULL DEFAULT 0,
    gate_status          TEXT NOT NULL DEFAULT 'pending',  -- pending | clear | flagged | blocked | overridden
    gate_reason          TEXT,
    drafted_at           TEXT,
    draft_result         TEXT                    -- ok | not_enough_substance | error
);

CREATE TABLE IF NOT EXISTS question (
    id                   INTEGER PRIMARY KEY,
    asked_at             TEXT NOT NULL,
    text                 TEXT NOT NULL,
    context_ids          TEXT,                   -- comma-separated substance ids the question was built from
    message_id           INTEGER,                -- telegram message, so a reply can be linked back
    answered_substance_id INTEGER REFERENCES substance(id)
);

CREATE TABLE IF NOT EXISTS draft (
    id                   INTEGER PRIMARY KEY,
    created_at           TEXT NOT NULL,
    substance_id         INTEGER NOT NULL REFERENCES substance(id),
    platform             TEXT NOT NULL DEFAULT 'linkedin',
    angle                TEXT,
    body                 TEXT NOT NULL,          -- as generated, never overwritten
    final_body           TEXT,                   -- after your edit; NULL means shipped as generated
    status               TEXT NOT NULL DEFAULT 'proposed',  -- proposed | approved | killed | shipped
    video_flag           INTEGER NOT NULL DEFAULT 0,
    video_script         TEXT,
    reviewed_at          TEXT,
    shipped_at           TEXT
);

CREATE TABLE IF NOT EXISTS signal (
    id                   INTEGER PRIMARY KEY,
    created_at           TEXT NOT NULL,
    draft_id             INTEGER REFERENCES draft(id),
    note                 TEXT NOT NULL
);

-- scheduler bookkeeping (last-run dates, so a restart does not skip a day)
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_substance_pipeline ON substance(shareable, gate_status, drafted_at);
CREATE INDEX IF NOT EXISTS idx_draft_status ON draft(status);
