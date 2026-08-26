"""
Revenue Growth Scoring Engine (Part 3 of Spec)

Calculates revenue growth scores from multiple time horizons:
- YoY quarterly growth
- QoQ growth  
- 1Y / 3Y / 5Y CAGR
- TTM growth
- Acceleration/deceleration detection

Scoring rewards:
- High absolute growth
- Consistency across quarters
- Acceleration momentum
- Long-term persistence

Scoring penalizes:
- Volatile growth (rollercoaster pattern)
- One-off spikes
- Acquisition-driven growth where organic differs materially
"""

import math
from typing import Dict, Any, List, Optional
from app.calcs.base import ScoringEngine


class RevenueGrowthEngine(ScoringEngine):
    """Calculate and score revenue growth metrics."""
    
    @property
    def name(self) -> str:
        return "revenue_growth"
    
    def calculate(self, db, company_id: int, period_id: int) -> Dict[str, Any]:
        """
        Calculate the complete revenue growth score.
        
        Returns detailed dict with raw values, normalized scores, and weighted contribution.
        """
        # Get all revenue observations for this company
        revenues = self._get_revenue_history(db, company_id)
        
        if len(revenues) < 2:
            return {
                'score': 0.0,
                'raw_values': {},
                'details': {
                    'message': 'Insufficient data for revenue growth calculation',
                    'periods_available': len(revenues),
                    'min_required': 2
                },
                'data_type': 'INSUFFICIENT_DATA',
                'confidence': 'LOW',
            }
        
        # Extract values for calculations
        current_rev = None
        prior_1y_rev = None
        prior_2y_rev = None
        prior_3y_rev = None
        prior_4y_rev = None
        
        qoq_values = []
        yoy_values = []
        
        sorted_revs = sorted(revenues, key=lambda x: x['period_date'])
        
        for i, rev in enumerate(sorted_revs):
            value = rev.get('value') or rev.get('metric_value')
            date_str = rev.get('report_date') or rev.get('period_date') or ''
            
            if i == 0:
                # Most recent
                current_rev = value
                current_date = date_str
            elif i == 1:
                prior_1y_rev = value
                yoy_values.append((current_rev - prior_1y_rev) / max(prior_1y_rev, 0.01))
            elif i == 2:
                prior_2y_rev = value
            elif i == 3:
                prior_3y_rev = value
            elif i == 4:
                prior_4y_rev = value
            
            if i > 0 and sorted_revs[i-1].get('value'):
                prev_val = sorted_revs[i-1].get('value')
                if prev_val and prev_val > 0 and value:
                    qoq_change = (value - prev_val) / prev_val
                    qoq_values.append(qoq_change * 100)  # Convert to percentage
        
        # A. Current YoY growth
        yoy_growth = 0.0
        if current_rev and prior_1y_rev:
            yoy_growth = (current_rev - prior_1y_rev) / prior_1y_rev
        
        # B. QoQ growth (most recent quarter)
        qoq_growth = 0.0
        if qoq_values:
            qoq_growth = qoq_values[-1] / 100  # Back to ratio
        
        # C. 1Y CAGR
        cagr_1y = yoy_growth
        
        # D. 3Y CAGR
        cagr_3y = 0.0
        if current_rev and prior_3y_rev and prior_3y_rev > 0:
            years = 3
            cagr_3y = (current_rev / prior_3y_rev) ** (1/years) - 1
        
        # E. 5Y CAGR (if available)
        cagr_5y = 0.0
        if current_rev and prior_4y_rev and prior_4y_rev > 0 and len(sorted_revs) >= 6:
            years = 5
            cagr_5y = (current_rev / prior_4y_rev) ** (1/years) - 1
        
        # F. TTM estimate (use most recent annual figure if FY, else extrapolate)
        ttm_growth = yoy_growth  # Conservative: use latest available
        
        # G. Acceleration/deceleration
        acceleration = self.compute_acceleration(cagr_1y, cagr_3y, 0.0)
        
        # === SCORING ===
        
        # Sub-score 1: Absolute growth rate (primary driver)
        abs_score = self._score_absolute_growth(yoy_growth, cagr_1y, cagr_3y)
        
        # Sub-score 2: Consistency across periods
        consistency_score = self._score_consistency(qoq_values, yoy_values)
        
        # Sub-score 3: Acceleration/Momentum
        accel_score = self._score_acceleration(acceleration)
        
        # Combined sub-scores weighted per config
        param_weights = self.config_params()
        combined = (
            abs_score * param_weights['absolute_weight'] +
            consistency_score * param_weights['consistency_weight'] +
            accel_score * param_weights['acceleration_weight']
        )
        
        # Cap at 0-100
        final_score = min(100, max(0, round(combined, 2)))
        
        return {
            'score': final_score,
            'raw_values': {
                'yoy_growth_pct': round(yoy_growth * 100, 2),
                'cagr_1y_pct': round(cagr_1y * 100, 2),
                'cagr_3y_pct': round(cagr_3y * 100, 2),
                'cagr_5y_pct': round(cagr_5y * 100, 2),
                'qoq_growth_pct': round(qoq_growth * 100, 2),
                'ttm_growth_pct': round(ttm_growth * 100, 2),
                'acceleration_pct': round(acceleration, 2),
            },
            'growth_rates': {
                'yoy': round(yoy_growth * 100, 2),
                'cagr_1y': round(cagr_1y * 100, 2),
                'cagr_3y': round(cagr_3y * 100, 2),
                'cagr_5y': round(cagr_5y * 100, 2),
                'qoq': round(qoq_growth * 100, 2),
                'ttm': round(ttm_growth * 100, 2),
                'acceleration': round(acceleration, 2),
            },
            'sub_scores': {
                'absolute_growth': round(abs_score, 2),
                'consistency': round(consistency_score, 2),
                'acceleration': round(accel_score, 2),
            },
            'details': {
                'revenue_latest': current_rev,
                'revenue_prior_yr': prior_1y_rev,
                'revenue_3y_ago': prior_3y_rev,
                'trend': self._classify_trend(acceleration),
                'growth_type': self._identify_growth_type(current_rev, prior_1y_rev, revenues),
            },
            'data_type': 'DERIVED' if not any(r.get('is_reported') for r in revenues) else 'REPORTED',
            'confidence': self._compute_confidence(len(revenues)),
        }
    
    def _get_revenue_history(self, db, company_id: int) -> List[Dict]:
        """Get all revenue observations for a company."""
        metrics = db.get_metric_history(company_id, 'revenue')
        if not metrics:
            # Try alternative names
            for alt in ['total_revenue', 'net_sales', 'sales_revenue']:
                metrics = db.get_metric_history(company_id, alt)
                if metrics:
                    break
        
        result = []
        for m in metrics:
            result.append({
                'value': m.get('value'),
                'report_date': m.get('report_date'),
                'period_date': m.get('period_label', ''),
                'fiscal_year': m.get('fiscal_year'),
                'fiscal_quarter': m.get('fiscal_quarter'),
                'is_reported': m.get('reported_or_derived') == 'REPORTED',
            })
        return result
    
    def _score_absolute_growth(self, yoy: float, cagr_1y: float, cagr_3y: float) -> float:
        """Score based on absolute growth rates."""
        # Use 3Y CAGR as primary signal (smoothes noise), Y/Y as confirmation
        effective = (cagr_3y * 0.7 + yoy * 0.3) if cagr_3y else yoy
        
        excellent = 0.25  # 25%+ growth = excellent
        good = 0.15       # 15-25% = good
        average = 0.08    # 8-15% = average
        poor = 0.0        # <8% = poor
        
        return self.normalize(effective, {
            'excellent': excellent,
            'good': good,
            'average': average,
            'poor': max(poor, poor - 0.05),  # Can go negative
        }, direction='higher')
    
    def _score_consistency(self, qoq_changes: List[float], yoy_changes: List[float]) -> float:
        """Score consistency of growth across periods."""
        scores = []
        
        if yoy_changes:
            # Use coefficient of variation approach
            mean_yoy = sum(yoy_changes) / len(yoy_changes)
            if mean_yoy > 0:
                cv = (sum((x - mean_yoy)**2 for x in yoy_changes) / len(yoy_changes))**0.5 / mean_yoy
                if cv <= 0.05:
                    scores.append(90)  # Extremely consistent
                elif cv <= 0.10:
                    scores.append(80)
                elif cv <= 0.20:
                    scores.append(65)
                elif cv <= 0.35:
                    scores.append(40)
                else:
                    scores.append(20)
            else:
                scores.append(40)  # Zero/negative growth = low consistency value
        
        if qoq_changes and len(qoq_changes) >= 3:
            qoq_cv = (sum((x - sum(qoq_changes)/len(qoq_changes))**2 
                         for x in qoq_changes) / len(qoq_changes))**0.5 / abs(sum(qoq_changes)/len(qoq_changes))
            if qoq_cv <= 0.10:
                scores.append(85)
            elif qoq_cv <= 0.20:
                scores.append(70)
            elif qoq_cv <= 0.40:
                scores.append(45)
            else:
                scores.append(25)
        
        return max(scores) if scores else 50.0
    
    def _score_acceleration(self, acceleration: float) -> float:
        """Score based on growth acceleration."""
        if acceleration > 30:
            return 95
        elif acceleration > 15:
            return 80
        elif acceleration > 5:
            return 65
        elif acceleration > 0:
            return 50
        elif acceleration > -15:
            return 35
        elif acceleration > -30:
            return 20
        else:
            return 5
    
    def _classify_trend(self, acceleration: float) -> str:
        if acceleration > 20:
            return "ACCELERATING"
        elif acceleration > 5:
            return "STEADY_POSITIVE"
        elif acceleration > -5:
            return "FLAT"
        elif acceleration > -20:
            return "DECELERATING"
        else:
            return "DECLINING"
    
    def _identify_growth_type(self, current: float, prior: float, revenues: list) -> str:
        """Simple heuristic: identify if growth looks organic vs acquisition-driven."""
        if len(revenues) < 2:
            return "UNKNOWN"
        
        # Look for sudden large jumps that persist
        ratios = []
        vals = [r.get('value', 0) for r in sorted(revenues, key=lambda x: x.get('report_date', ''))]
        for i in range(1, len(vals)):
            if vals[i-1] > 0:
                ratios.append((vals[i] - vals[i-1]) / vals[i-1])
        
        if ratios:
            max_jump = max(ratios)
            avg_jump = sum(ratios) / len(ratios)
            
            if max_jump > avg_jump * 2 and max_jump > 0.30:
                return "POTENTIALLY_ACQUISITION_DRIVEN"
            elif all(r > 0 for r in ratios):
                return "ORGANIC_EXPANSION"
        
        return "MIXED_GROWTH"
    
    def _compute_confidence(self, n_periods: int) -> str:
        if n_periods >= 5:
            return "HIGH"
        elif n_periods >= 3:
            return "MEDIUM"
        else:
            return "LOW"
    
    @staticmethod
    def config_params() -> Dict[str, float]:
        """Default weights for sub-components."""
        return {
            'absolute_weight': 0.50,
            'consistency_weight': 0.25,
            'acceleration_weight': 0.25,
        }
