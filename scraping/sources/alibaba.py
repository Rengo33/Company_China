"""
Scrape Alibaba.com supplier directory using authenticated Playwright session.

Alibaba blocks headless browsers and HTTP clients with CAPTCHA.
Solution: persistent Playwright session — solve CAPTCHA once, session saved for future runs.

Usage:
    python -m scraping.sources.alibaba --setup          # First time: open browser, solve CAPTCHA
    python -m scraping.sources.alibaba --category electronics --limit 50
    python -m scraping.sources.alibaba --category all --limit 30
"""

import asyncio
import random
import re
from pathlib import Path

import click
from rich.console import Console
from rich.progress import Progress

from scraping.config.settings import settings
from scraping.sources.trade_shows import save_companies, _extract_domain

console = Console()

SESSION_DIR = str(Path(__file__).resolve().parent.parent / ".alibaba-session")

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


async def setup_alibaba_session():
    """Open browser for user to solve CAPTCHA and/or log in. Session saved for future use."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=SESSION_DIR,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else await context.new_page()

        await page.goto("https://www.alibaba.com/trade/search?SearchText=electronics&tab=supplier",
                        wait_until="domcontentloaded")

        console.print("[bold]Browser opened — solve the CAPTCHA if shown.[/bold]")
        console.print("Once you see supplier results, the session is ready.")
        console.print("Waiting up to 3 minutes...")

        # Wait for the user to solve CAPTCHA — check for supplier data
        for _ in range(36):
            await asyncio.sleep(5)
            try:
                content = await page.content()
                if "companyName" in content or "company-name" in content:
                    console.print("[green]Supplier data visible — session saved![/green]")
                    await context.close()
                    return True
            except Exception:
                pass

        console.print("[yellow]Timeout. Try again with --setup[/yellow]")
        await context.close()
        return False


async def scrape_alibaba_suppliers(
    category: str = "electronics",
    limit: int = 50,
) -> list[dict]:
    """
    Scrape Alibaba supplier directory using saved session.
    Runs with visible browser (headless gets blocked).
    """
    from playwright.async_api import async_playwright

    results = []
    cat_slug = CATEGORIES.get(category, category)
    seen_companies = set()

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=SESSION_DIR,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )

        try:
            page = context.pages[0] if context.pages else await context.new_page()
            page_num = 1

            with Progress() as progress:
                task = progress.add_task(f"Scraping Alibaba ({cat_slug})...", total=limit)

                while len(results) < limit:
                    url = (
                        f"https://www.alibaba.com/trade/search?"
                        f"SearchText={cat_slug}&tab=supplier&page={page_num}"
                    )

                    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    await asyncio.sleep(random.uniform(3, 6))

                    # Check for CAPTCHA and wait for user to solve it
                    content = await page.content()
                    if await _wait_for_captcha(page, content):
                        content = await page.content()

                    # Extract supplier data from page
                    suppliers = await _extract_suppliers_from_page(page, content)

                    if not suppliers:
                        console.print(f"  [dim]No suppliers on page {page_num}, stopping[/dim]")
                        break

                    for supplier in suppliers:
                        if len(results) >= limit:
                            break

                        name = supplier["name"]
                        if name in seen_companies:
                            continue
                        seen_companies.add(name)

                        # Visit supplier profile for details
                        profile_data = await _scrape_supplier_profile(page, supplier["profile_url"])
                        await asyncio.sleep(random.uniform(2, 4))

                        domain = ""
                        has_site = None
                        if profile_data.get("website"):
                            domain = _extract_domain(profile_data["website"])
                            has_site = True
                        else:
                            has_site = False

                        results.append({
                            "name": name,
                            "name_cn": "",
                            "domain": domain,
                            "industry": cat_slug,
                            "source_url": supplier["profile_url"],
                            "marketplace_url": supplier["profile_url"],
                            "has_standalone_site": has_site,
                            "company_size": profile_data.get("company_size", ""),
                            "notes": _build_notes(supplier, profile_data),
                            "_contact_name": profile_data.get("contact_name", ""),
                            "_contact_title": profile_data.get("contact_title", ""),
                        })
                        progress.update(task, advance=1)
                        console.print(f"  [green]+[/green] {name[:60]}")

                    page_num += 1
                    await asyncio.sleep(random.uniform(5, 10))

        finally:
            await context.close()

    return results


async def _wait_for_captcha(page, content: str = "") -> bool:
    """Detect CAPTCHA and wait for user to solve it. Returns True if CAPTCHA was found and solved."""
    if not content:
        content = await page.content()

    captcha_signals = ["Captcha", "slide to verify", "Slide to verify", "unusual traffic"]
    is_captcha = any(s.lower() in content.lower() for s in captcha_signals)

    if not is_captcha:
        return False

    # Play a system beep to alert the user
    import subprocess
    try:
        subprocess.Popen(["afplay", "/System/Library/Sounds/Glass.aiff"])
    except Exception:
        pass

    console.print("\n[bold yellow]⚠ CAPTCHA — solve it in the browser window, scraper will continue automatically[/bold yellow]")

    for i in range(60):  # Wait up to 5 minutes
        await asyncio.sleep(5)
        try:
            content = await page.content()
            if not any(s.lower() in content.lower() for s in captcha_signals):
                console.print("[green]CAPTCHA solved, continuing...[/green]\n")
                await asyncio.sleep(2)
                return True
        except Exception:
            pass

    console.print("[red]CAPTCHA timeout (5 min). Stopping.[/red]")
    return True


async def _extract_suppliers_from_page(page, content: str) -> list[dict]:
    """Extract supplier names and profile links from search results page."""
    suppliers = []
    seen = set()

    # Primary method: extract from DOM links — these have the correct store subdomains
    # Pattern: //companyname.en.alibaba.com/company_profile.html
    links = await page.query_selector_all("a[href*='.en.alibaba.com/company_profile']")

    for link in links:
        try:
            href = await link.get_attribute("href") or ""
            name = (await link.inner_text()).strip()

            # Skip review/feedback links
            if "feedback" in href or "Rating" in name or not name or len(name) < 3:
                continue

            if name in seen:
                continue
            seen.add(name)

            # Normalize URL
            if href.startswith("//"):
                href = "https:" + href

            suppliers.append({
                "name": name,
                "profile_url": href,
            })
        except Exception:
            continue

    # Fallback: extract from JSON data if DOM didn't work
    if not suppliers:
        names = re.findall(r'"companyName"\s*:\s*"([^"]+)"', content)
        store_urls = re.findall(r'"supplierHref"\s*:\s*"([^"]+)"', content)

        for i, name in enumerate(names):
            if name in seen:
                continue
            seen.add(name)
            href = store_urls[i] if i < len(store_urls) else ""
            if href:
                href = href.replace("\\/", "/")
                if href.startswith("//"):
                    href = "https:" + href
            suppliers.append({
                "name": name,
                "profile_url": href,
            })

    return suppliers


async def _scrape_supplier_profile(page, profile_url: str) -> dict:
    """Visit supplier's Alibaba contact page to get website + contact person."""
    data = {
        "website": "",
        "contact_name": "",
        "contact_title": "",
        "company_size": "",
        "address": "",
        "products": "",
    }

    if not profile_url:
        return data

    try:
        # Visit the CONTACTS page — this has the company website URL + contact person
        base = re.match(r"(https?://[^/]+)", profile_url)
        if not base:
            return data

        contact_url = base.group(1) + "/contactinfo.html"
        await page.goto(contact_url, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(random.uniform(4, 7))

        # Handle CAPTCHA on profile page too
        content = await page.content()
        await _wait_for_captcha(page, content)

        text = await page.inner_text("body")
        content = await page.content()

        # Extract company website — shown on contact page
        website_match = re.search(
            r"Company website:\s*\n?\s*(https?://[^\s\n]+)", text, re.IGNORECASE
        )
        if website_match:
            url = website_match.group(1).strip()
            if "alibaba.com" not in url:
                data["website"] = url

        # Also check HTML for website links
        if not data["website"]:
            web_links = re.findall(r'href="(https?://[^"]+)"', content)
            for link in web_links:
                if "alibaba.com" not in link and "alicdn" not in link and "aliyun" not in link:
                    # Likely the company's own website
                    data["website"] = link
                    break

        # Extract contact person — pattern: "Mr./Ms./Miss Name\nTitle"
        name_match = re.search(
            r"((?:Mr\.?|Mrs\.?|Ms\.?|Miss)\s+[A-Za-z]+(?:\s+[A-Za-z]+)?)",
            text
        )
        if name_match:
            data["contact_name"] = name_match.group(1).strip()

        # Extract title (line after the name)
        title_match = re.search(
            r"(?:Mr\.?|Mrs\.?|Ms\.?|Miss)\s+\S+\s*\n\s*([A-Za-z][\w\s]{2,30}?)(?:\n|Company)",
            text
        )
        if title_match:
            title = title_match.group(1).strip()
            if title.lower() not in ("company", "view", "chat", "send"):
                data["contact_title"] = title

        # Now visit company profile for size + products
        profile_main = base.group(1) + "/company_profile.html"
        await page.goto(profile_main, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(random.uniform(2, 4))

        text2 = await page.inner_text("body")

        # Employee count / years in industry
        years_match = re.search(r"Years in industry\s*\n?\s*(\d+)", text2)
        emp_match = re.search(r"Total Employees\s*[\n:]\s*(\d[\d,\-]*)", text2)

        if emp_match:
            emp_str = emp_match.group(1).replace(",", "").split("-")[0]
            try:
                count = int(emp_str)
                if count < 50:
                    data["company_size"] = "small"
                elif count < 200:
                    data["company_size"] = "medium"
                else:
                    data["company_size"] = "large"
            except ValueError:
                pass

        # Main products
        prod_match = re.search(r"Main product[s]?\s*\n", text2, re.IGNORECASE)
        if prod_match:
            # Products are listed after "Main products" header
            start = prod_match.end()
            products_text = text2[start:start+300].strip()
            # Take first meaningful line
            first_line = products_text.split("\n")[0].strip()
            if first_line and len(first_line) > 5:
                data["products"] = first_line[:200]

    except Exception as e:
        console.print(f"  [dim]Profile error: {e}[/dim]")

    return data


def _build_notes(supplier: dict, profile: dict) -> str:
    """Build notes string from supplier and profile data."""
    parts = []
    if profile.get("address"):
        parts.append(f"Address: {profile['address']}")
    if profile.get("products"):
        parts.append(f"Products: {profile['products'][:150]}")
    if profile.get("company_size"):
        parts.append(f"Size: {profile['company_size']}")
    return " | ".join(parts) if parts else f"Alibaba store: {supplier.get('profile_url', '')}"


async def scrape_all_alibaba_categories(limit_per_category: int = 30) -> list[dict]:
    """Scrape all Alibaba categories. Saves per category."""
    all_results = []

    for cat_key in CATEGORIES:
        console.print(f"\n[bold cyan]Category: {cat_key}[/bold cyan]")
        results = await scrape_alibaba_suppliers(category=cat_key, limit=limit_per_category)
        all_results.extend(results)
        save_companies(results, source="alibaba")
        console.print(f"  Found {len(results)} suppliers (total: {len(all_results)})")

    return all_results


@click.command()
@click.option("--setup", is_flag=True, help="Set up session: open browser to solve CAPTCHA")
@click.option("--category", type=click.Choice(list(CATEGORIES.keys()) + ["all"]), default="electronics")
@click.option("--limit", default=30, help="Max suppliers per category")
def main(setup: bool, category: str, limit: int):
    """Scrape Alibaba.com supplier directory."""
    if setup:
        asyncio.run(setup_alibaba_session())
        return

    # Check if session exists
    session_path = Path(SESSION_DIR)
    if not session_path.exists() or not list(session_path.iterdir()):
        console.print("[yellow]No saved session. Run with --setup first to solve the CAPTCHA.[/yellow]")
        console.print("  python3 -m scraping.sources.alibaba --setup")
        return

    if category == "all":
        console.print(f"[bold]Scraping Alibaba — ALL {len(CATEGORIES)} categories (limit={limit}/cat)...[/bold]")
        results = asyncio.run(scrape_all_alibaba_categories(limit_per_category=limit))
        console.print(f"\n[bold green]Total: {len(results)} suppliers[/bold green]")
    else:
        console.print(f"[bold]Scraping Alibaba ({category}, limit={limit})...[/bold]")
        results = asyncio.run(scrape_alibaba_suppliers(category=category, limit=limit))
        console.print(f"Found {len(results)} suppliers")
        save_companies(results, source="alibaba")


if __name__ == "__main__":
    main()
