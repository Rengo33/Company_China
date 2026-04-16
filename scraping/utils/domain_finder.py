"""
Find a company's website domain from its name.

Uses DuckDuckGo HTML search (no API key, no CAPTCHA) to find
the company's actual website.
"""

import asyncio
import re
from urllib.parse import unquote

from scraping.utils.http import StealthClient

# Domains to skip (not company sites)
SKIP_DOMAINS = {
    "made-in-china.com", "alibaba.com", "aliexpress.com", "globalsources.com",
    "linkedin.com", "facebook.com", "twitter.com", "instagram.com",
    "youtube.com", "wikipedia.org", "bloomberg.com", "reuters.com",
    "amazon.com", "amazon.de", "amazon.co.uk", "amazon.fr", "amazon.it", "amazon.es",
    "ebay.com", "ebay.de", "ebay.co.uk",
    "google.com", "bing.com", "duckduckgo.com",
    "dnb.com", "zoominfo.com", "crunchbase.com",
    "temu.com", "shein.com", "wish.com",
}


async def find_company_domain(company_name: str) -> str:
    """Search DuckDuckGo for a company's website."""
    query = f"{company_name} official website"

    async with StealthClient() as client:
        try:
            resp = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
            )

            if resp.status_code != 200:
                return ""

            # DDG wraps result links via redirect: uddg=<encoded_url>
            raw_urls = re.findall(r'uddg=(https?[^&"]+)', resp.text)
            urls = [unquote(u) for u in raw_urls]

            for url in urls:
                domain = _extract_clean_domain(url)
                if domain and not any(skip in domain for skip in SKIP_DOMAINS):
                    return domain

        except Exception:
            pass

    return ""


def _extract_clean_domain(url: str) -> str:
    """Extract domain from URL, stripping www."""
    match = re.match(r"https?://(?:www\.)?([^/]+)", url.lower())
    if not match:
        return ""
    domain = match.group(1)
    # Skip IP addresses, localhost etc.
    if re.match(r"\d+\.\d+\.\d+\.\d+", domain):
        return ""
    return domain


async def enrich_domains(companies: list[dict], delay: float = 2.0) -> list[dict]:
    """Add domains to companies that don't have one."""
    from rich.progress import Progress

    with Progress() as progress:
        task = progress.add_task("Finding company domains...", total=len(companies))

        for c in companies:
            if not c.get("domain"):
                domain = await find_company_domain(c["name"])
                if domain:
                    c["domain"] = domain
            progress.advance(task)
            await asyncio.sleep(delay)

    return companies
