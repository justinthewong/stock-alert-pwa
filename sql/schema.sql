CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker VARCHAR(6) NOT NULL,
    side VARCHAR(4) NOT NULL,
    share_count INTEGER NOT NULL,
    target_price FLOAT NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'active',
    last_checked_at DATETIME,
    triggered_at DATETIME,
    last_depth_json TEXT,
    created_at DATETIME NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_alerts_ticker ON alerts (ticker);
CREATE INDEX IF NOT EXISTS ix_alerts_status ON alerts (status);

CREATE TABLE IF NOT EXISTS push_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint TEXT NOT NULL UNIQUE,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    user_agent TEXT,
    created_at DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS alert_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id INTEGER,
    event VARCHAR(64) NOT NULL,
    detail TEXT,
    created_at DATETIME NOT NULL,
    FOREIGN KEY(alert_id) REFERENCES alerts (id) ON DELETE CASCADE
);
