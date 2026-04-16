"""
Requests-based Alibaba scraper (bypasses CAPTCHA).

Uses cookies from the existing Playwright session (.alibaba-session/)
with curl_cffi TLS fingerprinting. No browser needed for scraping —
just to create the session once.

Setup:
    python -m scraping.sources.alibaba --setup   # one-time manual CAPTCHA solve

Usage:
    python -m scraping.sources.alibaba_requests --category electronics --limit 50
    python -m scraping.sources.alibaba_requests --category all --limit 30
"""

import asyncio
import json
import random
import re
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.progress import Progress

from scraping.config.settings import settings
from scraping.sources.trade_shows import save_companies, _extract_domain
from scraping.utils.http import StealthClient

console = Console()

SESSION_DIR = str(Path(__file__).resolve().parent.parent / ".alibaba-session")
COOKIE_CACHE = Path(__file__).resolve().parent.parent / ".alibaba-cookies.json"

# Same categories as the Playwright version
CATEGORIES = {
    "electronics": "consumer-electronics",
    "home": "home-and-garden",
    "beauty": "beauty-personal-care",
    "auto": "vehicles-accessories",
    "fashion": "apparel",
    "sports": "sports-entertainment",
    "lighting": "lights-lighting",
    "machinery": "machinery",
    "furniture": "furniture",
    "construction": "construction-real-estate",
    "electrical": "electronic-components-supplies",
    "packaging": "packaging-printing",
    "toys": "toys-hobbies",
    "security": "security-protection",
    "tools": "tools-hardware",
    "health": "health-medical",
    "food": "food-beverage",
    "textile": "textiles-leather-products",
    "chemicals": "chemicals",
    "office": "office-school-supplies",
}


async def warm_up_session(category_slug: str = "consumer-electronics") -> dict:
    """
    Warm up the Alibaba session by visiting search via real browser.

    This gets a legitimate session (solves CAPTCHA if needed), then
    returns cookies for use with HTTP requests.
    """
    from playwright.async_api import async_playwright

    console.print("[dim]Warming up session via browser...[/dim]")
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=SESSION_DIR,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1440, "height": 900},
        )

        try:
            page = context.pages[0] if context.pages else await context.new_page()
            url = f"https://www.alibaba.com/trade/search?SearchText={category_slug}&tab=supplier&page=1"
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(5)

            # Wait for supplier data to appear (or CAPTCHA to be solved)
            for attempt in range(60):  # up to 5 min
                content = await page.content()
                if '"companyName"' in content:
                    console.print("[green]Session warmed up — supplier data visible[/green]")
                    break
                # Check if CAPTCHA is showing
                title = await page.title()
                if "captcha" in title.lower() or "verify" in title.lower():
                    if attempt == 0:
                        import subprocess
                        try:
                            subprocess.Popen(["afplay", "/System/Library/Sounds/Glass.aiff"])
                        except Exception:
                            pass
                        console.print("[bold yellow]⚠ Solve CAPTCHA in browser — scraper will continue automatically[/bold yellow]")
                await asyncio.sleep(5)

            # Extract cookies
            cookies = await context.cookies()
        finally:
            await context.close()

    alibaba_cookies = {
        c["name"]: c["value"]
        for c in cookies
        if "alibaba" in c.get("domain", "")
    }

    # Cache
    import time
    COOKIE_CACHE.write_text(json.dumps({
        "_ts": time.time(),
        "cookies": alibaba_cookies,
    }))

    console.print(f"[dim]Cached {len(alibaba_cookies)} cookies[/dim]")
    return alibaba_cookies


async def get_session_cookies(force_refresh: bool = False, category_slug: str = "consumer-electronics") -> dict:
    """
    Get session cookies, either from cache or by warming up via browser.
    """
    if not force_refresh and COOKIE_CACHE.exists():
        try:
            data = json.loads(COOKIE_CACHE.read_text())
            # Shorter cache — 2 hours — because Alibaba flags stale sessions
            import time
            if time.time() - data.get("_ts", 0) < 2 * 3600:
                return data.get("cookies", {})
        except Exception:
            pass

    return await warm_up_session(category_slug)


def _build_cookie_header(cookies: dict) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


