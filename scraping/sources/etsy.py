"""
Scrape Etsy for Chinese sellers.

Etsy has lots of Chinese sellers in handmade/custom product categories.
Their shop policies page often says "Ships from: China" and shows their
registered business location. Many of these are small operations without
their own DTC website — perfect EightFold targets.

Usage:
    python -m scraping.sources.etsy --category jewelry --limit 30
    python -m scraping.sources.etsy --category all --limit 20
"""

import asyncio
import random
import re

import click
from rich.console import Console
from rich.progress import Progress

from scraping.config.settings import settings
from scraping.sources.trade_shows import save_companies, _extract_domain
from scraping.utils.domain_finder import find_company_domain
from scraping.utils.http import StealthClient

console = Console()

CATEGORIES = {
    "jewelry": "jewelry",
    "home": "home-and-living",
    "art": "art-and-collectibles",
    "accessories": "accessories",
    "bath": "bath-and-beauty",
    "clothing": "clothing",
    "electronics": "electronics-and-accessories",
    "paper": "paper-and-party-supplies",
    "toys": "toys-and-games",
    "craft": "craft-supplies-and-tools",
}

CHINESE_LOCATION_MARKERS = {
    "china", "shenzhen", "guangzhou", "shanghai", "beijing",
    "yiwu", "hangzhou", "dongguan", "xiamen", "ningbo",
    "guangdong", "zhejiang", "fujian", "jiangsu",
}


async def scrape_etsy_chinese_sellers(
    category: str = "jewelry", limit: int = 30
) -> list[dict]:
    """
    Find Chinese Etsy sellers.

    Strategy:
    1. Search Etsy with filter `ship_to=chinese location` via URL
    2. Extract shop names + URLs
    3. Check shop's "Policies" page for China shipping
    4. Use domain_finder to see if they have a standalone site

    Uses Playwright because Etsy blocks regular HTTP clients with 403.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        console.print("[red]Playwright required for Etsy scraper[/red]")
        return []

    results = []
    seen_shops = set()
    cat_slug = CATEGORIES.get(category, category)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        page = await context.new_page()

        # Wrap client-like interface for the helper function
        class PageClient:
            async def get(self, url):
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(random.uniform(2, 4))

                class Resp:
                    pass

                r = Resp()
                r.status_code = 200
                r.text = await page.content()
                return r

        client = PageClient()

        with Progress() as progress:
            task = progress.add_task(f"Scraping Etsy ({cat_slug})...", total=limit)

            page_num = 1
            while len(results) < limit and page_num <= 5:
                try:
                    url = f"https://www.etsy.com/c/{cat_slug}?ref=pagination&page={page_num}"
                    resp = await client.get(url)

                    # Extract shop URLs from listings
                    # Etsy URLs look like: https://www.etsy.com/shop/SHOPNAME
                    shop_urls = set(re.findall(
                        r'href="(https?://www\.etsy\.com/shop/[A-Za-z0-9_\-]+)"',
                        resp.text,
                    ))

                    # Also match relative
                    shop_urls.update(
                        "https://www.etsy.com" + m
                        for m in re.findall(r'href="(/shop/[A-Za-z0-9_\-]+)"', resp.text)
                    )

                    if not shop_urls:
                        break

                    for shop_url in shop_urls:
                        if len(results) >= limit:
                            break
                        shop_name = shop_url.rsplit("/", 1)[-1]
                        if shop_name in seen_shops:
                            continue
                        seen_shops.add(shop_name)

                        # Visit shop page to check for Chinese location
                        shop_data = await _check_chinese_shop(client, shop_url)
                        if not shop_data or not shop_data.get("is_chinese"):
                            continue

                        # Try to find their external website
                        domain = ""
                        has_site = None
                        if shop_data.get("website"):
                            domain = _extract_domain(shop_data["website"])
                            has_site = True
                        else:
                            # Try domain search
                            domain = await find_company_domain(shop_data.get("display_name") or shop_name)
                            has_site = bool(domain)
                            await asyncio.sleep(settings.rate_limit_default)

                        results.append({
                            "name": shop_data.get("display_name") or shop_name,
                            "name_cn": "",
                            "domain": domain,
                            "industry": cat_slug,
                            "source_url": shop_url,
                            "marketplace_url": shop_url,
                            "eu_countries_active": "",
                            "has_standalone_site": has_site,
                            "company_size": "micro",  # Etsy sellers are tiny
                            "notes": f"Etsy shop | Ships from: {shop_data.get('location', 'China')}",
                        })
                        progress.update(task, advance=1)
                        console.print(f"  [green]+[/green] {shop_data.get('display_name') or shop_name}")

                        await asyncio.sleep(random.uniform(2, 4))

                    page_num += 1
                    await asyncio.sleep(random.uniform(3, 5))

                except Exception as e:
                    console.print(f"[red]Error: {e}[/red]")
                    break

        await browser.close()

    return results


async def _check_chinese_shop(client, shop_url: str) -> dict | None:
    """Check if an Etsy shop is based in China."""
    try:
        resp = await client.get(shop_url)
        if resp.status_code != 200:
            return None

        html = resp.text

        # Look for location indicators in the shop page
        location_patterns = [
            r'"country_name"\s*:\s*"([^"]+)"',
            r'"shop_location"\s*:\s*"([^"]+)"',
            r'data-appears-component-name="shop2-about-section"[^>]*>([^<]*(?:China|china)[^<]*)',
        ]

        location = ""
        for pat in location_patterns:
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                location = m.group(1).strip()
                break

        # Scan all visible text for Chinese markers
        text_lower = html.lower()
        is_chinese = False
        for marker in CHINESE_LOCATION_MARKERS:
            # Require word-boundary match to avoid false positives
            if re.search(rf"\b{marker}\b", text_lower):
                is_chinese = True
                if not location:
                    location = marker.title()
                break

        if not is_chinese:
            return None

        # Extract shop display name
        name_match = re.search(r'"shop_name"\s*:\s*"([^"]+)"', html)
        display_name = name_match.group(1) if name_match else ""

        # Extract external website (if shown in "About" section)
        website = ""
        ext_urls = re.findall(r'href="(https?://[^"]+)"', html)
        for url in ext_urls:
            if "etsy.com" in url or "etsystatic" in url or "facebook.com" in url or "instagram.com" in url:
                continue
            website = url
            break

        return {
            "is_chinese": True,
            "location": location,
            "display_name": display_name,
            "website": website,
        }

    except Exception:
        return None


@click.command()
@click.option("--category", type=click.Choice(list(CATEGORIES.keys()) + ["all"]), default="jewelry")
@click.option("--limit", default=30, help="Max Chinese sellers to find")
def main(category: str, limit: int):
    """Scrape Etsy for Chinese sellers."""
    if category == "all":
        console.print(f"[bold]Scraping Etsy ALL categories (limit={limit}/cat)...[/bold]")
        all_results = []
        for cat in CATEGORIES:
            console.print(f"\n[bold cyan]Category: {cat}[/bold cyan]")
            results = asyncio.run(scrape_etsy_chinese_sellers(category=cat, limit=limit))
            all_results.extend(results)
            save_companies(results, source="etsy")
        console.print(f"\n[bold green]Total: {len(all_results)} Chinese sellers[/bold green]")
    else:
        console.print(f"[bold]Scraping Etsy ({category}, limit={limit})...[/bold]")
        results = asyncio.run(scrape_etsy_chinese_sellers(category=category, limit=limit))
        console.print(f"Found {len(results)} Chinese sellers")
        save_companies(results, source="etsy")


if __name__ == "__main__":
    main()
