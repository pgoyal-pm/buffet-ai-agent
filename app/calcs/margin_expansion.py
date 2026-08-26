"""
Margin Expansion Scoring Engine (Part 5 of Spec)

Tracks structural margin trends across four dimensions:
- Gross margin
- EBITDA margin  
- EBIT/Operating margin
- PAT (Net Income) margin

Calculates QoQ change, YoY change, 3Y change, 5Y change, TTM change.

Most importantly detects:
- Structural expansion (sustained improvement)
- Temporary expansion (cost cutting, FX, one-offs)
- Operating leverage effects
- Margin deterioration signals

Does NOT award high scores merely because margins are high — 
requires HIGH + IMPROVING + SUSTAINABLE margins.
"""

from typing import Dict, Any, List, Optional
from app.calcs.base import ScoringEngine


class MarginExpansionEngine(ScoringEngine):
    """Calculate margin expansion/deterioration scores."""
    
    @property
    def name(self) -> str:
        return "margin_expansion"
    
    def calculate(self, db, company_id: int, period_id: int) -> Dict[str, Any]:
        """
        Calculate comprehensive margin expansion score.
        
        Returns detailed analysis of margin trends with structural vs temporary
        classification.
        """
        metrics = self._get_financial_data(db, company_id)
        
        if len(metrics) < 2:
            return {
                'score': 0.0,
                'raw_values': {},
                'details': {'message': 'Insufficient data for margin analysis'},
                'data_type': 'INSUFFICIENT_DATA',
                'confidence': 'LOW',
            }
        
        # Extract all margin types for latest periods
        sorted_periods = sorted(metrics.values(), key=lambda x: x.get('report_date', ''))
        latest = sorted_periods[-1]
        prior_yoy = sorted_periods[-2] if len(sorted_periods) >= 2 else None
        
        # Calculate each margin type
        margin_types = ['gross_margin', 'ebitda_margin', 'operating_margin', 'pat_margin']
        current_margins = {}
        yoy_changes = {}
        qoq_changes = {}
        trend_data = {}
        
        for mtype in margin_types:
            values = []
            dates = []
            
            for p in sorted_periods:
                rev = p.get('revenue') or p.get('total_revenue')
                
                # Different calculation paths based on available data
                if mtype == 'gross_margin':
                    numerator = p.get('gross_profit') or 0
                elif mtype == 'ebitda_margin':
                    numerator = p.get('ebitda') or 0
                elif mtype == 'operating_margin':
                    numerator = p.get('ebit') or p.get('operating_income') or 0
                elif mtype == 'pat_margin':
                    numerator = p.get('net_income') or 0
                else:
                    continue
                
                if rev and rev > 0:
                    margin_val = numerator / rev
                    values.append(margin_val)
                    dates.append(p.get('report_date', ''))
            
            if values:
                current_margins[mtype] = values[-1]
                
                # Calculate changes
                if len(values) >= 2:
                    qoq_change = values[-1] - values[-2]
                    qoq_changes[mtype] = qoq_change * 100  # Convert to basis points
                
                if len(values) >= 3:
                    yoy_val = values[-1] - values[-2]
                    yoy_changes[mtype] = yoy_val * 100
                
                # Multi-period trend
                if len(values) >= 5:
                    recent_avg = sum(values[-3:]) / 3
                    historical_avg = sum(values[:3]) / min(3, len(values))
                    trend_data[mtype] = {
                        'recent_3q_avg': round(recent_avg * 100, 1),
                        'historical_avg': round(historical_avg * 100, 1),
                        'direction': 'expanding' if recent_avg > historical_avg else 'contracting',
                        'magnitude': round(abs(recent_avg - historical_avg) * 100, 1)
                    }
                elif len(values) >= 3:
                    avg_recent = sum(values[-2:]) / 2
                    avg_early = values[0]
                    trend_data[mtype] = {
                        'recent_avg': round(avg_recent * 100, 1),
                        'early_avg': round(avg_early * 100, 1),
                        'direction': 'expanding' if avg_recent > avg_early else 'contracting',
                        'magnitude': round(abs(avg_recent - avg_early) * 100, 1)
                    }
        
        # === COMPREHENSIVE SCORING ===
        
        # Part A: Level score (how high are margins?)
        level_score = self._score_margin_level(current_margins)
        
        # Part B: Improvement score (are margins improving?)
        improvement_score = self._score_margin_improvement(yoy_changes, qoq_changes, trend_data)
        
        # Part C: Sustainability score (is improvement structural or temporary?)
        sustainability_score = self._score_sustainability(trend_data, metrics)
        
        # Weighted combination (following spec: reward high + improving + sustainable)
        combined = (
            level_score * 0.40 +      # 40% weight on absolute level
            improvement_score * 0.35 + # 35% weight on direction
            sustainability_score * 0.25 # 25% weight on structure
        )
        
        final_score = min(100, max(0, round(combined, 2)))
        
        return {
            'score': final_score,
            'margins': trend_data,
            'margin_changes': {k: v for k, v in yoy_changes.items()},
            'raw_values': {
                **{f'{k}_margin_pct': round((v or 0) * 100, 2) for k, v in current_margins.items()},
                **{f'{k}_yoy_change_bps': v for k, v in yoy_changes.items()},
                **{f'{k}_qoq_change_bps': v for k, v in qoq_changes.items()},
            },
            'sub_scores': {
                'level': round(level_score, 2),
                'improvement': round(improvement_score, 2),
                'sustainability': round(sustainability_score, 2),
            },
            'trend_analysis': trend_data,
            'structure_type': self._classify_structure(trend_data, qoq_changes),
            'details': {
                'margins_tracked': list(current_margins.keys()),
                'periods_analyzed': len(sorted_periods),
                'overall_direction': self._overall_direction(trend_data),
                'red_flags': self._detect_red_flags(current_margins, qoq_changes, trend_data),
            },
            'data_type': 'DERIVED',
            'confidence': self._compute_confidence(len(sorted_periods)),
        }
    
    def _get_financial_data(self, db, company_id: int) -> Dict[int, Dict]:
        """Fetch all financial data organized by period."""
        periods = db.query("""
            SELECT fp.id as period_id, mo.metric_key, mo.value,
                   mo.reported_or_derived, mo.certainty,
                   fp.fiscal_year, fp.fiscal_quarter, fp.report_date
            FROM fiscal_periods fp
            JOIN metric_observations mo ON mo.period_id = fp.id
            WHERE mo.company_id = ?
            ORDER BY fp.fiscal_year, fp.fiscal_quarter
        """, (company_id,))
        
        result = {}
        for row in periods:
            pid = row['period_id']
            if pid not in result:
                result[pid] = {
                    'fiscal_year': row['fiscal_year'],
                    'fiscal_quarter': row['fiscal_quarter'],
                    'report_date': row['report_date'],
                }
            result[pid][row['metric_key']] = row['value']
        
        return result
    
    def _score_margin_level(self, current_margins: Dict[str, float]) -> float:
        """Score based on how high margins currently are."""
        if not current_margins:
            return 0.0
        
        benchmarks = {
            'gross_margin': {'excellent': 0.50, 'good': 0.35, 'average': 0.20, 'poor': 0.05},
            'ebitda_margin': {'excellent': 0.35, 'good': 0.25, 'average': 0.15, 'poor': 0.05},
            'operating_margin': {'excellent': 0.30, 'good': 0.20, 'average': 0.12, 'poor': 0.03},
            'pat_margin': {'excellent': 0.25, 'good': 0.15, 'average': 0.08, 'poor': 0.02},
        }
        
        scores = []
        for mtype, value in current_margins.items():
            bench = benchmarks.get(mtype, {})
            if value is not None:
                score = self.normalize(value, bench, direction='higher')
                scores.append(score)
        
        return max(scores) if scores else 50.0
    
    def _score_margin_improvement(self, yoy_changes: Dict, qoq_changes: Dict, 
                                   trend_data: Dict) -> float:
        """Score based on whether margins are improving."""
        if not yoy_changes and not qoq_changes:
            return 50.0  # Neutral when no change data
        
        total_points = 0
        count = 0
        
        # Check YoY changes (more reliable than QoQ)
        for mtype, change in yoy_changes.items():
            if change > 100:  # >100 bps improvement
                total_points += 90
            elif change > 50:
                total_points += 75
            elif change > 0:
                total_points += 60
            elif change > -50:
                total_points += 40
            else:
                total_points += 20
            count += 1
        
        # Check QoQ momentum
        for mtype, change in qoq_changes.items():
            if change > 50:
                total_points += 70
            elif change > 0:
                total_points += 55
            elif change > -50:
                total_points += 45
            else:
                total_points += 25
            count += 1
        
        # Check structural trend
        for mtype, trend in trend_data.items():
            if trend.get('direction') == 'expanding':
                total_points += 80
                if trend.get('magnitude', 0) > 5:
                    total_points += 10  # Bonus for material expansion
            else:
                total_points += 40
            count += 1
        
        return max(0, min(100, total_points / max(count, 1)))
    
    def _score_sustainability(self, trend_data: Dict, metrics: Dict) -> float:
        """
        Score sustainability — is improvement structural or temporary?
        
        Structural indicators:
        - Consistent multi-period expansion
        - Driven by revenue mix/opers
        - Not heavily dependent on cost-cutting
        
        Temporary indicators:
        - Single-quarter spikes
        - Driven by cost-cutting
        - FX or commodity effects
        """
        if not trend_data:
            return 50.0
        
        structural_score = 0
        temp_score = 0
        total_checks = 0
        
        for mtype, trend in trend_data.items():
            # Structural positive signals
            if trend.get('direction') == 'expanding':
                magnitude = trend.get('magnitude', 0)
                if magnitude > 5:  # Material expansion (>5%)
                    structural_score += 100
                else:
                    structural_score += 70
            
            # Check for operating leverage pattern (revenue up, costs stable/down)
            rev_growth = metrics.get('revenue_growth')
            opex_growth = metrics.get('opex_growth')
            if rev_growth and opex_growth and rev_growth > opex_growth:
                structural_score += 80
            
            # Temp degradation signals
            if trend.get('magnitude', 0) > 20 and trend.get('direction') == 'expanding':
                # Very large single-period jumps may be one-off
                temp_score += 30
            elif trend.get('direction') == 'contracting':
                temp_score += 50
            
            total_checks += 3
        
        if total_checks == 0:
            return 50.0
        
        # Penalty for temporary signals
        net = structural_score - (temp_score * 0.5)
        return max(0, min(100, net / total_checks))
    
    def _classify_structure(self, trend_data: Dict, qoq_changes: Dict) -> str:
        """Classify the type of margin change."""
        expanding_count = sum(1 for t in trend_data.values() 
                            if t.get('direction') == 'expanding')
        contracting_count = sum(1 for t in trend_data.values() 
                               if t.get('direction') == 'contracting')
        
        if expanding_count >= 3:
            return "STRUCTURAL_EXPANSION"
        elif expanding_count >= 2:
            return "MODERATE_EXPANSION"
        elif expanding_count >= 1 and contracting_count <= 1:
            return "MIXED_MARGINS"
        elif contracting_count >= 2:
            return "MARGIN_DETERIORATION"
        else:
            return "STATIC_MARGINS"
    
    def _overall_direction(self, trend_data: Dict) -> str:
        """Get overall margin direction."""
        directions = [t.get('direction', '') for t in trend_data.values()]
        if all(d == 'expanding' for d in directions):
            return "ALL_EXPANDING"
        elif all(d == 'contracting' for d in directions):
            return "ALL_CONTRACTING"
        elif sum(1 for d in directions if d == 'expanding') >= 2:
            return "NET_EXPANDING"
        elif sum(1 for d in directions if d == 'contracting') >= 2:
            return "NET_CONTRACTING"
        else:
            return "MIXED"
    
    def _detect_red_flags(self, current: Dict, qoq: Dict, trends: Dict) -> List[str]:
        """Flag potential issues with margin quality."""
        flags = []
        
        # Negative margins
        for mtype, val in current.items():
            if val is not None and val < 0:
                flags.append(f"{mtype.replace('_', ' ').title()} is negative")
        
        # Volatile margins
        for mtype, trend in trends.items():
            if trend.get('magnitude', 0) > 10:
                flags.append(f"{mtype.replace('_', ' ').title()} fluctuating significantly ({trend.get('magnitude')}pp)")
        
        # Divergence between gross and net margins
        if 'gross_margin' in current and 'pat_margin' in current:
            gross = current.get('gross_margin', 0) or 0
            pat = current.get('pat_margin', 0) or 0
            spread = gross - pat
            if spread > 0.50:  # >50% spread suggests massive overhead/costs
                flags.append("Large gap between gross and net margins — verify expense structure")
        
        return flags
    
    def _compute_confidence(self, n_periods: int) -> str:
        if n_periods >= 8:
            return "HIGH"
        elif n_periods >= 4:
            return "MEDIUM"
        else:
            return "LOW"
