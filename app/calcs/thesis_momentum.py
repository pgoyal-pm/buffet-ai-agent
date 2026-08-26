"""
Thesis Momentum Scoring Engine (Part 8 of Spec)

Every new quarter answers: "Is the investment thesis getting stronger or weaker?"

Compares latest period with:
- Previous quarter
- Same quarter last year
- Previous 4-quarter average
- 3-year trend
- Original investment thesis baseline

Classifies: STRONGLY_STRENGTHENING / STRENGTHENING / STABLE / WEAKENING / STRONGLY_WEAKENING / BROKEN

Tracks multiple dimensions:
Revenue, Margins, ROIC, Incremental ROIC, Reinvestment, Market Share,
Customer Economics, Capital Allocation, Balance Sheet, Regulatory Risks

Generates a comprehensive momentum score (0-100).
"""

from typing import Dict, Any, List, Optional
from app.calcs.base import ScoringEngine


class ThesisMomentumEngine(ScoringEngine):
    """Calculate thesis momentum score."""
    
    @property
    def name(self) -> str:
        return "thesis_momentum"
    
    def calculate(self, db, company_id: int, period_id: int, 
                  compounder_score: float = None) -> Dict[str, Any]:
        """
        Calculate thesis momentum by comparing current state across multiple baselines.
        
        Returns comprehensive analysis with dimension-by-dimension comparison.
        """
        # Get all periods for this company
        periods = db.query("""
            SELECT fp.*, 
                   mo.metric_key, mo.value, mo.reported_or_derived, mo.certainty
            FROM fiscal_periods fp
            LEFT JOIN metric_observations mo ON mo.period_id = fp.id AND mo.company_id = ?
            WHERE fp.company_id = ?
            ORDER BY fp.report_date DESC
        """, (company_id, company_id))
        
        # Organize by period
        period_map = {}
        for row in periods:
            pid = row['id']
            if pid not in period_map:
                period_map[pid] = {
                    'report_date': row['report_date'],
                    'fiscal_year': row['fiscal_year'],
                    'fiscal_quarter': row['fiscal_quarter'],
                    'period_label': row.get('period_label', ''),
                }
            if row['metric_key']:
                period_map[pid][row['metric_key']] = row['value']
        
        sorted_pids = sorted(period_map.keys(), key=lambda p: period_map[p]['report_date'], reverse=True)
        
        if not sorted_pids:
            return {
                'score': 50.0,
                'classification': 'NO_DATA',
                'dimension_scores': {},
                'details': {'message': 'No financial data available'},
            }
        
        latest_pid = sorted_pids[0]
        prior_q_pid = sorted_pids[1] if len(sorted_pids) > 1 else None
        prior_yoq_pid = sorted_pids[3] if len(sorted_pids) > 3 else None
        recent_avg_pid = sorted_pids[:4] if len(sorted_pids) >= 4 else sorted_pids
        
        latest = period_map[latest_pid]
        prior_q = period_map[prior_q_pid] if prior_q_pid else {}
        prior_yoq = period_map[prior_yoq_pid] if prior_yoq_pid else {}
        
        # Calculate dimension scores
        dimension_results = {
            'revenue_growth': self._evaluate_dimension(latest, prior_q, prior_yoq, recent_avg_pid, 'revenue'),
            'gross_margin': self._evaluate_margin_change(latest, prior_q, prior_yoq, 'gross_profit'),
            'operating_margin': self._evaluate_margin_change(latest, prior_q, prior_yoq, 'operating_income'),
            'net_margin': self._evaluate_margin_change(latest, prior_q, prior_yoq, 'net_income'),
            'roic_trend': self._evaluate_roic_trend(latest, prior_q, 'invested_capital', 'nopat'),
            'capex_efficiency': self._evaluate_capex_efficiency(latest, prior_q),
            'balance_sheet_strength': self._evaluate_balance_sheet(latest, prior_q),
            'cash_generation': self._evaluate_cash_generation(latest, prior_q),
        }
        
        # Weight dimensions based on relevance
        weights = {
            'revenue_growth': 0.25,
            'gross_margin': 0.10,
            'operating_margin': 0.15,
            'net_margin': 0.15,
            'roic_trend': 0.15,
            'capex_efficiency': 0.10,
            'balance_sheet_strength': 0.08,
            'cash_generation': 0.07,
        }
        
        # Compute weighted momentum score
        total_weighted = 0.0
        total_weight = 0.0
        
        for dim, result in dimension_results.items():
            w = weights.get(dim, 0.1)
            total_weighted += result['score'] * w
            total_weight += w
        
        momentum_score = total_weighted / max(total_weight, 0.01)
        
        # Classification
        classification = self._classify_momentum(momentum_score)
        
        # Cross-check against compounder score change
        if compounder_score is not None:
            cs_change = self._get_compounder_score_change(db, company_id, period_id)
            if abs(cs_change) > 5:
                # Significant CS change should correlate with thesis momentum
                if (cs_change > 0 and momentum_score < 50) or (cs_change < 0 and momentum_score > 50):
                    notes = ["Compounder Score moved significantly but thesis momentum differs — investigate divergence"]
                else:
                    notes = ["Compounder Score movement aligns with thesis momentum assessment"]
            else:
                notes = ["Compounder Score stable — thesis should be consistent with status quo"]
        else:
            notes = []
        
        return {
            'score': round(momentum_score, 2),
            'classification': classification,
            'dimension_scores': {k: v['score'] for k, v in dimension_results.items()},
            'dimension_details': dimension_results,
            'comparison': {
                'compared_to_prior_q': prior_q_pid,
                'compared_to_yoy': prior_yoq_pid,
                'baseline_periods_analyzed': min(len(recent_avg_pid), 4),
            },
            'notes': notes,
            'data_type': 'DERIVED',
            'confidence': self._compute_confidence(len(sorted_pids)),
        }
    
    def _get_data_value(self, period: Dict, metric_key: str) -> Optional[float]:
        """Extract value from period dict."""
        return period.get(metric_key)
    
    def _evaluate_dimension(self, latest: Dict, prior_q: Dict, prior_yoq: Dict,
                            recent_avg_pids: List[int], metric: str) -> Dict[str, Any]:
        """Evaluate a single dimension's momentum."""
        current = self._get_data_value(latest, metric)
        prior_q_val = self._get_data_value(prior_q, metric)
        prior_yoq_val = self._get_data_value(prior_yoq, metric)
        
        rev_latest = self._get_data_value(latest, 'revenue') or self._get_data_value(latest, 'total_revenue')
        rev_prior_q = self._get_data_value(prior_q, 'revenue') or self._get_data_value(prior_q, 'total_revenue')
        
        # Revenue growth direction
        if current and prior_q_val and rev_latest and rev_prior_q:
            qoq_current = (current - prior_q_val) / prior_q_val if prior_q_val else 0
            qoq_rev = (rev_latest - rev_prior_q) / rev_prior_q if rev_prior_q else 0
            
            score = self._directional_score(qoq_current, qoq_rev)
            trend = "STRENGTHENING" if score > 60 else "WEAKENING" if score < 40 else "STABLE"
        elif current and prior_yoq_val:
            yoy = (current - prior_yoq_val) / prior_yoq_val if prior_yoq_val else 0
            score = self._directional_score(yoy, yoy)
            trend = "STRENGTHENING" if score > 60 else "WEAKENING" if score < 40 else "STABLE"
        else:
            score = 50.0
            trend = "UNKNOWN"
        
        return {
            'score': score,
            'trend': trend,
            'change_detected': qoq_current if 'qoq_current' in locals() else None,
        }
    
    def _evaluate_margin_change(self, latest: Dict, prior_q: Dict, prior_yoq: Dict,
                                  numerator_key: str) -> Dict[str, Any]:
        """Evaluate margin expansion/contraction."""
        current_num = self._get_data_value(latest, numerator_key)
        prior_q_num = self._get_data_value(prior_q, numerator_key)
        current_rev = self._get_data_value(latest, 'revenue') or self._get_data_value(latest, 'total_revenue')
        prior_q_rev = self._get_data_value(prior_q, 'revenue') or self._get_data_value(prior_q, 'total_revenue')
        
        if current_num and prior_q_num and current_rev and prior_q_rev and current_rev > 0 and prior_q_rev > 0:
            current_margin = current_num / current_rev
            prior_margin = prior_q_num / prior_q_rev
            margin_change = (current_margin - prior_margin) * 100  # Basis points
            
            if margin_change > 5:
                score = 80
                trend = "EXPANDING"
            elif margin_change > 0:
                score = 60
                trend = "SLIGHTLY_EXPANDING"
            elif margin_change > -5:
                score = 40
                trend = "SLIGHTLY_CONTRACTING"
            else:
                score = 20
                trend = "CONTRACTING"
        else:
            score = 50.0
            trend = "INSUFFICIENT_DATA"
        
        return {
            'score': score,
            'trend': trend,
            'margin_change_bps': margin_change if 'margin_change' in locals() else None,
        }
    
    def _evaluate_roic_trend(self, latest: Dict, prior_q: Dict, cap_key: str, nopat_key: str) -> Dict[str, Any]:
        """Evaluate ROIC trend direction."""
        latest_nopat = self._get_data_value(latest, nopat_key)
        latest_cap = self._get_data_value(latest, cap_key)
        prior_nopat = self._get_data_value(prior_q, nopat_key)
        prior_cap = self._get_data_value(prior_q, cap_key)
        
        if latest_nopat and latest_cap and prior_nopat and prior_cap:
            try:
                latest_roic = latest_nopat / latest_cap if latest_cap != 0 else 0
                prior_roic = prior_nopat / prior_cap if prior_cap != 0 else 0
                
                roic_change = (latest_roic - prior_roic) * 100
                if roic_change > 1:
                    score = 80
                    trend = "ROIC_EXPANDING"
                elif roic_change > 0:
                    score = 60
                    trend = "ROIC_STABLE_POSITIVE"
                elif roic_change > -1:
                    score = 40
                    trend = "ROIC_SLIGHTLY_DECLINING"
                else:
                    score = 20
                    trend = "ROIC_DECLINING"
            except ZeroDivisionError:
                score = 50.0
                trend = "CALCULATION_ERROR"
        else:
            score = 50.0
            trend = "DATA_UNAVAILABLE"
        
        return {
            'score': score,
            'trend': trend,
        }
    
    def _evaluate_capex_efficiency(self, latest: Dict, prior_q: Dict) -> Dict[str, Any]:
        """Evaluate capital expenditure efficiency."""
        capex = self._get_data_value(latest, 'capex') or self._get_data_value(latest, 'capital_expenditure')
        ocf = self._get_data_value(latest, 'operating_cash_flow')
        fcf = self._get_data_value(latest, 'free_cash_flow')
        
        if capex and ocf and ocf > 0:
            capex_ratio = capex / ocf
            if capex_ratio > 0.3:
                score = 60  # Reinvesting aggressively
            elif capex_ratio > 0.1:
                score = 75  # Efficient reinvestment
            else:
                score = 50  # Minimal reinvestment
        elif fcf and fcf > 0:
            score = 60  # Generating free cash flow is positive
        else:
            score = 40  # No clear signal
        
        return {
            'score': score,
            'trend': 'AGGRESSIVE_REINVESTMENT' if capex_ratio > 0.2 else 'EFFICIENT_CAPITAL_ALLOCATION',
        }
    
    def _evaluate_balance_sheet(self, latest: Dict, prior_q: Dict) -> Dict[str, Any]:
        """Evaluate balance sheet strength trend."""
        assets = self._get_data_value(latest, 'total_assets')
        equity = self._get_data_value(latest, 'shareholders_equity') or self._get_data_value(latest, 'stockholders_equity')
        debt = self._get_data_value(latest, 'long_term_debt') or self._get_data_value(latest, 'total_debt')
        
        if equity and assets:
            equity_ratio = equity / assets if assets > 0 else 0
            if equity_ratio > 0.6:
                score = 90  # Strong balance sheet
            elif equity_ratio > 0.4:
                score = 70
            elif equity_ratio > 0.2:
                score = 50
            else:
                score = 30  # Highly leveraged
        else:
            score = 50.0
        
        return {
            'score': score,
            'equity_ratio_pct': round(equity_ratio * 100, 1) if 'equity_ratio' in locals() else None,
        }
    
    def _evaluate_cash_generation(self, latest: Dict, prior_q: Dict) -> Dict[str, Any]:
        """Evaluate cash generation momentum."""
        ocf = self._get_data_value(latest, 'operating_cash_flow')
        prev_ocf = self._get_data_value(prior_q, 'operating_cash_flow')
        
        if ocf and prev_ocf and prev_ocf > 0:
            growth = (ocf - prev_ocf) / prev_ocf
            if growth > 0.10:
                score = 85
                trend = "ACCELERATING"
            elif growth > 0:
                score = 65
                trend = "GROWING"
            elif growth > -0.10:
                score = 45
                trend = "DECLINING"
            else:
                score = 25
                trend = "SHRINKING"
        elif ocf and ocf > 0:
            score = 70
            trend = "POSITIVE_CASH_FLOW"
        else:
            score = 40
            trend = "NEGATIVE_OR_INSUFFICIENT"
        
        return {
            'score': score,
            'trend': trend,
            'ocf_growth_pct': round(growth * 100, 1) if 'growth' in locals() else None,
        }
    
    def _directional_score(self, current_change: float, baseline_change: float) -> float:
        """Score how well the current metric performs relative to baseline."""
        if current_change > baseline_change + 0.05:
            return 80  # Significantly better than baseline
        elif current_change > baseline_change:
            return 65  # Better than baseline
        elif current_change > baseline_change - 0.05:
            return 50  # Near baseline
        elif current_change > baseline_change - 0.10:
            return 35  # Worse than baseline
        else:
            return 20  # Much worse than baseline
    
    def _classify_momentum(self, score: float) -> str:
        """Classify thesis momentum."""
        if score >= 85:
            return "STRONGLY_STRENGTHENING"
        elif score >= 70:
            return "STRENGTHENING"
        elif score >= 55:
            return "STABLE"
        elif score >= 40:
            return "WEAKENING"
        elif score >= 25:
            return "STRONGLY_WEAKENING"
        else:
            return "BROKEN"
    
    def _get_compounder_score_change(self, db, company_id: int, period_id: int) -> float:
        """Get change in Compounder Score from prior quarter."""
        scores = db.fetchall("""
            SELECT compounder_score, period_label 
            FROM compounder_scores 
            WHERE company_id = ? 
            ORDER BY data_timestamp DESC
        """, (company_id,))
        
        if len(scores) >= 2:
            return scores[-1]['compounder_score'] - scores[0]['compounder_score']
        return 0.0
    
    def _compute_confidence(self, n_periods: int) -> str:
        if n_periods >= 8:
            return "HIGH"
        elif n_periods >= 4:
            return "MEDIUM"
        elif n_periods >= 2:
            return "LOW"
        else:
            return "VERY_LOW"
