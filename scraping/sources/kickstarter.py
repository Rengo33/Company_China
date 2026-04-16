"""
Find Chinese creators on Kickstarter + Indiegogo.

These are startup-stage Chinese teams launching products for Western audiences.
They desperately need better branding/localization — prime EightFold targets.

Usage:
    python -m scraping.sources.kickstarter --platform kickstarter --category technology --limit 30
    python -m scraping.sources.kickstarter --platform indiegogo --limit 20
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

# Chinese city/province markers in creator location
CHINESE_MARKERS = {
    "china", "hong kong", "shenzhen", "guangzhou", "shanghai",
    "beijing", "yiwu", "hangzhou", "dongguan", "xiamen",
    "taipei",  # Taiwan
}

KICKSTARTER_CATEGORIES = {
    "technology": "technology",
    "design": "design",
    "games": "games",
    "fashion": "fashion",
    "food": "food",
    "art": "art",
}


async def scrape_kickstarter(
    category: str = "technology", limit: int = 30
) -> list[dict]:
    """
    Find Chinese-based Kickstarter creators.

    Strategy:
    1. Browse category page
    2. Extract project URLs
    3. Visit each project to check creator location
    4. Find their external website
    """
    results = []
    seen = set()
    cat_slug = KICKSTARTER_CATEGORIES.get(category, category)

    async with StealthClient() as client:
        with Progress() as progress:
            task = progress.add_task(f"Scraping Kickstarter ({cat_slug})...", total=limit)

            # Kickstarter discover page
            for page_num in range(1, 8):
                if len(results) >= limit:
                    break

                try:
                    url = f"https://www.kickstarter.com/discover/advanced?category_id={_kickstarter_cat_id(cat_slug)}&sort=popularity&page={page_num}"
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        break

                    # Extract project URLs
                    project_urls = set(re.findall(
                        r'href="(https?://www\.kickstarter\.com/projects/[^"]+)"',
                        resp.text,
                    ))
                    # Strip query params and anchor
                    project_urls = {re.sub(r"[?#].*$", "", u) for u in project_urls}

                    if not project_urls:
                        break

                    for proj_url in project_urls:
                        if len(results) >= limit:
                            break
                        if proj_url in seen:
                            continue
                        seen.add(proj_url)

                        proj_data = await _check_chinese_creator(client, proj_url)
                        if not proj_data or not proj_data.get("is_chinese"):
                            continue

                        domain = ""
                        has_site = None
                        if proj_data.get("website"):
                            domain = _extract_domain(proj_data["website"])
                            has_site = True
                        elif proj_data.get("creator_name"):
                            domain = await find_company_domain(proj_data["creator_name"])
                            has_site = bool(domain)
                            await asyncio.sleep(settings.rate_limit_default)

                        results.append({
                            "name": proj_data.get("creator_name") or proj_data.get("title", ""),
                            "name_cn": "",
                            "domain": domain,
                            "industry": cat_slug,
                            "source_url": proj_url,
                            "marketplace_url": proj_url,
                            "eu_countries_active": "",
                            "has_standalone_site": has_site,
                            "company_size": "micro",
                            "notes": (
                                f"Kickstarter | Title: {proj_data.get('title', '')[:80]}"
                                f" | Location: {proj_data.get('location', '')}"
                                f" | Backers: {proj_data.get('backers', '?')}"
                            ),
                        })
                        progress.update(task, advance=1)
                        console.print(f"  [green]+[/green] {proj_data.get('creator_name')} — {proj_data.get('title', '')[:50]}")

                        await asyncio.sleep(random.uniform(2, 4))

                    await asyncio.sleep(random.uniform(3, 5))

                except Exception as e:
                    console.print(f"[red]Error: {e}[/red]")
                    break

    return results


def _kickstarter_cat_id(slug: str) -> int:
    """Kickstarter category IDs."""
    ids = {
        "technology": 16, "design": 7, "games": 12,
        "fashion": 9, "food": 10, "art": 1,
    }
    return ids.get(slug, 16)


async def _check_chinese_creator(client, project_url: str) -> dict | None:
    """Check if a Kickstarter project is from a Chinese creator."""
    try:
        resp = await client.get(project_url)
        if resp.status_code != 200:
            return None

        html = resp.text
        text_lower = html.lower()

        # Location is in the project page — often "Creator in Shenzhen, China"
        location = ""
        loc_match = re.search(
            r'"location"\s*:\s*{\s*"displayable_name"\s*:\s*"([^"]+)"',
            html,
        )
        if loc_match:
            location = loc_match.group(1)
        else:
            loc_match = re.search(r'"state"\s*:\s*"([^"]+)",\s*"country"\s*:\s*"([^"]+)"', html)
            if loc_match:
                location = f"{loc_match.group(1)}, {loc_match.group(2)}"

        is_chinese = False
        if location:
            loc_lower = location.lower()
            if any(m in loc_lower for m in CHINESE_MARKERS):
                is_chinese = True

        if not is_chinese:
            return None

        # Extract title
        title_match = re.search(r'<meta property="og:title" content="([^"]+)"', html)
        title = title_match.group(1) if title_match else ""

        # Extract creator name
        creator_match = re.search(r'"creator"\s*:\s*{[^}]*"name"\s*:\s*"([^"]+)"', html)
        creator_name = creator_match.group(1) if creator_match else ""

        # Extract external website (creator's main site, not social)
        website = ""
        for m in re.finditer(r'href="(https?://[^"]+)"', html):
            url = m.group(1)
            if any(s in url for s in ["kickstarter", "facebook", "twitter", "instagram",
                                       "youtube", "google", "cdn.", "ytimg"]):
                continue
            website = url
            break

        # Backer count
        backers_match = re.search(r'"backers_count"\s*:\s*(\d+)', html)
        backers = backers_match.group(1) if backers_match else ""

        return {
            "is_chinese": True,
            "location": location,
            "title": title,
            "creator_name": creator_name,
            "website": website,
            "backers": backers,
        }

    except Exception:
        return None


@click.command()
@click.option("--category", type=click.Choice(list(KICKSTARTER_CATEGORIES.keys())), default="technology")
@click.option("--limit", default=30, help="Max Chinese creators to find")
def main(category: str, limit: int):
    """Find Chinese creators on Kickstarter."""
    console.print(f"[bold]Scraping Kickstarter ({category}, limit={limit})...[/bold]")
    results = asyncio.run(scrape_kickstarter(category=category, limit=limit))
    console.print(f"Found {len(results)} Chinese creators")
    save_companies(results, source="kickstarter")


if __name__ == "__main__":
    main()
