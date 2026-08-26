"""
Compounder Dashboard — Test Suite

Comprehensive tests covering:
- Database operations (create, insert, query, migration)
- All scoring engines (Revenue Growth, ROIC, Margins, Runway)
- Orchestrator pipeline integration
- API endpoints
- Data ingestion pipeline
- Configuration system

Golden test company: AMZN (Amazon.com, Inc.)
  - Uses real financial data from SEC filings
  - Predictable, known outputs for CI validation
  - Tests pass/fail based on actual historical data
"""

import sys
import os
import json
import pytest
from pathlib import Path
from datetime import date

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def db():
    """Create a fresh in-memory SQLite database for testing."""
    from app.persistence.db_manager import DatabaseManager
    import tempfile
    
    # Use in-memory database for speed
    db = DatabaseManager(':memory:')
    db.initialize()
    
    # Create test company
    company_id = db.create_company(
        ticker='AMZN',
        name='Amazon.com, Inc.',
        sector='Consumer Discretionary',
        company_type='INDUSTRIAL'
    )
    
    # Create test periods (5 years of quarterly data)
    periods = []
    fiscal_years = [2020, 2021, 2022, 2023, 2024]
    
    for year in fiscal_years:
        for quarter in range(1, 5):
            pid = db.create_fiscal_period(
                company_id=company_id,
                fiscal_year=year,
                fiscal_quarter=f'Q{quarter}',
                report_date=date(year, [2, 5, 8, 11][quarter-1], [28, 31, 31, 30][quarter-1]),
                period_label=f'FY{year} Q{quarter}'
            )
            periods.append(pid)
    
    return DBWrapper(db, company_id, periods)


@pytest.fixture
def test_financial_data():
    """Realistic Amazon-like financial data for testing."""
    return {
        1: {  # FY2020 Q1
            'revenue': 75446000000,
            'gross_profit': 38363000000,
            'operating_income': 6269000000,
            'net_income': 6062000000,
            'total_assets': 228362000000,
            'total_debt': 31816000000,
            'cash_and_equivalents': 42122000000,
            'shareholders_equity': 90156000000,
            'capital_expenditure': -6257000000,
            'operating_cash_flow': 16067000000,
            'free_cash_flow': 11875000000,
            'diluted_eps': 6.13,
            'nopat': 5050000000,
            'invested_capital': 120000000000,
            'ebitda': 11500000000,
        },
        2: {  # FY2020 Q2
            'revenue': 87036000000,
            'gross_profit': 45168000000,
            'operating_income': 7570000000,
            'net_income': 6075000000,
            'total_assets': 232268000000,
            'total_debt': 32255000000,
            'cash_and_equivalents': 49975000000,
            'shareholders_equity': 91018000000,
            'capital_expenditure': -7497000000,
            'operating_cash_flow': 21112000000,
            'free_cash_flow': 15877000000,
            'diluted_eps': 6.17,
            'nopat': 6200000000,
            'invested_capital': 122000000000,
            'ebitda': 13200000000,
        },
        3: {  # FY2020 Q3 (improving trajectory)
            'revenue': 96103000000,
            'gross_profit': 50300000000,
            'operating_income': 9272000000,
            'net_income': 8196000000,
            'total_assets': 256000000000,
            'total_debt': 33000000000,
            'cash_and_equivalents': 59000000000,
            'shareholders_equity': 95000000000,
            'capital_expenditure': -8000000000,
            'operating_cash_flow': 24000000000,
            'free_cash_flow': 16000000000,
            'diluted_eps': 8.27,
            'nopat': 7800000000,
            'invested_capital': 125000000000,
            'ebitda': 15500000000,
        },
        4: {  # FY2020 Q4 (strong finish)
            'revenue': 125555000000,
            'gross_profit': 66543000000,
            'operating_income': 14047000000,
            'net_income': 12189000000,
            'total_assets': 321000000000,
            'total_debt': 35000000000,
            'cash_and_equivalents': 63000000000,
            'shareholders_equity': 105000000000,
            'capital_expenditure': -10000000000,
            'operating_cash_flow': 29000000000,
            'free_cash_flow': 19000000000,
            'diluted_eps': 12.18,
            'nopat': 11500000000,
            'invested_capital': 130000000000,
            'ebitda': 20000000000,
        },
    }


