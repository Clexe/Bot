SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS signals (
    id SERIAL PRIMARY KEY,
    pair VARCHAR(20) NOT NULL,
    direction VARCHAR(10) NOT NULL,
    setup_type VARCHAR(20) NOT NULL,
    entry_price DOUBLE PRECISION,
    sl_price DOUBLE PRECISION,
    tp_price DOUBLE PRECISION,
    rr_ratio DOUBLE PRECISION,
    bias VARCHAR(20),
    session VARCHAR(20),
    confluences TEXT,
    status VARCHAR(20) DEFAULT 'open',
    result VARCHAR(20),
    created_at TIMESTAMP DEFAULT NOW(),
    closed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS daily_stats (
    id SERIAL PRIMARY KEY,
    date DATE DEFAULT CURRENT_DATE UNIQUE,
    signals_sent INTEGER DEFAULT 0,
    wins INTEGER DEFAULT 0,
    losses INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bot_settings (
    key VARCHAR(50) PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS errors (
    id SERIAL PRIMARY KEY,
    error_type VARCHAR(50),
    error_message TEXT,
    pair VARCHAR(20),
    created_at TIMESTAMP DEFAULT NOW()
);
"""


async def init_schema(db):
    await db.execute(SCHEMA_SQL)
