"""
Search Facebook Ad Library for Chinese companies advertising in EU countries.

The Facebook Ad Library is FREE and PUBLIC — anyone can search it.
Companies running ads in Europe are actively spending money to reach EU customers.

Usage:
    python -m scraping.sources.facebook_ads --country DE --limit 20
    python -m scraping.sources.facebook_ads --country DE --search "LED light" --limit 30
"""

import asyncio
import re

import click
import httpx
from rich.console import Console
from rich.progress import Progress
from selectolax.parser import HTMLParser

from scraping.config.settings import settings
from scraping.sources.trade_shows import save_companies, _extract_domain
from scraping.utils.domain_finder import find_company_domain

console = Console()

EU_COUNTRIES = ["DE", "FR", "GB", "NL", "IT", "ES", "AT", "BE", "PL", "PT"]

# Product categories where Chinese brands dominate EU advertising
SEARCH_KEYWORDS = [
    # Electronics
    "LED light", "phone case", "smart watch", "drone", "power bank",
    "security camera", "robot vacuum", "3D printer", "wireless earbuds",
    "action camera", "ring light", "car accessories", "solar panel",
    "electric scooter", "massage gun", "air purifier", "projector",
    "bluetooth speaker", "dash cam", "smart home", "mini fan",
    "portable charger", "wireless charger", "car charger",
    # Beauty / health
    "facial massager", "hair remover", "teeth whitening", "skincare device",
    "fitness tracker", "smart scale", "posture corrector",
    # Home
    "LED strip", "smart bulb", "silicone mat", "storage box",
    "pet camera", "automatic feeder", "tabletop fountain",
    # Fashion / accessories
    "wristwatch", "ring", "necklace", "wallet", "backpack",
    # Tools
    "laser engraver", "soldering iron", "diy kit", "multitool",
]

# Chinese character range for detecting Chinese brand names
CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")

# Common pinyin syllables that strongly suggest a Chinese brand
PINYIN_SYLLABLES = {
    "xiao", "hua", "da", "zhong", "tian", "jin", "chang", "guang",
    "shen", "dong", "ai", "bei", "fei", "gao", "hui", "lian", "mei",
    "xin", "ying", "zhi", "long", "ming", "xing", "yu", "cheng",
    "wei", "jian", "kang", "feng", "hong", "peng", "qing", "sheng",
    "tao", "yong", "zhan", "zi", "kai", "yu", "li", "xi",
}

# Known Western brand names to exclude
WESTERN_BRANDS = {
    "APPLE", "GOOGLE", "AMAZON", "SAMSUNG", "SONY", "BOSE", "DYSON",
    "TESLA", "NOKIA", "BOSCH", "SIEMENS", "PHILIPS", "LG", "HP",
    "DELL", "INTEL", "NIKE", "ADIDAS", "PUMA", "UNDER ARMOUR",
    "ZARA", "H&M", "UNIQLO", "IKEA", "NESTLE", "COCACOLA",
    "PEPSI", "MICROSOFT", "YAMAHA", "PANASONIC",
}

# Facebook Ad Library search URL (public, no auth needed)
AD_LIBRARY_URL = "https://www.facebook.com/ads/library/"


