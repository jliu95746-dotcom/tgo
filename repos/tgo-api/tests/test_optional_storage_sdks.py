"""An absent optional storage SDK keeps the original constructor failure."""

import builtins
import runpy
from pathlib import Path
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError

from app.services.storage import minio
from app.services import geoip_service


@pytest.mark.parametrize(
    "module,missing,backend,arguments",
    [
        (
            "minio",
            "boto3",
            "MinIOBackend",
            {
                "endpoint_url": "https://storage.invalid",
                "bucket_name": "sample",
                "access_key_id": "synthetic",
                "secret_access_key": "synthetic",
            },
        ),
        (
            "minio",
            "botocore.exceptions",
            "MinIOBackend",
            {
                "endpoint_url": "https://storage.invalid",
                "bucket_name": "sample",
                "access_key_id": "synthetic",
                "secret_access_key": "synthetic",
            },
        ),
        (
            "aliyun_oss",
            "oss2",
            "AliyunOSSBackend",
            {
                "endpoint": "https://storage.invalid",
                "bucket_name": "sample",
                "access_key_id": "synthetic",
                "access_key_secret": "synthetic",
            },
        ),
    ],
)
def test_missing_sdk_retains_actionable_import_error(
    module: str,
    missing: str,
    backend: str,
    arguments: dict[str, str],
) -> None:
    importer = builtins.__import__

    def import_without_sdk(name, *args, **kwargs):
        if name == missing:
            raise ImportError("deliberately absent SDK")
        return importer(name, *args, **kwargs)

    path = Path(minio.__file__).with_name(f"{module}.py")
    with patch("builtins.__import__", side_effect=import_without_sdk):
        namespace = runpy.run_path(str(path))
    with pytest.raises(ImportError, match="package is required"):
        namespace[backend](**arguments)
    if module == "minio":
        assert namespace["ClientError"] is Exception


def test_available_client_error_is_the_sdk_exception() -> None:
    assert minio.ClientError is ClientError


def test_missing_ip2region_sdk_keeps_geolocation_optional() -> None:
    importer = builtins.__import__

    def import_without_sdk(name, *args, **kwargs):
        if name.startswith("ip2region"):
            raise ImportError("deliberately absent SDK")
        return importer(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=import_without_sdk):
        namespace = runpy.run_path(geoip_service.__file__)
    assert not namespace["IP2REGION_AVAILABLE"]
    service = namespace["GeoIPService"]()
    assert not service._init_ip2region("not-opened.xdb")
    assert service._lookup_ip2region("8.8.8.8").is_empty()
