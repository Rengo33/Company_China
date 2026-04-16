"""Initialize the SQLite database with all tables."""

import sys
from pathlib import Path

from sqlmodel import SQLModel, create_engine

from scraping.config.settings import settings
from scraping.db.models import Company, Contact, Outreach, Website  # noqa: F401


def init_db():
    db_path = Path(settings.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    SQLModel.metadata.create_all(engine)
    print(f"Database ready at {db_path}")
    return engine


def get_engine():
    return create_engine(f"sqlite:///{settings.db_path}", echo=False)


if __name__ == "__main__":
    init_db()
