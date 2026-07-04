from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

    app_name: str = 'Scientific Knot ML Service'
    llm_provider: str = 'yandex'

    yandex_api_key: str | None = None
    yandex_folder_id: str | None = None
    yandex_model: str = "yandexgpt-5-lite"

    openai_compatible_base_url: str = 'http://localhost:11434/v1'
    openai_compatible_api_key: str = 'local'
    openai_compatible_model: str = 'gpt-oss:20b'

    retrieval_index_dir: str = '/data/retrieval-index'
    embedding_model: str = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
    embedding_query_prefix: str = ''
    embedding_passage_prefix: str = ''
    dense_enabled: bool = True
    lexical_enabled: bool = True
    reranker_model: str | None = None
    retrieval_dense_k: int = 100
    retrieval_lexical_k: int = 100
    retrieval_rrf_k: int = 60
    retrieval_rerank_pool: int = 50

    extraction_target_chars: int = 2500
    extraction_overlap_chars: int = 300
    extraction_max_chunks: int = 60
    extraction_max_entities: int = 12
    extraction_max_relations: int = 10
    extraction_max_facts: int = 8
    extraction_max_numeric_values: int = 12
    extraction_temperature: float = 0.1
    extraction_max_tokens: int = 2200
    llm_timeout_seconds: int = 120


settings = Settings()