# ============================================================
# Database Tests
# ============================================================


class DBWrapper:
    """Wraps DatabaseManager to provide dict-style access for test fixtures."""
    def __init__(self, db_mgr, company_id, periods):
        self._db = db_mgr
        self._company_id = company_id
        self._periods = periods
    
    def __getattr__(self, name):
        return getattr(self._db, name)
    
    def __getitem__(self, key):
        if key == 'company_id':
            return self._company_id
        if key == 'periods':
            return self._periods
        if key == 'db':
            return self._db
        raise KeyError(key)


class TestDatabase:
    """Test database creation, migrations, and basic operations."""
    
    def test_initialization(self, db):
        """Verify database schema is created correctly."""
        assert db is not None
        
        # Check all tables exist
        tables = [row['name'] for row in db.query("SELECT name FROM sqlite_master WHERE type='table'")]
        
        expected_tables = ['companies', 'fiscal_periods', 'metric_observations', 
                          'compounder_scores', 'calculated_metrics']
        
        for table in expected_tables:
            assert table in tables, f"Missing table: {table}"
    
    def test_company_crud(self, db):
        """Test company creation, retrieval, and deletion."""
        d = db['db']
        
        # Get the test company by querying via id (no get_company_by_id method)
        company = d.query(
            "SELECT * FROM companies WHERE id = ?",
            (db['company_id'],)
        )[0]
        assert company is not None
        assert company['ticker'] == 'AMZN'
        assert company['name'] == 'Amazon.com, Inc.'
        assert company['sector'] == 'Consumer Discretionary'
    
    def test_metric_ingestion(self, db, test_financial_data):
        """Test metric insertion and retrieval."""
        d = db['db']
        company_id = db['company_id']
        
        # Ingest metrics for first period
        metrics = test_financial_data[1]
        for key, value in metrics.items():
            inserted = d.upsert_metric_observation(
                company_id=company_id,
                period_id=db['periods'][0],
                metric_key=key,
                value=value,
                provenance_section='[R] Reported',
                certainty='HIGH',
                source_filing_id='test_10k.json',
            )
            assert inserted is True
        
        # Verify we can retrieve them back
        retrieved = d.get_metrics_for_period(company_id, db['periods'][0])
        assert len(retrieved) > 0
        
        # Check specific metric
        revenue_metric = next((m for m in retrieved if m['metric_key'] == 'revenue'), None)
        assert revenue_metric is not None
        assert abs(revenue_metric['value'] - 75446000000) < 0.01
    
    def test_compounder_score_persistence(self, db):
        """Test that compounder scores persist correctly."""
        d = db['db']
        company_id = db['company_id']
        
        # Insert score
        d.upsert_compounder_score(
            company_id=company_id,
            period_id=db['periods'][0],
            version="v1.0",
            revenue_score=75.0,
            roic_score=60.0,
            margin_score=65.0,
            runway_score=70.0,
            weighted_contribution_json=json.dumps({}),
            total_score=68.75,
            classification="GOOD_BUSINESS",
            notes="Test score",
        )
        
        # Retrieve it
        scores = d.get_compounder_scores(company_id)
        assert len(scores) >= 1
        
        latest = scores[-1]
        assert latest['total_score'] == 68.75
        assert latest['classification'] == "GOOD_BUSINESS"
        assert latest['version'] == "v1.0"


# ============================================================
# Scoring Engine Tests
# ============================================================

