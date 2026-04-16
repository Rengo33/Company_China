"""
Scrape EU company registries for Chinese-owned companies.

Primary source: UK Companies House (free REST API).
Strategy: Search for officers with nationality "Chinese", then get their companies.

Usage:
    python -m scraping.sources.company_registries --limit 50
"""

import asyncio
from base64 import b64encode

import click
import httpx
from rich.console import Console
from rich.progress import Progress

from scraping.config.settings import settings
from scraping.sources.trade_shows import save_companies, _extract_domain

console = Console()

CH_BASE = "https://api.company-information.service.gov.uk"


def _ch_headers() -> dict:
    """Companies House uses HTTP Basic auth with API key as username."""
    if not settings.companies_house_api_key:
        return {}
    token = b64encode(f"{settings.companies_house_api_key}:".encode()).decode()
    return {"Authorization": f"Basic {token}"}


# SIC codes for retail/trading/tech (small company focus)
SMALL_COMPANY_SICS = {
    "46": "Wholesale trade",
    "47": "Retail trade",
    "62": "Computer programming/consultancy",
    "63": "Information service activities",
    "70": "Management consultancy",
    "73": "Advertising/market research",
    "74": "Other professional activities",
    "82": "Office admin/business support",
}

SMALL_ACCOUNT_CATEGORIES = {"micro-entity", "small", "dormant", "total-exemption-small", "total-exemption-full"}


async def search_chinese_companies(limit: int = 50, small_only: bool = False) -> list[dict]:
    """
    Find Chinese-owned companies via Companies House.

    Approach:
    1. Search for companies with keywords suggesting Chinese origin
    2. For each, fetch company profile for website and SIC codes
    3. Optionally filter for small/micro companies only
    """
    results = []
    search_terms = [
        "shenzhen", "guangzhou", "beijing", "shanghai", "hangzhou",
        "dongguan", "yiwu", "xiamen", "chinese trading", "china trading",
        "china import", "china export", "sino",
    ]

    if not settings.companies_house_api_key:
        console.print("[red]No COMPANIES_HOUSE_API_KEY set. Get one free at developer.company-information.service.gov.uk[/red]")
        return results

    async with httpx.AsyncClient(
        timeout=30,
        headers=_ch_headers(),
    ) as client:
        with Progress() as progress:
            task = progress.add_task("Searching Companies House...", total=limit)

            for term in search_terms:
                if len(results) >= limit:
                    break

                start = 0
                while len(results) < limit:
                    try:
                        resp = await client.get(
                            f"{CH_BASE}/search/companies",
                            params={
                                "q": term,
                                "items_per_page": 20,
                                "start_index": start,
                            },
                        )

                        if resp.status_code == 429:
                            console.print("[yellow]Rate limited, waiting 30s...[/yellow]")
                            await asyncio.sleep(30)
                            continue

                        if resp.status_code != 200:
                            break

                        data = resp.json()
                        items = data.get("items", [])

                        if not items:
                            break

                        for item in items:
                            if len(results) >= limit:
                                break

                            company_number = item.get("company_number", "")
                            company_name = item.get("title", "")
                            status = item.get("company_status", "")

                            # Only active companies
                            if status != "active":
                                continue

                            # Fetch full profile for website
                            profile = await _get_company_profile(client, company_number)
                            website = ""
                            sic_codes = []
                            company_size = "unknown"
                            accounts_category = ""

                            if profile:
                                website = profile.get("links", {}).get("website", "")
                                if not website:
                                    website = profile.get("website", "")
                                sic_codes = profile.get("sic_codes", [])

                                # Determine company size from accounts
                                accounts = profile.get("accounts", {})
                                accounts_category = accounts.get("accounting_reference_date", {}).get("type", "")
                                last_accounts = accounts.get("last_accounts", {})
                                acc_type = last_accounts.get("type", "").lower()

                                if acc_type in SMALL_ACCOUNT_CATEGORIES or "micro" in acc_type:
                                    company_size = "micro" if "micro" in acc_type else "small"
                                elif acc_type in ("medium", "medium-company"):
                                    company_size = "medium"
                                elif acc_type in ("full", "group"):
                                    company_size = "large"

                            # Small-only filter
                            if small_only:
                                if company_size in ("large", "medium"):
                                    continue
                                # Also filter by SIC code — prefer retail/trading/tech
                                if sic_codes:
                                    has_relevant_sic = any(
                                        sic[:2] in SMALL_COMPANY_SICS for sic in sic_codes
                                    )
                                    if not has_relevant_sic:
                                        continue

                            address = item.get("address_snippet", "")

                            results.append({
                                "name": company_name,
                                "name_cn": "",
                                "domain": _extract_domain(website),
                                "industry": ", ".join(sic_codes),
                                "source_url": f"https://find-and-update.company-information.service.gov.uk/company/{company_number}",
                                "notes": f"Address: {address}",
                                "company_size": company_size,
                                "eu_countries_active": "GB",
                                "has_standalone_site": bool(website),
                            })
                            progress.update(task, advance=1)

                            await asyncio.sleep(settings.rate_limit_companies_house)

                        start += 20

                    except httpx.HTTPError as e:
                        console.print(f"[red]HTTP error: {e}[/red]")
                        break

    return results


