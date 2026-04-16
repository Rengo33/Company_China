"""
Email contact finder — free approach (no paid APIs).

Strategy:
1. Scrape company website for email addresses (/contact, /about, /impressum)
2. Check WHOIS records for registrant email
3. Pattern guessing + DNS MX verification

Usage:
    python -m scraping.contacts.email_finder --limit 10
"""

import asyncio
import re
from typing import Optional

import click
from rich.console import Console
from rich.table import Table
from sqlmodel import Session, select

from scraping.config.settings import settings
from scraping.db.init_db import get_engine
from scraping.db.models import Company, Contact
from scraping.utils.http import StealthClient
from scraping.utils.skip_domains import is_skip_email, is_skip_domain

console = Console()

# Regex for email addresses
EMAIL_RE = re.compile(
    r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b"
)

# Pages most likely to contain contact info
CONTACT_PATHS = [
    "/contact", "/contact-us", "/kontakt", "/contacto",
    "/about", "/about-us",
    "/impressum", "/imprint", "/legal-notice",
    "/",  # homepage as fallback
]

# (Skip logic moved to scraping/utils/skip_domains.py — is_skip_email())


async def find_emails_on_website(domain: str) -> list[str]:
    """Scrape a company's website for email addresses."""
    found_emails = set()

    async with StealthClient() as client:
        for path in CONTACT_PATHS:
            for scheme in ["https", "http"]:
                url = f"{scheme}://{domain}{path}"
                try:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        continue

                    emails = EMAIL_RE.findall(resp.text)
                    for email in emails:
                        email = email.lower().strip()
                        if _is_valid_email(email):
                            found_emails.add(email)

                    # Also check for mailto: links
                    mailto = re.findall(r'mailto:([^"\'?\s]+)', resp.text)
                    for m in mailto:
                        email = m.lower().strip()
                        if _is_valid_email(email):
                            found_emails.add(email)

                    if found_emails:
                        break  # Got emails, no need to try more paths

                    await asyncio.sleep(1)

                except Exception:
                    continue

            if found_emails:
                break  # Got emails with this scheme

    return sorted(found_emails)


async def find_email_whois(domain: str) -> Optional[str]:
    """Try to get registrant email from WHOIS data. Rejects hosting/privacy emails."""
    try:
        import whois
        w = whois.whois(domain)
        emails = w.get("emails")
        if isinstance(emails, str):
            emails = [emails]
        if emails:
            for email in emails:
                email = email.lower().strip()
                if not _is_valid_email(email):
                    continue
                # Prefer emails that match the company's domain
                email_domain = email.rsplit("@", 1)[-1]
                if email_domain == domain or email_domain.endswith("." + domain):
                    return email
                # Only return non-matching WHOIS email as last resort
                # (often these are hosting provider emails, not useful)
        return None
    except Exception:
        pass
    return None


async def verify_mx_exists(domain: str) -> bool:
    """Check if a domain has MX records (can receive email)."""
    try:
        import dns.resolver
        dns.resolver.resolve(domain, "MX")
        return True
    except Exception:
        return False


def guess_email_patterns(domain: str, name: str = "") -> list[str]:
    """Generate likely email patterns for a domain."""
    patterns = [
        f"info@{domain}",
        f"contact@{domain}",
        f"hello@{domain}",
        f"sales@{domain}",
        f"enquiry@{domain}",
        f"service@{domain}",
    ]

    if name:
        parts = name.lower().split()
        if len(parts) >= 2:
            first, last = parts[0], parts[-1]
            patterns.extend([
                f"{first}@{domain}",
                f"{first}.{last}@{domain}",
                f"{first[0]}.{last}@{domain}",
                f"{first}{last}@{domain}",
            ])

    return patterns


def _is_valid_email(email: str, company_domain: str = "") -> bool:
    """Filter out junk emails. If company_domain is given, be stricter."""
    email = email.lower().strip()

    if is_skip_email(email):
        return False

    # Must have valid TLD
    if not re.match(r".+@.+\..{2,}", email):
        return False

    return True


def _prioritize_emails(emails: list[str], company_domain: str) -> list[str]:
    """Sort emails by quality — company-domain emails first, then by type."""
    def sort_key(email: str) -> tuple:
        domain_match = company_domain in email
        is_personal = any(
            email.startswith(p)
            for p in ["info@", "contact@", "hello@", "sales@"]
        )
        return (not domain_match, not is_personal, email)

    return sorted(emails, key=sort_key)


async def find_contacts_for_companies(limit: int = 10):
    """Find email contacts for companies in the database."""
    engine = get_engine()

    with Session(engine) as session:
        companies = session.exec(
            select(Company).where(Company.domain != "").limit(limit * 2)
        ).all()

        found_count = 0
        results = []

        for company in companies:
            if found_count >= limit:
                break

            # Skip if we already have contacts
            existing = session.exec(
                select(Contact).where(Contact.company_id == company.id)
            ).first()
            if existing and existing.email:
                continue

            console.print(f"Finding contacts for [bold]{company.name}[/bold] ({company.domain})...")

            # 1. Scrape website
            emails = await find_emails_on_website(company.domain)
            source = "website_scrape"

            # 2. Try WHOIS if no emails found
            if not emails:
                whois_email = await find_email_whois(company.domain)
                if whois_email:
                    emails = [whois_email]
                    source = "whois"

            # 3. Try guessing common patterns if still nothing
            if not emails:
                has_mx = await verify_mx_exists(company.domain)
                if has_mx:
                    emails = guess_email_patterns(company.domain)[:3]
                    source = "pattern_guess"

            # Prioritize and save
            emails = _prioritize_emails(emails, company.domain)

            if emails:
                primary_email = emails[0]

                if existing:
                    existing.email = primary_email
                    existing.source = source
                else:
                    contact = Contact(
                        company_id=company.id,
                        email=primary_email,
                        source=source,
                        email_verified=(source == "website_scrape"),
                    )
                    session.add(contact)

                results.append((company.name, company.domain, primary_email, source))
                found_count += 1

            await asyncio.sleep(settings.rate_limit_scrape)

        session.commit()

    # Print results
    if results:
        table = Table(title=f"Found {found_count} email contacts")
        table.add_column("Company", style="bold")
        table.add_column("Domain")
        table.add_column("Email")
        table.add_column("Source")

        for name, domain, email, source in results:
            table.add_row(name, domain, email, source)

        console.print(table)
    else:
        console.print("[yellow]No new contacts found.[/yellow]")


@click.command()
@click.option("--limit", default=10, help="Max companies to find contacts for")
def main(limit: int):
    """Find email contacts for discovered companies."""
    asyncio.run(find_contacts_for_companies(limit=limit))


if __name__ == "__main__":
    main()
