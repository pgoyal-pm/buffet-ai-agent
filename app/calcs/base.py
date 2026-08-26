"""
Compounder Intelligence Dashboard — Scoring Engine Base Class

Base class for all scoring engines. Each engine must implement:
- name(): Display name
- calculate(db, company_id, period_id): Returns score dict with raw inputs + weighted score
- normalize(value, direction="higher"): Maps value to 0-100 scale

The framework handles weighting, versioning, and persistence.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional


class ScoringEngine(ABC):
    """Base class for all scoring engines."""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Display name of this engine."""
        pass
    
    @property
    def max_weight(self) -> float:
        """Maximum weight contribution. Default is config-driven."""
        from app.config import get_config
        return get_config().weights.get(self.name, 0.0)
    
    @staticmethod
    def normalize(value: float, 
                  good_range: Dict[str, float],
                  direction: str = "higher") -> float:
        """
        Normalize a metric value to 0-100 scale.
        
        Args:
            value: The actual metric value (e.g., growth rate of 0.25)
            good_range: Dict with 'excellent', 'good', 'average', 'poor' thresholds
                       e.g., {'excellent': 0.30, 'good': 0.20, 'average': 0.15, 'poor': 0.10}
            direction: "higher" means higher values are better; "lower" reverses scale
        
        Returns:
            Score between 0-100
        """
        if value is None or value <= 0:
            if direction == "lower":
                return 50.0  # Neutral on 0-value if lower-is-better
            return 0.0
        
        excellent = good_range.get('excellent')
        good = good_range.get('good')
        average = good_range.get('average')
        poor = good_range.get('poor')
        
        if not excellent:
            # Fallback linear scale based on best known
            return min(100, max(0, (value / excellent) * 85 + 15))
        
        if direction == "higher":
            if value >= excellent:
                return 90 + min(10, (value - excellent) * 50)  # Cap at 100
            elif value >= good:
                return 70 + ((value - good) / (excellent - good)) * 20
            elif value >= average:
                return 50 + ((value - average) / (good - average)) * 20
            else:
                return max(0, ((value - poor) / (average - poor)) * 50)
        else:
            # Lower is better (invert scale)
            if value <= poor:
                return 90
            elif value <= average:
                return 70 + ((average - value) / (average - poor)) * 20
            elif value <= good:
                return 50 + ((good - value) / (average - good)) * 20
            else:
                return max(0, ((excellent - value) / (good - excellent)) * 50)
    
    @staticmethod
    def compute_consistency(values: List[float], tolerance: float = 0.05) -> float:
        """
        Compute consistency score (0-100) for a time series.
        
        Measures how stable the metric is across periods.
        Penalizes high volatility but rewards moderate stability.
        Does NOT reward deterioration disguised as consistency.
        """
        if len(values) < 3:
            return 50.0  # Not enough data to judge
        
        # Calculate coefficient of variation
        mean_val = sum(values) / len(values)
        if mean_val == 0:
            return 50.0
        
        variance = sum((v - mean_val) ** 2 for v in values) / len(values)
        std_dev = variance ** 0.5
        cv = std_dev / abs(mean_val)
        
        # CV of 0 = perfect consistency, 1+ = very volatile
        if cv <= 0.05:
            return 95  # Extremely consistent
        elif cv <= 0.10:
            return 85  # Very consistent
        elif cv <= 0.20:
            return 70  # Moderately consistent
        elif cv <= 0.35:
            return 50  # Somewhat volatile
        elif cv <= 0.50:
            return 30  # Highly volatile
        else:
            return 10  # Extremely volatile
    
    @staticmethod
    def compute_acceleration(current: float, prior: float, prev_prior: float) -> float:
        """
        Measure acceleration in a metric.
        
        Returns a score from -100 to +100 where:
        - Positive = accelerating/improving
        - Negative = decelerating/deteriorating
        - Near zero = flat
        """
        if current is None or prior is None or prev_prior is None:
            return 0.0
        
        # First derivative: change from last to current
        d1 = current - prior
        
        # Second derivative: change in change
        d2 = prior - prev_prior
        
        if abs(d2) < 1e-10:
            return 0.0  # No acceleration
        
        # Normalize by magnitude of first derivative
        base = abs(d1) + abs(d2)
        if base == 0:
            return 0.0
        
        # Sign determines direction, magnitude normalized to [-1, 1] then scaled to [-100, 100]
        acceleration = (d2 / base) * 100
        return max(-100, min(100, round(acceleration, 2)))
    
    @abstractmethod
    def calculate(self, db, company_id: int, period_id: int) -> Dict[str, Any]:
        """
        Main calculation method. Must return:
        {
            'score': float (0-100),           # Normalized score
            'raw_values': dict,               # Input values used
            'details': dict,                  # Explanation of calculation
            'data_type': str ('REPORTED'|'DERIVED'),
            'confidence': str ('HIGH'|'MEDIUM'|'LOW'),
        }
        """
        pass
    
    def calculate_weighted(self, db, company_id: int, period_id: int) -> Dict[str, Any]:
        """Calculate and apply weight. Wrapper around calculate()."""
        result = self.calculate(db, company_id, period_id)
        weight = self.max_weight
        result['weighted_score'] = result['score'] * weight
        result['weight'] = weight
        return result
