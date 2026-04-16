"""
Aggregate website quality scoring.

Combines translation, performance, mobile, design, and security scores
into a single 0–100 composite score.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class WebsiteScore:
    overall: int
    translation: int
    performance: int
    mobile: int
    design: int
    security: int
    seo: int
    verdict: str  # "high_value_lead", "moderate", "good_website"


# Weights must sum to 1.0
WEIGHTS = {
    "translation": 0.35,
    "performance": 0.15,
    "mobile": 0.15,
    "design": 0.20,
    "security": 0.10,
    "seo": 0.05,
}


def compute_score(
    translation: int = 50,
    performance: int = 50,
    mobile: int = 50,
    design: int = 50,
    security: int = 50,
    seo: int = 50,
) -> WebsiteScore:
    """Compute weighted composite score from individual dimensions."""
    overall = int(
        translation * WEIGHTS["translation"]
        + performance * WEIGHTS["performance"]
        + mobile * WEIGHTS["mobile"]
        + design * WEIGHTS["design"]
        + security * WEIGHTS["security"]
        + seo * WEIGHTS["seo"]
    )

    overall = max(0, min(100, overall))

    if overall < 40:
        verdict = "high_value_lead"
    elif overall < 55:
        verdict = "moderate"
    else:
        verdict = "good_website"

    return WebsiteScore(
        overall=overall,
        translation=translation,
        performance=performance,
        mobile=mobile,
        design=design,
        security=security,
        seo=seo,
        verdict=verdict,
    )
