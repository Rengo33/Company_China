"""
Scrape contact details from Made-in-China.com using authenticated session.

Extracts phone, mobile, fax, email, contact person name and title
for all companies in the database.

Usage:
    python -m scraping.contacts.mic_contacts --limit 50
"""

import asyncio

import click
from rich.console import Console
from rich.table import Table
from sqlmodel import Session, select

from scraping.db.init_db import get_engine, init_db
from scraping.db.models import Company, Contact
from scraping.utils.mic_session import scrape_contact_pages_batch

console = Console()


async def scrape_all_contacts(limit: int = 50):
    """Scrape MIC contact pages for companies that don't have contacts yet."""
    init_db()
    engine = get_engine()

    with Session(engine) as session:
        # Get companies that have a MIC source URL but no contact yet
        companies = session.exec(select(Company).where(
            Company.source_url.contains("made-in-china.com")
        )).all()

        # Filter to those without contacts
        to_scrape = []
        for c in companies:
            existing = session.exec(
                select(Contact).where(
                    Contact.company_id == c.id,
                    Contact.source == "mic_contact_page",
                )
            ).first()
            if not existing:
                to_scrape.append(c)

        to_scrape = to_scrape[:limit]

        if not to_scrape:
            console.print("[yellow]All companies already have contacts scraped.[/yellow]")
            return

        console.print(f"[bold]Scraping contacts for {len(to_scrape)} companies...[/bold]")
        console.print("[dim]Browser will open — don't close it[/dim]\n")

        urls = [c.source_url for c in to_scrape]
        contacts = await scrape_contact_pages_batch(urls, delay=2.0)

        # Save to database
        saved = 0
        results = []

        for company, mic_contact in zip(to_scrape, contacts):
            has_data = mic_contact.telephone or mic_contact.mobile or mic_contact.email or mic_contact.contact_name

            if has_data:
                contact = Contact(
                    company_id=company.id,
                    name=mic_contact.contact_name,
                    title=mic_contact.contact_title,
                    email=mic_contact.email,
                    phone=mic_contact.telephone or mic_contact.mobile,
                    source="mic_contact_page",
                    email_verified=bool(mic_contact.email),
                )
                session.add(contact)
                saved += 1

                results.append((
                    company.name,
                    mic_contact.telephone or mic_contact.mobile,
                    mic_contact.email,
                    mic_contact.contact_name,
                    mic_contact.contact_title,
                ))

        session.commit()

    # Print results
    if results:
        table = Table(title=f"Scraped {saved} contacts")
        table.add_column("Company", style="bold", max_width=30)
        table.add_column("Phone")
        table.add_column("Email")
        table.add_column("Contact")
        table.add_column("Title")

        for name, phone, email, contact_name, title in results:
            table.add_row(name, phone or "—", email or "—", contact_name or "—", title or "—")

        console.print(table)
    else:
        console.print("[yellow]No contact data found.[/yellow]")


@click.command()
@click.option("--limit", default=50, help="Max companies to scrape contacts for")
def main(limit: int):
    """Scrape MIC contact pages for phone/email/contact person."""
    asyncio.run(scrape_all_contacts(limit=limit))


if __name__ == "__main__":
    main()
