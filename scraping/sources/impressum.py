"""
Find Chinese-owned German e-commerce sites via their Impressum pages.

German law requires every commercial website to have an "Impressum" with
full business details (name, address, registration number, VAT ID).
Many small German e-commerce sites are run by Chinese owners — the
Impressum reveals it (foreign address, Chinese name, etc.).

Strategy:
1. Get a seed list of German e-commerce sites (from various sources)
2. Fetch their /impressum page
3. Parse for Chinese ownership indicators

Usage:
    python -m scraping.sources.impressum --seed-file seeds.txt --limit 50
    python -m scraping.sources.impressum --discover --limit 30
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
from scraping.utils.http import StealthClient

console = Console()

CHINESE_MARKERS = {
    "china", "shenzhen", "guangzhou", "shanghai", "beijing", "yiwu",
    "hangzhou", "dongguan", "xiamen", "ningbo", "qingdao", "chengdu",
    "guangdong", "zhejiang", "fujian", "jiangsu", "shandong",
    "p.r. china", "pr china", "people's republic", "volksrepublik china",
    "中国",
}

# Common Chinese surnames that appear in Impressum owner names
CHINESE_SURNAMES = {
    "wang", "li", "zhang", "liu", "chen", "yang", "huang", "wu",
    "zhou", "xu", "sun", "ma", "zhu", "guo", "lin", "he", "gao",
    "luo", "song", "zheng", "han", "feng", "deng", "tang", "xiao",
}

IMPRESSUM_PATHS = [
    "/impressum", "/impressum.html", "/imprint", "/legal/imprint",
    "/about/impressum", "/ueber-uns/impressum", "/impressum/",
]


async def check_impressum(client, domain: str) -> dict | None:
    """Fetch a site's Impressum page and check for Chinese ownership."""
    for path in IMPRESSUM_PATHS:
        for scheme in ("https", "http"):
            url = f"{scheme}://{domain}{path}"
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    continue

                text = resp.text

                # Strip HTML
                clean = re.sub(r"<[^>]+>", " ", text)
                clean = re.sub(r"\s+", " ", clean).lower()

                # Check for Chinese location markers
                chinese_markers = [m for m in CHINESE_MARKERS if m in clean]
                if not chinese_markers:
                    # Also check for Chinese surname patterns
                    has_chinese_name = any(
                        re.search(rf"\b{sur}\b", clean) for sur in CHINESE_SURNAMES
                    )
                    if not has_chinese_name:
                        continue

                # Extract key data
                address_match = re.search(
                    r"(?:anschrift|adresse|address)[:\s]+([^\n]{20,200})",
                    clean, re.IGNORECASE,
                )
                email_match = re.search(
                    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text
                )
                phone_match = re.search(
                    r"(?:tel|telefon|phone)[:\s]*(\+?\d[\d\s\-/]{7,20})",
                    clean, re.IGNORECASE,
                )
                vat_match = re.search(r"DE\d{9}", text)
                hre_match = re.search(r"HRB\s*\d+", text)

                # Extract owner name — often after "Inhaber:" or "Geschäftsführer:"
                owner_match = re.search(
                    r"(?:inhaber|geschäftsführer|owner)[:\s]+([A-Z][\w\s\-]{3,50})",
                    text,
                )

                return {
                    "impressum_url": url,
                    "markers_found": chinese_markers,
                    "address": address_match.group(1).strip()[:200] if address_match else "",
                    "email": email_match.group(0) if email_match else "",
                    "phone": phone_match.group(1).strip() if phone_match else "",
                    "vat_id": vat_match.group(0) if vat_match else "",
                    "hrb": hre_match.group(0) if hre_match else "",
                    "owner": owner_match.group(1).strip() if owner_match else "",
                }

            except Exception:
                continue

    return None


async def scrape_impressum_domains(domains: list[str], limit: int = 50) -> list[dict]:
    """Check a list of domains for Chinese ownership via Impressum."""
    results = []

    async with StealthClient() as client:
        with Progress() as progress:
            task = progress.add_task("Checking Impressums...", total=len(domains))

            for domain in domains:
                if len(results) >= limit:
                    break

                progress.update(task, advance=1)
                data = await check_impressum(client, domain)
                if not data:
                    continue

                # Extract company name from Impressum HTML if possible
                name = data.get("owner") or domain

                results.append({
                    "name": name,
                    "name_cn": "",
                    "domain": domain,
                    "industry": "",
                    "source_url": data["impressum_url"],
                    "eu_countries_active": "DE",
                    "has_standalone_site": True,
                    "company_size": "small",
                    "notes": (
                        f"Chinese ownership signals: {', '.join(data['markers_found'])[:100]}"
                        f" | Address: {data.get('address', '')[:100]}"
                        f" | VAT: {data.get('vat_id', '')} | HRB: {data.get('hrb', '')}"
                    ),
                    "_impressum_email": data.get("email", ""),
                    "_impressum_phone": data.get("phone", ""),
                })
                console.print(f"  [green]+[/green] {name} ({domain})")

                await asyncio.sleep(random.uniform(1, 3))

    return results


@click.command()
@click.option("--seed-file", type=click.Path(exists=True), help="File with one domain per line")
@click.option("--domain", multiple=True, help="Specific domain(s) to check")
@click.option("--limit", default=50, help="Max Chinese-owned sites to find")
def main(seed_file: str, domain: tuple, limit: int):
    """Check German e-commerce sites' Impressums for Chinese ownership."""
    domains = list(domain)

    if seed_file:
        domains.extend(
            line.strip() for line in Path(seed_file).read_text().splitlines()
            if line.strip() and not line.startswith("#")
        )

    if not domains:
        console.print("[yellow]No domains provided. Use --domain or --seed-file.[/yellow]")
        console.print("Example seed file (one domain per line):")
        console.print("  ikarus-autoteile.de")
        console.print("  smartphone-shop.de")
        return

    console.print(f"[bold]Checking Impressum on {len(domains)} domains...[/bold]")
    results = asyncio.run(scrape_impressum_domains(domains, limit=limit))
    console.print(f"\nFound {len(results)} Chinese-owned sites")
    save_companies(results, source="impressum")


if __name__ == "__main__":
    main()
