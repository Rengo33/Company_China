"""
Scrape Amazon EU marketplaces for Chinese DTC sellers.

EU law (Digital Services Act) requires sellers to disclose their business address.
We visit seller profile pages and filter for Chinese addresses.

These companies are already spending money selling in Europe — ideal EightFold leads.

Usage:
    python -m scraping.sources.amazon_eu --marketplace de --limit 20
    python -m scraping.sources.amazon_eu --marketplace de --category electronics --limit 50
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

console = Console()

# Chinese cities/provinces that appear in seller addresses
CHINESE_CITIES = {
    "shenzhen", "guangzhou", "dongguan", "yiwu", "hangzhou", "shanghai",
    "beijing", "xiamen", "ningbo", "foshan", "zhongshan", "suzhou",
    "chengdu", "wuhan", "qingdao", "tianjin", "nanjing", "wenzhou",
    "fuzhou", "changsha", "jinan", "hefei", "kunming", "zhengzhou",
    "guangdong", "zhejiang", "fujian", "jiangsu", "shandong",
    "china", "cn", "p.r.c", "prc",
}

AMAZON_DOMAINS = {
    "de": "https://www.amazon.de",
    "uk": "https://www.amazon.co.uk",
    "fr": "https://www.amazon.fr",
}

# Bestseller category paths (Amazon.de — similar on .co.uk/.fr)
CATEGORIES = {
    "electronics": "/gp/bestsellers/ce-de/",
    "home": "/gp/bestsellers/kitchen/",
    "beauty": "/gp/bestsellers/beauty/",
    "sports": "/gp/bestsellers/sports/",
    "tools": "/gp/bestsellers/diy/",
    "lighting": "/gp/bestsellers/lighting/",
    "garden": "/gp/bestsellers/garden/",
    "toys": "/gp/bestsellers/toys/",
    "computers": "/gp/bestsellers/computers/",
    "pet": "/gp/bestsellers/pet-supplies/",
}


async def scrape_amazon_sellers(
    marketplace: str = "de",
    category: str = "electronics",
    limit: int = 20,
) -> list[dict]:
    """
    Scrape Amazon for Chinese sellers by visiting bestseller pages,
    extracting seller links, and checking their business addresses.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        console.print("[red]Playwright not installed. Run: pip install playwright && playwright install chromium[/red]")
        return []

    results = []
    base_url = AMAZON_DOMAINS.get(marketplace, AMAZON_DOMAINS["de"])
    cat_path = CATEGORIES.get(category, CATEGORIES["electronics"])
    seen_sellers = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            locale="de-DE" if marketplace == "de" else "en-GB",
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        page = await context.new_page()

        with Progress() as progress:
            task = progress.add_task(f"Scraping Amazon.{marketplace} sellers...", total=limit)

            try:
                # Step 1: Visit bestseller page and collect product links
                url = base_url + cat_path
                console.print(f"  Loading {url}")
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(random.uniform(3, 5))

                # Extract product links from bestseller grid
                product_links = await _extract_product_links(page, base_url)
                console.print(f"  Found {len(product_links)} products")

                # Step 2: Visit each product to find the seller
                for prod_url in product_links:
                    if len(results) >= limit:
                        break

                    try:
                        seller_info = await _extract_seller_from_product(page, prod_url, base_url)
                        if not seller_info:
                            continue

                        seller_id = seller_info.get("seller_id", "")
                        if seller_id in seen_sellers:
                            continue
                        seen_sellers.add(seller_id)

                        # Step 3: Visit seller profile and check address
                        seller_data = await _scrape_seller_profile(
                            page, seller_info["profile_url"], base_url, marketplace
                        )

                        if seller_data and seller_data.get("is_chinese"):
                            # Step 4: Find standalone website
                            domain = ""
                            has_site = None
                            if seller_data.get("website"):
                                domain = _extract_domain(seller_data["website"])
                                has_site = True
                            else:
                                domain = await find_company_domain(seller_data["name"])
                                has_site = bool(domain)

                            results.append({
                                "name": seller_data["name"],
                                "name_cn": "",
                                "domain": domain,
                                "industry": category,
                                "source_url": seller_info["profile_url"],
                                "marketplace_url": seller_info["profile_url"],
                                "eu_countries_active": marketplace.upper(),
                                "has_standalone_site": has_site,
                                "company_size": "small",
                                "notes": f"Amazon {marketplace.upper()} seller | Address: {seller_data.get('address', '')}",
                            })
                            progress.update(task, advance=1)
                            console.print(f"  [green]+[/green] {seller_data['name']}")

                        await asyncio.sleep(random.uniform(
                            settings.rate_limit_amazon - 2,
                            settings.rate_limit_amazon + 2,
                        ))

                    except Exception as e:
                        console.print(f"  [dim]Skip: {e}[/dim]")
                        continue

            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")

        await browser.close()

    return results


