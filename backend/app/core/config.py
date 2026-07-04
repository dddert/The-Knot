from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Scientific Knot"
    app_env: str = "local"
    use_mock_ml: bool = True
    mock_extracted_document_path: str = "/app/mock/mock_extracted_document.json"
    storage_dir: str = "/app/storage"

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "password"

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "scientific_knot"
    postgres_user: str = "sk_user"
    postgres_password: str = "sk_password"

    ml_service_url: str = "http://localhost:9000"
    expose_debug: bool = False

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
