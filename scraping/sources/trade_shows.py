"""
Scrape trade show exhibitor directories for Chinese companies.

Primary source: Canton Fair (cantonfair.org.cn) — 25k+ exhibitors per session.
Also supports: CES, IFA Berlin, MWC Barcelona.

Usage:
    python -m scraping.sources.trade_shows --fair canton-fair --limit 50
"""

import asyncio
import re
from urllib.parse import urljoin

import click
import httpx
from rich.console import Console
from rich.progress import Progress
from sqlmodel import Session, select

from scraping.config.settings import settings
from scraping.db.init_db import get_engine
from scraping.db.models import Company

console = Console()

CANTON_FAIR_BASE = "https://www.cantonfair.org.cn/en/exhibitor/search"
# Canton Fair exhibitor search API (JSON endpoint behind the search page)
CANTON_FAIR_API = "https://www.cantonfair.org.cn/api/exhibitor/search"


async def scrape_canton_fair(limit: int = 100, category: str = "") -> list[dict]:
    """
    Scrape Canton Fair exhibitor directory.

    The Canton Fair website has a search page that loads exhibitors via a JSON API.
    We call the API directly with pagination.
    """
    results = []
    page = 1
    per_page = 20

    async with httpx.AsyncClient(
        timeout=30,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "application/json",
            "Referer": "https://www.cantonfair.org.cn/en/exhibitor/search",
        },
    ) as client:
        with Progress() as progress:
            task = progress.add_task("Scraping Canton Fair exhibitors...", total=limit)

            while len(results) < limit:
                try:
                    params = {
                        "page": page,
                        "pageSize": per_page,
                        "keyword": category,
                    }
                    resp = await client.get(CANTON_FAIR_API, params=params)

                    if resp.status_code != 200:
                        # Fall back to HTML scraping if API doesn't work
                        console.print(f"[yellow]API returned {resp.status_code}, trying HTML scrape...[/yellow]")
                        html_results = await _scrape_canton_fair_html(client, limit, category)
                        results.extend(html_results)
                        break

                    data = resp.json()
                    exhibitors = data.get("data", {}).get("list", [])

                    if not exhibitors:
                        break

                    for ex in exhibitors:
                        if len(results) >= limit:
                            break
                        results.append({
                            "name": ex.get("companyNameEn", ex.get("companyName", "")),
                            "name_cn": ex.get("companyName", ""),
                            "domain": _extract_domain(ex.get("website", "")),
                            "industry": ex.get("categoryName", ""),
                            "source_url": ex.get("website", ""),
                            "notes": f"Booth: {ex.get('boothNo', '')}",
                        })
                        progress.update(task, advance=1)

                    page += 1
                    await asyncio.sleep(settings.rate_limit_default)

                except httpx.HTTPError as e:
                    console.print(f"[red]HTTP error: {e}[/red]")
                    break

    return results


async def _scrape_canton_fair_html(
    client: httpx.AsyncClient, limit: int, category: str
) -> list[dict]:
    """Fallback: scrape the HTML search results page directly."""
    results = []
    page = 1

    while len(results) < limit:
        try:
            resp = await client.get(
                CANTON_FAIR_BASE,
                params={"page": page, "keyword": category},
            )
            if resp.status_code != 200:
                break

            html = resp.text
            # Extract exhibitor cards — adjust selectors based on actual page structure
            # Canton Fair typically shows: company name, booth, category, website
            names = re.findall(r'class="exhibitor-name[^"]*"[^>]*>([^<]+)<', html)
            websites = re.findall(r'href="(https?://[^"]+)"[^>]*class="exhibitor-website', html)

            if not names:
                break

            for i, name in enumerate(names):
                if len(results) >= limit:
                    break
                website = websites[i] if i < len(websites) else ""
                results.append({
                    "name": name.strip(),
                    "name_cn": "",
                    "domain": _extract_domain(website),
                    "industry": category,
                    "source_url": website,
                    "notes": f"Canton Fair page {page}",
                })

            page += 1
            await asyncio.sleep(settings.rate_limit_default)

        except httpx.HTTPError:
            break

    return results


