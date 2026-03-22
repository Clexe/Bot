SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    telegram_chat_id BIGINT UNIQUE NOT NULL,
    tier VARCHAR(20) DEFAULT 'free',
    paystack_customer_id VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW(),
    is_active BOOLEAN DEFAULT true
);

CREATE TABLE IF NOT EXISTS signals (
    id SERIAL PRIMARY KEY,
    signal_type VARCHAR(20) NOT NULL,
    pair VARCHAR(20) NOT NULL,
    direction VARCHAR(10) NOT NULL,
    entry DECIMAL(20,5),
    sl DECIMAL(20,5),
    tp1 DECIMAL(20,5),
    tp2 DECIMAL(20,5),
    tp3 DECIMAL(20,5),
    rr_tp1 DECIMAL(5,2),
    rr_tp2 DECIMAL(5,2),
    rr_tp3 DECIMAL(5,2),
    score INTEGER,
    max_score INTEGER,
    cot_bias VARCHAR(10),
    cot_percentile INTEGER,
    wyckoff_phase VARCHAR(10),
    htf_bias VARCHAR(10),
    poi_type VARCHAR(30),
    poi_price DECIMAL(20,5),
    poi_touch_count INTEGER DEFAULT 0,
    judas_swing BOOLEAN DEFAULT false,
    kill_zone VARCHAR(30),
    mss_confirmed BOOLEAN DEFAULT false,
    volume_profile_confluence BOOLEAN DEFAULT false,
    rationale TEXT,
    is_manual BOOLEAN DEFAULT false,
    sent_at TIMESTAMP DEFAULT NOW(),
    outcome VARCHAR(20),
    outcome_recorded_at TIMESTAMP,
    final_rr_achieved DECIMAL(5,2),
    pips_result DECIMAL(10,2),
    trade_duration_minutes INTEGER
);

CREATE TABLE IF NOT EXISTS oc_levels (
    id SERIAL PRIMARY KEY,
    pair VARCHAR(20) NOT NULL,
    timeframe VARCHAR(10) NOT NULL,
    level_price DECIMAL(20,5) NOT NULL,
    level_type VARCHAR(20) NOT NULL,
    freshness_status VARCHAR(20) DEFAULT 'fresh',
    touch_count INTEGER DEFAULT 0,
    first_detected_at TIMESTAMP DEFAULT NOW(),
    last_tested_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS order_blocks (
    id SERIAL PRIMARY KEY,
    pair VARCHAR(20) NOT NULL,
    timeframe VARCHAR(10) NOT NULL,
    ob_type VARCHAR(20) NOT NULL,
    ob_high DECIMAL(20,5),
    ob_low DECIMAL(20,5),
    ob_midpoint DECIMAL(20,5),
    validity_status VARCHAR(20) DEFAULT 'valid',
    touch_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS storylines (
    id SERIAL PRIMARY KEY,
    pair VARCHAR(20) NOT NULL,
    direction VARCHAR(10) NOT NULL,
    origin_level DECIMAL(20,5),
    target_level DECIMAL(20,5),
    dol_target DECIMAL(20,5),
    wyckoff_phase VARCHAR(10),
    cot_bias VARCHAR(10),
    confirmed_at TIMESTAMP DEFAULT NOW(),
    invalidated_at TIMESTAMP,
    is_active BOOLEAN DEFAULT true
);

CREATE TABLE IF NOT EXISTS rejected_setups (
    id SERIAL PRIMARY KEY,
    engine_type VARCHAR(20),
    pair VARCHAR(20),
    direction VARCHAR(10),
    score INTEGER,
    gate_failed INTEGER,
    rejection_reason TEXT,
    detected_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS delivery_queue (
    id SERIAL PRIMARY KEY,
    signal_id INTEGER REFERENCES signals(id),
    chat_id BIGINT NOT NULL,
    message TEXT NOT NULL,
    deliver_at TIMESTAMP NOT NULL,
    delivered BOOLEAN DEFAULT false,
    delivered_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS admin_logs (
    id SERIAL PRIMARY KEY,
    admin_chat_id BIGINT NOT NULL,
    command TEXT NOT NULL,
    parameters TEXT,
    executed_at TIMESTAMP DEFAULT NOW(),
    result TEXT
);

CREATE TABLE IF NOT EXISTS bot_settings (
    key VARCHAR(50) PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS errors (
    id SERIAL PRIMARY KEY,
    error_type VARCHAR(50),
    error_message TEXT,
    pair VARCHAR(20),
    logged_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS cot_cache (
    id SERIAL PRIMARY KEY,
    pair VARCHAR(20) NOT NULL,
    bias VARCHAR(10) NOT NULL,
    percentile INTEGER NOT NULL,
    commercial_net INTEGER,
    lookback_weeks INTEGER DEFAULT 32,
    cached_at TIMESTAMP DEFAULT NOW(),
    valid_until TIMESTAMP
);

INSERT INTO bot_settings (key, value) VALUES
    ('min_precision_score', '10'),
    ('min_flow_score', '6'),
    ('bot_paused', 'false'),
    ('paused_pairs', ''),
    ('cot_lookback_weeks', '32'),
    ('min_precision_rr', '3.0'),
    ('min_flow_rr', '2.0'),
    ('max_risk_percent', '1.0'),
    ('flow_signals_enabled', 'true'),
    ('precision_signals_enabled', 'true'),
    ('duplicate_prevention_hours', '4')
ON CONFLICT (key) DO NOTHING;
"""


async def initialize_schema(db):
    """Create all required tables and seed default settings."""
    await db.execute(SCHEMA_SQL)
