"""
Compounder Intelligence Dashboard — Configuration & Scoring Weights

Default scoring weights following the spec exactly:
- Revenue Growth: 25%
- ROIC / Incremental ROIC: 30%
- Margin Expansion: 20%
- Reinvestment Runway: 25%

Total: 100%

Weights are stored as both a Python dict AND persisted in the database
so they can be updated without code deployment.

Classification system (spec v1.0):
- 90-100: Exceptional Compounder
- 80-89:  Strong Compounder
- 70-79:  Potential Compounder
- 60-69:  Average
- 50-59:  Weak
- <50:    Non-Compounder
- DATA_INCOMPLETE: One or more of four inputs missing (do NOT classify)
"""

import json
from pathlib import Path
from typing import Dict, Any, List


# Default scoring weights — these are the ONLY place defaults live
DEFAULT_WEIGHTS: Dict[str, float] = {
    "revenue_growth": 0.25,
    "roic": 0.30,
    "margin_expansion": 0.20,
    "reinvestment_runway": 0.25,
}

# Classification thresholds (as defined in spec v1.0)
# These map exclusively to the Compounder Score (0-100)
# They do NOT include STRONG_BUSINESS / WEAK_BUSINESS — those belong
# to a separate business-quality assessment if one exists.
CLASSIFICATION_THRESHOLDS = [
    (90, "Exceptional Compounder"),
    (80, "Strong Compounder"),
    (70, "Potential Compounder"),
    (60, "Average"),
    (50, "Weak"),
    (0,  "Non-Compounder"),
]

# Special sentinel values for data completeness
DATA_INCOMPLETE = "DATA_INCOMPLETE"     # One+ of four inputs missing
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"  # Weighted avg couldn't be computed (all zero/fail)

# Revenue growth sub-scores weighting
REVENUE_GROWTH_PARAMS = {
    "absolute_weight": 0.50,      # How much weight to absolute growth rate
    "consistency_weight": 0.25,   # Consistency across quarters
    "acceleration_weight": 0.25,  # Momentum / acceleration
}

# ROIC thresholds (by industry type)
ROIC_BENCHMARKS = {
    "INDUSTRIAL": {
        "excellent": 0.30,
        "good": 0.20,
        "average": 0.15,
        "poor": 0.10,
        "cost_of_capital": 0.10,
    },
    "TECH": {
        "excellent": 0.25,
        "good": 0.18,
        "average": 0.12,
        "poor": 0.08,
        "cost_of_capital": 0.09,
    },
    "FINANCIAL": {
        "excellent": 0.15,
        "good": 0.10,
        "average": 0.07,
        "poor": 0.05,
        "cost_of_capital": 0.08,
    },
    "DEFAULT": {
        "excellent": 0.30,
        "good": 0.20,
        "average": 0.15,
        "poor": 0.10,
        "cost_of_capital": 0.10,
    },
}

# Margin targets
MARGIN_BENCHMARKS = {
    "gross_margin": {
        "excellent": 0.50,
        "good": 0.35,
        "average": 0.20,
        "poor": 0.05,
    },
    "ebitda_margin": {
        "excellent": 0.35,
        "good": 0.25,
        "average": 0.15,
        "poor": 0.05,
    },
    "operating_margin": {
        "excellent": 0.30,
        "good": 0.20,
        "average": 0.12,
        "poor": 0.03,
    },
    "pat_margin": {
        "excellent": 0.25,
        "good": 0.15,
        "average": 0.08,
        "poor": 0.02,
    },
}

# Thresholds that trigger alerts
ALERT_THRESHOLDS = {
    "compounder_score_change": 5,  # Points change triggers INFO alert
    "compounder_score_class_change": True,  # Class boundary crossing → IMPORTANT
    "incremental_roic_drop": 0.10,  # 10 percentage point drop
    "revenue_growth_deceleration": 0.10,  # 10pp deceleration
    "margin_deterioration": 0.05,  # 5pp deterioration
    "thesis_momentum_shift": 1,  # Category shift
}