async def _extract_product_links(page, base_url: str) -> list[str]:
    """Extract product links from a bestseller/category page."""
    links = set()

    # Amazon bestseller pages use various selectors
    selectors = [
        "a.a-link-normal[href*='/dp/']",
        "[data-asin] a[href*='/dp/']",
        ".zg-item-immersion a[href*='/dp/']",
        ".p13n-sc-uncoverable-faceout a[href*='/dp/']",
    ]

    for sel in selectors:
        elements = await page.query_selector_all(sel)
        for el in elements:
            href = await el.get_attribute("href")
            if href and "/dp/" in href:
                # Normalize URL
                if href.startswith("/"):
                    href = base_url + href
                # Strip query params
                href = re.sub(r'\?.*', '', href)
                links.add(href)

    return list(links)[:100]  # cap to avoid too many requests


async def _extract_seller_from_product(page, product_url: str, base_url: str) -> dict | None:
    """Visit a product page and extract the seller info."""
    try:
        await page.goto(product_url, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(random.uniform(2, 4))

        # Look for "Sold by" or "Verkauf durch" seller link
        seller_selectors = [
            "#sellerProfileTriggerId",
            "a[href*='/sp?']",
            "a[href*='seller=']",
            "#merchant-info a",
        ]

        for sel in seller_selectors:
            el = await page.query_selector(sel)
            if el:
                href = await el.get_attribute("href")
                name = await el.inner_text()
                if href:
                    if href.startswith("/"):
                        href = base_url + href
                    # Extract seller ID
                    sid_match = re.search(r'seller=([A-Z0-9]+)', href)
                    return {
                        "profile_url": href,
                        "seller_name": name.strip(),
                        "seller_id": sid_match.group(1) if sid_match else name,
                    }

    except Exception:
        pass

    return None


async def _scrape_seller_profile(page, profile_url: str, base_url: str, marketplace: str) -> dict | None:
    """Visit a seller profile page and extract business address."""
    try:
        await page.goto(profile_url, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(random.uniform(2, 4))

        text = await page.inner_text("body")

        # Extract business name
        name = ""
        name_el = await page.query_selector("#page-section-detail-seller-info h1, .a-box h1")
        if name_el:
            name = (await name_el.inner_text()).strip()

        if not name:
            # Try from page title
            title = await page.title()
            name = title.replace("Amazon.de: ", "").replace("Amazon.co.uk: ", "").strip()

        # Extract business address — EU DSA requires this
        address = ""
        address_patterns = [
            r"(?:Geschäftsadresse|Business Address|Adresse professionnelle)[:\s]*\n?([\s\S]{10,200}?)(?:\n\n|\n[A-Z])",
            r"(?:Handelsregisternummer|Trade register|Registration number)[:\s]",
        ]

        for pat in address_patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                address = m.group(1).strip() if m.lastindex else ""
                break

        # Broader address extraction: look for Chinese city names anywhere in text
        if not address:
            # Check if any Chinese city appears in the page text
            text_lower = text.lower()
            for city in CHINESE_CITIES:
                if city in text_lower:
                    # Try to extract a larger address block around it
                    idx = text_lower.find(city)
                    start = max(0, text.rfind("\n", 0, idx))
                    end = min(len(text), text.find("\n\n", idx))
                    address = text[start:end].strip()
                    break

        is_chinese = _is_chinese_address(address) or _is_chinese_address(text)

        # Extract website if listed
        website = ""
        website_el = await page.query_selector("a[href*='redirector.amazon'][href*='url=']")
        if website_el:
            href = await website_el.get_attribute("href")
            url_match = re.search(r'url=([^&]+)', href)
            if url_match:
                from urllib.parse import unquote
                website = unquote(url_match.group(1))

        return {
            "name": name,
            "address": address[:200],
            "is_chinese": is_chinese,
            "website": website,
        }

    except Exception:
        pass

    return None


def _is_chinese_address(text: str) -> bool:
    """Check if text contains Chinese address indicators."""
    text_lower = text.lower()
    matches = sum(1 for city in CHINESE_CITIES if city in text_lower)
    return matches >= 1


@click.command()
@click.option("--marketplace", type=click.Choice(list(AMAZON_DOMAINS.keys())), default="de")
@click.option("--category", type=click.Choice(list(CATEGORIES.keys()) + ["all"]), default="electronics")
@click.option("--limit", default=20, help="Max sellers to find")
def main(marketplace: str, category: str, limit: int):
    """Scrape Amazon EU for Chinese sellers."""
    if category == "all":
        console.print(f"[bold]Scraping Amazon.{marketplace} — all categories (limit={limit} per category)...[/bold]")
        all_results = []
        for cat in CATEGORIES:
            console.print(f"\n[bold cyan]Category: {cat}[/bold cyan]")
            results = asyncio.run(scrape_amazon_sellers(marketplace=marketplace, category=cat, limit=limit))
            all_results.extend(results)
            save_companies(results, source=f"amazon_{marketplace}")
        console.print(f"\n[bold green]Total: {len(all_results)} Chinese sellers found[/bold green]")
    else:
        console.print(f"[bold]Scraping Amazon.{marketplace} ({category}, limit={limit})...[/bold]")
        results = asyncio.run(scrape_amazon_sellers(marketplace=marketplace, category=category, limit=limit))
        console.print(f"Found {len(results)} Chinese sellers")
        save_companies(results, source=f"amazon_{marketplace}")


if __name__ == "__main__":
    main()
