import os


class Settings:
    database_url: str = os.environ.get(
        "DATABASE_URL",
        "postgresql://challenge:challenge@postgres:5432/capacity_db",
    )


settings = Settings()
