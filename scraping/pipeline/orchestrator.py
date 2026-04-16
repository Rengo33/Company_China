"""
Main pipeline orchestrator with mode system.

Modes:
    --mode dtc       → Amazon EU + AliExpress (DTC brands selling in Europe)
    --mode ads       → Facebook Ad Library (brands spending ad money in EU)
    --mode registry  → Companies House UK (small Chinese-owned companies)
    --mode b2b       → Made-in-China + Canton Fair (B2B manufacturers)
    --mode all       → everything

Single source:
    --source amazon-de         → just Amazon Germany
    --source facebook-ads      → just Facebook Ad Library
    --source made-in-china     → just Made-in-China
    --source companies-house   → just Companies House

Usage:
    python -m scraping.pipeline.orchestrator --mode dtc --limit 20
    python -m scraping.pipeline.orchestrator --mode ads --country DE --limit 30
    python -m scraping.pipeline.orchestrator --source amazon-de --category electronics --limit 10
    python -m scraping.pipeline.orchestrator --assess-only --limit 50
"""

import asyncio
import importlib

import click
from rich.console import Console
from rich.panel import Panel

from scraping.db.init_db import init_db
from scraping.sources.trade_shows import save_companies

console = Console()

# ── Source Registry ──
# Each entry: (display_name, module_path, async_function_name, default_kwargs)
SOURCE_REGISTRY = {
    "amazon-de": ("Amazon Germany", "scraping.sources.amazon_eu", "scrape_amazon_sellers", {"marketplace": "de"}),
    "amazon-uk": ("Amazon UK", "scraping.sources.amazon_eu", "scrape_amazon_sellers", {"marketplace": "uk"}),
    "amazon-fr": ("Amazon France", "scraping.sources.amazon_eu", "scrape_amazon_sellers", {"marketplace": "fr"}),
    "facebook-ads": ("Facebook Ad Library", "scraping.sources.facebook_ads", "search_fb_ad_library", {}),
    "made-in-china": ("Made-in-China.com", "scraping.sources.alibaba_sellers", "scrape_all_categories", {}),
    "canton-fair": ("Canton Fair", "scraping.sources.trade_shows", "scrape_canton_fair", {}),
    "companies-house": ("Companies House UK", "scraping.sources.company_registries", "search_chinese_companies", {}),
}

# ── Modes ──
MODES = {
    "dtc": ["amazon-de", "amazon-uk", "amazon-fr"],
    "ads": ["facebook-ads"],
    "registry": ["companies-house"],
    "b2b": ["made-in-china", "canton-fair"],
    "all": list(SOURCE_REGISTRY.keys()),
}

ALL_SOURCES = list(SOURCE_REGISTRY.keys())


def _run_source(source_key: str, limit: int, **extra_kwargs):
    """Run a single source scraper and save results."""
    display_name, module_path, func_name, default_kwargs = SOURCE_REGISTRY[source_key]
    console.print(f"  [cyan]{display_name}[/cyan]...")

    module = importlib.import_module(module_path)
    scraper = getattr(module, func_name)

    kwargs = {**default_kwargs, **extra_kwargs}

    # Adjust limit kwarg name based on source
    if source_key == "made-in-china":
        kwargs["limit_per_category"] = limit
    else:
        kwargs["limit"] = limit

    companies = asyncio.run(scraper(**kwargs))

    # Determine source slug for DB
    slug = source_key.replace("-", "_")
    save_companies(companies, source=slug)
    console.print(f"    → {len(companies)} companies found")
    return companies


@click.command()
@click.option("--mode", type=click.Choice(list(MODES.keys())), default=None,
              help="Scraping mode: dtc, ads, registry, b2b, all")
@click.option("--source", type=click.Choice(ALL_SOURCES), default=None,
              help="Run a single source")
@click.option("--category", default="all", help="Product category (for Amazon/MIC)")
@click.option("--country", default="DE", help="Country code (for Facebook Ads)")
@click.option("--marketplace", default=None, help="Amazon marketplace override (de/uk/fr)")
@click.option("--limit", default=20, help="Max items per source/category")
@click.option("--assess/--no-assess", default=True, help="Run website assessment")
@click.option("--contacts/--no-contacts", "do_contacts", default=True, help="Find email contacts")
@click.option("--export/--no-export", "do_export", default=True, help="Export to CSV")
@click.option("--assess-only", is_flag=True, help="Only run assessment on existing data")
@click.option("--contacts-only", is_flag=True, help="Only find contacts for existing data")
def main(mode, source, category, country, marketplace, limit, assess,
         do_contacts, do_export, assess_only, contacts_only):
    """EightFold lead generation pipeline."""
    console.print(Panel.fit(
        "[bold green]EightFold Lead Generation Pipeline[/bold green]\n"
        "Discover → Assess → Contact → Export",
        border_style="green",
    ))

    init_db()

    # ── Shortcut modes ──
    if assess_only:
        console.print("\n[bold]Running Website Assessment only[/bold]")
        from scraping.assessment.runner import assess_unassessed
        asyncio.run(assess_unassessed(limit=limit))
        return

    if contacts_only:
        console.print("\n[bold]Running Contact Finding only[/bold]")
        from scraping.contacts.email_finder import find_contacts_for_companies
        asyncio.run(find_contacts_for_companies(limit=limit))
        return

    # ── Determine which sources to run ──
    if source:
        sources_to_run = [source]
    elif mode:
        sources_to_run = MODES[mode]
    else:
        console.print("[yellow]No --mode or --source specified. Use --mode dtc/ads/registry/b2b/all[/yellow]")
        console.print("\nAvailable modes:")
        for m, srcs in MODES.items():
            names = [SOURCE_REGISTRY[s][0] for s in srcs]
            console.print(f"  --mode {m:10s} → {', '.join(names)}")
        return

    # ── Step 1: Source scraping ──
    mode_label = f"mode={mode}" if mode else f"source={source}"
    console.print(f"\n[bold cyan]Step 1/4: Source Scraping ({mode_label})[/bold cyan]")

    for src_key in sources_to_run:
        extra = {}
        if "amazon" in src_key:
            if category != "all":
                extra["category"] = category
            if marketplace:
                extra["marketplace"] = marketplace
        elif src_key == "facebook-ads":
            extra["country"] = country
        elif src_key == "made-in-china":
            pass  # uses limit_per_category

        _run_source(src_key, limit=limit, **extra)

    # ── Step 2: Website assessment ──
    if assess:
        console.print("\n[bold cyan]Step 2/4: Website Assessment[/bold cyan]")
        from scraping.assessment.runner import assess_unassessed
        asyncio.run(assess_unassessed(limit=limit))

    # ── Step 3: Contact finding ──
    if do_contacts:
        console.print("\n[bold cyan]Step 3/4: Contact Finding[/bold cyan]")
        from scraping.contacts.email_finder import find_contacts_for_companies
        asyncio.run(find_contacts_for_companies(limit=limit))

    # ── Step 4: Export ──
    if do_export:
        console.print("\n[bold cyan]Step 4/4: Export[/bold cyan]")
        from scraping.pipeline.exporter import export_csv
        export_csv()

    console.print("\n[bold green]Pipeline complete![/bold green]")


if __name__ == "__main__":
    main()