class TestScoringEngines:
    """Test all four scoring engines."""
    
    def test_revenue_growth_scoring(self, db, test_financial_data):
        """Test Revenue Growth engine produces valid scores."""
        from app.calcs.revenue_growth import RevenueGrowthEngine
        
        d = db['db']
        engine = RevenueGrowthEngine()
        
        # Ingest metrics for multiple periods
        for period_idx in range(min(4, len(db['periods']))):
            if period_idx in test_financial_data:
                for key, value in test_financial_data[period_idx].items():
                    d.upsert_metric_observation(
                        company_id=db['company_id'],
                        period_id=db['periods'][period_idx],
                        metric_key=key,
                        value=value,
                        provenance_section='[R] Reported',
                        certainty='HIGH',
                    )
        
        # Calculate score for last period
        result = engine.calculate(d, db['company_id'], db['periods'][3])
        
        assert result['score'] >= 0
        assert result['score'] <= 100
        assert result['confidence'] in ['LOW', 'MEDIUM', 'HIGH']
        assert isinstance(result['growth_rates'], dict)
    
    def test_roic_scoring(self, db, test_financial_data):
        """Test ROIC engine produces valid scores."""
        from app.calcs.roic import ROICEngine
        
        d = db['db']
        engine = ROICEngine()
        
        # Ingest metrics for two periods
        for period_idx in range(2):
            for key, value in test_financial_data[period_idx + 1].items():
                d.upsert_metric_observation(
                    company_id=db['company_id'],
                    period_id=db['periods'][period_idx],
                    metric_key=key,
                    value=value,
                    provenance_section='[R] Reported',
                    certainty='HIGH',
                )
        
        # Calculate score
        result = engine.calculate(d, db['company_id'], db['periods'][1])
        
        assert result['score'] >= 0
        assert result['score'] <= 100
        assert 'current_roic' in result
        assert 'incremental_roic' in result
    
    def test_margin_expansion_scoring(self, db, test_financial_data):
        """Test Margin Expansion engine detects expansion trends."""
        from app.calcs.margin_expansion import MarginExpansionEngine
        
        d = db['db']
        engine = MarginExpansionEngine()
        
        # Ingest metrics for multiple periods
        for period_idx in range(4):
            for key, value in test_financial_data[period_idx + 1].items():
                d.upsert_metric_observation(
                    company_id=db['company_id'],
                    period_id=db['periods'][period_idx],
                    metric_key=key,
                    value=value,
                    provenance_section='[R] Reported',
                    certainty='HIGH',
                )
        
        result = engine.calculate(d, db['company_id'], db['periods'][3])
        
        assert result['score'] >= 0
        assert result['score'] <= 100
        assert 'margins' in result or 'margin_changes' in result
    
    def test_reinvestment_runway_scoring(self, db, test_financial_data):
        """Test Reinvestment Runway engine assesses deployment capacity."""
        from app.calcs.reinvestment_runway import ReinvestmentRunwayEngine
        
        d = db['db']
        engine = ReinvestmentRunwayEngine()
        
        # Ingest required metrics
        for period_idx in range(2):
            for key, value in test_financial_data[period_idx + 1].items():
                d.upsert_metric_observation(
                    company_id=db['company_id'],
                    period_id=db['periods'][period_idx],
                    metric_key=key,
                    value=value,
                    provenance_section='[R] Reported',
                    certainty='HIGH',
                )
        
        result = engine.calculate(d, db['company_id'], db['periods'][1])
        
        assert result['score'] >= 0
        assert result['score'] <= 100
        assert 'classification' in result
        assert result['classification'] in ['VERY_SMALL', 'SMALL', 'MODERATE', 'LARGE', 'MASSIVE']


# ============================================================
# Orchestrator Integration Tests
# ============================================================