def _parse_supplier_data(html: str) -> list[dict]:
    """Extract company names + profile URLs from the search results HTML."""
    # Company names from JSON blob
    names = re.findall(r'"companyName"\s*:\s*"([^"]+)"', html)
    # Company IDs correspond to order of names
    company_ids = re.findall(r'"companyId"\s*:\s*"(\d+)"', html)
    # Store subdomains (profile URLs)
    subdomains = re.findall(r'([a-z0-9\-]+)\.en\.alibaba\.com/company_profile\.html', html)
    # Dedupe subdomains preserving order
    seen = set()
    ordered_subs = []
    for s in subdomains:
        if s not in seen:
            seen.add(s)
            ordered_subs.append(s)

    # Build list — pair names with subdomains by order
    results = []
    for i, name in enumerate(names):
        profile_url = ""
        if i < len(ordered_subs):
            profile_url = f"https://{ordered_subs[i]}.en.alibaba.com/company_profile.html"
        results.append({
            "name": name,
            "profile_url": profile_url,
            "company_id": company_ids[i] if i < len(company_ids) else "",
        })
    return results


async def _scrape_supplier_profile(client: StealthClient, cookie_header: str, profile_url: str) -> dict:
    """Visit a supplier's contactinfo page to get website + contact person."""
    data = {"website": "", "contact_name": "", "contact_title": "",
            "company_size": "", "address": "", "products": ""}

    if not profile_url:
        return data

    try:
        base = re.match(r"(https?://[^/]+)", profile_url)
        if not base:
            return data

        contact_url = base.group(1) + "/contactinfo.html"
        resp = await client.get(contact_url, headers={"Cookie": cookie_header})

        if resp.status_code != 200:
            return data

        text = resp.text

        # Company website (shown even when not logged in)
        website_match = re.search(
            r"Company website[:\s<>/]*\s*(https?://[^\s<>\"]+)",
            text, re.IGNORECASE,
        )
        if website_match:
            url = website_match.group(1).strip().rstrip("/")
            if "alibaba.com" not in url:
                data["website"] = url

        # Fallback: look for non-alibaba external links
        if not data["website"]:
            ext_links = re.findall(r'href="(https?://[^"]+)"', text)
            for link in ext_links:
                if ("alibaba" not in link and "alicdn" not in link
                        and "aliyun" not in link and "baidu" not in link
                        and "google" not in link):
                    data["website"] = link
                    break

        # Contact person — "Mr./Ms./Miss Name" pattern
        name_match = re.search(
            r"((?:Mr\.?|Mrs\.?|Ms\.?|Miss)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})",
            re.sub(r"<[^>]+>", " ", text),
        )
        if name_match:
            data["contact_name"] = name_match.group(1).strip()

        # Title (near contact name)
        title_patterns = [
            r"((?:Sales|Export|Marketing|Overseas|Foreign Trade|International|General)[^\n<]{0,30}(?:Manager|Director|Representative|Supervisor|Executive))",
            r"\b(CEO|COO|CFO|VP|President|Owner|Founder)\b",
        ]
        for pat in title_patterns:
            m = re.search(pat, re.sub(r"<[^>]+>", " ", text), re.IGNORECASE)
            if m:
                data["contact_title"] = m.group(1).strip()
                break

    except Exception as e:
        console.print(f"  [dim]Profile error: {e}[/dim]")

    return data


