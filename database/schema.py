SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT PRIMARY KEY,
    settings JSONB DEFAULT '{}'::jsonb,
    is_active BOOLEAN DEFAULT TRUE,
    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS signal_history (
    id SERIAL PRIMARY KEY,
    pair VARCHAR(20) NOT NULL,
    direction VARCHAR(4) NOT NULL,
    mode VARCHAR(10) NOT NULL,
    entry_price DOUBLE PRECISION NOT NULL,
    tp_price DOUBLE PRECISION NOT NULL,
    sl_price DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    outcome VARCHAR(10) DEFAULT 'OPEN',
    close_price DOUBLE PRECISION,
    closed_at TIMESTAMP,
    pnl_pips DOUBLE PRECISION DEFAULT 0,
    tp_stage INTEGER DEFAULT 0,
    zone_type VARCHAR(10) DEFAULT '',
    regime VARCHAR(20) DEFAULT '',
    confidence VARCHAR(10) DEFAULT 'medium'
);
CREATE TABLE IF NOT EXISTS sent_signals (
    signal_key VARCHAR(100) PRIMARY KEY,
    price DOUBLE PRECISION NOT NULL,
    direction VARCHAR(4) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS signals (
    id SERIAL PRIMARY KEY,
    pair VARCHAR(20) NOT NULL,
    direction VARCHAR(10) NOT NULL,
    entry DECIMAL(20,5), sl DECIMAL(20,5), tp1 DECIMAL(20,5), tp2 DECIMAL(20,5), tp3 DECIMAL(20,5),
    rr_tp1 DECIMAL(5,2), rr_tp2 DECIMAL(5,2), rr_tp3 DECIMAL(5,2), score INTEGER,
    htf_bias VARCHAR(10), poi_type VARCHAR(30), poi_price DECIMAL(20,5), kill_zone VARCHAR(30), rationale TEXT,
    is_manual BOOLEAN DEFAULT false, sent_at TIMESTAMP DEFAULT NOW(), outcome VARCHAR(20), outcome_recorded_at TIMESTAMP,
    final_rr DECIMAL(5,2), pips_result DECIMAL(10,2), trade_duration_minutes INTEGER
);
CREATE TABLE IF NOT EXISTS oc_levels (
    id SERIAL PRIMARY KEY, pair VARCHAR(20) NOT NULL, timeframe VARCHAR(10) NOT NULL,
    level_price DECIMAL(20,5) NOT NULL, level_type VARCHAR(20) NOT NULL,
    freshness_status VARCHAR(20) DEFAULT 'fresh', touch_count INTEGER DEFAULT 0,
    first_detected_at TIMESTAMP DEFAULT NOW(), last_tested_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS order_blocks (
    id SERIAL PRIMARY KEY, pair VARCHAR(20) NOT NULL, timeframe VARCHAR(10) NOT NULL,
    ob_type VARCHAR(20) NOT NULL, ob_high DECIMAL(20,5), ob_low DECIMAL(20,5), ob_midpoint DECIMAL(20,5),
    validity_status VARCHAR(20) DEFAULT 'valid', touch_count INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS storylines (
    id SERIAL PRIMARY KEY, pair VARCHAR(20) NOT NULL, direction VARCHAR(10) NOT NULL,
    origin_level DECIMAL(20,5), target_level DECIMAL(20,5), dol_target DECIMAL(20,5),
    confirmed_at TIMESTAMP DEFAULT NOW(), invalidated_at TIMESTAMP, is_active BOOLEAN DEFAULT true
);
CREATE TABLE IF NOT EXISTS rejected_setups (
    id SERIAL PRIMARY KEY, pair VARCHAR(20), direction VARCHAR(10), score INTEGER, rejection_reason TEXT, detected_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS delivery_queue (
    id SERIAL PRIMARY KEY, signal_id INTEGER REFERENCES signals(id), chat_id BIGINT NOT NULL,
    message TEXT NOT NULL, deliver_at TIMESTAMP NOT NULL, delivered BOOLEAN DEFAULT false, delivered_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS admin_logs (
    id SERIAL PRIMARY KEY, admin_chat_id BIGINT NOT NULL, command TEXT NOT NULL, parameters TEXT, executed_at TIMESTAMP DEFAULT NOW(), result TEXT
);
CREATE TABLE IF NOT EXISTS bot_settings (
    key VARCHAR(50) PRIMARY KEY, value TEXT NOT NULL, updated_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS errors (
    id SERIAL PRIMARY KEY, error_type VARCHAR(50), error_message TEXT, pair VARCHAR(20), logged_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_signal_history_pair ON signal_history(pair, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_signal_history_outcome ON signal_history(outcome);
INSERT INTO bot_settings (key, value) VALUES
('min_signal_score', '7'), ('bot_paused', 'false'), ('paused_pairs', '')
ON CONFLICT (key) DO NOTHING;
"""

async def initialize_schema(db):
    """Create all required tables and seed default settings."""
    await db.execute(SCHEMA_SQL)
