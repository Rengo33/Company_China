"""
Scrape Chinese B2B marketplaces for suppliers with international presence.

Primary source: Made-in-China.com (reliable, no CAPTCHA).
These are Chinese manufacturers/exporters — many have their own websites (often poor quality).

Usage:
    python -m scraping.sources.alibaba_sellers --category electronics --limit 50
"""

import asyncio
import re

import click
from rich.console import Console
from rich.progress import Progress
from selectolax.parser import HTMLParser

from scraping.config.settings import settings
from scraping.sources.trade_shows import save_companies, _extract_domain
from scraping.utils.domain_finder import find_company_domain
from scraping.utils.http import StealthClient

# Also save contacts from MIC profiles
from scraping.db.init_db import get_engine, init_db
from scraping.db.models import Contact
from sqlmodel import Session, select

console = Console()

# All category slugs on made-in-china.com (~40 companies per page, paginated)
CATEGORIES = {
    "agriculture": "agriculture",
    "apparel": "apparel-accessories",
    "auto": "auto",
    "chemical": "chemical",
    "computers": "computer-products",
    "construction": "construction-decoration",
    "electrical": "electrical-electronics",
    "electronics": "electronics",
    "energy": "energy",
    "food": "food-beverage",
    "furniture": "furniture",
    "gifts": "gifts-crafts",
    "health": "health-medicine",
    "home": "home-supplies",
    "industrial": "industrial-equipment",
    "lighting": "lights-lighting",
    "machinery": "machinery",
    "minerals": "minerals-metallurgy",
    "office": "office-supplies",
    "packaging": "packaging-printing",
    "rubber": "rubber-plastics",
    "security": "security-protection",
    "shoes": "shoes-accessories",
    "sports": "sports-entertainment",
    "textile": "textile",
    "tools": "tools-hardware",
    "transport": "transportation",
}

MIC_BASE = "https://www.made-in-china.com/manufacturers"


async def scrape_mic_sellers(
    category: str = "electronics", limit: int = 50
) -> list[dict]:
    """
    Scrape Made-in-China.com supplier directory.

    Strategy:
    1. Browse manufacturer category pages
    2. Extract company names + profile URLs from listing
    3. Visit each company profile to find their external website
    """
    results = []
    cat_slug = CATEGORIES.get(category, category)
    page_num = 1

    async with StealthClient() as client:
        with Progress() as progress:
            task = progress.add_task(f"Scraping Made-in-China ({cat_slug})...", total=limit)

            while len(results) < limit:
                url = f"{MIC_BASE}/{cat_slug}.html" if page_num == 1 else f"{MIC_BASE}/{cat_slug}/{page_num}.html"

                try:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        console.print(f"[yellow]Page {page_num}: status {resp.status_code}[/yellow]")
                        break

                    companies = _parse_mic_listing(resp.text)
                    if not companies:
                        break

                    for company in companies:
                        if len(results) >= limit:
                            break

                        # Find external website via DuckDuckGo search
                        domain = await find_company_domain(company["name"])

                        # Scrape contact info from MIC profile
                        contact_info = await scrape_mic_contact_info(client, company["profile_url"])
                        await asyncio.sleep(settings.rate_limit_default)

                        notes_parts = []
                        if company["products"]:
                            notes_parts.append(f"Products: {company['products'][:200]}")
                        if contact_info["address"]:
                            notes_parts.append(f"Address: {contact_info['address']}")
                        if contact_info["employees"]:
                            notes_parts.append(f"Employees: {contact_info['employees']}")

                        results.append({
                            "name": company["name"],
                            "name_cn": "",
                            "domain": domain,
                            "industry": cat_slug,
                            "source_url": company["profile_url"],
                            "notes": " | ".join(notes_parts),
                            "_contact_name": contact_info["contact_name"],
                            "_contact_title": contact_info["contact_title"],
                        })
                        progress.update(task, advance=1)

                    page_num += 1
                    await asyncio.sleep(settings.rate_limit_default)

                except Exception as e:
                    console.print(f"[red]Error on page {page_num}: {e}[/red]")
                    break

    return results


def _parse_mic_listing(html: str) -> list[dict]:
    """Parse Made-in-China manufacturer listing page."""
    tree = HTMLParser(html)
    companies = []

    # Company names are in h2/h3 heading links
    headings = tree.css("h2 a, h3 a")

    # Company detail boxes contain products and business type
    boxes = tree.css(".company-box")

    for i, h in enumerate(headings):
        name = h.text(strip=True)
        href = h.attributes.get("href", "")

        if not name or not href:
            continue

        # Normalize URL
        if href.startswith("//"):
            href = "https:" + href
        elif href.startswith("/"):
            href = "https://www.made-in-china.com" + href

        # Extract products from the corresponding company box
        products = ""
        if i < len(boxes):
            prod_el = boxes[i].css_first("span[title]")
            if prod_el:
                products = prod_el.attributes.get("title", "")

        companies.append({
            "name": name,
            "profile_url": href,
            "products": products,
        })

    return companies