async def _get_company_profile(client: httpx.AsyncClient, company_number: str) -> dict | None:
    """Fetch full company profile from Companies House."""
    try:
        resp = await client.get(f"{CH_BASE}/company/{company_number}")
        if resp.status_code == 200:
            return resp.json()
    except httpx.HTTPError:
        pass
    return None


async def search_officers_by_nationality(limit: int = 50) -> list[dict]:
    """
    Alternative approach: search for officers, filter by Chinese names.

    Companies House advanced search doesn't directly filter by nationality,
    but we can search for common Chinese surnames as officer names.
    """
    results = []
    common_surnames = [
        "Wang", "Li", "Zhang", "Liu", "Chen", "Yang", "Huang", "Wu",
        "Zhou", "Xu", "Sun", "Ma", "Zhu", "Guo", "Lin", "He",
    ]

    if not settings.companies_house_api_key:
        console.print("[red]No COMPANIES_HOUSE_API_KEY set.[/red]")
        return results

    async with httpx.AsyncClient(timeout=30, headers=_ch_headers()) as client:
        with Progress() as progress:
            task = progress.add_task("Searching officers...", total=limit)

            for surname in common_surnames:
                if len(results) >= limit:
                    break

                try:
                    resp = await client.get(
                        f"{CH_BASE}/search/officers",
                        params={"q": surname, "items_per_page": 20},
                    )

                    if resp.status_code != 200:
                        continue

                    data = resp.json()
                    for officer in data.get("items", []):
                        if len(results) >= limit:
                            break

                        # Get officer's company appointments
                        appt_link = officer.get("links", {}).get("self", "")
                        if not appt_link:
                            continue

                        appts = await _get_officer_appointments(client, appt_link)
                        for appt in appts:
                            company_name = appt.get("appointed_to", {}).get("company_name", "")
                            company_number = appt.get("appointed_to", {}).get("company_number", "")

                            if company_name and company_number:
                                profile = await _get_company_profile(client, company_number)
                                website = profile.get("website", "") if profile else ""

                                results.append({
                                    "name": company_name,
                                    "name_cn": "",
                                    "domain": _extract_domain(website),
                                    "industry": "",
                                    "source_url": f"https://find-and-update.company-information.service.gov.uk/company/{company_number}",
                                    "notes": f"Officer: {officer.get('title', '')}",
                                })
                                progress.update(task, advance=1)

                        await asyncio.sleep(settings.rate_limit_companies_house)

                except httpx.HTTPError:
                    continue

    return results


async def _get_officer_appointments(client: httpx.AsyncClient, link: str) -> list:
    """Fetch an officer's company appointments."""
    try:
        resp = await client.get(f"{CH_BASE}{link}/appointments")
        if resp.status_code == 200:
            return resp.json().get("items", [])
    except httpx.HTTPError:
        pass
    return []


@click.command()
@click.option("--limit", default=50, help="Max companies to find")
@click.option("--method", type=click.Choice(["keyword", "officers"]), default="keyword",
              help="Search by keyword or by Chinese officer names")
@click.option("--small-only", is_flag=True, help="Only include micro/small companies in retail/trading/tech")
def main(limit: int, method: str, small_only: bool):
    """Search UK Companies House for Chinese-owned companies."""
    label = f"{method} method, limit={limit}"
    if small_only:
        label += ", small-only"
    console.print(f"[bold]Searching Companies House ({label})...[/bold]")

    if method == "keyword":
        companies = asyncio.run(search_chinese_companies(limit=limit, small_only=small_only))
    else:
        companies = asyncio.run(search_officers_by_nationality(limit=limit))

    console.print(f"Found {len(companies)} companies")
    save_companies(companies, source="companies_house")


if __name__ == "__main__":
    main()
