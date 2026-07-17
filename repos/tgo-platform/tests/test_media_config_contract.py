from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def build_settings(**overrides: object) -> Settings:
    return Settings(
        api_base_url="http://tgo-api:8000",
        database_url="postgresql+asyncpg://user:pass@postgres/tgo",
        _env_file=None,
        **overrides,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("media_retention_days", 0),
        ("media_download_timeout_seconds", 0),
        ("media_image_max_bytes", 0),
        ("media_voice_max_bytes", 0),
        ("media_image_max_pixels", 0),
        ("media_image_max_frames", 0),
        ("media_voice_max_duration_seconds", 0),
        ("media_job_batch_size", 0),
        ("media_job_max_concurrency", 0),
        ("media_job_max_attempts", 0),
        ("media_job_lease_seconds", 0),
        ("media_job_total_timeout_seconds", 0),
        ("media_job_poll_interval_seconds", 0),
        ("media_cleanup_interval_seconds", 0),
        ("media_cleanup_batch_size", 0),
    ],
)
def test_invalid_media_numeric_configuration_fails_fast(
    field: str,
    value: int,
) -> None:
    with pytest.raises(ValidationError):
        build_settings(**{field: value})


def test_encryption_key_id_is_bounded_to_database_column() -> None:
    with pytest.raises(ValidationError):
        build_settings(media_encryption_key_id="x" * 101)
