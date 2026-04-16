"""
Find a company's website domain from its name.

Uses DuckDuckGo HTML search (no API key, no CAPTCHA) to find
the company's actual website, filtering out B2B directories and junk.
"""

import asyncio
import re
from urllib.parse import unquote

from scraping.utils.http import StealthClient
from scraping.utils.skip_domains import is_skip_domain


async def find_company_domain(company_name: str) -> str:
    """Search DuckDuckGo for a company's website."""
    # More specific query with "official" + optional location helps filter aggregators
    queries = [
        f'"{company_name}" official website',
        f'"{company_name}" contact us',
    ]

    async with StealthClient() as client:
        for query in queries:
            try:
                resp = await client.get(
                    "https://html.duckduckgo.com/html/",
                    params={"q": query},
                )

                if resp.status_code != 200:
                    continue

                # DDG wraps result links via redirect: uddg=<encoded_url>
                raw_urls = re.findall(r'uddg=(https?[^&"]+)', resp.text)
                urls = [unquote(u) for u in raw_urls]

                # Score candidates — prefer domains that look like the company
                candidates = []
                name_tokens = _name_tokens(company_name)

                for url in urls:
                    domain = _extract_clean_domain(url)
                    if not domain:
                        continue
                    if is_skip_domain(domain):
                        continue

                    # Score: +1 per name token appearing in the domain
                    score = sum(1 for t in name_tokens if t in domain)
                    # Bonus for ccTLDs that match Chinese brands expanding globally
                    if domain.endswith(".com") or domain.endswith(".cn"):
                        score += 0.5
                    # Penalty for very long or dashy domains
                    if domain.count("-") > 2:
                        score -= 1

                    candidates.append((score, domain))

                # Return highest-scoring domain
                if candidates:
                    candidates.sort(key=lambda x: -x[0])
                    top_score, top_domain = candidates[0]
                    # Only return if there's meaningful signal OR it's the only result
                    if top_score > 0 or len(candidates) == 1:
                        return top_domain

            except Exception:
                continue

    return ""


def _name_tokens(name: str) -> list[str]:
    """Get meaningful tokens from a company name for domain matching."""
    # Remove common noise words
    noise = {
        "co", "ltd", "limited", "inc", "corporation", "corp",
        "group", "company", "technology", "tech", "technologies",
        "industry", "industries", "industrial", "trading", "trade",
        "international", "global", "china", "chinese",
        "manufacturing", "manufacturer", "the", "and", "of",
        "products", "product", "equipment", "machinery",
    }
    tokens = re.findall(r"[a-z]+", name.lower())
    return [t for t in tokens if len(t) >= 3 and t not in noise]


def _extract_clean_domain(url: str) -> str:
    """Extract domain from URL, stripping www."""
    match = re.match(r"https?://(?:www\.)?([^/]+)", url.lower())
    if not match:
        return ""
    domain = match.group(1)
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
