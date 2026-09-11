from sqlalchemy import inspect, text


V6_COLUMNS = {
    "analyses": {"run_id": "VARCHAR(32)", "strategy_version": "VARCHAR(40)", "strategy_config_hash": "VARCHAR(64)",
        "ai_status": "VARCHAR(32)", "ai_provider": "VARCHAR(32)", "ai_model": "VARCHAR(80)", "ai_result": "JSON"},
    "watchlist": {"price": "NUMERIC(18,4)", "setup_quality": "INTEGER", "trend": "VARCHAR(24)",
        "structure": "VARCHAR(24)", "support": "NUMERIC(18,4)", "resistance": "NUMERIC(18,4)",
        "rr": "NUMERIC(10,4)", "run_id": "VARCHAR(32)"},
    "positions": {"run_id": "VARCHAR(32)", "strategy_version": "VARCHAR(40)"},
    "orders": {"run_id": "VARCHAR(32)", "strategy_version": "VARCHAR(40)",
        "strategy_config_hash": "VARCHAR(64)", "risk_amount": "NUMERIC(18,4)"},
    "trades": {"run_id": "VARCHAR(32)", "strategy_version": "VARCHAR(40)"},
    "decision_logs": {"run_id": "VARCHAR(32)", "strategy_version": "VARCHAR(40)", "strategy_config_hash": "VARCHAR(64)"},
    "portfolio_snapshots": {"run_id": "VARCHAR(32)"},
    "scan_runs": {"run_id": "VARCHAR(32)", "timeframe": "VARCHAR(8) DEFAULT '15m' NOT NULL",
        "closed_candle_timestamp": "TIMESTAMP", "watchlist_count": "INTEGER DEFAULT 0 NOT NULL",
        "signals": "INTEGER DEFAULT 0 NOT NULL", "entries": "INTEGER DEFAULT 0 NOT NULL"},
}


def ensure_schema_compatibility(engine):
    inspector=inspect(engine);tables=set(inspector.get_table_names())
    with engine.begin() as connection:
        for table,columns in V6_COLUMNS.items():
            if table not in tables:continue
            existing={column["name"] for column in inspector.get_columns(table)}
            for name,declaration in columns.items():
                if name not in existing:
                    connection.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {declaration}'))
