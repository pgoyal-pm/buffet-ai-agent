"""
Valuation Engine (Part 13 of Spec)

Separate from Compounder Score — never inflates business quality assessment.

Tracks:
- P/E, EV/EBIT, EV/EBITDA, P/S, P/FCF
- FCF yield
- Market Cap, Enterprise Value

Historical valuation ranges (1Y, 3Y, 5Y, 10Y) for percentile analysis.

Output: VALUATION VS COMPOUNDING MATRIX that answers:
"Is current valuation expensive relative to the quality and duration of compounding?"
"""

from typing import Dict, Any, List, Optional
from datetime import datetime


class ValuationEngine:
    """Calculate valuation metrics and historical percentile rankings."""
    
    def __init__(self, db):
        self.db = db
    
    def calculate(self, company_id: int, period_id: int, market_price: float = None) -> Dict[str, Any]:
        """
        Calculate comprehensive valuation metrics.
        
        Returns dict with all multiples, enterprise value, and historical percentiles.
        """
        # Get financial data for the period
        financials = self._get_financial_data(company_id, period_id)
        
        if not financials:
            return {
                'success': False,
                'error': 'No financial data available',
                'metrics': {}
            }
        
        # Core calculations
        market_cap = self._calculate_market_cap(market_price, financials)
        enterprise_value = self._calculate_enterprise_value(financials)
        
        # Calculate all multiples
        metrics = {
            'pe_ratio': self._calc_pe(market_price, financials),
            'ev_ebitda': self._calc_ev_ebitda(enterprise_value, financials),
            'ev_ebit': self._calc_ev_ebit(enterprise_value, financials),
            'ps_ratio': self._calc_ps(market_cap, financials),
            'fcf_yield': self._calc_fcf_yield(market_cap, financials),
            'peg_ratio': self._calc_peg_ratio(market_price, financials),
        }
        
        # Historical percentile analysis
        historical_context = self._get_historical_percentiles(company_id, metrics)
        
        # Composite score vs valuation extremes
        extremity_score = self._assess_valuation_extremity(metrics['pe_ratio'])
        
        return {
            'success': True,
            'market_cap': round(market_cap, 2) if market_cap else None,
            'enterprise_value': round(enterprise_value, 2) if enterprise_value else None,
            'current_multiple': {k: round(v, 2) if v else None for k, v in metrics.items()},
            'historical_context': historical_context,
            'extremity_assessment': {
                'is_expensive': extremity_score > 70,
                'is_cheap': extremity_score < 30,
                'extremity_score': extremity_score,  # 0=special cheap, 100=special expensive
                'assessment': self._classify_extremity(extremity_score),
            },
            'valuation_vs_compounding': self._generate_valuation_matrix(
                company_id, period_id, metrics, historical_context
            ),
            'notes': self._generate_notes(metrics, financials),
        }
    
    def _get_financial_data(self, company_id: int, period_id: int) -> Dict[str, Any]:
        """Fetch all relevant financial data for a period."""
        metrics = self.db.get_metrics_for_period(company_id, period_id)
        result = {}
        
        for m in metrics:
            key = m['metric_key']
            val = m['value']
            if val is not None:
                result[key] = val
        
        # Ensure we have required fields with defaults
        if not result.get('net_income'):
            op_income = result.get('operating_income') or result.get('ebit')
            if op_income:
                tax_rate = result.get('tax_rate', 0.25)
                result['net_income'] = op_income * (1 - tax_rate)
        
        return result
    
    def _calculate_market_cap(self, price_per_share: Optional[float], financials: Dict) -> Optional[float]:
        """Calculate or verify market capitalization."""
        if price_per_share and financials.get('diluted_shares_outstanding'):
            return price_per_share * financials['diluted_shares_outstanding']
        
        if financials.get('market_cap'):
            return financials['market_cap']
        
        if financials.get('equity_value'):
            return financials['equity_value']
        
        return None
    
    def _calculate_enterprise_value(self, financials: Dict) -> Optional[float]:
        """Calculate Enterprise Value = Market Cap + Debt - Cash."""
        market_cap = self._calculate_market_cap(None, financials)
        if not market_cap:
            return None
        
        total_debt = financials.get('total_debt') or (
            (financials.get('long_term_debt') or 0) + 
            (financials.get('current_debt') or 0)
        )
        cash_and_equivalents = financials.get('cash_and_equivalents') or \
                              financials.get('cash_and_short_term_investments') or 0
        
        return market_cap + total_debt - cash_and_equivalents
    
    def _calc_pe(self, price: Optional[float], financials: Dict) -> Optional[float]:
        """P/E ratio = Price per share / EPS."""
        eps = financials.get('diluted_eps') or financials.get('basic_eps')
        if not eps or eps == 0:
            # Alternative: Market Cap / Net Income
            if financials.get('net_income') and financials.get('net_income') != 0:
                mc = self._calculate_market_cap(None, financials)
                return mc / financials['net_income'] if mc else None
            return None
        
        if price:
            return price / eps
        
        # Try to derive from market cap
        mc = self._calculate_market_cap(None, financials)
        if mc and financials.get('net_income'):
            return mc / financials['net_income']
        
        return None
    
    def _calc_ev_ebitda(self, ev: Optional[float], financials: Dict) -> Optional[float]:
        """EV/EBITDA = Enterprise Value / EBITDA."""
        ebitda = financials.get('ebitda') or financials.get('ebit')  # EBIT fallback
        if not ebitda or ebitda == 0:
            return None
        return ev / ebitda if ev else None
    
    def _calc_ev_ebit(self, ev: Optional[float], financials: Dict) -> Optional[float]:
        """EV/EBIT = Enterprise Value / EBIT."""
        ebit = financials.get('ebit') or financials.get('operating_income')
        if not ebit or ebit == 0:
            return None
        return ev / ebit if ev else None
    
    def _calc_ps(self, mc: Optional[float], financials: Dict) -> Optional[float]:
        """P/S = Market Cap / Revenue."""
        revenue = financials.get('revenue') or financials.get('total_revenue')
        if not revenue or revenue == 0:
            return None
        return mc / revenue if mc else None
    
    def _calc_fcf_yield(self, mc: Optional[float], financials: Dict) -> Optional[float]:
        """FCF Yield = Free Cash Flow / Market Cap (as percentage)."""
        fcf = financials.get('free_cash_flow')
        if not fcf or not mc or mc == 0:
            return None
        return (fcf / mc) * 100
    
    def _calc_peg_ratio(self, price: Optional[float], financials: Dict) -> Optional[float]:
        """PEG Ratio = P/E / Revenue Growth Rate (%)."""
        pe = self._calc_pe(price, financials)
        if not pe:
            return None
        
        growth = financials.get('revenue_growth_pct') or financials.get('yoy_growth')
        if not growth or growth == 0:
            return None
        
        return pe / (growth * 100) if isinstance(growth, float) else pe / growth
    
    def _get_historical_percentiles(self, company_id: int, metrics: Dict) -> Dict[str, Any]:
        """Get historical valuation range statistics."""
        hist_metrics = self.db.query("""
            SELECT vh.pe_ratio, vh.ev_ebitda, vh.ps_ratio, vh.calculated_at, fp.period_label
            FROM valuation_history vh
            JOIN fiscal_periods fp ON fp.id = vh.period_id
            WHERE vh.company_id = ?
            ORDER BY vh.calculated_at ASC
        """, (company_id,))
        
        result = {}
        pe_vals = []
        ebitda_vals = []
        ps_vals = []
        
        for row in hist_metrics:
            if row['pe_ratio']: pe_vals.append(row['pe_ratio'])
            if row['ev_ebitda']: ebitda_vals.append(row['ev_ebitda'])
            if row['ps_ratio']: ps_vals.append(row['ps_ratio'])
        
        def calc_percentile(values, current_val):
            if not values:
                return {'count': 0}
            
            sorted_vals = sorted(values)
            n = len(sorted_vals)
            
            p25 = sorted_vals[n // 4]
            median = sorted_vals[n // 2]
            p75 = sorted_vals[3 * n // 4]
            min_v = sorted_vals[0]
            max_v = sorted_vals[-1]
            
            pct = sum(1 for v in sorted_vals if v <= (current_val or 0)) / n * 100
            
            return {
                'count': n,
                'p25': round(p25, 2),
                'median': round(median, 2),
                'p75': round(p75, 2),
                'min': round(min_v, 2),
                'max': round(max_v, 2),
                'current_percentile': round(pct, 1),
                'interpretation': (
                    'VERY CHEAP' if pct < 10 else
                    'CHEAP' if pct < 25 else
                    'FAIR' if pct < 60 else
                    'EXPENSIVE' if pct < 90 else
                    'VERY EXPENSIVE'
                )
            }
        
        if pe_vals:
            result['pe_ratio'] = calc_percentile(pe_vals, metrics.get('pe_ratio'))
        if ebitda_vals:
            result['ev_ebitda'] = calc_percentile(ebitda_vals, metrics.get('ev_ebitda'))
        if ps_vals:
            result['ps_ratio'] = calc_percentile(ps_vals, metrics.get('ps_ratio'))
        
        return result
    
    def _assess_valuation_extremity(self, pe_ratio: Optional[float]) -> float:
        """Assess how extreme the current valuation is (0=special cheap, 100=special expensive)."""
        if pe_ratio is None:
            return 50  # Neutral when unknown
        
        # Non-linear scoring based on P/E benchmarks
        if pe_ratio < 5:
            return 5   # Extremely cheap
        elif pe_ratio < 10:
            return 20  # Cheap
        elif pe_ratio < 15:
            return 35  # Reasonable
        elif pe_ratio < 20:
            return 50  # Fair
        elif pe_ratio < 30:
            return 70  # Expensive
        elif pe_ratio < 50:
            return 85  # Very expensive
        else:
            return 95  # Extremely expensive
    
    def _classify_extremity(self, score: float) -> str:
        """Classify valuation extremity."""
        if score < 20:
            return "SPECIAL_CHEAP"
        elif score < 35:
            return "ATtractive_VALUATION"
        elif score < 65:
            return "FAIR_VALUE"
        elif score < 85:
            return "EXPENSIVE"
        else:
            return "SPECIAL_EXPENSIVE"
    
    def _generate_valuation_matrix(self, company_id: int, period_id: int,
                                    metrics: Dict, historical: Dict) -> Dict[str, Any]:
        """Generate the valuation-vs-compounding matrix."""
        latest_cs = self.db.get_latest_compounder_score(company_id)
        
        if not latest_cs:
            return {'message': 'No compounder score available for matrix'}
        
        cs = latest_cs.get('compounder_score', 0)
        classification = latest_cs.get('classification', '')
        
        pe_pctl = historical.get('pe_ratio', {}).get('current_percentile', None)
        
        # Simple matrix logic
        if cs >= 80:
            quality = "COMPETITIVE_COMPOUNDER"
        elif cs >= 60:
            quality = "STRONG_BUSINESS"
        elif cs >= 40:
            quality = "AVERAGE_BUSINESS"
        else:
            quality = "WEAK_BUSINESS"
        
        if pe_pctl is not None:
            if pe_pctl > 75:
                pricing = "PREMIUM_PRICED"
            elif pe_pctl > 40:
                pricing = "REASONABLY_PRICED"
            elif pe_pctl > 15:
                pricing = "DISCOUNT_PRICED"
            else:
                pricing = "DEEP_DISCOUNT"
        else:
            pricing = "UNKNOWN_PRICING"
        
        return {
            'quality_tier': quality,
            'pricing_assessment': pricing,
            'compounder_score': cs,
            'compounder_classification': classification,
            'key_multiples': {k: v for k, v in metrics.items() if v},
            'historical_median_pe': historical.get('pe_ratio', {}).get('median'),
            'interpretation': self._matrix_interpretation(cs, pe_pctl),
        }
    
    def _matrix_interpretation(self, cs: float, pe_pctile: Optional[float]) -> str:
        """Generate plain-language interpretation of the valuation matrix."""
        parts = []
        
        if cs >= 80 and pe_pctile and pe_pctile > 75:
            parts.append("High-quality compounder at premium multiple.")
        elif cs >= 80 and pe_pctile and pe_pctile < 25:
            parts.append("Excellent compounder potentially undervalued — strong buy candidate.")
        elif cs >= 60 and pe_pctile and pe_pctile > 75:
            parts.append("Strong business but pricey. Wait for better entry point.")
        elif cs >= 60 and pe_pctile and pe_pctile < 25:
            parts.append("Good business at attractive valuation.")
        elif cs < 40 and pe_pctile and pe_pctile < 25:
            parts.append("Deep discount but weak fundamentals — potential value trap.")
        else:
            parts.append("Insufficient data for definitive assessment.")
        
        return " ".join(parts)
    
    def _generate_notes(self, metrics: Dict, financials: Dict) -> List[str]:
        """Generate explanatory notes about the valuation."""
        notes = []
        
        if metrics.get('pe_ratio') and financials.get('net_income', 0) <= 0:
            notes.append("P/E is meaningless for companies with negative earnings")
        
        if metrics.get('fcf_yield') and metrics['fcf_yield'] > 15:
            notes.append("Very high FCF yield may indicate temporary dislocation or structural risk")
        
        if metrics.get('pe_ratio') and metrics.get('ps_ratio'):
            rev_to_net = financials.get('revenue') and financials.get('revenue') > 0
            net_margin_approx = metrics['pe_ratio'] > 0 and metrics['ps_ratio'] > 0 and \
                               metrics['ps_ratio'] / metrics['pe_ratio'] < 1
            if rev_to_net and net_margin_approx:
                implied_net_margin = metrics['ps_ratio'] / metrics['pe_ratio']
                notes.append(f"Implied net margin from multiples: {implied_net_margin:.1%}")
        
        return notes if notes else ["Valuation calculated successfully"]
