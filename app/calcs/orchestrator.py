"""
Scoring Orchestrator — Ties all engines together

Single entry point that:
1. Takes a company + period as input
2. Calls each scoring engine independently
3. Combines weighted scores into final Compounder Score
4. Calculates thesis momentum
5. Persists all results to database
6. Triggers alerts if thresholds are breached

Design rules:
- Engines returning None (data unavailable) → signal DATA_INCOMPLETE
- A score of 0 is NOT valid for missing inputs
- Classification derives EXCLUSIVELY from the latest Compounder Score
- STRONG_BUSINESS / WEAK_BUSINESS must NEVER appear in classification
"""

from typing import Dict, Any, List, Optional
from datetime import datetime
import json


class ScoringOrchestrator:
    """Orchestrates all scoring engines into a unified calculation pipeline."""
    
    def __init__(self, db):
        self.db = db
        
        # Import engines lazily to avoid circular imports
        from app.calcs.revenue_growth import RevenueGrowthEngine
        from app.calcs.roic import ROICEngine
        from app.calcs.margin_expansion import MarginExpansionEngine
        from app.calcs.reinvestment_runway import ReinvestmentRunwayEngine
        from app.calcs.thesis_momentum import ThesisMomentumEngine
        from app.calcs.valuation import ValuationEngine
        
        self.engines = {
            'revenue_growth': RevenueGrowthEngine(),
            'roic': ROICEngine(),
            'margin_expansion': MarginExpansionEngine(),
            'reinvestment_runway': ReinvestmentRunwayEngine(),
            'thesis_momentum': ThesisMomentumEngine(),
        }
        self.valuation_engine = ValuationEngine(db)
        
        # Alert system
        from app.calcs.alerts import AlertSystem
        self.alert_system = AlertSystem(db)
        
        # Get config weights
        from app.config import get_config
        self.config = get_config()
        
        # Core engines that feed the Compounder Score
        self.core_engine_names = ['revenue_growth', 'roic', 'margin_expansion', 'reinvestment_runway']
    
    def calculate_all(self, company_id: int, period_id: int) -> Dict[str, Any]:
        """
        Calculate complete scoring suite for a company-period combination.
        
        Returns comprehensive result dict with all engine outputs,
        compounder score, thesis momentum, and alert triggers.
        """
        result = {
            'company_id': company_id,
            'period_id': period_id,
            'calculated_at': datetime.utcnow().isoformat(),
            'engines': {},
            'compounder_score': None,
            'classification': None,
            'data_complete': True,  # Will be False if any core engine lacks data
            'missing_data_reasons': [],
            'thesis_momentum': None,
            'alerts_generated': [],
            'success': True,
            'errors': [],
        }
        
        try:
            # Run all scoring engines independently
            engine_results = {}
            
            for name, engine in self.engines.items():
                try:
                    weight = engine.max_weight
                    calc_result = engine.calculate_weighted(self.db, company_id, period_id)
                    
                    engine_results[name] = {
                        **calc_result,
                        'weight': weight,
                        'weighted_contribution': calc_result.get('weighted_score', 0),
                    }
                    
                except Exception as e:
                    engine_results[name] = {
                        'score': None,  # NOT 0 — None signals missing data
                        'weighted_score': None,
                        'error': str(e),
                        'details': {'message': f'Engine failed: {e}'},
                    }
                    result['errors'].append(f"{name}: {e}")
                    if name in self.core_engine_names:
                        result['data_complete'] = False
            
            result['engines'] = engine_results
            
            # Calculate Compounder Score from four core engines
            cs_result = self._calculate_compounder_score(engine_results)
            result.update(cs_result)
            
            # Calculate thesis momentum (depends on other scores)
            try:
                comp_score = cs_result.get('compounder_score')
                if comp_score not in (None, 'DATA_INCOMPLETE'):
                    momentum_result = self.engines['thesis_momentum'].calculate(
                        self.db, company_id, period_id, comp_score
                    )
                    
                    result['thesis_momentum'] = momentum_result
                    
                    # Persist thesis momentum
                    self.db.upsert_momentum_score(
                        company_id=company_id,
                        period_id=period_id,
                        prev_period_id=self._get_prev_period_id(company_id, period_id),
                        momentum_score=momentum_result.get('score', 0),
                        classification=momentum_result.get('classification', 'UNKNOWN'),
                        trend_data=momentum_result.get('dimension_details', {}),
                    )
                    
            except Exception as e:
                result['errors'].append(f"thesis_momentum: {e}")
            
            # Persist compounder score (only if we have one)
            if cs_result.get('compounder_score') is not None:
                self.db.upsert_compounder_score(
                    company_id=company_id,
                    period_id=period_id,
                    version="v1.0",
                    revenue_score=engine_results.get('revenue_growth', {}).get('score', 0),
                    roic_score=engine_results.get('roic', {}).get('score', 0),
                    margin_score=engine_results.get('margin_expansion', {}).get('score', 0),
                    runway_score=engine_results.get('reinvestment_runway', {}).get('score', 0),
                    weighted_contribution_json=self._json_safe(cs_result.get('weighted_contribution', {})),
                    total_score=cs_result['compounder_score'],
                    classification=cs_result.get('classification'),
                    data_complete=result.get('data_complete', True),
                    data_incomplete_reasons=result.get('missing_data_reasons', []),
                    notes=f"Calculated at {datetime.utcnow().isoformat()}",
                )
            
            # Calculate valuation (separate from compounder score)
            try:
                market_price = self._get_current_price(company_id)
                val_result = self.valuation_engine.calculate(
                    company_id, period_id, market_price
                )
                engine_results['valuation'] = val_result
                result['valuation'] = val_result
            except Exception as e:
                result['errors'].append(f"valuation: {e}")
            
            # Check for alerts using full result including engines, momentum, valuation
            alerts = self.alert_system.check_all(company_id, result)
            result['alerts_generated'] = alerts
            
        except Exception as e:
            result['success'] = False
            result['errors'].append(f"Orchestration failed: {e}")
        
        return result
    
    def _calculate_compounder_score(self, engine_results: Dict) -> Dict[str, Any]:
        """Calculate final Compounder Score from individual engine scores.
        
        Rules:
        - If any core engine returns None (missing data) → DATA_INCOMPLETE
        - Score of 0 is NEVER treated as valid for a missing engine
        - Only CLASSIFICATION_THRESHOLDS apply; STRONG/WEAK_BUSINESS forbidden
        """
        contributions = {}
        weighted_total = 0.0
        total_weight = 0.0
        missing_engines = []
        
        for name in self.core_engine_names:
            eng_result = engine_results.get(name, {})
            score = eng_result.get('score')
            weight = eng_result.get('weight', 0)
            
            if score is None:
                # Missing data — do NOT default to 0
                contributions[name] = {
                    'raw_score': None,
                    'weight': weight,
                    'weighted_contribution': None,
                    'status': 'MISSING_DATA',
                }
                missing_engines.append(name)
                continue
            
            weighted_contribution = score * weight
            weighted_total += weighted_contribution
            total_weight += weight
            
            contributions[name] = {
                'raw_score': score,
                'weight': weight,
                'weighted_contribution': weighted_contribution,
                'status': 'OK',
            }
        
        # If any core engine is missing data, mark as DATA_INCOMPLETE
        if missing_engines:
            return {
                'compounder_score': None,
                'classification': 'DATA_INCOMPLETE',
                'weighted_contribution': contributions,
                'total_weight': total_weight,
                'data_complete': False,
                'missing_data_reasons': [
                    f"{name} has no available data" for name in missing_engines
                ],
            }
        
        if total_weight == 0:
            # All engines returned but somehow no weight was used
            return {
                'compounder_score': 0.0,
                'classification': 'INSUFFICIENT_DATA',
                'weighted_contribution': contributions,
                'total_weight': 0,
            }
        
        compounder_score = weighted_total / total_weight
        classification = self.config.classification(compounder_score)
        
        return {
            'compounder_score': round(compounder_score, 2),
            'classification': classification,
            'weighted_contribution': contributions,
            'total_weight': total_weight,
            'data_complete': True,
        }
    
    def _get_prev_period_id(self, company_id: int, current_period_id: int) -> Optional[int]:
        """Get the previous period ID for this company."""
        periods = self.db.query("""
            SELECT id FROM fiscal_periods 
            WHERE company_id = ? AND id < ?
            ORDER BY report_date DESC
            LIMIT 1
        """, (company_id, current_period_id))
        
        if periods:
            return periods[0]['id']
        return None
    
    def _get_current_price(self, company_id: int) -> Optional[float]:
        """Get current/latest market price for a company."""
        latest = self.db.query("""
            SELECT m.value FROM metric_observations m
            JOIN fiscal_periods fp ON fp.id = m.period_id
            WHERE m.company_id = ? AND m.metric_key IN ('current_price', 'market_price')
            ORDER BY fp.report_date DESC
            LIMIT 1
        """, (company_id,))
        
        if latest and latest[0].get('value'):
            return float(latest[0]['value'])
        return None
    
    @staticmethod
    def _json_safe(obj) -> str:
        """Convert object to JSON string safely."""
        try:
            return json.dumps(obj)
        except (TypeError, ValueError):
            return "{}"
    
    def recalculate_company(self, company_id: int) -> List[Dict[str, Any]]:
        """
        Recalculate scores for all periods of a company.
        Used for bulk reprocessing when methodology changes.
        """
        periods = self.db.get_periods_for_company(company_id)
        results = []
        
        for period in periods:
            result = self.calculate_all(company_id, period['id'])
            results.append(result)
        
        return results
    
    def recalculate_all(self) -> Dict[str, int]:
        """Recalculate scores for all companies and all periods."""
        companies = self.db.list_companies()
        total_success = 0
        total_failed = 0
        
        for company in companies:
            try:
                self.recalculate_company(company['id'])
                total_success += 1
            except Exception as e:
                total_failed += 1
        
        return {
            'companies_processed': total_success,
            'failed': total_failed,
            'timestamp': datetime.utcnow().isoformat(),
        }


def create_orchestrator(db) -> ScoringOrchestrator:
    """Factory function for creating orchestrator instance."""
    return ScoringOrchestrator(db)
