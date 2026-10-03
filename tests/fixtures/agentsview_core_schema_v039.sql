-- AgentsView sessions.db core tables (schema only), copied from agentsview v0.39.0 internal/db/schema.sql
-- (dataVersion 74). Used by tests/agentsview_fixture.py for the user_version 74 fixtures; no data.
CREATE TABLE sessions (
    id          TEXT PRIMARY KEY,
    project     TEXT NOT NULL,
    machine     TEXT NOT NULL DEFAULT 'local',
    agent       TEXT NOT NULL DEFAULT 'claude',
    agent_label TEXT NOT NULL DEFAULT '',
    entrypoint  TEXT NOT NULL DEFAULT '',
    first_message TEXT,
    display_name TEXT,
    session_name TEXT,
    started_at  TEXT,
    ended_at    TEXT,
    message_count INTEGER NOT NULL DEFAULT 0,
    user_message_count INTEGER NOT NULL DEFAULT 0,
    file_path   TEXT,
    file_size   INTEGER,
    file_mtime  INTEGER,
    next_ordinal INTEGER NOT NULL DEFAULT 0,
    last_entry_uuid TEXT,
    -- SQLite-only sync bookkeeping: whether the Claude full parser fell
    -- back to linear processing for this file (NULL = unknown/legacy or
    -- non-Claude). Read by the incremental parser to skip fork
    -- detection on linear-bound transcripts. Like next_ordinal and
    -- last_entry_uuid, this is machine-local parse state deliberately
    -- not mirrored to PostgreSQL or DuckDB: parsers never run against
    -- those read-side stores, and any copy that drops it degrades to
    -- the conservative NULL verdict (full parse re-derives it).
    claude_linear_parse INTEGER,
    file_inode  INTEGER,
    file_device INTEGER,
    file_hash   TEXT,
    local_modified_at TEXT,
    transcript_revision TEXT NOT NULL DEFAULT '0',
    parent_session_id TEXT,
    relationship_type TEXT NOT NULL DEFAULT '',
    total_output_tokens INTEGER NOT NULL DEFAULT 0,
    peak_context_tokens INTEGER NOT NULL DEFAULT 0,
    has_total_output_tokens INTEGER NOT NULL DEFAULT 0,
    has_peak_context_tokens INTEGER NOT NULL DEFAULT 0,
    is_automated INTEGER NOT NULL DEFAULT 0,
    tool_failure_signal_count INTEGER NOT NULL DEFAULT 0,
    tool_retry_count INTEGER NOT NULL DEFAULT 0,
    edit_churn_count INTEGER NOT NULL DEFAULT 0,
    consecutive_failure_max INTEGER NOT NULL DEFAULT 0,
    outcome TEXT NOT NULL DEFAULT 'unknown',
    outcome_confidence TEXT NOT NULL DEFAULT 'low',
    ended_with_role TEXT NOT NULL DEFAULT '',
    final_failure_streak INTEGER NOT NULL DEFAULT 0,
    signals_pending_since TEXT,
    compaction_count INTEGER NOT NULL DEFAULT 0,
    mid_task_compaction_count INTEGER NOT NULL DEFAULT 0,
    context_pressure_max REAL,
    health_score INTEGER,
    health_grade TEXT,
    has_tool_calls INTEGER NOT NULL DEFAULT 0,
    has_context_data INTEGER NOT NULL DEFAULT 0,
    quality_signal_version INTEGER NOT NULL DEFAULT 0,
    short_prompt_count INTEGER NOT NULL DEFAULT 0,
    unstructured_start INTEGER NOT NULL DEFAULT 0,
    missing_success_criteria_count INTEGER NOT NULL DEFAULT 0,
    missing_verification_count INTEGER NOT NULL DEFAULT 0,
    duplicate_prompt_count INTEGER NOT NULL DEFAULT 0,
    no_code_context_count INTEGER NOT NULL DEFAULT 0,
    runaway_tool_loop_count INTEGER NOT NULL DEFAULT 0,
    data_version INTEGER NOT NULL DEFAULT 0,
    cwd TEXT NOT NULL DEFAULT '',
    git_branch TEXT NOT NULL DEFAULT '',
    source_session_id TEXT NOT NULL DEFAULT '',
    source_version TEXT NOT NULL DEFAULT '',
    transcript_fidelity TEXT NOT NULL DEFAULT '',
    parser_malformed_lines INTEGER NOT NULL DEFAULT 0,
    is_truncated INTEGER NOT NULL DEFAULT 0,
    -- SQLite-only sync bookkeeping (like next_ordinal): TRUE when the
    -- last write to this row went through the incremental-append path
    -- rather than a full re-normalization. Consumed only by parse-diff
    -- to suppress benign incremental-vs-full skew; deliberately not
    -- mirrored to PG/DuckDB.
    last_write_incremental INTEGER NOT NULL DEFAULT 0,
    deleted_at  TEXT,
    -- NULL remains the established user-trash representation; source_missing
    -- is recoverable when the file reappears.
    deletion_cause TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    termination_status TEXT,
    secret_leak_count INTEGER NOT NULL DEFAULT 0,
    secrets_rules_version TEXT NOT NULL DEFAULT '',
    sync_marker TEXT
);
CREATE TABLE messages (
    id             INTEGER PRIMARY KEY,
    session_id     TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    ordinal        INTEGER NOT NULL,
    role           TEXT NOT NULL,
    content        TEXT NOT NULL,
    thinking_text  TEXT NOT NULL DEFAULT '',
    timestamp      TEXT,
    has_thinking   INTEGER NOT NULL DEFAULT 0,
    has_tool_use   INTEGER NOT NULL DEFAULT 0,
    content_length INTEGER NOT NULL DEFAULT 0,
    is_system      INTEGER NOT NULL DEFAULT 0,
    model TEXT NOT NULL DEFAULT '',
    token_usage TEXT NOT NULL DEFAULT '',
    context_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    has_context_tokens INTEGER NOT NULL DEFAULT 0,
    has_output_tokens INTEGER NOT NULL DEFAULT 0,
    claude_message_id TEXT NOT NULL DEFAULT '',
    claude_request_id TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL DEFAULT '',
    source_subtype TEXT NOT NULL DEFAULT '',
    source_uuid TEXT NOT NULL DEFAULT '',
    source_parent_uuid TEXT NOT NULL DEFAULT '',
    is_sidechain INTEGER NOT NULL DEFAULT 0,
    is_compact_boundary INTEGER NOT NULL DEFAULT 0,
    UNIQUE(session_id, ordinal)
);
CREATE TABLE tool_calls (
    id         INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL
        REFERENCES messages(id) ON DELETE CASCADE,
    session_id TEXT NOT NULL
        REFERENCES sessions(id) ON DELETE CASCADE,
    tool_name  TEXT NOT NULL,
    category   TEXT NOT NULL,
    tool_use_id TEXT,
    input_json  TEXT,
    skill_name  TEXT,
    result_content_length INTEGER,
    result_content        TEXT,
    subagent_session_id TEXT,
    file_path  TEXT,
    call_index INTEGER
);
CREATE TABLE tool_result_events (
    id                       INTEGER PRIMARY KEY,
    session_id               TEXT NOT NULL
        REFERENCES sessions(id) ON DELETE CASCADE,
    tool_call_message_ordinal INTEGER NOT NULL,
    call_index               INTEGER NOT NULL DEFAULT 0,
    tool_use_id              TEXT,
    agent_id                 TEXT,
    subagent_session_id      TEXT,
    source                   TEXT NOT NULL,
    status                   TEXT NOT NULL,
    content                  TEXT NOT NULL,
    content_length           INTEGER NOT NULL DEFAULT 0,
    timestamp                TEXT,
    event_index              INTEGER NOT NULL DEFAULT 0
);
