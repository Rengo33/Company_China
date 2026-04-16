"""
Made-in-China.com authenticated scraper.

Uses a persistent Playwright session with saved login to access
contact details (phone, email, fax, contact person) that are hidden
behind the login wall.

First run: call setup_session() to log in and save the session.
Subsequent runs: call scrape_contact_page() — it reuses the saved session.
"""

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from rich.console import Console

console = Console()

SESSION_DIR = str(Path(__file__).resolve().parent.parent / ".mic-session")


@dataclass
class MICContact:
    telephone: str = ""
    mobile: str = ""
    fax: str = ""
    email: str = ""
    contact_name: str = ""
    contact_title: str = ""


async def scrape_contact_page(profile_url: str) -> MICContact:
    """
    Scrape a company's contact-info page using the saved login session.
    Returns phone, email, contact name, title.
    """
    from playwright.async_api import async_playwright

    contact = MICContact()

    # Derive contact-info URL from profile URL
    base_match = re.match(r"(https?://[^/]+)", profile_url)
    if not base_match:
        return contact

    contact_url = base_match.group(1) + "/contact-info.html"

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=SESSION_DIR,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )

        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(contact_url, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(3)

            text = await page.inner_text("body")

            if "Sign In for Details" in text:
                console.print("[yellow]Session expired — run setup_session() to re-login[/yellow]")
                return contact

            # Parse contact details from page text
            contact = _parse_contact_text(text)

        except Exception as e:
            console.print(f"[red]Error scraping {contact_url}: {e}[/red]")
        finally:
            await context.close()

    return contact


async def scrape_contact_pages_batch(profile_urls: list[str], delay: float = 2.0) -> list[MICContact]:
    """
    Scrape multiple contact pages in one browser session (much faster).
    """
    from playwright.async_api import async_playwright

    results = []

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=SESSION_DIR,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )

        try:
            page = context.pages[0] if context.pages else await context.new_page()

            for url in profile_urls:
                base_match = re.match(r"(https?://[^/]+)", url)
                if not base_match:
                    results.append(MICContact())
                    continue

                contact_url = base_match.group(1) + "/contact-info.html"

                try:
                    await page.goto(contact_url, wait_until="domcontentloaded", timeout=20000)
                    await asyncio.sleep(delay)

                    text = await page.inner_text("body")

                    if "Sign In for Details" in text:
                        console.print("[yellow]Session expired[/yellow]")
                        results.append(MICContact())
                        break

                    contact = _parse_contact_text(text)
                    results.append(contact)

                except Exception as e:
                    results.append(MICContact())

        finally:
            await context.close()

    return results


def _parse_contact_text(text: str) -> MICContact:
    """Parse contact details from the page body text."""
    contact = MICContact()

    # Phone numbers
    tel_match = re.search(r"Telephone:\s*\n?\s*([\d\-+]+)", text)
    if tel_match:
        contact.telephone = tel_match.group(1).strip()

    mobile_match = re.search(r"Mobile Phone:\s*\n?\s*([\d\-+]+)", text)
    if mobile_match:
        contact.mobile = mobile_match.group(1).strip()

    fax_match = re.search(r"Fax:\s*\n?\s*([\d\-+]+)", text)
    if fax_match:
        contact.fax = fax_match.group(1).strip()

    # Email (if present)
    email_match = re.search(r"E-mail:\s*\n?\s*([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})", text)
    if email_match:
        contact.email = email_match.group(1).strip()

    # Contact person — "Mr./Miss/Ms. Name" pattern
    name_match = re.search(r"((?:Mr\.?|Mrs\.?|Ms\.?|Miss)\s+[A-Za-z]+(?:\s+[A-Za-z]+)?)", text)
    if name_match:
        name = name_match.group(1).strip()
        # Remove junk words that sometimes appear after the name
        name = re.sub(r"\s+(?:Chat|Sales|Export|Send|Now|Inquiry).*", "", name, flags=re.IGNORECASE)
        contact.contact_name = name.strip()

    # Contact title
    title_patterns = [
        r"((?:Sales|Export|Marketing|Overseas|Foreign Trade|International|General)[^\n]{0,30}(?:Manager|Director|Representative|Supervisor|Executive))",
        r"((?:CEO|COO|CFO|VP|President|Owner|Founder))",
    ]
    for pat in title_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            contact.contact_title = m.group(1).strip()
            break

    return contact


async def setup_session():
    """Open a browser for manual login. Session is saved for future use."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=SESSION_DIR,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://www.made-in-china.com/sign-in", wait_until="domcontentloaded")

        console.print("[bold]Log in to Made-in-China.com in the browser window.[/bold]")
        console.print("Waiting for login...")

        for _ in range(60):
            await asyncio.sleep(5)
            cookies = await context.cookies()
            if any(c["name"] == "lg" for c in cookies):
                console.print("[green]Login saved![/green]")
                break

        await context.close()
