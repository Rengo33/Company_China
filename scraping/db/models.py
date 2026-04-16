"""SQLModel ORM models for the leads database."""

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


class Company(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    name_cn: str = ""
    domain: str = Field(default="", index=True)
    domain_cn: str = ""
    country_hq: str = "CN"
    eu_countries_active: str = ""  # comma-separated: "DE,GB,FR"
    industry: str = ""
    source: str = ""              # e.g. "canton_fair", "companies_house", "alibaba"
    source_url: str = ""
    employee_count: Optional[int] = None
    company_size: str = ""            # "micro", "small", "medium", "large", "unknown"
    marketplace_url: str = ""         # Amazon/AliExpress store URL
    has_standalone_site: Optional[bool] = None  # True/False/None(unknown)
    notes: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Website(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    url: str
    language: str = ""            # detected primary language
    is_primary_eu_site: bool = True

    # Scores (0–100, null = not yet assessed)
    overall_score: Optional[int] = None
    translation_score: Optional[int] = None
    performance_score: Optional[int] = None
    mobile_score: Optional[int] = None
    design_score: Optional[int] = None
    security_score: Optional[int] = None
    seo_score: Optional[int] = None

    screenshot_path: str = ""
    assessed_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Contact(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    name: str = ""
    title: str = ""
    email: str = Field(default="", index=True)
    email_verified: bool = False
    linkedin_url: str = ""
    wechat_id: str = ""
    phone: str = ""
    source: str = ""              # e.g. "website_scrape", "whois", "pattern_guess"
    opted_out: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Outreach(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    contact_id: int = Field(foreign_key="contact.id", index=True)
    channel: str = ""             # "email", "wechat", "linkedin"
    status: str = "queued"        # queued / sent / opened / replied / bounced
    message_template: str = ""
    sent_at: Optional[datetime] = None
    opened_at: Optional[datetime] = None
    replied_at: Optional[datetime] = None
