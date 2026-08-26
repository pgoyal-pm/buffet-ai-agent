"""
Compounder Intelligence Dashboard — Database Manager

Production-grade SQLite database manager for the Compounder Dashboard.
Handles:
- Connection pooling (via synchronous wrapper)
- Migration tracking
- Schema versioning
- Safe data operations

Design principles:
- All operations are idempotent (safe to run multiple times)
- Append-only pattern enforced via UNIQUE constraints at table level
- Foreign keys enabled by default
- Migrations tracked in a dedicated table
"""

import json
import sqlite3
import threading
from pathlib import Path
from typing import Optional, Any, Dict, List
from datetime import datetime


class DatabaseManager:
    """
    Central database manager for the Compounder Dashboard.
    
    Single instance per application lifecycle. Thread-safe via connection-per-thread
    model using threading.local().
    """
    
    def __init__(self, db_path: str):
        self._db_path = db_path
        self._local = threading.local()
        self._schema_version = "v1.0"
        
        # Ensure directory exists
        db_dir = Path(db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)
    
    @property
    def path(self) -> str:
        return self._db_path
    
    def _get_conn(self) -> sqlite3.Connection:
        """Get thread-local connection."""
        if not hasattr(self._local, 'conn'):
            self._local.conn = sqlite3.connect(
                self._db_path,
                detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES
            )
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA foreign_keys = ON")
            self._local.conn.execute("PRAGMA journal_mode = WAL")  # Better concurrency
            self._local.conn.execute("PRAGMA busy_timeout = 5000")
        return self._local.conn
    
    def close(self):
        """Close thread-local connection."""
        if hasattr(self._local, 'conn') and self._local.conn:
            self._local.conn.close()
            self._local.conn = None
    
    def initialize(self):
        """Alias for ensure_schema_up_to_date — creates tables on first run."""
        self.ensure_schema_up_to_date()
    
    def ensure_schema_up_to_date(self):
        """
        Create tables and indexes if they don't exist.
        This is safe to call multiple times.
        """
        from app.models.schema import get_create_sql
        
        conn = self._get_conn()
        conn.row_factory = sqlite3.Row
        
        try:
            # Track schema version
            conn.execute("""
                CREATE TABLE IF NOT EXISTS _schema_migrations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    migration_name TEXT UNIQUE NOT NULL,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Check if main schema already applied
            row = conn.execute("SELECT id FROM _schema_migrations WHERE migration_name = ?", 
                          ("main_schema",)).fetchone()
            
            if not row:
                conn.executescript(get_create_sql())
                
                # Mark migration as applied
                conn.execute(
                    "INSERT INTO _schema_migrations(migration_name) VALUES (?)",
                    ("main_schema",)
                )
                
                conn.commit()
                
            # Re-enable foreign keys (executescript issues implicit COMMIT which resets pragmas)
            conn.execute("PRAGMA foreign_keys = ON")
                
        except Exception as e:
            conn.rollback()
            raise RuntimeError(f"Schema migration failed: {e}")
    
    # ==================== RAW QUERY HELPERS ====================
    
    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute a SQL statement and return cursor."""
        conn = self._get_conn()
        cursor = conn.execute(sql, params)
        return cursor
    
    def executemany(self, sql: str, params_list: list) -> sqlite3.Cursor:
        """Execute a SQL statement with multiple parameter sets."""
        conn = self._get_conn()
        cursor = conn.executemany(sql, params_list)
        return cursor
    
    def fetchone(self, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        """Execute SELECT query and return first row as dict."""
        cursor = self.execute(sql, params)
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None
    
    def fetchall(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        """Execute SELECT query and return all rows as list of dicts."""
        cursor = self.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]
    
    def commit(self):
        """Commit current transaction."""
        conn = self._get_conn()
        conn.commit()
    
    # ==================== COMPANY OPERATIONS ====================
    
    def create_company(self, ticker: str, name: str, cik: Optional[str] = None,
                       nse_symbol: Optional[str] = None, sector: str = "UNKNOWN",
                       country: str = "US", company_type: str = "INDUSTRIAL") -> int:
        """
        Insert a new company. Returns company_id.
        If company with same ticker exists, returns existing ID (idempotent).
        """
        conn = self._get_conn()
        cursor = conn.execute(
            """INSERT OR IGNORE INTO companies(ticker, name, cik, nse_symbol, sector, country, company_type)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (ticker.upper(), name, cik, nse_symbol, sector, country, company_type)
        )
        conn.commit()
        
        # Get the ID
        cursor = conn.execute("SELECT id FROM companies WHERE ticker = ?", (ticker.upper(),))
        row = cursor.fetchone()
        return row['id'] if row else 0
    
    def get_company_by_ticker(self, ticker: str) -> Optional[Dict]:
        """Look up a company by ticker symbol."""
        return self.fetchone(
            "SELECT * FROM companies WHERE ticker = ?",
            (ticker.upper(),)
        )
    
    def get_company_by_cik(self, cik: str) -> Optional[Dict]:
        """Look up a company by CIK number."""
        return self.fetchone(
            "SELECT * FROM companies WHERE cik = ?",
            (cik,)
        )
    
    def update_company(self, company_id: int, **kwargs):
        """Update company fields dynamically."""
        allowed_fields = {'name', 'sector', 'country', 'company_type', 'nse_symbol'}
        updates = [f"{k} = ?" for k in kwargs if k in allowed_fields]
        values = [kwargs[k] for k in kwargs if k in allowed_fields]
        values.append(company_id)
        
        if updates:
            sql = f"UPDATE companies SET {', '.join(updates)}, updated_at=CURRENT_TIMESTAMP WHERE id = ?"
            self.execute(sql, tuple(values))
            self.commit()
    
    def list_companies(self) -> List[Dict]:
        """Return all registered companies."""
        return self.fetchall(
            "SELECT * FROM companies ORDER BY ticker"
        )
    
    def get_company_by_id(self, company_id: int) -> Optional[Dict]:
        """Get a company by its primary key ID."""
        return self.fetchone("SELECT * FROM companies WHERE id = ?", (company_id,))
    
    # ==================== PERIOD OPERATIONS ====================
    
    def create_fiscal_period(self, company_id: int, fiscal_year: int,
                            fiscal_quarter: str = "A", report_date: str = "",
                            filing_date: Optional[str] = None, form: str = "10-K",
                            accession_number: Optional[str] = None,
                            source_filing_id: Optional[str] = None,
                            period_label: Optional[str] = None) -> int:
        """Insert a fiscal period. Returns period_id."""
        conn = self._get_conn()
        
        # Generate period label if not provided
        if not period_label:
            if fiscal_quarter == "A":
                period_label = f"FY{fiscal_year}"
            else:
                period_label = f"{fiscal_quarter} FY{fiscal_year}"
        
        # Normalize None to empty string for consistent lookups
        src_id = source_filing_id or ""
        
        try:
            cursor = conn.execute("""
                INSERT OR IGNORE INTO fiscal_periods(
                    company_id, fiscal_year, fiscal_quarter, report_date, 
                    filing_date, form, accession_number, source_filing_id, period_label
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (company_id, fiscal_year, fiscal_quarter, report_date,
                  filing_date, form, accession_number, src_id, period_label))
            conn.commit()
        except Exception:
            pass  # Duplicate is fine
        
        # Get ID (handle both None and empty string as same)
        row = None
        cursor = conn.execute(
            "SELECT id FROM fiscal_periods WHERE company_id = ? AND report_date = ? AND COALESCE(source_filing_id,'') = ?",
            (company_id, report_date, src_id)
        )
        row = cursor.fetchone()
        return row['id'] if row else 0
    
    def get_periods_for_company(self, company_id: int) -> List[Dict]:
        """Return all periods for a company."""
        return self.fetchall(
            """SELECT fp.*, c.ticker, c.name 
               FROM fiscal_periods fp 
               JOIN companies c ON c.id = fp.company_id
               WHERE fp.company_id = ?
               ORDER BY fp.report_date DESC""",
            (company_id,)
        )
    
    def get_latest_period_for_company(self, company_id: int) -> Optional[Dict]:
        """Get most recent period for a company."""
        return self.fetchone(
            """SELECT fp.* FROM fiscal_periods fp 
               WHERE fp.company_id = ? 
               ORDER BY fp.report_date DESC LIMIT 1""",
            (company_id,)
        )
    
    # ==================== METRIC OBSERVATIONS ====================
    
    def upsert_metric_observation(self, company_id: int, period_id: int,
                                   metric_key: str, value: float,
                                   unit: str = "raw", scale: str = "USD",
                                   source_filing_id: str = "", accession_number: Optional[str] = None,
                                   filing_form: Optional[str] = None,
                                   reported_or_derived: str = "REPORTED",
                                   certainty: str = "HIGH",
                                   evidence_excerpt: Optional[str] = None,
                                   provenance_section: Optional[str] = None) -> bool:
        """
        Idempotently insert or update a metric observation.
        Returns True if inserted, False if already existed.
        
        Unique constraint prevents duplicates:
        UNIQUE(company_id, period_id, metric_key, source_filing_id)
        """
        try:
            self.execute("""
                INSERT INTO metric_observations(
                    company_id, period_id, metric_key, value, unit, scale,
                    source_filing_id, accession_number, filing_form,
                    reported_or_derived, certainty, evidence_excerpt, provenance_section
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (company_id, period_id, metric_key, value, unit, scale,
                  source_filing_id, accession_number, filing_form,
                  reported_or_derived, certainty, evidence_excerpt, provenance_section))
            self.commit()
            return True  # Inserted
        except sqlite3.IntegrityError:
            # Update existing record instead (e.g., after restatement)
            self.execute("""
                UPDATE metric_observations SET 
                    value = ?, unit = ?, scale = ?,
                    reported_or_derived = ?, certainty = ?,
                    evidence_excerpt = ?, provenance_section = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE company_id = ? AND period_id = ? AND metric_key = ? AND source_filing_id = ?
            """, (value, unit, scale, reported_or_derived, certainty,
                  evidence_excerpt, provenance_section,
                  company_id, period_id, metric_key, source_filing_id))
            self.commit()
            return False  # Updated existing
    
    def get_metrics_for_period(self, company_id: int, period_id: int) -> List[Dict]:
        """Get all metric observations for a specific period."""
        return self.fetchall("""
            SELECT mo.* FROM metric_observations mo
            WHERE mo.company_id = ? AND mo.period_id = ?
            ORDER BY mo.metric_key
        """, (company_id, period_id))
    
    def get_metrics_for_company(self, company_id: int, period_ids: List[int] = None) -> List[Dict]:
        """Get metrics across periods for a company."""
        if period_ids:
            placeholders = ','.join(['?' for _ in period_ids])
            return self.fetchall(f"""
                SELECT mo.* FROM metric_observations mo
                WHERE mo.company_id = ? AND mo.period_id IN ({placeholders})
                ORDER BY mo.period_id, mo.metric_key
            """, [company_id] + period_ids)
        else:
            return self.fetchall("""
                SELECT mo.* FROM metric_observations mo
                WHERE mo.company_id = ?
                ORDER BY mo.period_id, mo.metric_key
            """, (company_id,))
    
    def get_metric_history(self, company_id: int, metric_key: str, limit_period_id: Optional[int] = None) -> List[Dict]:
        """
        Get time series for a single metric across periods.
        
        Args:
            company_id: Company ID
            metric_key: Metric key name (e.g., 'revenue', 'net_income')
            limit_period_id: If set, only include periods with id <= this value.
                           This ensures scoring for period N only uses data through period N.
        """
        if limit_period_id:
            where_clause = "WHERE mo.company_id = ? AND mo.metric_key = ? AND fp.id <= ?"
            params = (company_id, metric_key, limit_period_id)
        else:
            where_clause = "WHERE mo.company_id = ? AND mo.metric_key = ?"
            params = (company_id, metric_key)
        
        return self.fetchall(f"""
            SELECT mo.id as observation_id, mo.metric_key, mo.value, mo.unit, mo.scale,
                   mo.reported_or_derived, mo.certainty, mo.provenance_section,
                   fp.fiscal_year, fp.fiscal_quarter, fp.report_date, fp.period_label,
                   fp.id as period_id
            FROM metric_observations mo
            JOIN fiscal_periods fp ON fp.id = mo.period_id
            {where_clause}
            ORDER BY fp.report_date DESC
        """, params)
    
    def get_metrics_for_all_keys(self, company_id: int, metric_keys: List[str], limit_period_id: Optional[int] = None) -> Dict[int, Dict[str, float]]:
        """
        Get multiple metrics across periods, grouped by period_id.
        
        Returns: {period_id: {metric_key: value}, ...}
        
        Used by ROIC, margin_expansion, reinvestment_runway engines.
        """
        if not metric_keys:
            return {}
        
        placeholders = ','.join(['?' for _ in metric_keys])
        if limit_period_id:
            period_filter = "AND fp.id <= ?"
            extra_param = (limit_period_id,)
        else:
            period_filter = ""
            extra_param = ()
        
        rows = self.fetchall(f"""
            SELECT mo.metric_key, mo.value, mo.reported_or_derived, mo.certainty,
                   mo.provenance_section, fp.fiscal_year, fp.fiscal_quarter, 
                   fp.report_date, fp.period_label, fp.id as period_id
            FROM metric_observations mo
            JOIN fiscal_periods fp ON fp.id = mo.period_id
            WHERE mo.company_id = ? AND mo.metric_key IN ({placeholders}) {period_filter}
            ORDER BY fp.report_date DESC
        """, [company_id] + metric_keys + list(extra_param))
        
        result: dict = {}
        for row in rows:
            pid = row['period_id']
            if pid not in result:
                result[pid] = {'fiscal_year': row.get('fiscal_year'), 'report_date': row.get('report_date'), 'period_label': row.get('period_label')}
            result[pid][row['metric_key']] = row.get('value')
        
        return result
    
    # ==================== SCORE CALCULATIONS ====================
    
    def upsert_compounder_score(self, company_id: int, period_id: int, version: str,
                                 revenue_score: float, roic_score: float,
                                 margin_score: float, runway_score: float,
                                 weighted_contribution_json: str, total_score: float,
                                 classification: Optional[str] = None,
                                 notes: Optional[str] = None,
                                 data_complete: Optional[bool] = None,
                                 data_incomplete_reasons: Optional[List[str]] = None) -> bool:
        """Insert compounder score. Upsert based on unique (company_id, period_id, version).
        
        Added data_complete flag and data_incomplete_reasons to track whether missing
        engine inputs prevented a valid score from being computed.
        """
        reasons_json = json.dumps(data_incomplete_reasons or [])
        conn = self._get_conn()
        try:
            cursor = conn.execute("""
                INSERT INTO compounder_scores(
                    company_id, period_id, version,
                    revenue_growth_score, roic_score, margin_expansion_score, reinvestment_runway_score,
                    weighted_contribution_json, compounder_score, classification, scoring_methodology_notes,
                    data_timestamp, data_complete, data_incomplete_reasons
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?)
            """, (company_id, period_id, version,
                  revenue_score, roic_score, margin_score, runway_score,
                  weighted_contribution_json, total_score, classification, notes,
                  str(int(data_complete)) if data_complete is not None else '',
                  reasons_json))
            conn.commit()
            return True  # Inserted
        except sqlite3.IntegrityError:
            conn.execute("""
                UPDATE compounder_scores SET
                    revenue_growth_score = COALESCE(?, revenue_growth_score),
                    roic_score = COALESCE(?, roic_score),
                    margin_expansion_score = COALESCE(?, margin_expansion_score),
                    reinvestment_runway_score = COALESCE(?, reinvestment_runway_score),
                    compounder_score = COALESCE(?, compounder_score),
                    classification = COALESCE(?, classification),
                    scoring_methodology_notes = COALESCE(?, scoring_methodology_notes),
                    data_timestamp = CURRENT_TIMESTAMP,
                    data_complete = COALESCE(?, data_complete),
                    data_incomplete_reasons = COALESCE(?, data_incomplete_reasons)
                WHERE company_id = ? AND period_id = ? AND version = ?
            """, (revenue_score, roic_score, margin_score, runway_score,
                  total_score, classification, notes,
                  str(int(data_complete)) if data_complete is not None else '',
                  reasons_json,
                  company_id, period_id, version))
            conn.commit()
            return False  # Updated existing
    
    def compute_dedup_hash(self, company_id: int, alert_type: str, message: str) -> str:
        """Compute a hash for alert deduplication."""
        return f"{company_id}|{alert_type or ''}|{message[:120] if message else ''}"
    
    def get_latest_scores_per_company(self, limit: int = 50) -> List[Dict]:
        """Get the LATEST VALID score for EACH company.
        
        Returns exactly one row per company — their most recently calculated score.
        Filters out DATA_INCOMPLETE scores so they don't appear in top compounds.
        
        Uses MAX(period_id) per company rather than MAX(id) because:
        - Scores may be inserted in any order (batch inserts often go reverse chron).
        - period_id from fiscal_periods is strictly ascending over time.
        - Therefore highest period_id = most recent fiscal period = "latest".
        """
        return self.fetchall("""
            SELECT cs.*, c.ticker, c.name, c.sector, fp.period_label,
                   cs.data_complete, cs.data_incomplete_reasons
            FROM compounder_scores cs
            JOIN companies c ON c.id = cs.company_id
            JOIN fiscal_periods fp ON fp.id = cs.period_id
            WHERE cs.period_id IN (
                SELECT MAX(cs2.period_id)
                FROM compounder_scores cs2
                GROUP BY cs2.company_id
            )
              AND cs.classification NOT IN ('DATA_INCOMPLETE', 'INSUFFICIENT_DATA')
            ORDER BY cs.compounder_score DESC
            LIMIT ?
        """, (limit,))
    
    def get_company_count_with_valid_scores(self) -> int:
        """Count DISTINCT companies that have at least one valid Compounder Score.
        
        Replaces 'scores_stored' which counted ALL rows including duplicates.
        Excludes DATA_INCOMPLETE / INSUFFICIENT_DATA classifications.
        """
        result = self.scalar("""
            SELECT COUNT(DISTINCT company_id) FROM compounder_scores
            WHERE classification NOT IN ('DATA_INCOMPLETE', 'INSUFFICIENT_DATA')
        """)
        return result if result else 0
    
    def list_all_compounder_scores(self) -> List[Dict]:
        """Return every raw compounder_score row for history view.
        
        This endpoint is intentionally separate from dashboard_latest.
        Use only when the client needs full historical drill-down.
        """
        return self.fetchall("""
            SELECT cs.*, c.ticker, c.name, c.sector, fp.period_label
            FROM compounder_scores cs
            JOIN companies c ON c.id = cs.company_id
            JOIN fiscal_periods fp ON fp.id = cs.period_id
            ORDER BY cs.data_timestamp DESC
            LIMIT 200
        """)
    
    def get_compounder_scores(self, company_id: int, limit: int = 50) -> List[Dict]:
        """Get all historical scores for a company."""
        return self.fetchall("""
            SELECT cs.*, fp.period_label, fp.report_date
            FROM compounder_scores cs
            JOIN fiscal_periods fp ON fp.id = cs.period_id
            WHERE cs.company_id = ?
            ORDER BY cs.data_timestamp DESC
            LIMIT ?
        """, (company_id, limit))
    
    def get_latest_compounder_score(self, company_id: int) -> Optional[Dict]:
        """Get the most recent score for a company."""
        return self.fetchone("""
            SELECT cs.*, fp.period_label, fp.report_date
            FROM compounder_scores cs
            JOIN fiscal_periods fp ON fp.id = cs.period_id
            WHERE cs.company_id = ?
            ORDER BY cs.data_timestamp DESC
            LIMIT 1
        """, (company_id,))
    
    # ==================== THESIS MANAGEMENT ====================
    
    def upsert_thesis_statement(self, company_id: int, period_id: int,
                                 core_thesis: str, supporting_factors: list,
                                 risk_factors: list, breaking_conditions: list,
                                 version: int = 1) -> int:
        """Insert or update thesis statement."""
        conn = self._get_conn()
        try:
            cursor = conn.execute("""
                INSERT OR REPLACE INTO thesis_statements(
                    company_id, period_id, version,
                    core_thesis, supporting_factors, risk_factors, thesis_breaking_conditions
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (company_id, period_id, version,
                  core_thesis, json.dumps(supporting_factors),
                  json.dumps(risk_factors), json.dumps(breaking_conditions)))
            conn.commit()
            return cursor.lastrowid
        except Exception as e:
            conn.rollback()
            raise
    
    def get_thesis(self, company_id: int, period_id: Optional[int] = None) -> Optional[Dict]:
        """Get latest thesis for a company."""
        if period_id:
            return self.fetchone(
                "SELECT * FROM thesis_statements WHERE company_id = ? AND period_id = ?",
                (company_id, period_id)
            )
        return self.fetchone(
            "SELECT * FROM thesis_statements WHERE company_id = ? ORDER BY last_updated DESC LIMIT 1",
            (company_id,)
        )
    
    def upsert_momentum_score(self, company_id: int, period_id: int,
                               prev_period_id: Optional[int], momentum_score: float,
                               classification: str, trend_data: dict) -> int:
        """Insert thesis momentum score."""
        conn = self._get_conn()
        try:
            cursor = conn.execute("""
                INSERT INTO thesis_momentum_scores(
                    company_id, period_id, prev_period_id,
                    momentum_score, classification, trend_data, compared_to_previous_real
                ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (company_id, period_id, prev_period_id,
                  momentum_score, classification, json.dumps(trend_data)))
            conn.commit()
            return cursor.lastrowid
        except Exception as e:
            conn.rollback()
            raise
    
    def get_momentum_scores(self, company_id: int) -> List[Dict]:
        """Get momentum history."""
        return self.fetchall("""
            SELECT tm.*, fp.period_label
            FROM thesis_momentum_scores tm
            JOIN fiscal_periods fp ON fp.id = tm.period_id
            WHERE tm.company_id = ?
            ORDER BY tm.created_at ASC
        """, (company_id,))
    
    # ==================== VALUATION ====================
    
    def upsert_valuation(self, company_id: int, period_id: int, market_cap: Optional[float] = None,
                         enterprise_value: Optional[float] = None, pe_ratio: Optional[float] = None,
                         ev_ebitda: Optional[float] = None, ps_ratio: Optional[float] = None,
                         fcff_yield: Optional[float] = None, price_per_share: Optional[float] = None) -> bool:
        """Upsert valuation data. Returns True if inserted, False if updated."""
        try:
            self.execute("""
                INSERT INTO valuation_history(
                    company_id, period_id, market_cap, enterprise_value,
                    pe_ratio, ev_ebitda, ps_ratio, fcff_yield, price_per_share
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (company_id, period_id, market_cap, enterprise_value,
                  pe_ratio, ev_ebitda, ps_ratio, fcff_yield, price_per_share))
            self.commit()
            return True
        except sqlite3.IntegrityError:
            self.execute("""
                UPDATE valuation_history SET
                    market_cap = COALESCE(?, market_cap),
                    enterprise_value = COALESCE(?, enterprise_value),
                    pe_ratio = COALESCE(?, pe_ratio),
                    ev_ebitda = COALESCE(?, ev_ebitda),
                    ps_ratio = COALESCE(?, ps_ratio),
                    fcff_yield = COALESCE(?, fcff_yield),
                    price_per_share = COALESCE(?, price_per_share)
                WHERE company_id = ? AND period_id = ?
            """, (market_cap, enterprise_value, pe_ratio, ev_ebitda,
                  ps_ratio, fcff_yield, price_per_share,
                  company_id, period_id))
            self.commit()
            return False
    
    def get_valuation_history(self, company_id: int) -> List[Dict]:
        """Get all valuations for a company."""
        return self.fetchall("""
            SELECT v.*, fp.period_label, fp.report_date
            FROM valuation_history v
            JOIN fiscal_periods fp ON fp.id = v.period_id
            WHERE v.company_id = ?
            ORDER BY v.calculated_at DESC
        """, (company_id,))
    
    def get_current_valuation_percentile(self, company_id: int, metric: str = "pe_ratio") -> Optional[Dict]:
        """Get current percentile ranking for a valuation metric."""
        # First get the latest valuation
        latest = self.fetchone("""
            SELECT * FROM valuation_history 
            WHERE company_id = ? 
            ORDER BY calculated_at DESC 
            LIMIT 1
        """, (company_id,))
        
        if not latest:
            return None
        
        current_val = latest.get(metric)
        if current_val is None:
            return None
        
        # Get statistical summary
        stats = self.fetchone("""
            SELECT 
                MIN(value) as min_val,
                MAX(value) as max_val,
                AVG(value) as avg_val,
                COUNT(*) as data_points
            FROM (
                SELECT vh.pe_ratio as value
                FROM valuation_history vh
                WHERE vh.company_id = ? AND vh.pe_ratio IS NOT NULL
                UNION ALL
                SELECT v2.pe_ratio
                FROM valuation_history v2
                WHERE v2.company_id != ? AND v2.pe_ratio IS NOT NULL
            )
        """, (company_id, company_id))
        
        if not stats:
            return None
        
        # Simple percentile calculation
        all_vals = self.fetchall("""
            SELECT pe_ratio as value FROM valuation_history
            WHERE pe_ratio IS NOT NULL
            ORDER BY pe_ratio ASC
        """, (company_id,))
        
        sorted_vals = [v['value'] for v in all_vals if v['value'] > 0]
        if not sorted_vals:
            return None
        
        percentile = sum(1 for v in sorted_vals if v < current_val) / len(sorted_vals) * 100
        
        return {
            'current': current_val,
            'mean': stats['avg_val'],
            'min': stats['min_val'],
            'max': stats['max_val'],
            'data_points': stats['data_points'],
            'percentile': round(percentile, 2)
        }
    
    # ==================== ALERTS ====================

    def compute_alert_dedup_hash(self, company_id: int, alert_type: str,
                                  period: str, metric: str, version: str = 'v1.0') -> str:
        """Compute deterministic hash for alert dedup key."""
        import hashlib
        raw = f"{company_id}|{alert_type}|{period}|{metric}|{version}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def is_duplicate_alert(self, dedup_hash: str) -> bool:
        """Check if an alert with this dedup_hash already exists."""
        row = self.fetchone("SELECT id FROM alerts WHERE dedup_hash = ?", (dedup_hash,))
        return row is not None

    def create_alert(self, company_id: int, alert_level: str, alert_type: str,
                     message: str, triggered_by: Optional[str] = None,
                     dedup_metric: Optional[str] = None, dedup_period: Optional[str] = None,
                     dedup_threshold: Optional[float] = None, dedup_version: str = 'v1.0') -> Optional[int]:
        """Create an alert with deduplication. Returns alert_id or None if duplicate.
        
        Dedup key components: company_id + alert_type + period + metric + version.
        Two identical conditions are never persisted as separate rows.
        """
        dedup_hash = self.compute_alert_dedup_hash(
            company_id, alert_type,
            dedup_period or '', dedup_metric or '', dedup_version
        )
        
        if self.is_duplicate_alert(dedup_hash):
            return None  # Duplicate — silently skip
        
        cursor = self.execute("""
            INSERT INTO alerts(company_id, alert_level, alert_type, message, triggered_by,
                               dedup_hash, dedup_metric, dedup_period, dedup_threshold, dedup_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (company_id, alert_level, alert_type, message, triggered_by,
              dedup_hash, dedup_metric, dedup_period, dedup_threshold, dedup_version))
        self.commit()
        return cursor.lastrowid
    
    def get_unacknowledged_alerts(self, company_id: Optional[int] = None) -> List[Dict]:
        """Get all unacknowledged alerts."""
        if company_id:
            return self.fetchall("""
                SELECT a.*, c.ticker, c.name
                FROM alerts a
                JOIN companies c ON c.id = a.company_id
                WHERE a.acknowledged = 0 AND a.company_id = ?
                ORDER BY a.triggered_at DESC
            """, (company_id,))
        return self.fetchall("""
            SELECT a.*, c.ticker, c.name
            FROM alerts a
            JOIN companies c ON c.id = a.company_id
            WHERE a.acknowledged = 0
            ORDER BY a.triggered_at DESC
        """)
    
    def acknowledge_alert(self, alert_id: int):
        """Mark an alert as acknowledged."""
        self.execute("UPDATE alerts SET acknowledged = 1 WHERE id = ?", (alert_id,))
        self.commit()
    
    def get_alerts(self, company_id: Optional[int] = None, limit: int = 20) -> List[Dict]:
        """Get alerts, optionally filtered by company."""
        if company_id:
            return self.fetchall("""
                SELECT a.*, c.ticker, c.name
                FROM alerts a
                JOIN companies c ON c.id = a.company_id
                WHERE a.company_id = ?
                ORDER BY a.triggered_at DESC LIMIT ?
            """, (company_id, limit))
        return self.fetchall("""
            SELECT a.*, c.ticker, c.name
            FROM alerts a
            JOIN companies c ON c.id = a.company_id
            ORDER BY a.triggered_at DESC LIMIT ?
        """, (limit,))
    
    def delete_company(self, company_id: int) -> bool:
        """Delete a company by ID. Returns True if deleted."""
        cursor = self.execute("DELETE FROM companies WHERE id = ?", (company_id,))
        self.commit()
        return cursor.rowcount > 0
    
    def update_weights_db(self, new_weights: Dict, db_path: str) -> bool:
        """Update scoring weights stored in SQLite config table."""
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS weight_config(key TEXT PRIMARY KEY, value TEXT)")
            for key, val in new_weights.items():
                conn.execute(
                    "INSERT OR REPLACE INTO weight_config VALUES (?, ?)",
                    (key, json.dumps(val))
                )
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            return False
        finally:
            conn.close()
    
    # ==================== DATA QUALITY LOGGING ====================
    
    def log_data_quality_issue(self, company_id: Optional[int], period_id: Optional[int],
                                metric_key: Optional[str], issue: str, severity: str = "MEDIUM",
                                notes: Optional[str] = None) -> int:
        """Log a data quality issue."""
        cursor = self.execute("""
            INSERT INTO data_quality_log(company_id, period_id, metric_key, quality_issue, severity, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (company_id, period_id, metric_key, issue, severity, notes))
        self.commit()
        return cursor.lastrowid
    
    # ==================== GENERIC CRUD ====================
    
    def query(self, sql: str, params: tuple = ()) -> List[Dict]:
        """Generic query method returning list of dicts."""
        return self.fetchall(sql, params)
    
    def scalar(self, sql: str, params: tuple = ()) -> Optional[Any]:
        """Execute scalar query."""
        row = self.fetchone(sql, params)
        if row:
            return list(row.values())[0]
        return None
    
    def count(self, table: str, condition: Optional[str] = None, params: tuple = ()) -> int:
        """Count rows in a table."""
        where = f"WHERE {condition}" if condition else ""
        result = self.scalar(f"SELECT COUNT(*) FROM {table} {where}", params)
        return result if result else 0