async def search_fb_ad_library(
    country: str = "DE",
    search_term: str = "",
    limit: int = 20,
) -> list[dict]:
    """
    Search Facebook Ad Library for Chinese advertisers targeting an EU country.

    Uses Playwright to search the Ad Library UI (the API requires access tokens
    that have become harder to get — the web UI is more reliable).
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        console.print("[red]Playwright required. Run: pip install playwright && playwright install chromium[/red]")
        return []

    results = []
    seen_advertisers = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            locale="en-US",
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        page = await context.new_page()

        keywords = [search_term] if search_term else SEARCH_KEYWORDS

        with Progress() as progress:
            task = progress.add_task(f"Searching FB Ad Library ({country})...", total=limit)

            for keyword in keywords:
                if len(results) >= limit:
                    break

                try:
                    # Build the Ad Library search URL
                    url = (
                        f"{AD_LIBRARY_URL}?"
                        f"active_status=active"
                        f"&ad_type=all"
                        f"&country={country}"
                        f"&q={keyword}"
                        f"&search_type=keyword_unordered"
                    )

                    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    await asyncio.sleep(5)

                    # Scroll to load more results
                    for _ in range(3):
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        await asyncio.sleep(2)

                    # Extract advertiser info from the ad cards
                    advertisers = await _extract_advertisers(page)

                    for adv in advertisers:
                        if len(results) >= limit:
                            break

                        name = adv["name"]
                        if name in seen_advertisers:
                            continue

                        # Check if this looks like a Chinese brand
                        if not _looks_chinese_brand(name, adv.get("website", "")):
                            continue

                        seen_advertisers.add(name)

                        # Find their website
                        domain = ""
                        has_site = None
                        if adv.get("website"):
                            domain = _extract_domain(adv["website"])
                            has_site = True
                        else:
                            domain = await find_company_domain(name)
                            has_site = bool(domain)
                            await asyncio.sleep(settings.rate_limit_default)

                        page_url = adv.get("page_url", "")

                        results.append({
                            "name": name,
                            "name_cn": "",
                            "domain": domain,
                            "industry": keyword,
                            "source_url": page_url or url,
                            "marketplace_url": page_url,
                            "eu_countries_active": country,
                            "has_standalone_site": has_site,
                            "company_size": "small",
                            "notes": f"FB ads in {country} for '{keyword}' | Page: {page_url}",
                        })
                        progress.update(task, advance=1)
                        console.print(f"  [green]+[/green] {name}")

                except Exception as e:
                    console.print(f"  [dim]Error searching '{keyword}': {e}[/dim]")

                await asyncio.sleep(settings.rate_limit_facebook)

        await browser.close()

    return results


async def _extract_advertisers(page) -> list[dict]:
    """Extract advertiser names and page links from Ad Library search results."""
    advertisers = []

    # Ad Library cards show: advertiser name, "See ad details" link, page link
    # The structure varies, so try multiple selectors
    cards = await page.query_selector_all(
        "[class*='_7jvw'], [class*='ad-library'], [role='article']"
    )

    if not cards:
        # Fallback: get all links that look like Facebook page links
        text = await page.inner_text("body")
        # Extract page names from the visible text
        # Ad Library shows "Ad by <Page Name>" or just the page name prominently
        pass

    for card in cards:
        try:
            # Get advertiser name (usually in a heading or bold text)
            name_el = await card.query_selector("strong, h3, h4, [class*='name']")
            if not name_el:
                continue
            name = (await name_el.inner_text()).strip()
            if not name or len(name) < 2:
                continue

            # Get page link
            page_link = ""
            links = await card.query_selector_all("a[href*='facebook.com']")
            for link in links:
                href = await link.get_attribute("href")
                if href and "/ads/library/" not in href:
                    page_link = href
                    break

            advertisers.append({
                "name": name,
                "page_url": page_link,
                "website": "",  # Will be enriched later
            })

        except Exception:
            continue

    # Also try extracting from page text if card extraction didn't work well
    if len(advertisers) < 3:
        try:
            content = await page.content()
            # Facebook Ad Library embeds advertiser data in the page
            # Look for page names in the HTML
            names = re.findall(r'"page_name"\s*:\s*"([^"]+)"', content)
            page_ids = re.findall(r'"page_id"\s*:\s*"(\d+)"', content)

            for i, name in enumerate(names):
                if name not in [a["name"] for a in advertisers]:
                    page_id = page_ids[i] if i < len(page_ids) else ""
                    page_url = f"https://www.facebook.com/{page_id}" if page_id else ""
                    advertisers.append({
                        "name": name,
                        "page_url": page_url,
                        "website": "",
                    })
        except Exception:
            pass

    return advertisers


def _looks_chinese_brand(name: str, website: str = "") -> bool:
    """
    Heuristic: does this advertiser name look like a Chinese brand?

    Signals (any ONE triggers positive):
    - Contains Chinese characters
    - Website is .cn or Chinese hosting
    - Contains known Chinese city name (Shenzhen, Guangzhou, etc.)
    - Name contains pinyin syllables
    - Short all-caps single-word brand name (not a known Western brand)
    """
    if not name or len(name) < 3:
        return False

    # Hard exclude: known Western brands
    if name.upper().strip() in WESTERN_BRANDS:
        return False

    # Chinese characters in name — strongest signal
    if CHINESE_RE.search(name):
        return True

    # Website on .cn domain
    if website and (".cn" in website or ".com.cn" in website):
        return True

    name_lower = name.lower()

    # Chinese city/province in name (e.g. "Shenzhen XYZ Co.")
    chinese_locations = [
        "shenzhen", "guangzhou", "shanghai", "beijing", "yiwu",
        "hangzhou", "dongguan", "xiamen", "ningbo", "qingdao",
        "foshan", "zhongshan", "guangdong", "zhejiang", "fujian",
    ]
    if any(loc in name_lower for loc in chinese_locations):
        return True

    # Chinese company suffixes
    cn_suffixes = ["co., ltd", "co.,ltd", "technology co", "trading co",
                   "electronic co", "industries limited"]
    if any(suf in name_lower for suf in cn_suffixes):
        return True

    # Pinyin syllable detection: 2+ syllables OR 1 distinctive syllable in short name
    name_tokens = re.findall(r"[a-z]+", name_lower)
    pinyin_hits = sum(1 for t in name_tokens if t in PINYIN_SYLLABLES)
    if pinyin_hits >= 2:
        return True
    # Short name (<= 2 tokens) with 1 distinctive pinyin syllable
    if len(name_tokens) <= 2 and pinyin_hits >= 1:
        return True
    # Check within the name string for common pinyin openers
    distinctive_openers = ("xiao", "hua", "zhong", "shen", "guang", "dong", "bei")
    if any(name_lower.startswith(op) for op in distinctive_openers):
        return True

    # Single-word all-caps brand (UGREEN, BASEUS, TOZO pattern)
    stripped = name.strip()
    if re.match(r"^[A-Z]{4,10}$", stripped) and " " not in stripped:
        return True

    # CamelCase branding that contains a known CN brand token
    # e.g. "BaseusGlobal", "XiaomiEU", "AnkerDirect"
    known_cn_brands = ("baseus", "xiaomi", "ugreen", "anker", "huawei",
                        "lenovo", "oppo", "vivo", "tozo", "tcl",
                        "hikvision", "dji", "ecoflow", "bluetti", "govee")
    if any(brand in name_lower for brand in known_cn_brands):
        return True

    return False


@click.command()
@click.option("--country", type=click.Choice(EU_COUNTRIES), default="DE")
@click.option("--search", "search_term", default="", help="Specific search term (default: uses built-in keyword list)")
@click.option("--limit", default=20, help="Max advertisers to find")
def main(country: str, search_term: str, limit: int):
    """Search Facebook Ad Library for Chinese brands advertising in EU."""
    console.print(f"[bold]Searching FB Ad Library — ads targeting {country} (limit={limit})...[/bold]")
    results = asyncio.run(search_fb_ad_library(country=country, search_term=search_term, limit=limit))
    console.print(f"\nFound {len(results)} Chinese advertisers")
    save_companies(results, source="facebook_ads")


if __name__ == "__main__":
    main()
