PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    event_schema_version TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    close_ts INTEGER NOT NULL,
    thresholds_version TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL,
    market_snapshot_json TEXT NOT NULL,
    raw_refs_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_symbol_close_ts
    ON events(symbol, close_ts);

CREATE INDEX IF NOT EXISTS idx_events_trigger_type
    ON events(trigger_type);

CREATE TABLE IF NOT EXISTS analysis_runs (
    id INTEGER PRIMARY KEY,
    event_id TEXT NOT NULL,
    rerun_of INTEGER,
    started_at INTEGER NOT NULL,
    finished_at INTEGER,
    prompt_version TEXT NOT NULL,
    agent_versions_json TEXT NOT NULL,
    thresholds_version TEXT NOT NULL,
    risk_rules_version TEXT NOT NULL,
    status TEXT NOT NULL,
    error_code TEXT,
    error_message TEXT,
    cost_usd REAL,
    FOREIGN KEY(event_id) REFERENCES events(event_id),
    FOREIGN KEY(rerun_of) REFERENCES analysis_runs(id)
);

CREATE INDEX IF NOT EXISTS idx_analysis_runs_event_id
    ON analysis_runs(event_id);

CREATE TABLE IF NOT EXISTS risk_checks (
    id INTEGER PRIMARY KEY,
    analysis_run_id INTEGER NOT NULL,
    risk_rules_version TEXT NOT NULL,
    account_equity REAL NOT NULL,
    max_loss_pct REAL NOT NULL,
    max_loss_amount REAL NOT NULL,
    leverage_cap REAL NOT NULL,
    stop_distance REAL NOT NULL,
    suggested_position_size REAL NOT NULL,
    margin_mode TEXT NOT NULL,
    liq_safety_margin REAL,
    daily_loss_state TEXT NOT NULL,
    consecutive_loss_state TEXT NOT NULL,
    input_json TEXT NOT NULL,
    output_json TEXT NOT NULL,
    verdict TEXT NOT NULL,
    FOREIGN KEY(analysis_run_id) REFERENCES analysis_runs(id)
);

CREATE INDEX IF NOT EXISTS idx_risk_checks_run_id
    ON risk_checks(analysis_run_id);

CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY,
    analysis_run_id INTEGER NOT NULL,
    ticket_id TEXT UNIQUE NOT NULL,
    status TEXT NOT NULL,
    action TEXT NOT NULL,
    self_reported_confidence TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY(analysis_run_id) REFERENCES analysis_runs(id)
);

CREATE INDEX IF NOT EXISTS idx_tickets_created_at
    ON tickets(created_at);

CREATE TABLE IF NOT EXISTS manual_decisions (
    id INTEGER PRIMARY KEY,
    ticket_id INTEGER NOT NULL,
    decision TEXT NOT NULL,
    note TEXT,
    decided_at INTEGER NOT NULL,
    FOREIGN KEY(ticket_id) REFERENCES tickets(id)
);

CREATE TABLE IF NOT EXISTS trade_reviews (
    id INTEGER PRIMARY KEY,
    ticket_id INTEGER NOT NULL,
    entry REAL,
    exit REAL,
    pnl REAL,
    note TEXT,
    reviewed_at INTEGER NOT NULL,
    FOREIGN KEY(ticket_id) REFERENCES tickets(id)
);

CREATE TABLE IF NOT EXISTS quick_lookups (
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    requested_at INTEGER NOT NULL,
    prompt_version TEXT NOT NULL,
    model TEXT NOT NULL,
    response_summary TEXT NOT NULL,
    cost_usd REAL
);
