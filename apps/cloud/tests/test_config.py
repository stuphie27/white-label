from app.core.config import Settings


def test_postgres_url_is_normalised():
    settings = Settings(database_url="postgresql://user:pass@example.com/db")
    assert settings.database_url.startswith("postgresql+psycopg://")


def test_development_defaults_to_local_sqlite():
    settings = Settings(environment="development")
    assert settings.database_url.startswith("sqlite")
