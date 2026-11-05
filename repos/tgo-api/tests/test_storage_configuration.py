"""Reject incomplete storage configuration before creating clients."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services import storage
from app.services.storage import aliyun_oss, minio


CONFIGURATION = {
    "STORAGE_TYPE": "local",
    "UPLOAD_DIR": "unused-synthetic-directory",
    "API_BASE_URL": "https://api.example.invalid",
    "OSS_ENDPOINT": "oss.example.invalid",
    "OSS_BUCKET_NAME": "synthetic-bucket",
    "OSS_ACCESS_KEY_ID": "synthetic-access-id",
    "OSS_ACCESS_KEY_SECRET": "synthetic-access-secret",
    "OSS_BUCKET_URL": None,
    "MINIO_URL": "https://s3.example.invalid",
    "MINIO_ACCESS_KEY_ID": "synthetic-access-id",
    "MINIO_SECRET_ACCESS_KEY": "synthetic-access-secret",
    "MINIO_BUCKET_NAME": "synthetic-bucket",
    "MINIO_UPLOAD_URL": None,
    "MINIO_DOWNLOAD_URL": None,
}
REQUIRED = {
    "oss": (
        "OSS_ENDPOINT",
        "OSS_BUCKET_NAME",
        "OSS_ACCESS_KEY_ID",
        "OSS_ACCESS_KEY_SECRET",
    ),
    "minio": (
        "MINIO_URL",
        "MINIO_ACCESS_KEY_ID",
        "MINIO_SECRET_ACCESS_KEY",
        "MINIO_BUCKET_NAME",
    ),
}


@pytest.fixture
def factories(monkeypatch):
    local, oss, s3 = Mock(), Mock(), Mock()
    monkeypatch.setattr(storage, "LocalStorageBackend", local)
    monkeypatch.setattr(aliyun_oss, "AliyunOSSBackend", oss)
    monkeypatch.setattr(minio, "MinIOBackend", s3)
    return local, oss, s3


@pytest.mark.parametrize("kind", ["local", "oss", "minio", " MINIO "])
def test_valid_backend_selection_keeps_the_configured_values(
    monkeypatch, factories, kind
):
    values = {**CONFIGURATION, "STORAGE_TYPE": kind}
    monkeypatch.setattr(storage, "settings", SimpleNamespace(**values))
    result = storage.get_storage_backend()
    index = {"local": 0, "oss": 1, "minio": 2}[kind.strip().lower()]
    selected = factories[index]
    assert result is selected.return_value
    assert sum(factory.call_count for factory in factories) == 1
    if index == 0:
        selected.assert_called_once_with(
            base_path=values["UPLOAD_DIR"], api_base_url=values["API_BASE_URL"]
        )
    elif index == 1:
        selected.assert_called_once_with(
            endpoint=values["OSS_ENDPOINT"],
            bucket_name=values["OSS_BUCKET_NAME"],
            access_key_id=values["OSS_ACCESS_KEY_ID"],
            access_key_secret=values["OSS_ACCESS_KEY_SECRET"],
            bucket_url=None,
        )
    else:
        selected.assert_called_once_with(
            endpoint_url=values["MINIO_URL"],
            access_key_id=values["MINIO_ACCESS_KEY_ID"],
            secret_access_key=values["MINIO_SECRET_ACCESS_KEY"],
            bucket_name=values["MINIO_BUCKET_NAME"],
            upload_url=None,
            download_url=None,
        )


@pytest.mark.parametrize("kind", ["s33", "", " ", "synthetic-secret"])
def test_unknown_backend_never_falls_back_to_local(
    monkeypatch, factories, kind
):
    values = {**CONFIGURATION, "STORAGE_TYPE": kind}
    monkeypatch.setattr(storage, "settings", SimpleNamespace(**values))
    with pytest.raises(ValueError) as error:
        storage.get_storage_backend()
    assert "STORAGE_TYPE" in str(error.value)
    assert "synthetic-secret" not in str(error.value)
    assert not any(factory.called for factory in factories)


@pytest.mark.parametrize(
    "kind,field",
    [(kind, field) for kind, fields in REQUIRED.items() for field in fields],
)
@pytest.mark.parametrize("missing", [None, "", " \t"])
def test_cloud_configuration_is_required_before_client_creation(
    monkeypatch, factories, kind, field, missing
):
    values = {**CONFIGURATION, "STORAGE_TYPE": kind, field: missing}
    monkeypatch.setattr(storage, "settings", SimpleNamespace(**values))
    with pytest.raises(ValueError) as error:
        storage.get_storage_backend()
    assert field in str(error.value)
    assert "synthetic-access-secret" not in str(error.value)
    assert not any(factory.called for factory in factories)


def test_secret_values_are_validated_without_rewriting(monkeypatch, factories):
    values = {
        **CONFIGURATION,
        "STORAGE_TYPE": "minio",
        "MINIO_SECRET_ACCESS_KEY": "  synthetic-secret-with-spaces  ",
    }
    monkeypatch.setattr(storage, "settings", SimpleNamespace(**values))
    storage.get_storage_backend()
    assert factories[2].call_args.kwargs["secret_access_key"] == (
        values["MINIO_SECRET_ACCESS_KEY"]
    )


def test_lazy_backend_does_not_cache_failed_configuration(monkeypatch):
    backend = Mock()
    factory = Mock(side_effect=[ValueError("Invalid STORAGE_TYPE"), backend])
    monkeypatch.setattr(storage, "_storage", None)
    monkeypatch.setattr(storage, "get_storage_backend", factory)
    with pytest.raises(ValueError):
        storage.get_storage()
    assert storage.get_storage() is backend
    assert storage.get_storage() is backend
    assert factory.call_count == 2
