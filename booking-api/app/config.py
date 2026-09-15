import os


class Settings:
    database_url: str = os.environ.get(
        "DATABASE_URL",
        "postgresql://challenge:challenge@postgres:5432/booking_db",
    )
    capacity_api_url: str = os.environ.get(
        "CAPACITY_API_URL",
        "http://capacity-api:8000",
    )
    challenge_mode: bool = os.environ.get("CHALLENGE_MODE", "").lower() in (
        "true",
        "1",
        "yes",
    )


settings = Settings()
