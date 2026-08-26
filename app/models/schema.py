"""
Compounder Intelligence Dashboard — Database Models

Defines all persistent tables using pure SQL DDL. Designed for SQLite
but written in a portable way. Tables use explicit primary keys and
clear foreign key relationships.

Design principles:
- Append-only scoring and observation records (never overwrite)
- Idempotent inserts (UNIQUE constraints prevent duplicates)
- Explicit indexes on query paths
- Versioned calculations
"""

# Schema version tracking - increment when making breaking changes
SCHEMA_VERSION = "v1.0"

# All CREATE TABLE statements in execution order (FK deps)
CREATE_TABLES_SQL = [
    """
    CREATE TABLE IF NOT EXISTS companies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT UNIQUE NOT NULL COLLATE NOCASE,
        name TEXT NOT NULL,
        cik TEXT UNIQUE,
        nse_symbol TEXT,
        bse_scrip_code TEXT,
        sector TEXT DEFAULT 'UNKNOWN',
        country TEXT DEFAULT 'US',
        company_type TEXT DEFAULT 'INDUSTRIAL' CHECK(company_type IN ('INDUSTRIAL','TECH','FINANCIAL','HEALTHCARE','CONSUMER','ENERGY','UTILITIES','OTHER')),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS fiscal_periods (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL REFERENCES companies(id),
        fiscal_year INTEGER NOT NULL,
        fiscal_quarter TEXT DEFAULT 'A' CHECK(fiscal_quarter IN ('Q1','Q2','Q3','Q4','H1','H2','A','FY')),
        report_date TEXT NOT NULL,           -- YYYY-MM-DD when financials were reported
        filing_date TEXT,                    -- YYYY-MM-DD when filing was made
        form TEXT DEFAULT '10-K',            -- 10-K/10-Q/8-K/etc
        accession_number TEXT,               -- SEC accession number if applicable
        source_filing_id TEXT,               -- Internal filing identifier
        period_label TEXT,                   -- Human readable: e.g., "FY2024" or "Q3 FY2025"
        UNIQUE(company_id, report_date, fiscal_quarter, source_filing_id)
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS metric_observations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL REFERENCES companies(id),
        period_id INTEGER NOT NULL REFERENCES fiscal_periods(id),
        metric_key TEXT NOT NULL,
        
        value REAL NOT NULL,
        unit TEXT DEFAULT 'raw',             -- raw/dollar_amount/percent/ratio/per_share
        scale TEXT DEFAULT 'USD',            -- USD/EUR/INR/etc
        
        source_filing_id TEXT NOT NULL,
        accession_number TEXT,
        filing_form TEXT,
        
        reported_or_derived TEXT NOT NULL CHECK(reported_or_derived IN ('REPORTED','DERIVED')),
        certainty TEXT NOT NULL CHECK(certainty IN ('HIGH','MEDIUM','LOW','DERIVED','INFERENCE')),
        
        evidence_excerpt TEXT,
        provenance_section TEXT,
        
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        
        UNIQUE(company_id, period_id, metric_key, source_filing_id)
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS calculated_metrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL REFERENCES companies(id),
        period_id INTEGER NOT NULL REFERENCES fiscal_periods(id),
        metric_key TEXT NOT NULL,
        
        value REAL NOT NULL,
        
        formula TEXT NOT NULL,                -- How was this calculated?
        inputs_json TEXT,                     -- JSON of input values used
        confidence TEXT DEFAULT 'MEDIUM',     -- HIGH/MEDIUM/LOW
        
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        
        UNIQUE(company_id, period_id, metric_key)
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS compounder_scores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL REFERENCES companies(id),
        period_id INTEGER NOT NULL REFERENCES fiscal_periods(id),
        
        version TEXT NOT NULL DEFAULT 'v1.0',
        
        revenue_growth_score REAL NOT NULL,
        roic_score REAL NOT NULL,
        margin_expansion_score REAL NOT NULL,
        reinvestment_runway_score REAL NOT NULL,
        
        weighted_contribution_json TEXT NOT NULL,  -- JSON: {"revenue": 65*0.25, ...}
        
        compounder_score REAL NOT NULL,
        classification TEXT,
        
        scoring_methodology_notes TEXT,
        
        data_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        
        UNIQUE(company_id, period_id, version)
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS thesis_statements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL REFERENCES companies(id),
        period_id INTEGER NOT NULL REFERENCES fiscal_periods(id),
        
        version INTEGER DEFAULT 1,
        
        core_thesis TEXT NOT NULL,              -- Why invest? Thesis statement.
        supporting_factors TEXT NOT NULL,       -- JSON array of factors
        risk_factors TEXT NOT NULL,             -- JSON array of risks
        thesis_breaking_conditions TEXT NOT NULL, -- JSON array of conditions
        
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS thesis_momentum_scores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL REFERENCES companies(id),
        period_id INTEGER NOT NULL REFERENCES fiscal_periods(id),
        prev_period_id INTEGER,                 -- Previous quarter for comparison
        
        momentum_score REAL NOT NULL,           -- 0-100
        
        classification TEXT NOT NULL,
        
        trend_data TEXT NOT NULL,               -- JSON: detailed trends per dimension
        compared_to_previous_real TIMESTAMP NOT NULL,
        
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS valuation_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL REFERENCES companies(id),
        period_id INTEGER NOT NULL REFERENCES fiscal_periods(id),
        
        market_cap REAL,
        enterprise_value REAL,
        pe_ratio REAL,
        ev_ebitda REAL,
        ps_ratio REAL,
        fcff_yield REAL,
        price_per_share REAL,
        calculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL REFERENCES companies(id),
        
        alert_level TEXT NOT NULL CHECK(alert_level IN ('INFO','WATCH','IMPORTANT','CRITICAL')),
        alert_type TEXT NOT NULL,
        message TEXT NOT NULL,
        triggered_by TEXT,
        
        acknowledged INTEGER DEFAULT 0,
        triggered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS data_quality_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER REFERENCES companies(id),
        period_id INTEGER REFERENCES fiscal_periods(id),
        metric_key TEXT,
        
        quality_issue TEXT NOT NULL,         -- missing_data/conflicting_sources/restated/no_data/one_off_item
        severity TEXT DEFAULT 'MEDIUM',      -- HIGH/MEDIUM/LOW
        
        notes TEXT,
        logged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS scoring_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        
        config_name TEXT UNIQUE NOT NULL,      -- e.g., 'compounder_weights', 'thresholds'
        config_version TEXT NOT NULL DEFAULT 'v1.0',
        config_data JSON NOT NULL,
        description TEXT,
        
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
]

# INDEXES (Part 23 of spec)
CREATE_INDEXES_SQL = [
    # Metric observations
    "CREATE INDEX IF NOT EXISTS idx_obs_company ON metric_observations(company_id)",
    "CREATE INDEX IF NOT EXISTS idx_obs_period ON metric_observations(period_id)",
    "CREATE INDEX IF NOT EXISTS idx_obs_metric ON metric_observations(metric_key)",
    "CREATE INDEX IF NOT EXISTS idx_obs_company_period ON metric_observations(company_id, period_id)",
    
    # Compounder scores
    "CREATE INDEX IF NOT EXISTS idx_comp_company ON compounder_scores(company_id)",
    "CREATE INDEX IF NOT EXISTS idx_comp_period ON compounder_scores(period_id)",
    "CREATE INDEX IF NOT EXISTS idx_comp_company_period ON compounder_scores(company_id, period_id)",
    "CREATE INDEX IF NOT EXISTS idx_comp_version ON compounder_scores(version)",
    
    # Calculated metrics
    "CREATE INDEX IF NOT EXISTS idx_calc_company ON calculated_metrics(company_id)",
    "CREATE INDEX IF NOT EXISTS idx_calc_period ON calculated_metrics(period_id)",
    "CREATE INDEX IF NOT EXISTS idx_calc_metric ON calculated_metrics(metric_key)",
    
    # Thesis
    "CREATE INDEX IF NOT EXISTS idx_thesis_company ON thesis_statements(company_id)",
    "CREATE INDEX IF NOT EXISTS idx_thesis_period ON thesis_statements(period_id)",
    "CREATE INDEX IF NOT EXISTS idx_thesis_companies ON thesis_statements(company_id, period_id)",
    "CREATE INDEX IF NOT EXISTS idx_mom_company ON thesis_momentum_scores(company_id)",
    "CREATE INDEX IF NOT EXISTS idx_mom_period ON thesis_momentum_scores(period_id)",
    "CREATE INDEX IF NOT EXISTS idx_mom_companies ON thesis_momentum_scores(company_id, period_id)",
    
    # Valuation
    "CREATE INDEX IF NOT EXISTS idx_val_company ON valuation_history(company_id)",
    "CREATE INDEX IF NOT EXISTS idx_val_period ON valuation_history(period_id)",
    "CREATE INDEX IF NOT EXISTS idx_val_companies ON valuation_history(company_id, period_id)",
    
    # Alerts
    "CREATE INDEX IF NOT EXISTS idx_alerts_company ON alerts(company_id)",
    "CREATE INDEX IF NOT EXISTS idx_alerts_ack ON alerts(acknowledged)",
    "CREATE INDEX IF NOT EXISTS idx_alerts_unacked ON alerts(acknowledged, triggered_at)",
    
    # Data quality
    "CREATE INDEX IF NOT EXISTS idx_dq_company ON data_quality_log(company_id)",
    "CREATE INDEX IF NOT EXISTS idx_dq_period ON data_quality_log(period_id)",
    
    # Companies
    "CREATE INDEX IF NOT EXISTS idx_companies_ticker ON companies(ticker)",
    "CREATE INDEX IF NOT EXISTS idx_companies_cik ON companies(cik)",
    "CREATE INDEX IF NOT EXISTS idx_companies_nse ON companies(nse_symbol)",
]

# Foreign key support is enabled at connection time
FOREIGN_KEY_PRAGMA = "PRAGMA foreign_keys = ON"


def get_create_sql():
    """Return all DDL as a single string, one statement per semicolon."""
    return ";\n\n".join(s.strip() for s in CREATE_TABLES_SQL + CREATE_INDEXES_SQL) + ";"