async def scrape_alibaba_requests(
    category: str = "electronics", limit: int = 50
) -> list[dict]:
    """Scrape Alibaba via HTTP requests (no browser)."""
    results = []
    cat_slug = CATEGORIES.get(category, category)
    seen = set()

    cookies = await get_session_cookies(category_slug=cat_slug)
    if not cookies:
        console.print("[red]No cookies available. Run: python -m scraping.sources.alibaba --setup[/red]")
        return []

    cookie_header = _build_cookie_header(cookies)

    async with StealthClient() as client:
        with Progress() as progress:
            task = progress.add_task(f"Scraping Alibaba ({cat_slug})...", total=limit)

            page_num = 1
            consecutive_fails = 0
            while len(results) < limit and page_num <= 20:
                url = (
                    f"https://www.alibaba.com/trade/search?"
                    f"SearchText={cat_slug}&tab=supplier&page={page_num}"
                )

                try:
                    resp = await client.get(url, headers={"Cookie": cookie_header})
                except Exception as e:
                    console.print(f"[yellow]HTTP error: {e}[/yellow]")
                    consecutive_fails += 1
                    if consecutive_fails >= 3:
                        break
                    await asyncio.sleep(random.uniform(5, 10))
                    continue

                if resp.status_code != 200:
                    console.print(f"[yellow]Page {page_num}: status {resp.status_code}[/yellow]")
                    # Check for CAPTCHA — session likely expired
                    if "slide to verify" in resp.text.lower() or "captcha" in resp.text.lower()[:5000]:
                        console.print("[red]Session expired. Refresh with: python -m scraping.sources.alibaba --setup[/red]")
                    break

                # Detect CAPTCHA wall in 200 response (sufei-punish, awsc.js markers)
                is_captcha = (
                    "sufei-punish" in resp.text[:5000]
                    or "awsc.js" in resp.text[:5000]
                    or "slide to verify" in resp.text[:5000].lower()
                )
                if is_captcha:
                    console.print("[yellow]CAPTCHA wall — re-warming session via browser...[/yellow]")
                    cookies = await warm_up_session(cat_slug)
                    if not cookies:
                        break
                    cookie_header = _build_cookie_header(cookies)
                    # Retry same page
                    continue

                suppliers = _parse_supplier_data(resp.text)
                if not suppliers:
                    console.print(f"  [dim]No suppliers on page {page_num}, stopping[/dim]")
                    break

                consecutive_fails = 0

                for supplier in suppliers:
                    if len(results) >= limit:
                        break
                    name = supplier["name"]
                    if name in seen:
                        continue
                    seen.add(name)

                    # Visit profile for website + contact
                    profile_data = await _scrape_supplier_profile(
                        client, cookie_header, supplier["profile_url"]
                    )
                    await asyncio.sleep(random.uniform(1, 2))

                    domain = _extract_domain(profile_data.get("website", ""))
                    has_site = bool(domain)

                    notes_parts = []
                    if profile_data.get("address"):
                        notes_parts.append(f"Address: {profile_data['address']}")
                    if profile_data.get("products"):
                        notes_parts.append(f"Products: {profile_data['products'][:150]}")
                    if supplier["profile_url"]:
                        notes_parts.append(f"Alibaba store: {supplier['profile_url']}")

                    results.append({
                        "name": name,
                        "name_cn": "",
                        "domain": domain,
                        "industry": cat_slug,
                        "source_url": supplier["profile_url"],
                        "marketplace_url": supplier["profile_url"],
                        "has_standalone_site": has_site,
                        "company_size": profile_data.get("company_size", ""),
                        "notes": " | ".join(notes_parts),
                        "_contact_name": profile_data.get("contact_name", ""),
                        "_contact_title": profile_data.get("contact_title", ""),
                    })
                    progress.update(task, advance=1)
                    console.print(f"  [green]+[/green] {name[:55]} | {domain or '(no site)'}")

                page_num += 1
                await asyncio.sleep(random.uniform(2, 4))

    return results


async def scrape_all_categories(limit_per_category: int = 30) -> list[dict]:
    """Scrape all Alibaba categories. Saves per category."""
    all_results = []

    for cat_key in CATEGORIES:
        console.print(f"\n[bold cyan]Category: {cat_key}[/bold cyan]")
        results = await scrape_alibaba_requests(category=cat_key, limit=limit_per_category)
        all_results.extend(results)
        save_companies(results, source="alibaba")
        console.print(f"  Total so far: {len(all_results)}")

    return all_results


@click.command()
@click.option("--category", type=click.Choice(list(CATEGORIES.keys()) + ["all"]), default="electronics")
@click.option("--limit", default=30, help="Max suppliers per category")
@click.option("--refresh-cookies", is_flag=True, help="Force refresh cookies from browser session")
def main(category: str, limit: int, refresh_cookies: bool):
    """Scrape Alibaba.com via HTTP requests (no browser CAPTCHA)."""
    if refresh_cookies and COOKIE_CACHE.exists():
        COOKIE_CACHE.unlink()
        console.print("[dim]Cookie cache cleared[/dim]")

    if category == "all":
        console.print(f"[bold]Scraping Alibaba (requests) — ALL {len(CATEGORIES)} categories (limit={limit}/cat)...[/bold]")
        results = asyncio.run(scrape_all_categories(limit_per_category=limit))
        console.print(f"\n[bold green]Total: {len(results)} suppliers[/bold green]")
    else:
        console.print(f"[bold]Scraping Alibaba (requests) ({category}, limit={limit})...[/bold]")
        results = asyncio.run(scrape_alibaba_requests(category=category, limit=limit))
        console.print(f"Found {len(results)} suppliers")
        save_companies(results, source="alibaba")


if __name__ == "__main__":
    main()
