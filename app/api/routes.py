"""
API Routes — FastAPI endpoints for Compounder Dashboard

Endpoints:
- GET /health - Health check
- GET/POST /companies - List/add companies
- GET /companies/{id} - Get company details + scores
- DELETE /companies/{id} - Remove company
- POST /companies/{id}/calculate - Trigger full recalculation
- GET /companies/{id}/scores - Historical compounder scores
- GET /companies/{id}/alerts - Alert history
- POST /pipeline/import-json - Ingest SEC extraction JSON
- GET /metrics/{company_id}?period_id=N - Financial metrics for period
- GET /config/weights - Current scoring weights
- PUT /config/weights - Update weighting matrix
"""

from typing import Dict, Any, Optional, List
from datetime import datetime
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel


# Pydantic models for request/response
class CompanyCreate(BaseModel):
    ticker: str
    name: str
    sector: str = "Unknown"
    industry: str = "Unknown"


class CompanyUpdate(BaseModel):
    name: Optional[str] = None
    sector: Optional[str] = None
    industry: Optional[str] = None


class MetricInput(BaseModel):
    metric_key: str
    value: float


class ManualMetricsInput(BaseModel):
    metrics: Dict[str, Any]
    provenance: str = "[R] Reported"


class RebalanceWeights(BaseModel):
    revenue_growth: Optional[float] = None
    roic: Optional[float] = None
    margin_expansion: Optional[float] = None
    reinvestment_runway: Optional[float] = None


class PipelineIngestionResult(BaseModel):
    success: bool
    metrics_ingested: int
    errors: List[str]
    provenance_summary: Dict[str, int]