# Canonical metric name mapping (from extraction output → canonical keys)
CANONICAL_METRIC_MAP = {
    # Income statement
    "total_revenue": "revenue",
    "total revenue": "revenue",
    "net_sales": "revenue",
    "net sales": "revenue",
    "sales_revenue": "revenue",
    "net_revenue": "revenue",
    "cost_of_revenue": "cogs",
    "cost of revenue": "cogs",
    "cost_of_goods_sold": "cogs",
    "cost of goods sold": "cogs",
    "gross_profit": "gross_profit",
    "gross profit": "gross_profit",
    "operating_income": "operating_income",
    "operating income": "operating_income",
    "ebitda": "ebitda",
    "ebit": "ebit",
    "net_income": "net_income",
    "net income": "net_income",
    "diluted_eps": "diluted_eps",
    "diluted eps": "diluted_eps",
    "basic_eps": "basic_eps",
    "basic eps": "basic_eps",
    "weighted_average_shares": "diluted_shares_outstanding",
    "weighted average shares outstanding": "diluted_shares_outstanding",
    
    # Cash flow
    "operating_cash_flow": "operating_cash_flow",
    "capital_expenditure": "capex",
    "free_cash_flow": "free_cash_flow",
    
    # Balance sheet
    "total_assets": "total_assets",
    "current_liabilities": "current_liabilities",
    "long_term_debt": "long_term_debt",
    "total_debt": "total_debt",
    "stockholders_equity": "shareholders_equity",
    "cash_and_equivalents": "cash_and_short_term_investments",
    "accounts_receivable": "accounts_receivable",
    "inventory": "inventory",
    "net_ppe": "net_property_plant_equipment",
    
    # Derived metrics we expect
    "nopat": "nopat",
    "invested_capital": "invested_capital",
    "incremental_roic_1y": "incremental_roic_1y",
    "incremental_roic_3y": "incremental_roic_3y",
    "incremental_roic_5y": "incremental_roic_5y",
    "enterprise_value": "enterprise_value",
    "market_capitalization": "market_cap",
}


def normalize_metric_name(raw_name: str) -> str:
    """Convert raw extracted metric name to canonical form."""
    key = raw_name.strip().lower()
    return CANONICAL_METRIC_MAP.get(key, key.replace(" ", "_").replace("-", "_"))


class Config:
    """Application configuration."""
    
    def __init__(self, database_path: str = "/data/compounder_dashboard.db"):
        self.database_path = database_path
    
    @property
    def weights(self) -> Dict[str, float]:
        return DEFAULT_WEIGHTS
    
    @property
    def weights_sum(self) -> float:
        return sum(self.weights.values())
    
    def classification(self, score: float) -> str:
        """Map a numeric Compounder Score to its classification label.
        
        Rule: only CLASSIFICATION_THRESHOLDS apply here.
        STRONG_BUSINESS / WEAK_BUSINESS must NOT appear — they belong
        to a separate quality assessment if it exists.
        """
        for threshold, label in CLASSIFICATION_THRESHOLDS:
            if score >= threshold:
                return label
        return CLASSIFICATION_THRESHOLDS[-1][1]
    
    @property
    def normalization_params(self) -> Dict[str, Any]:
        """Returns the normalization thresholds used by scoring engines."""
        return {
            "revenue_growth": REVENUE_GROWTH_PARAMS,
            "roic_benchmarks": ROIC_BENCHMARKS,
            "margin_benchmarks": MARGIN_BENCHMARKS,
            "classifications": {str(t): l for t, l in CLASSIFICATION_THRESHOLDS},
        }
    
    @property
    def classification_ranges(self) -> Dict[str, str]:
        """Classification ranges as label → range string (readable)."""
        ranges = {}
        for i in range(len(CLASSIFICATION_THRESHOLDS)):
            low = CLASSIFICATION_THRESHOLDS[i][0]
            high = CLASSIFICATION_THRESHOLDS[i+1][0] - 1 if i + 1 < len(CLASSIFICATION_THRESHOLDS) else "100"
            label = CLASSIFICATION_THRESHOLDS[i][1]
            if high == "100":
                ranges[label] = f">= {low}"
            else:
                ranges[label] = f"{low}-{high}"
        # Add DATA_INCOMPLETE
        ranges[DATA_INCOMPLETE] = "One+ input missing"
        return ranges
    
    @property
    def compounder_classification_thresholds(self) -> List[tuple]:
        """Return the raw classification threshold list for API consumers."""
        return CLASSIFICATION_THRESHOLDS


def get_config():
    """Get application config singleton."""
    if not hasattr(get_config, '_instance'):
        get_config._instance = Config()
    return get_config._instance


if __name__ == "__main__":
    cfg = Config()
    print(f"Database: {cfg.database_path}")
    print(f"Weights: {cfg.weights}")
    print(f"Sum: {cfg.weights_sum}")
    print("\nClassification lookup:")
    for s in [95, 85, 75, 65, 55, 40]:
        print(f"  Score {s}: {cfg.classification(s)}")
