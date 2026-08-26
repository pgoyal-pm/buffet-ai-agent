"""
ROIC / Incremental ROIC Scoring Engine (Part 4 of Spec)

Calculates two primary metrics:
1. Current ROIC (Return on Invested Capital) = NOPAT / Invested Capital
2. Incremental ROIC / MECI = ΔNOPAT / ΔInvested Capital over multiple windows (1Y, 3Y, 5Y)

Handles edge cases for financial companies and flags unusual results caused by:
- acquisitions, divestitures, accounting changes, restructuring
- large cash balances, financial-company balance sheets
"""

import math
from typing import Dict, Any, List, Optional
from app.calcs.base import ScoringEngine


class ROICEngine(ScoringEngine):
    """Calculate ROIC and Incremental ROIC scores."""
    
    @property
    def name(self) -> str:
        return "roic"
    
    def calculate(self, db, company_id: int, period_id: int) -> Dict[str, Any]:
        """
        Calculate ROIC and incremental ROIC from raw observations.
        
        Returns comprehensive dict with current ROIC, incremental ROIC values,
        and normalized scores.
        """
        # Get required financial observations scoped to target period
        metrics = self._get_financial_data(db, company_id, limit_period_id=period_id)
        
        if not metrics or len(metrics) < 2:
            return {
                'score': 0.0,
                'raw_values': {},
                'details': {'message': 'Insufficient financial data for ROIC calculation'},
                'data_type': 'INSUFFICIENT_DATA',
                'confidence': 'LOW',
            }
        
        # Build period lookup
        periods = sorted(metrics.values(), key=lambda x: x.get('report_date', ''))
        latest = periods[-1] if periods else {}
        
        # Calculate current ROIC using NOPAT and Invested Capital
        roic_1y = self._calculate_roic_for_period(latest, metrics)
        
        # Calculate incremental ROIC over multiple windows
        incremental_roic_1y = self._calculate_incremental_roic(periods, window=1)
        incremental_roic_3y = self._calculate_incremental_roic(periods, window=3) if len(periods) >= 4 else None
        incremental_roic_5y = self._calculate_incremental_roic(periods, window=5) if len(periods) >= 6 else None
        
        # Calculate weighted ROIC score using both current and incremental
        score = self._score_roic(roic_1y, incremental_roic_1y, incremental_roic_3y, incremental_roic_5y, metrics)
        
        # Detect anomalies
        anomalies = self._detect_anomalies(roic_1y, incremental_roic_1y, metrics)
        
        # Financial company flag
        is_financial = self._is_financial_company(db, company_id)
        if is_financial:
            notes = "Financial company — standard ROIC may not apply; consider industry-specific framework"
        else:
            notes = ""
        
        return {
            'score': round(score, 2),
            'raw_values': {
                'current_roic_pct': round((roic_1y or 0) * 100, 2),
                'incremental_roic_1y_pct': round((incremental_roic_1y or 0) * 100, 2),
                'incremental_roic_3y_pct': round((incremental_roic_3y or 0) * 100, 2) if incremental_roic_3y else None,
                'incremental_roic_5y_pct': round((incremental_roic_5y or 0) * 100, 2) if incremental_roic_5y else None,
            },
            'details': {
                'nopat': latest.get('nopat'),
                'invested_capital': latest.get('invested_capital'),
                'cost_of_capital': metrics.get('cost_of_capital'),
                'anomalies': anomalies,
                'notes': notes,
                'is_financial': is_financial,
            },
            'data_type': 'DERIVED',
            'confidence': self._compute_confidence(len(periods)),
        }
    
    def _get_financial_data(self, db, company_id: int, limit_period_id: Optional[int] = None) -> Dict[int, Dict]:
        """Fetch all available financial data for this company, scoped to period_id if provided."""
        if limit_period_id:
            where_clause = "WHERE mo.company_id = ? AND fp.id <= ?"
        else:
            where_clause = "WHERE mo.company_id = ?"
        params = (company_id, limit_period_id) if limit_period_id else (company_id,)
        
        periods = db.query(f"""
            SELECT fp.id as period_id, mo.metric_key, mo.value, 
                   mo.reported_or_derived, mo.certainty,
                   fp.fiscal_year, fp.fiscal_quarter, fp.report_date
            FROM fiscal_periods fp
            JOIN metric_observations mo ON mo.period_id = fp.id
            {where_clause}
            ORDER BY fp.fiscal_year, fp.fiscal_quarter
        """, params)
        
        # Organize by period
        result = {}
        for row in periods:
            pid = row['period_id']
            if pid not in result:
                result[pid] = {
                    'fiscal_year': row['fiscal_year'],
                    'fiscal_quarter': row['fiscal_quarter'],
                    'report_date': row['report_date'],
                }
            metric_name = row['metric_key']
            result[pid][metric_name] = row['value']
            result[pid][f'{metric_name}_reported'] = row['reported_or_derived']
        
        return result
    
    def _calculate_roic_for_period(self, period: Dict, all_periods: Dict) -> Optional[float]:
        """Calculate ROIC = NOPAT / Invested Capital for a single period."""
        nopat = period.get('nopat')
        invested_capital = period.get('invested_capital')
        
        if not nopat or not invested_capital or invested_capital == 0:
            # Calculate NOPAT if available components exist
            net_income = period.get('net_income')
            tax_rate = period.get('tax_rate') or 0.25  # Default assumption
            operating_leases = period.get('operating_leases') or 0
            
            if net_income:
                nopat_calc = net_income * (1 - tax_rate) + operating_leases
                nopat = nopat_calc
            
            # Calculate Invested Capital if available components exist
            total_assets = period.get('total_assets')
            current_liabilities = period.get('current_liabilities')
            excess_cash = period.get('excess_cash') or 0
            
            if total_assets and current_liabilities:
                cap_excl_cash = total_assets - current_liabilities - excess_cash
                invested_capital = cap_excl_cash if cap_excl_cash > 0 else None
        
        if not nopat or not invested_capital or invested_capital <= 0:
            return None
        
        return nopat / invested_capital
    
    def _calculate_incremental_roic(self, periods: List[Dict], window: int) -> Optional[float]:
        """
        Calculate Incremental ROIC = ΔNOPAT / ΔInvestedCapital over N-year window.
        
        This measures how efficiently the company converts additional capital into new earnings.
        """
        if len(periods) < window + 1:
            return None
        
        start_idx = -window - 1
        end_idx = -1
        
        try:
            nopat_start = periods[start_idx].get('nopat') or self._calc_nopat(periods[start_idx])
            nopat_end = periods[end_idx].get('nopat') or self._calc_nopat(periods[end_idx])
            
            inv_cap_start = periods[start_idx].get('invested_capital') or self._calc_invested_capital(periods[start_idx])
            inv_cap_end = periods[end_idx].get('invested_capital') or self._calc_invested_capital(periods[end_idx])
            
            delta_nopat = nopat_end - nopat_start
            delta_capital = inv_cap_end - inv_cap_start
            
            if delta_capital == 0 or abs(delta_capital) < 1e-6:
                return None  # No capital change to measure
            
            if delta_capital < 0 and delta_nopat > 0:
                # Shrinking capital base with higher earnings = positive signal
                return 1.0  # Cap at perfect
            
            return delta_nopat / delta_capital
            
        except (IndexError, ZeroDivisionError):
            return None
    
    def _calc_nopat(self, period: Dict) -> Optional[float]:
        """Calculate NOPAT from available components."""
        ebit = period.get('ebit') or period.get('operating_income')
        tax_rate = period.get('tax_rate') or 0.25
        
        if ebit:
            return ebit * (1 - tax_rate)
        return None
    
    def _calc_invested_capital(self, period: Dict) -> Optional[float]:
        """Calculate Invested Capital from balance sheet items."""
        # Prefer explicit value if available
        if period.get('invested_capital'):
            return period['invested_capital']
        
        total_assets = period.get('total_assets')
        current_liabilities = period.get('current_liabilities')
        excess_cash = period.get('cash_and_equivalents', 0) * 0.5  # Assume half is excess
        
        if total_assets and current_liabilities:
            inv_cap = total_assets - current_liabilities - excess_cash
            return inv_cap if inv_cap > 0 else None
        
        # Fallback: shareholders_equity + debt - cash
        equity = period.get('shareholders_equity')
        debt = period.get('long_term_debt') or period.get('total_debt')
        cash = period.get('cash_and_equivalents')
        
        if equity and debt and cash:
            return equity + debt - cash
        elif equity and debt:
            return equity + debt
        
        return None
    
    def _score_roic(self, current: Optional[float], incr_1y: Optional[float], 
                    incr_3y: Optional[float], incr_5y: Optional[float],
                    metrics: Dict) -> float:
        """Score ROIC based on both current level and incremental trends."""
        scores = []
        weights = [0.35, 0.30, 0.20, 0.15]  # Weight each component
        
        benchmarks = self._get_benchmarks(metrics)
        
        if current:
            scores.append(self.normalize_roic(current, benchmarks))
        
        if incr_1y:
            scores.append(self.normalize_roic(incr_1y, benchmarks, lower_is_worse=True))
        
        if incr_3y:
            scores.append(self.normalize_roic(incr_3y, benchmarks, lower_is_worse=True))
        
        if incr_5y:
            scores.append(self.normalize_roic(incr_5y, benchmarks, lower_is_worse=True))
        
        if not scores:
            return 0.0
        
        # Weighted average
        total_weight = sum(w for s, w in zip(scores, weights) if s != 0)
        weighted_sum = sum(s * w for s, w in zip(scores, weights[:len(scores)]))
        
        if total_weight == 0:
            return 0.0
        
        return min(100, max(0, weighted_sum / total_weight * 100))
    
    def normalize_roic(self, roic: float, benchmarks: Dict, lower_is_worse: bool = False) -> float:
        """Normalize ROIC to 0-100 scale."""
        excellent = benchmarks.get('excellent', 0.20)
        good = benchmarks.get('good', 0.15)
        average = benchmarks.get('average', 0.10)
        poor = benchmarks.get('poor', 0.05)
        
        # For incremental ROIC, very high can be suspicious (one-off)
        if roic >= excellent:
            base_score = 90
        elif roic >= good:
            base_score = 75
        elif roic >= average:
            base_score = 60
        elif roic >= poor:
            base_score = 40
        elif roic >= 0:
            base_score = 20
        else:
            base_score = 0
        
        # Adjust for diminishing returns on extremely high incremental ROIC
        if lower_is_worse and roic > excellent * 2:
            base_score = max(60, base_score - 10)  # Diminishing returns penalty
        
        return base_score
    
    def _get_benchmarks(self, metrics: Dict) -> Dict:
        """Get appropriate benchmarks based on company type."""
        company_type = metrics.get('company_type', 'INDUSTRIAL')
        benchmarks_map = {
            'INDUSTRIAL': {'excellent': 0.30, 'good': 0.20, 'average': 0.15, 'poor': 0.10},
            'TECH': {'excellent': 0.25, 'good': 0.18, 'average': 0.12, 'poor': 0.08},
            'FINANCIAL': {'excellent': 0.15, 'good': 0.10, 'average': 0.07, 'poor': 0.05},
        }
        return benchmarks_map.get(company_type, benchmarks_map['INDUSTRIAL'])
    
    def _detect_anomalies(self, roic: Optional[float], incremental: Optional[float], metrics: Dict) -> List[str]:
        """Detect anomalies that might affect ROIC reliability."""
        anomalies = []
        
        if roic is None and incremental is None:
            anomalies.append("Missing NOPAT or Invested Capital")
        
        # Check for acquisition effects
        goodwill_acq = metrics.get('goodwill', 0) + metrics.get('acquisition_premium', 0)
        total_assets = metrics.get('total_assets')
        if total_assets and goodwill_acq / max(total_assets, 0.01) > 0.30:
            anomalies.append("Significant goodwill/acquisition impact on capital base")
        
        # Check for restructuring charges
        restructuring = metrics.get('restructuring_charges')
        if restructuring and restructuring > metrics.get('net_income', 0) * 0.20:
            anomalies.append("Restructuring charges may distort earnings")
        
        return anomalies
    
    def _is_financial_company(self, db, company_id: int) -> bool:
        """Check if company is classified as financial."""
        company = db.fetchone("SELECT company_type FROM companies WHERE id = ?", (company_id,))
        return company and company['company_type'] == 'FINANCIAL'
    
    def _compute_confidence(self, n_periods: int) -> str:
        if n_periods >= 5:
            return "HIGH"
        elif n_periods >= 3:
            return "MEDIUM"
        else:
            return "LOW"