def create_app(db_path: str = "/data/compounder.db") -> FastAPI:
    """Factory function to create the FastAPI application."""
    from app.persistence.db_manager import DatabaseManager
    from app.calcs.orchestrator import create_orchestrator
    from app.pipeline.ingestion import DataIngestionPipeline
    
    db = DatabaseManager(db_path)
    db.initialize()
    
    orchestrator = create_orchestrator(db)
    pipeline = DataIngestionPipeline(db)
    
    app = FastAPI(
        title="Compounder Intelligence Dashboard",
        description="Persistent Compounder Intelligence — quarterly fundamental trend analysis",
        version="1.0.0",
        root_path="",  # Don't prepend any path prefix; Traefik handles stripping
    )
    
    @app.get("/health")
    async def health_check():
        return {
            'status': 'healthy',
            'version': '1.0.0',
            'timestamp': datetime.utcnow().isoformat(),
            'database': 'connected',
        }
    
    # --- Companies CRUD ---
    @app.get("/api/companies", include_in_schema=False)
    @app.get("/companies")
    async def list_companies():
        try:
            companies = db.list_companies()
            enriched = []
            for c in companies:
                latest_score = db.get_latest_compounder_score(c['id'])
                enriched.append({
                    **c,
                    'latest_compounder_score': latest_score.get('compounder_score') if latest_score else None,
                    'latest_classification': latest_score.get('classification') if latest_score else None,
                })
            return {'companies': enriched, 'count': len(enriched)}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.post("/api/companies")
    @app.post("/companies")  # Alias: works with and without /api prefix
    async def add_company(data: CompanyCreate):
        try:
            # Check duplicate
            existing = db.get_company_by_ticker(data.ticker.upper())
            if existing:
                raise HTTPException(status_code=409, detail=f"Ticker {data.ticker} already exists")
            
            company_id = db.create_company(
                ticker=data.ticker.upper(),
                name=data.name,
                sector=data.sector,
            )
            
            return {'success': True, 'company_id': company_id, 'ticker': data.ticker.upper()}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.get("/api/companies/{company_id}")
    @app.get("/companies/{company_id}")  # Alias: works with and without /api prefix
    async def get_company(company_id: int):
        try:
            company = db.get_company_by_id(company_id)
            if not company:
                raise HTTPException(status_code=404, detail="Company not found")
            
            latest_cs = db.get_latest_compounder_score(company_id)
            all_scores = db.get_compounder_scores(company_id)
            alerts = db.get_alerts(company_id)
            
            periods = db.get_periods_for_company(company_id)
            
            result = {
                'company': company,
                'latest_score': latest_cs,
                'historical_scores': all_scores[-10:],  # Last 10 periods
                'alerts': alerts[-20:],  # Last 20 alerts
                'periods_count': len(periods),
                'last_calculated_at': latest_cs.get('calculated_at') if latest_cs else None,
            }
            
            return result
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.delete("/api/companies/{company_id}")
    @app.delete("/companies/{company_id}")  # Alias: works with and without /api prefix
    async def delete_company(company_id: int):
        try:
            deleted = db.delete_company(company_id)
            if not deleted:
                raise HTTPException(status_code=404, detail="Company not found")
            return {'success': True, 'message': f"Company {company_id} deleted"}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.post("/api/companies/{company_id}/calculate")
    @app.post("/companies/{company_id}/calculate")  # Alias: works with and without /api prefix
    async def calculate_company(company_id: int, force: bool = False):
        """Trigger full calculation run for a specific company."""
        try:
            company = db.get_company_by_id(company_id)
            if not company:
                raise HTTPException(status_code=404, detail="Company not found")
            
            # Calculate for each period
            periods = db.get_periods_for_company(company_id)
            results = []
            
            for period in periods:
                # Skip already-calculated periods unless force=True
                if not force:
                    existing = db.query("""
                        SELECT id FROM compounder_scores 
                        WHERE company_id = ? AND period_id = ?
                    """, (company_id, period['id']))
                    if existing and len(existing) > 0:
                        continue  # Skip if already calculated
                
                calc_result = orchestrator.calculate_all(company_id, period['id'])
                results.append(calc_result)
            
            total_success = sum(1 for r in results if r.get('compounder_score') not in (None, 'N/A'))
            
            return {
                'success': True,
                'company_id': company_id,
                'periods_processed': len(results),
                'successful_calculations': total_success,
                'errors': [r['errors'] for r in results if r.get('errors')],
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.post("/api/companies/{company_id}/recalculate-all")
    @app.post("/companies/{company_id}/recalculate-all")  # Alias: works with and without /api prefix
    async def recalculate_all(company_id: int):
        """Force-recalculate ALL periods for a company (rebuild methodology)."""
        try:
            company = db.get_company_by_id(company_id)
            if not company:
                raise HTTPException(status_code=404, detail="Company not found")
            
            results = orchestrator.recalculate_company(company_id)
            total_success = sum(1 for r in results if r.get('compounder_score') not in (None, 'N/A'))
            
            return {
                'success': True,
                'company_id': company_id,
                'periods_reprocessed': len(results),
                'successful_calculations': total_success,
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.get("/api/companies/{company_id}/scores")
    @app.get("/companies/{company_id}/scores")  # Alias: works with and without /api prefix
    async def get_company_scores(company_id: int, limit: int = 20):
        """Get historical compounder scores with breakdown."""
        try:
            scores = db.get_compounder_scores(company_id, limit=limit)
            return {'scores': scores, 'count': len(scores)}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.get("/api/companies/{company_id}/alerts")
    @app.get("/companies/{company_id}/alerts")  # Alias: works with and without /api prefix
    async def get_company_alerts(company_id: int, limit: int = 50):
        """Get alert history for a company."""
        try:
            alerts = db.get_alerts(company_id, limit=limit)
            return {'alerts': alerts, 'count': len(alerts)}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.get("/api/metrics/{company_id}")
    @app.get("/metrics/{company_id}")  # Alias: works with and without /api prefix
    async def get_metrics(
        company_id: int, 
        period_id: Optional[int] = Query(None, description='Period ID'),
        metric_key: Optional[str] = Query(None, description='Specific metric key'),
    ):
        """Get financial metrics for a company-period."""
        try:
            company = db.get_company_by_id(company_id)
            if not company:
                raise HTTPException(status_code=404, detail="Company not found")
            
            if period_id:
                metrics = db.get_metrics_for_period(company_id, period_id)
            elif metric_key:
                metrics = db.query("""
                    SELECT mo.*, fp.fiscal_year, fp.fiscal_quarter, fp.report_date, fp.period_label
                    FROM metric_observations mo
                    JOIN fiscal_periods fp ON fp.id = mo.period_id
                    WHERE mo.company_id = ? AND mo.metric_key LIKE ?
                    ORDER BY fp.report_date DESC
                """, (company_id, metric_key if '%' in metric_key else f"%{metric_key}%"))
            else:
                # Get most recent period metrics
                latest_period = db.query("""
                    SELECT id FROM fiscal_periods 
                    WHERE company_id = ? ORDER BY report_date DESC LIMIT 1
                """, (company_id,))
                
                if latest_period:
                    metrics = db.get_metrics_for_period(company_id, latest_period[0]['id'])
                else:
                    metrics = []
            
            return {
                'company_id': company_id,
                'company_name': company['name'],
                'metrics': metrics,
                'count': len(metrics),
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.post("/api/pipeline/import-json")
    @app.post("/pipeline/import-json")  # Alias: works with and without /api prefix
    async def ingest_json_file(data: dict):
        """
        Ingest a parsed SEC filing JSON file.
        
        Expected payload:
        {
            "json_path": "/opt/data/buffett/data/amzn/extractions/2025-Q4_clean.json",
            "source_type": "SEC_10K"
        }
        """
        try:
            json_path = data.get('json_path')
            source_type = data.get('source_type', 'SEC_10K')
            
            if not json_path or not json_path.endswith('.json'):
                raise HTTPException(status_code=400, detail="Valid JSON path required")
            
            # Bulk import from directory
            base_dir = '/'.join(json_path.split('/')[:-1])
            
            result = pipeline.bulk_import_from_dir(
                base_dir=base_dir,
                pattern=json_path.split('/')[-1].split('_')[0] + '_clean.json',
                source_type=source_type,
            )
            
            return result
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.post("/api/pipeline/bulk-import-dir")
    @app.post("/pipeline/bulk-import-dir")  # Alias: works with and without /api prefix
    async def bulk_import_directory(data: dict):
        """
        Bulk import from a directory of SEC extraction files.
        
        Expected payload:
        {
            "base_dir": "/opt/data/buffett/data/amzn/extractions/",
            "pattern": "*_clean.json",
            "source_type": "SEC_10K"
        }
        """
        try:
            base_dir = data.get('base_dir')
            pattern = data.get('pattern', '*_clean.json')
            source_type = data.get('source_type', 'SEC_10K')
            
            if not base_dir:
                raise HTTPException(status_code=400, detail="base_dir required")
            
            result = pipeline.bulk_import_from_dir(
                base_dir=base_dir,
                pattern=pattern,
                source_type=source_type,
            )
            
            return result
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.get("/api/config/weights")
    @app.get("/config/weights")  # Alias: works with and without /api prefix
    async def get_weights():
        """Get current scoring weight configuration."""
        try:
            from app.config import get_config
            config = get_config()
            return {
                'weights': {
                    'revenue_growth': config.weights['revenue_growth'],
                    'roic': config.weights['roic'],
                    'margin_expansion': config.weights['margin_expansion'],
                    'reinvestment_runway': config.weights['reinvestment_runway'],
                },
                'normalization': config.normalization_params,
                'classifications': {
                    'COMPETITIVE_COMPOUNDER': '>= 80',
                    'STRONG_BUSINESS': '>= 60',
                    'GOOD_BUSINESS': '>= 40',
                    'WEAK_BUSINESS': '< 40',
                },
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.put("/api/config/weights")
    @app.put("/config/weights")  # Alias: works with and without /api prefix
    async def update_weights(weights: RebalanceWeights):
        """
        Update scoring weights. Changes persist immediately without restart.
        
        All four values must be provided. They will be normalized to sum to 1.0.
        """
        try:
            from app.config import get_config
            
            new_weights = {
                'revenue_growth': weights.revenue_growth if weights.revenue_growth is not None else 0.25,
                'roic': weights.roic if weights.roic is not None else 0.30,
                'margin_expansion': weights.margin_expansion if weights.margin_expansion is not None else 0.20,
                'reinvestment_runway': weights.reinvestment_runway if weights.reinvestment_runway is not None else 0.25,
            }
            
            from app.persistence.db_manager import DatabaseManager
            updated = DatabaseManager.update_weights_db(new_weights, db._db_path)
            
            # Reload config
            get_config.cache_clear()
            config = get_config()
            
            return {
                'success': True,
                'new_weights': {k: config.weights[k] for k in new_weights},
                'total_weight': config.total_weight,
                'requires_recalculation': True,
                'note': 'New weights take effect on next calculate call. Existing scores are immutable.',
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.post("/api/admin/recalculate-all")
    @app.post("/admin/recalculate-all")  # Alias: works with and without /api prefix
    async def recalculate_everything():
        """Full recalculation across all companies and periods."""
        try:
            result = orchestrator.recalculate_all()
            return result
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.get("/api/dashboard/latest")
    @app.get("/dashboard/latest")  # Alias: works with and without /api prefix
    async def dashboard_latest():
        """
        Dashboard overview endpoint — returns summarized data for the main view.
        
        Returns: top compounds, latest class changes, active alerts, system status.
        """
        try:
            # Top compounders
            top_companies_query = db.query("""
                SELECT cs.*, c.ticker, c.name, c.sector
                FROM compounder_scores cs
                JOIN companies c ON c.id = cs.company_id
                WHERE cs.is_latest = 1
                ORDER BY cs.total_score DESC
                LIMIT 20
            """)
            
            # Recent alerts
            recent_alerts = db.get_alerts(limit=10)
            
            # Class changes
            class_changes = db.query("""
                SELECT a.* FROM alerts a
                WHERE a.alert_type = 'CLASS_CHANGE'
                ORDER BY a.created_at DESC
                LIMIT 10
            """)
            
            # System status
            companies_count = db.query("SELECT COUNT(*) as cnt FROM companies")
            scores_count = db.query("SELECT COUNT(*) as cnt FROM compounder_scores")
            
            return {
                'top_compounders': top_companies_query[:10],
                'recent_alerts': recent_alerts,
                'recent_class_changes': list(class_changes),
                'system_status': {
                    'companies_count': companies_count[0]['cnt'] if companies_count else 0,
                    'scores_stored': scores_count[0]['cnt'] if scores_count else 0,
                    'dashboard_version': '1.0.0',
                    'calculated_at': datetime.utcnow().isoformat(),
                },
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    return app