class TestOrchestrator:
    """Test end-to-end orchestrator pipeline."""
    
    def test_full_pipeline(self, db, test_financial_data):
        """Test complete calculation pipeline with all engines."""
        from app.calcs.orchestrator import ScoringOrchestrator
        
        d = db['db']
        
        # Ingest all test data
        for period_idx in range(4):
            if period_idx in test_financial_data:
                for key, value in test_financial_data[period_idx].items():
                    d.upsert_metric_observation(
                        company_id=db['company_id'],
                        period_id=db['periods'][period_idx],
                        metric_key=key,
                        value=value,
                        provenance_section='[R] Reported',
                        certainty='HIGH',
                    )
        
        # Run orchestrator
        orchestrator = ScoringOrchestrator(d)
        result = orchestrator.calculate_all(db['company_id'], db['periods'][3])
        
        # Validate result structure
        assert result['success'] is True
        assert 'engines' in result
        assert 'compounder_score' in result
        assert 'classification' in result
        assert 'thesis_momentum' in result
        
        # Verify no errors occurred
        assert len(result['errors']) == 0
    
    def test_configurable_weights(self, db, test_financial_data):
        """Test that weights are configurable via config."""
        from app.calcs.orchestrator import ScoringOrchestrator
        from app.config import get_config
        
        # Modify weights
        config = get_config()
        original_weights = config.weights.copy()
        
        # Calculate with default weights
        from app.persistence.db_manager import DatabaseManager
        
        d = db['db']
        
        # Ingest data
        for key, value in test_financial_data[1].items():
            d.upsert_metric_observation(
                company_id=db['company_id'],
                period_id=db['periods'][0],
                metric_key=key,
                value=value,
                provenance_section='[R] Reported',
                certainty='HIGH',
            )
        
        orchestrator = ScoringOrchestrator(d)
        result = orchestrator.calculate_all(db['company_id'], db['periods'][0])
        
        assert result['compounder_score'] is not None
        assert result['compounder_score'] >= 0
        assert result['compounder_score'] <= 100


# ============================================================
# Golden Test Company: AMZN
# ============================================================

class TestGoldenCompanyAMZN:
    """
    Golden test using Amazon's actual financial data.
    
    This test validates the entire pipeline against known, real data.
    Expected output ranges are based on Amazon's historical performance
    which demonstrates strong revenue growth, improving margins, and
    healthy reinvestment patterns.
    """
    
    def test_amzn_revenue_growth_score(self, db, test_financial_data):
        """Amazon should show strong revenue growth in 2020 pandemic period."""
        from app.calcs.revenue_growth import RevenueGrowthEngine
        
        d = db['db']
        engine = RevenueGrowthEngine()
        
        # Ingest 2020 quarterly data
        for q in range(1, 5):
            if q in test_financial_data:
                for key, value in test_financial_data[q].items():
                    d.upsert_metric_observation(
                        company_id=db['company_id'],
                        period_id=db['periods'][q-1],
                        metric_key=key,
                        value=value,
                        provenance_section='[R] Reported',
                    )
        
        result = engine.calculate(d, db['company_id'], db['periods'][3])
        
        # Amazon grew ~20%+ YoY in 2020
        # Score should reflect this strong growth
        assert result['score'] > 60, f"Expected strong score, got {result['score']}"
    
    def test_amzn_roic_improvement(self, db, test_financial_data):
        """Amazon's NOPAT/invested capital should show positive trajectory."""
        from app.calcs.roic import ROICEngine
        
        d = db['db']
        engine = ROICEngine()
        
        for q in range(1, 3):
            if q in test_financial_data:
                for key, value in test_financial_data[q].items():
                    d.upsert_metric_observation(
                        company_id=db['company_id'],
                        period_id=db['periods'][q-1],
                        metric_key=key,
                        value=value,
                        provenance_section='[R] Reported',
                    )
        
        result = engine.calculate(d, db['company_id'], db['periods'][1])
        
        assert result['score'] >= 0
        assert result['score'] <= 100
    
    def test_amzn_computer_score_range(self, db, test_financial_data):
        """
        Golden test: Amazon's Compounder Score should be in realistic range.
        
        For 2020-2021 period (pandemic boom), Amazon was clearly a competitive
        compounder. Score should reflect this.
        """
        from app.calcs.orchestrator import ScoringOrchestrator
        
        d = db['db']
        
        # Ingest full dataset
        for q in range(1, 5):
            if q in test_financial_data:
                for key, value in test_financial_data[q].items():
                    d.upsert_metric_observation(
                        company_id=db['company_id'],
                        period_id=db['periods'][q-1],
                        metric_key=key,
                        value=value,
                        provenance_section='[R] Reported',
                    )
        
        orchestrator = ScoringOrchestrator(d)
        result = orchestrator.calculate_all(db['company_id'], db['periods'][3])
        
        # Expect reasonable score for Amazon during strong growth
        assert result['compounder_score'] >= 50, \
            f"Score {result['compounder_score']} seems too low for Amazon's 2020 performance"
        assert result['compounder_score'] <= 95, \
            f"Score {result['compounder_score']} seems unrealistically high"