async def _get_mic_company_website(client, profile_url: str) -> str:
    """Visit a company's Made-in-China profile to find their external website."""
    try:
        # The profile URL is like: //companyname.en.made-in-china.com/...
        # The company info page is at the root of their subdomain
        base_url = re.match(r"(https?://[^/]+)", profile_url)
        if not base_url:
            return ""

        company_url = base_url.group(1) + "/company-info.html"
        resp = await client.get(company_url)

        if resp.status_code != 200:
            return ""

        tree = HTMLParser(resp.text)

        # Look for "Company Homepage" or external website link
        for a in tree.css("a[href]"):
            href = a.attributes.get("href", "")
            text = a.text(strip=True).lower()

            if not href or "made-in-china" in href:
                continue

            # External website links
            if any(kw in text for kw in ["website", "homepage", "url", "www"]):
                if href.startswith("http"):
                    return href

        # Also check for website in company detail fields
        for el in tree.css(".detail-item, .info-item, td"):
            text = el.text(strip=True)
            url_match = re.search(r"(https?://[^\s<>\"]+)", text)
            if url_match:
                url = url_match.group(1)
                if "made-in-china" not in url:
                    return url

    except Exception:
        pass

    return ""


async def scrape_mic_contact_info(client, profile_url: str) -> dict:
    """Scrape contact info from a Made-in-China company profile."""
    info = {"contact_name": "", "contact_title": "", "address": "", "employees": ""}

    try:
        base_url = re.match(r"(https?://[^/]+)", profile_url)
        if not base_url:
            return info

        contact_url = base_url.group(1) + "/contact-info.html"
        resp = await client.get(contact_url)
        if resp.status_code != 200:
            return info

        tree = HTMLParser(resp.text)
        text_items = [el.text(strip=True) for el in tree.css("tr, .detail-item")]

        for item in text_items:
            if item.startswith("Address:"):
                info["address"] = item.replace("Address:", "").strip()
            elif item.startswith("Number of Employees:"):
                info["employees"] = item.replace("Number of Employees:", "").strip()

        # Find contact person — often listed with a title like "Sales Director"
        body_text = tree.css_first("body").text() if tree.css_first("body") else ""
        # MIC shows contact titles like "Sales Manager", "Overseas Marketing" etc.
        title_patterns = [
            r"((?:Sales|Export|Marketing|Overseas|Foreign Trade|International)[^,\n]{0,30}(?:Manager|Director|Representative|Dept\.|Department))",
        ]
        for pat in title_patterns:
            m = re.search(pat, body_text, re.IGNORECASE)
            if m:
                info["contact_title"] = m.group(1).strip()
                break

    except Exception:
        pass

    return info


async def scrape_all_categories(limit_per_category: int = 50) -> list[dict]:
    """Scrape every category on Made-in-China.com. Saves to DB after each category."""
    all_results = []

    for cat_key, cat_slug in CATEGORIES.items():
        console.print(f"\n[bold cyan]Category: {cat_key}[/bold cyan] ({cat_slug})")
        results = await scrape_mic_sellers(category=cat_key, limit=limit_per_category)
        all_results.extend(results)
        # Save after each category so data isn't lost
        save_companies(results, source="made_in_china")
        console.print(f"  Found {len(results)} sellers (total: {len(all_results)})")

    return all_results


@click.command()
@click.option("--category", type=click.Choice(list(CATEGORIES.keys()) + ["all"]), default="all")
@click.option("--limit", default=50, help="Max sellers per category")
def main(category: str, limit: int):
    """Scrape Made-in-China.com for Chinese suppliers."""
    if category == "all":
        console.print(f"[bold]Scraping ALL {len(CATEGORIES)} categories (limit={limit} per category)...[/bold]")
        sellers = asyncio.run(scrape_all_categories(limit_per_category=limit))
        # Already saved per-category inside scrape_all_categories
        console.print(f"\n[bold green]Total found: {len(sellers)} sellers[/bold green]")
    else:
        console.print(f"[bold]Scraping Chinese suppliers (category={category}, limit={limit})...[/bold]")
        sellers = asyncio.run(scrape_mic_sellers(category=category, limit=limit))
        console.print(f"\n[bold green]Total found: {len(sellers)} sellers[/bold green]")
        save_companies(sellers, source="made_in_china")


if __name__ == "__main__":
    main()
