"""
Reinvestment Runway Scoring Engine (Part 6 of Spec)

Assesses where the company can deploy the next ₹1 of capital.

Qualitative + quantitative component evaluating:
1. TAM size and growth
2. Current market penetration
3. Market-share opportunity
4. Geographic expansion potential
5. Product/cross-sell opportunities
6. Capacity expansion plans
7. Capital requirements for growth
8. Expected incremental ROIC on new investments

Classifies runway as: VERY_SMALL, SMALL, MODERATE, LARGE, MASSIVE

Key question: "Can the company reinvest a meaningful proportion of earnings 
for many years at attractive incremental returns?"

A large TAM alone does NOT produce a high score — must have:
Opportunity × Ability to invest × Attractive return
"""

from typing import Dict, Any, Optional, List
from app.calcs.base import ScoringEngine


class ReinvestmentRunwayEngine(ScoringEngine):
    """Calculate reinvestment runway score."""
    
    @property
    def name(self) -> str:
        return "reinvestment_runway"
    
    def calculate(self, db, company_id: int, period_id: int) -> Dict[str, Any]:
        """
        Calculate reinvestment runway score.
        
        Returns comprehensive assessment with qualitative and quantitative factors.
        """
        metrics = self._get_financial_data(db, company_id)
        
        if not metrics:
            return {
                'score': 0.0,
                'raw_values': {},
                'details': {'message': 'No financial data available'},
                'data_type': 'INSUFFICIENT_DATA',
                'confidence': 'LOW',
            }
        
        latest = metrics[-1] if metrics else {}
        prior_periods = metrics[:-1] if len(metrics) > 1 else []
        
        # Quantitative components
        free_capital_available = self._calculate_free_capital(latest)
        capital_intensity = self._calculate_capital_intensity(latest)
        reinvestment_rate = self._calculate_reinvestment_rate(latest, prior_periods)
        
        # Qualitative signals from available data
        organic_growth_signal = self._organic_growth_signal(prior_periods)
        capex_trend = self._capex_trend(prior_periods)
        ma_activity = self._ma_activity_indicator(latest, prior_periods)
        customer_metrics = self._customer_growth_signal(prior_periods)
        
        # Calculate scores for each dimension
        capacity_score = self._score_capacity(free_capital_available, latest)
        efficiency_score = self._score_efficiency(reinvestment_rate, prior_periods)
        signal_score = self._signal_strength(
            organic_growth_signal, capex_trend, ma_activity, customer_metrics
        )
        
        # Overall score
        overall = (
            capacity_score * 0.35 +
            efficiency_score * 0.35 +
            signal_score * 0.30
        )
        
        # Classification
        classification = self._classify_runway(overall)
        
        # Confidence adjustments
        confidence = self._compute_confidence(len(metrics), organic_growth_signal, capex_trend)
        
        return {
            'score': round(min(100, max(0, overall)), 2),
            'classification': classification,
            'raw_values': {
                'free_capital': free_capital_available,
                'capital_intensity_pct': round(capital_intensity * 100, 2),
                'reinvestment_rate_pct': round(reinvestment_rate * 100, 2),
                'organic_growth_rate': organic_growth_signal,
                'capex_trend_direction': capex_trend.get('direction'),
                'ma_activity_level': ma_activity,
            },
            'sub_scores': {
                'capacity_score': round(capacity_score, 2),
                'efficiency_score': round(efficiency_score, 2),
                'signal_strength': round(signal_score, 2),
            },
            'factors': {
                'capacity': capacity_score,
                'efficiency': efficiency_score,
                'signals': {
                    'organic_growth': organic_growth_signal,
                    'capex_trend': capex_trend,
                    'ma_activity': ma_activity,
                    'customer_growth': customer_metrics,
                }
            },
            'details': {
                'assessment': self._generate_assessment(classification, overall),
                'questions_to_monitor': self._monitoring_questions(classification),
            },
            'data_type': 'DERIVED',
            'confidence': confidence,
        }
    
    def _get_financial_data(self, db, company_id: int) -> List[Dict]:
        """Fetch ordered financial data for runway analysis."""
        periods = db.query("""
            SELECT fp.id as period_id, mo.metric_key, mo.value,
                   fp.fiscal_year, fp.fiscal_quarter, fp.report_date,
                   fp.period_label
            FROM fiscal_periods fp
            JOIN metric_observations mo ON mo.period_id = fp.id
            WHERE mo.company_id = ?
            ORDER BY fp.fiscal_year DESC, fp.fiscal_quarter DESC
        """, (company_id,))
        
        # Organize by period
        result = {}
        for row in periods:
            pid = row['period_id']
            if pid not in result:
                result[pid] = {
                    'fiscal_year': row['fiscal_year'],
                    'fiscal_quarter': row['fiscal_quarter'],
                    'report_date': row['report_date'],
                    'period_label': row.get('period_label', ''),
                }
            result[pid][row['metric_key']] = row['value']
        
        return list(result.values())
    
    def _calculate_free_capital(self, latest: Dict) -> float:
        """
        Calculate free capital available for reinvestment.
        
        Free Capital = Operating Cash Flow - Dividends - Buybacks
        
        This is the capital the company could potentially deploy at high returns.
        """
        ocf = latest.get('operating_cash_flow') or latest.get('cash_from_operations')
        dividends = latest.get('dividends_paid') or 0
        buybacks = latest.get('share_repurchases') or 0
        
        free_cap = (ocf or 0) - dividends - buybacks
        return max(0, free_cap)
    
    def _calculate_capital_intensity(self, latest: Dict) -> float:
        """
        Calculate capital intensity ratio.
        
        Capital Intensity = CapEx / Revenue
        
        Lower intensity suggests higher runway (less capital needed per unit of revenue).
        """
        capex = latest.get('capex') or latest.get('capital_expenditure')
        revenue = latest.get('revenue') or latest.get('total_revenue')
        
        if not revenue or revenue <= 0:
            return 0.5  # Default neutral
        
        return capex / revenue
    
    def _calculate_reinvestment_rate(self, latest: Dict, prior_periods: List[Dict]) -> float:
        """
        Calculate reinvestment rate over time.
        
        Reinvestment Rate = (CapEx - Depreciation) / Operating Income
        
        Measures what fraction of operating income is being reinvested.
        """
        if not latest:
            return 0.5
        
        capex = latest.get('capex') or latest.get('capital_expenditure')
        depreciation = latest.get('depreciation') or latest.get('amortization') or 0
        op_income = latest.get('operating_income') or latest.get('ebit')
        
        net_investment = (capex or 0) - depreciation
        op_inc = op_income or 1  # Avoid division by zero
        
        if op_inc == 0:
            return 0.5  # Neutral
        
        return min(1.0, max(0, net_investment / op_inc))
    
    def _organic_growth_signal(self, prior_periods: List[Dict]) -> float:
        """Estimate organic growth signal from historical data."""
        if len(prior_periods) < 2:
            return 0.5  # Insufficient data
        
        # Look for consistent top-line growth
        revenues = [p.get('revenue') or p.get('total_revenue') for p in prior_periods]
        valid_revs = [r for r in revenues if r and r > 0]
        
        if len(valid_revs) < 3:
            return 0.5
        
        # Check for consistent growth pattern
        growth_count = 0
        total_periods = len(valid_revs) - 1
        
        for i in range(1, len(valid_revs)):
            if valid_revs[i] >= valid_revs[i-1]:
                growth_count += 1
        
        return growth_count / max(total_periods, 1)
    
    def _capex_trend(self, prior_periods: List[Dict]) -> Dict:
        """Analyze capex trend direction."""
        if len(prior_periods) < 2:
            return {'direction': 'UNKNOWN', 'magnitude': 0}
        
        capexes = [p.get('capex') or p.get('capital_expenditure') or 0 for p in prior_periods]
        
        if capexes[0] <= 0:
            return {'direction': 'NEUTRAL', 'magnitude': 0}
        
        trend = (capexes[-1] - capexes[0]) / abs(capexes[0])
        
        if trend > 0.20:
            direction = 'ACCELERATING'
        elif trend > 0.05:
            direction = 'INCREASING'
        elif trend > -0.05:
            direction = 'STABLE'
        else:
            direction = 'DECREASING'
        
        return {'direction': direction, 'magnitude': round(trend, 2)}
    
    def _ma_activity_indicator(self, latest: Dict, prior_periods: List[Dict]) -> str:
        """Detect M&A activity from balance sheet changes."""
        # Look for goodwill/acquisition increases
        goodwill_current = latest.get('goodwill') or 0
        goodwill_prior = prior_periods[0].get('goodwill') or 0 if prior_periods else 0
        
        if goodwill_current > goodwill_prior * 1.2 and goodwill_current > 0:
            return 'ACTIVE'
        
        # Look for sudden asset base expansion
        assets_current = latest.get('total_assets') or 0
        assets_prior = prior_periods[0].get('total_assets') or 0 if prior_periods else 0
        
        if assets_prior > 0 and (assets_current - assets_prior) / assets_prior > 0.30:
            return 'SIGNIFICANT'
        
        return 'MINIMAL'
    
    def _customer_growth_signal(self, prior_periods: List[Dict]) -> str:
        """Estimate customer growth signal (proxy-based when direct data unavailable)."""
        # If we have user/customer count metrics
        user_counts = [p.get('customers') or p.get('active_users') for p in prior_periods]
        valid_users = [u for u in user_counts if u and u > 0]
        
        if len(valid_users) >= 2:
            if valid_users[-1] > valid_users[0] * 1.2:
                return 'GROWING'
            elif valid_users[-1] < valid_users[0] * 0.9:
                return 'DECLINING'
            else:
                return 'STABLE'
        
        # Fallback: infer from revenue growth vs revenue per user stability
        return 'UNKNOWN'
    
    def _score_capacity(self, free_capital: float, latest: Dict) -> float:
        """Score based on absolute capital available for reinvestment."""
        if free_capital <= 0:
            return 10.0  # No free capital = very limited runway
        
        revenue = latest.get('revenue') or latest.get('total_revenue') or 1
        
        # Scale relative to company size
        ratio = free_capital / max(revenue, 1)
        
        if ratio > 0.20:
            return 90
        elif ratio > 0.10:
            return 75
        elif ratio > 0.05:
            return 60
        elif ratio > 0.01:
            return 40
        else:
            return 25
    
    def _score_efficiency(self, reinvestment_rate: float, prior_periods: List[Dict]) -> float:
        """Score based on efficient use of reinvestment."""
        # High reinvestment rate = strong desire to compound
        if reinvestment_rate > 0.50:
            base = 80
        elif reinvestment_rate > 0.25:
            base = 65
        elif reinvestment_rate > 0.10:
            base = 50
        else:
            base = 30
        
        # Bonus if reinvestment is growing (accelerating investment)
        capexes = [p.get('capex') or 0 for p in prior_periods[-3:]]
        if len(capexes) >= 2 and capexes[0] > 0:
            if capexes[-1] > capexes[0] * 1.1:
                base = min(100, base + 10)
        
        return base
    
    def _signal_strength(self, organic_growth: float, capex_trend: Dict, 
                         ma_activity: str, customer_signal: str) -> float:
        """Combine qualitative signals into single score."""
        score = 0
        checks = 0
        
        # Organic growth signal
        if organic_growth > 0.7:
            score += 90
        elif organic_growth > 0.5:
            score += 60
        elif organic_growth > 0.3:
            score += 40
        else:
            score += 20
        checks += 1
        
        # Capex trend
        direction = capex_trend.get('direction', 'UNKNOWN')
        if direction in ('ACCELERATING', 'INCREASING'):
            score += 80
        elif direction == 'STABLE':
            score += 50
        elif direction == 'DECREASING':
            score += 30
        checks += 1
        
        # M&A activity
        if ma_activity == 'ACTIVE':
            score += 70
        elif ma_activity == 'SIGNIFICANT':
            score += 55
        else:
            score += 35
        checks += 1
        
        # Customer growth
        if customer_signal == 'GROWING':
            score += 75
        elif customer_signal == 'STABLE':
            score += 50
        elif customer_signal == 'DECLINING':
            score += 20
        else:
            score += 35  # Unknown = neutral penalty
        checks += 1
        
        return max(0, min(100, score / checks))
    
    def _classify_runway(self, score: float) -> str:
        """Classify runway category."""
        if score >= 80:
            return "MASSIVE"
        elif score >= 60:
            return "LARGE"
        elif score >= 40:
            return "MODERATE"
        elif score >= 20:
            return "SMALL"
        else:
            return "VERY_SMALL"
    
    def _generate_assessment(self, classification: str, score: float) -> str:
        """Generate narrative assessment."""
        assessments = {
            'MASSIVE': f"The company has substantial opportunity to deploy significant capital at attractive returns. Score: {score:.1f}/100.",
            'LARGE': f"The company has strong reinvestment opportunities ahead. Score: {score:.1f}/100.",
            'MODERATE': f"The company has moderate reinvestment runway. Growth exists but may be constrained. Score: {score:.1f}/100.",
            'SMALL': f"The company's reinvestment opportunities are limited. It should focus on efficient capital allocation rather than aggressive expansion. Score: {score:.1f}/100.",
            'VERY_SMALL': f"The company has minimal reinvestment runway. Consider whether it should focus on dividends/buybacks instead. Score: {score:.1f}/100.",
        }
        return assessments.get(classification, "Unable to assess runway.")
    
    def _monitoring_questions(self, classification: str) -> List[str]:
        """Questions to monitor next quarter."""
        questions = {
            'MASSIVE': [
                "Where specifically will the next ₹1 of capital be deployed?",
                "What is the expected incremental ROIC on new investments?",
                "Is management executing on stated expansion plans?",
            ],
            'LARGE': [
                "Are expansion plans accelerating or decelerating?",
                "What competitive moats protect the reinvestment opportunity?",
            ],
            'MODERATE': [
                "Is the runway expanding or contracting?",
                "Should management consider M&A to extend runway?",
            ],
            'SMALL': [
                "Why is the runway shrinking? Market saturation or strategic shift?",
                "Should capital be returned to shareholders instead?",
            ],
            'VERY_SMALL': [
                "Is this a mature business model deserving shareholder returns?",
                "Any adjacent market opportunities to explore?",
            ]
        }
        return questions.get(classification, ["Monitor reinvestment trends."])
    
    def _compute_confidence(self, n_periods: int, organic_signal: float, capex_trend: Dict) -> str:
        """Compute confidence level based on data quality."""
        base = "HIGH" if n_periods >= 5 else "MEDIUM" if n_periods >= 3 else "LOW"
        
        # Adjust for missing signals
        if organic_signal == 0.5 and capex_trend.get('direction') == 'UNKNOWN':
            if base == "HIGH":
                base = "MEDIUM"
        
        return base