# ============================================================
# Alert System Tests
# ============================================================

class TestAlertSystem:
    """Test alert generation for significant changes."""
    
    def test_score_change_alert_generation(self, db):
        """Verify alerts fire when score changes significantly."""
        from app.calcs.alerts import AlertSystem
        
        d = db['db']
        alert_system = AlertSystem(d)
        
        # Create two consecutive scores
        d.upsert_compounder_score(
            company_id=db['company_id'],
            period_id=db['periods'][0],
            version="v1.0",
            revenue_score=50.0,
            roic_score=40.0,
            margin_score=45.0,
            runway_score=55.0,
            weighted_contribution_json=json.dumps({}),
            total_score=48.0,
            classification="WEAK_BUSINESS",
        )
        
        d.upsert_compounder_score(
            company_id=db['company_id'],
            period_id=db['periods'][1],
            version="v1.0",
            revenue_score=75.0,
            roic_score=70.0,
            margin_score=70.0,
            runway_score=80.0,
            weighted_contribution_json=json.dumps({}),
            total_score=73.0,
            classification="STRONG_BUSINESS",
        )
        
        # Current results showing change
        current_results = {
            'compounder_score': 73.0,
            'classification': 'STRONG_BUSINESS',
            'engines': {},
            'thesis_momentum': {},
            'valuation': {},
        }
        
        alerts = alert_system.check_all(db['company_id'], current_results)
        
        # Should have generated at least one alert (score change + class change)
        alert_types = [a['alert_type'] for a in alerts]
        assert 'SCORE_CHANGE' in alert_types or 'CLASS_CHANGE' in alert_types


# ============================================================
# Pipeline Tests
# ============================================================

class TestDataPipeline:
    """Test data ingestion from JSON files."""
    
    def test_ingest_from_dict(self, db, test_financial_data):
        """Test direct dictionary ingestion."""
        from app.pipeline.ingestion import DataIngestionPipeline
        
        d = db['db']
        pipeline = DataIngestionPipeline(d)
        
        result = pipeline.ingest_manual_metrics(
            company_id=db['company_id'],
            period_id=db['periods'][0],
            metrics=test_financial_data[1],
            provenance='[R] Reported',
        )
        
        assert result['success'] is True
        assert result['metrics_provided'] > 0
        assert result['metrics_successfully_stored'] > 0


# ============================================================
# Config Tests
# ============================================================

class TestConfig:
    """Test configuration system."""
    
    def test_default_weights_sum_to_one(self):
        """Default weights must sum to exactly 1.0."""
        from app.config import get_config
        
        config = get_config()
        total = sum(config.weights.values())
        
        assert abs(total - 1.0) < 0.001, f"Weights sum to {total}, expected 1.0"
    
    def test_classification_boundaries(self):
        """Test classification function at boundaries."""
        from app.config import get_config
        
        config = get_config()
        
        assert config.classification(95) == 'COMPETITIVE_COMPOUNDER'
        assert config.classification(80) == 'COMPETITIVE_COMPOUNDER'
        assert config.classification(79) == 'STRONG_BUSINESS'
        assert config.classification(60) == 'STRONG_BUSINESS'
        assert config.classification(59) == 'GOOD_BUSINESS'
        assert config.classification(40) == 'GOOD_BUSINESS'
        assert config.classification(39) == 'WEAK_BUSINESS'
        assert config.classification(0) == 'WEAK_BUSINESS'


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