async def scrape_trade_show_generic(url: str, fair_name: str, limit: int = 100) -> list[dict]:
    """
    Generic scraper for trade show exhibitor pages (CES, IFA, MWC).
    These sites typically have paginated exhibitor directories.
    Uses Playwright for JS-rendered pages.
    """
    results = []

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        console.print("[red]Playwright not installed. Run: pip install playwright && playwright install chromium[/red]")
        return results

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)

            # Generic extraction: look for exhibitor list items
            # Each trade show has different HTML structure, so we try common patterns
            cards = await page.query_selector_all(
                "[class*='exhibitor'], [class*='company'], [data-type='exhibitor']"
            )

            for card in cards[:limit]:
                name_el = await card.query_selector("h2, h3, h4, [class*='name'], [class*='title']")
                link_el = await card.query_selector("a[href*='http']")

                name = await name_el.inner_text() if name_el else ""
                website = await link_el.get_attribute("href") if link_el else ""

                if name:
                    results.append({
                        "name": name.strip(),
                        "name_cn": "",
                        "domain": _extract_domain(website or ""),
                        "industry": "",
                        "source_url": website or url,
                        "notes": fair_name,
                    })

        finally:
            await browser.close()

    return results


def _extract_domain(url: str) -> str:
    """Extract clean domain from a URL."""
    if not url:
        return ""
    url = url.strip().lower()
    if not url.startswith("http"):
        url = "http://" + url
    match = re.match(r"https?://(?:www\.)?([^/]+)", url)
    return match.group(1) if match else ""


def save_companies(companies: list[dict], source: str):
    """Save scraped companies to the database, skipping duplicates by domain."""
    from scraping.db.init_db import init_db
    init_db()
    engine = get_engine()
    saved = 0

    with Session(engine) as session:
        for c in companies:
            # Skip duplicates by domain or name
            if c["domain"]:
                existing = session.exec(
                    select(Company).where(Company.domain == c["domain"])
                ).first()
                if existing:
                    continue
            if c["name"]:
                existing = session.exec(
                    select(Company).where(Company.name == c["name"])
                ).first()
                if existing:
                    continue

            company = Company(
                name=c["name"],
                name_cn=c.get("name_cn", ""),
                domain=c.get("domain", ""),
                industry=c.get("industry", ""),
                source=source,
                source_url=c.get("source_url", ""),
                notes=c.get("notes", ""),
                company_size=c.get("company_size", ""),
                marketplace_url=c.get("marketplace_url", ""),
                has_standalone_site=c.get("has_standalone_site"),
                eu_countries_active=c.get("eu_countries_active", ""),
            )
            session.add(company)
            session.flush()  # get company.id

            # Save contact info if present
            contact_name = c.get("_contact_name", "")
            contact_title = c.get("_contact_title", "")
            if contact_name or contact_title:
                from scraping.db.models import Contact
                contact = Contact(
                    company_id=company.id,
                    name=contact_name,
                    title=contact_title,
                    source="mic_profile",
                )
                session.add(contact)

            saved += 1

        session.commit()

    console.print(f"[green]Saved {saved} new companies (skipped {len(companies) - saved} duplicates)[/green]")
    return saved


FAIRS = {
    "canton-fair": ("Canton Fair", scrape_canton_fair),
}


@click.command()
@click.option("--fair", type=click.Choice(list(FAIRS.keys())), default="canton-fair")
@click.option("--limit", default=50, help="Max exhibitors to scrape")
@click.option("--category", default="", help="Product category filter")
def main(fair: str, limit: int, category: str):
    """Scrape trade show exhibitor directories."""
    fair_name, scraper = FAIRS[fair]
    console.print(f"[bold]Scraping {fair_name} exhibitors (limit={limit})...[/bold]")

    if scraper == scrape_canton_fair:
        companies = asyncio.run(scraper(limit=limit, category=category))
    else:
        companies = asyncio.run(scraper(limit=limit))

    console.print(f"Found {len(companies)} exhibitors")
    save_companies(companies, source=fair)


if __name__ == "__main__":
    main()
