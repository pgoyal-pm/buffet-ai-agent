"""
Alert System (Part 15 of Spec)

Automated alerting for significant changes:
- Compounder Score moves > 5 points quarter-over-quarter
- Class boundary crossings (e.g., Strong → Weak)
- Thesis momentum shifts
- Valuation extremes (top/bottom 10% historical percentile)
- Revenue decline warnings
- Margin collapse alerts
- ROIC deterioration signals

Each alert stores: company_id, timestamp, level, type, message, triggered_by, resolved
"""

import json
from typing import Dict, Any, Optional, List
from datetime import datetime


class AlertSystem:
    """Manages alert generation, storage, and retrieval."""
    
    def __init__(self, db):
        self.db = db
    
    def check_all(self, company_id: int, current_results: Dict[str, Any]) -> List[Dict]:
        """
        Check if any alert conditions are met after a calculation run.
        
        Args:
            company_id: The company being evaluated
            current_results: Complete orchestrator results
            
        Returns:
            List of generated alert dicts
        """
        alerts = []
        
        # Get latest compounder score
        cs = current_results.get('compounder_score')
        if cs is None or cs == 'N/A':
            return alerts
        
        prev_scores = self.db.get_compounder_scores(company_id)
        if len(prev_scores) < 2:
            return alerts
        
        prev_score = prev_scores[-2].get('compounder_score', 0)
        
        # --- Alert Type: Significant Score Change ---
        score_change = abs(cs - prev_score)
        if score_change > 5:
            direction = "increased" if cs > prev_score else "decreased"
            
            snapshot_data = json.dumps({
                'current_score': cs, 
                'previous_score': prev_score, 
                'change': round(score_change, 1)
            })
            
            alert = {
                'company_id': company_id,
                'alert_level': 'CRITICAL' if score_change > 10 else 'IMPORTANT' if score_change > 7 else 'INFO',
                'alert_type': 'SCORE_CHANGE',
                'message': f"Compounder Score changed by {score_change:.1f} ({direction}) from {prev_score:.1f} to {cs:.1f}",
                'triggered_by': 'Quarterly recalculation',
                'data_snapshot_json': snapshot_data,
                'generated_at': datetime.utcnow().isoformat(),
            }
            self.db.create_alert(**alert)
            alerts.append(alert)
        
        # --- Alert Type: Class Boundary Crossing ---
        new_class = current_results.get('classification', '')
        if new_class and new_class not in ('INSUFFICIENT_DATA', 'N/A'):
            prev_class = prev_scores[-2].get('classification', '')
            
            if prev_class != new_class and prev_class not in ('INSUFFICIENT_DATA', 'N/A'):
                alert = {
                    'company_id': company_id,
                    'alert_level': 'IMPORTANT',
                    'alert_type': 'CLASS_CHANGE',
                    'message': f"Compounder classification changed: {prev_class} → {new_class}",
                    'triggered_by': 'Class threshold crossing',
                }
                self.db.create_alert(**alert)
                alerts.append(alert)
        
        # --- Alert Type: Thesis Momentum Shift ---
        momentum = current_results.get('thesis_momentum', {})
        if momentum:
            mom_class = momentum.get('classification', '')
            valid_classes = ['STRONGLY_STRENGTHENING', 'STRENGTHENING', 'STABLE', 
                           'WEAKENING', 'STRONGLY_WEAKENING', 'BROKEN']
            
            prev_momentums = self.db.get_momentum_scores(company_id)
            if prev_momentums and mom_class in valid_classes:
                prev_mom_class = prev_momentums[-1].get('classification', '')
                
                if prev_mom_class in valid_classes:
                    curr_idx = valid_classes.index(mom_class)
                    prev_idx = valid_classes.index(prev_mom_class)
                    
                    if abs(curr_idx - prev_idx) > 1:
                        alert = {
                            'company_id': company_id,
                            'alert_level': 'CRITICAL',
                            'alert_type': 'THESIS_SHIFT',
                            'message': f"Thesis momentum shifted significantly: {prev_mom_class} → {mom_class}",
                            'triggered_by': 'Multi-category thesis change',
                        }
                        self.db.create_alert(**alert)
                        alerts.append(alert)
        
        # --- Alert Type: Revenue Decline Warning ---
        engines = current_results.get('engines', {})
        rev_result = engines.get('revenue_growth', {})
        if rev_result.get('score', 100) < 30:
            alert = {
                'company_id': company_id,
                'alert_level': 'IMPORTANT',
                'alert_type': 'REVENUE_DECLINE',
                'message': f"Revenue growth score critically low: {rev_result.get('score', 0):.1f}/100",
                'triggered_by': 'Growth engine assessment',
            }
            self.db.create_alert(**alert)
            alerts.append(alert)
        
        # --- Alert Type: Margin Collapse ---
        margin_result = engines.get('margin_expansion', {})
        if margin_result.get('score', 100) < 30:
            alert = {
                'company_id': company_id,
                'alert_level': 'IMPORTANT',
                'alert_type': 'MARGIN_COLLAPSE',
                'message': f"Margin expansion score critically low: {margin_result.get('score', 0):.1f}/100",
                'triggered_by': 'Margin engine assessment',
            }
            self.db.create_alert(**alert)
            alerts.append(alert)
        
        # --- Alert Type: ROIC Deterioration ---
        roic_result = engines.get('roic', {})
        if roic_result.get('score', 100) < 30:
            alert = {
                'company_id': company_id,
                'alert_level': 'IMPORTANT',
                'alert_type': 'ROIC_DETERIORATION',
                'message': f"ROIC score critically low: {roic_result.get('score', 0):.1f}/100",
                'triggered_by': 'ROIC engine assessment',
            }
            self.db.create_alert(**alert)
            alerts.append(alert)
        
        # --- Alert Type: Valuation Extremes ---
        val = current_results.get('valuation', {})
        if val and isinstance(val, dict) and val.get('extremity_assessment'):
            extremity = val['extremity_assessment']
            assessment = extremity.get('assessment', '')
            
            if assessment in ('SPECIAL_CHEAP', 'SPECIAL_EXPENSIVE'):
                snapshot_data = json.dumps({
                    'assessment': assessment,
                    'extremity_score': extremity.get('extremity_score'),
                    'is_expensive': extremity.get('is_expensive'),
                    'is_cheap': extremity.get('is_cheap'),
                })
                
                alert = {
                    'company_id': company_id,
                    'alert_level': 'IMPORTANT',
                    'alert_type': 'VALUATION_EXTREME',
                    'message': f"Valuation at extreme: {assessment}",
                    'triggered_by': 'Valuation percentiles',
                    'data_snapshot_json': snapshot_data,
                }
                self.db.create_alert(**alert)
                alerts.append(alert)
        
        return alerts
