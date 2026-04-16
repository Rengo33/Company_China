"""
Website assessment orchestrator.

Runs all assessment checks on a company's website and stores results.

Usage:
    python -m scraping.assessment.runner --unassessed --limit 10
"""

import asyncio
from datetime import datetime, timezone

import click
from rich.console import Console
from rich.table import Table
from sqlmodel import Session, select

from scraping.assessment.language import assess_language
from scraping.assessment.performance import assess_performance
from scraping.assessment.scoring import compute_score
from scraping.config.settings import settings
from scraping.db.init_db import get_engine
from scraping.db.models import Company, Website
from scraping.utils.http import StealthClient

console = Console()


async def assess_website(url: str) -> dict:
    """Run all assessments on a single URL. Returns score dict."""
    # Fetch page content for language analysis
    text = ""
    try:
        async with StealthClient() as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                # Extract visible text (strip HTML tags)
                import re
                text = re.sub(r"<[^>]+>", " ", resp.text)
                text = re.sub(r"\s+", " ", text).strip()
    except Exception as e:
        console.print(f"[yellow]Could not fetch {url}: {e}[/yellow]")

    # Language assessment
    lang_report = assess_language(text)

    # Performance assessment (Google PageSpeed)
    perf_report = await assess_performance(url)

    # Security: basic HTTPS check
    security_score = 80 if url.startswith("https://") else 20

    # Mobile + Design: placeholder scores for MVP
    # (Phase 2 will add Playwright-based mobile + design checks)
    mobile_score = 50   # neutral until Phase 2
    design_score = 50   # neutral until Phase 2

    # Compute aggregate
    scores = compute_score(
        translation=lang_report.score,
        performance=perf_report.performance_score,
        mobile=mobile_score,
        design=design_score,
        security=security_score,
        seo=perf_report.seo_score,
    )

    return {
        "overall_score": scores.overall,
        "translation_score": scores.translation,
        "performance_score": scores.performance,
        "mobile_score": scores.mobile,
        "design_score": scores.design,
        "security_score": scores.security,
        "seo_score": scores.seo,
        "language": lang_report.primary_language,
        "verdict": scores.verdict,
    }


async def assess_unassessed(limit: int = 10):
    """Find companies with websites that haven't been assessed yet, and assess them."""
    engine = get_engine()

    with Session(engine) as session:
        # Get companies that have a domain but no website assessment
        companies = session.exec(
            select(Company).where(Company.domain != "").limit(limit * 2)
        ).all()

        assessed_count = 0
        results = []

        for company in companies:
            if assessed_count >= limit:
                break

            # Check if already assessed
            existing = session.exec(
                select(Website).where(Website.company_id == company.id)
            ).first()

            if existing and existing.assessed_at:
                continue

            url = f"https://{company.domain}"
            console.print(f"Assessing [bold]{company.name}[/bold] ({url})...")

            scores = await assess_website(url)

            if existing:
                # Update existing record
                for key, val in scores.items():
                    if key != "verdict" and key != "language":
                        setattr(existing, key, val)
                existing.language = scores["language"]
                existing.assessed_at = datetime.now(timezone.utc)
            else:
                # Create new website record
                website = Website(
                    company_id=company.id,
                    url=url,
                    language=scores["language"],
                    overall_score=scores["overall_score"],
                    translation_score=scores["translation_score"],
                    performance_score=scores["performance_score"],
                    mobile_score=scores["mobile_score"],
                    design_score=scores["design_score"],
                    security_score=scores["security_score"],
                    seo_score=scores["seo_score"],
                    assessed_at=datetime.now(timezone.utc),
                )
                session.add(website)

            results.append((company.name, company.domain, scores))
            assessed_count += 1

            await asyncio.sleep(settings.rate_limit_default)

        session.commit()

    # Print results table
    if results:
        table = Table(title=f"Assessment Results ({assessed_count} sites)")
        table.add_column("Company", style="bold")
        table.add_column("Domain")
        table.add_column("Overall", justify="center")
        table.add_column("Translation", justify="center")
        table.add_column("Performance", justify="center")
        table.add_column("Verdict", justify="center")

        for name, domain, scores in results:
            verdict_style = "green" if scores["verdict"] == "good_website" else "red bold"
            table.add_row(
                name, domain,
                str(scores["overall_score"]),
                str(scores["translation_score"]),
                str(scores["performance_score"]),
                f"[{verdict_style}]{scores['verdict']}[/{verdict_style}]",
            )

        console.print(table)
    else:
        console.print("[yellow]No unassessed companies with domains found.[/yellow]")


@click.command()
@click.option("--unassessed", is_flag=True, default=True, help="Only assess unassessed sites")
@click.option("--limit", default=10, help="Max sites to assess")
def main(unassessed: bool, limit: int):
    """Run website quality assessment on discovered companies."""
    asyncio.run(assess_unassessed(limit=limit))


if __name__ == "__main__":
    main()
