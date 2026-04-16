"""
Website performance assessment using Google PageSpeed Insights API (free).

Fetches Lighthouse scores for performance, accessibility, SEO, and best practices.
"""

from dataclasses import dataclass
from typing import Optional

import httpx

from scraping.config.settings import settings

PAGESPEED_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


@dataclass
class PerformanceReport:
    performance_score: int      # 0–100
    accessibility_score: int    # 0–100
    seo_score: int              # 0–100
    best_practices_score: int   # 0–100
    lcp_ms: Optional[int]       # Largest Contentful Paint in ms
    fid_ms: Optional[int]       # First Input Delay in ms
    cls: Optional[float]        # Cumulative Layout Shift
    has_https: bool
    errors: list[str]


async def assess_performance(url: str) -> PerformanceReport:
    """
    Run Google PageSpeed Insights on a URL.
    Free tier: 25,000 requests/day with API key, 500/day without.
    """
    params = {
        "url": url,
        "category": ["performance", "accessibility", "seo", "best-practices"],
        "strategy": "mobile",
    }
    if settings.google_api_key:
        params["key"] = settings.google_api_key

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(PAGESPEED_URL, params=params)

            if resp.status_code != 200:
                return _empty_report(errors=[f"PageSpeed API returned {resp.status_code}"])

            data = resp.json()
            lighthouse = data.get("lighthouseResult", {})
            categories = lighthouse.get("categories", {})
            audits = lighthouse.get("audits", {})

            def score(cat: str) -> int:
                return int((categories.get(cat, {}).get("score", 0) or 0) * 100)

            # Core Web Vitals
            lcp = audits.get("largest-contentful-paint", {}).get("numericValue")
            fid = audits.get("max-potential-fid", {}).get("numericValue")
            cls_val = audits.get("cumulative-layout-shift", {}).get("numericValue")

            has_https = url.startswith("https://") or audits.get("is-on-https", {}).get("score", 0) == 1

            return PerformanceReport(
                performance_score=score("performance"),
                accessibility_score=score("accessibility"),
                seo_score=score("seo"),
                best_practices_score=score("best-practices"),
                lcp_ms=int(lcp) if lcp else None,
                fid_ms=int(fid) if fid else None,
                cls=round(cls_val, 3) if cls_val is not None else None,
                has_https=has_https,
                errors=[],
            )

    except Exception as e:
        return _empty_report(errors=[str(e)])


def _empty_report(errors: list[str] = None) -> PerformanceReport:
    return PerformanceReport(
        performance_score=0,
        accessibility_score=0,
        seo_score=0,
        best_practices_score=0,
        lcp_ms=None,
        fid_ms=None,
        cls=None,
        has_https=False,
        errors=errors or [],
    )
