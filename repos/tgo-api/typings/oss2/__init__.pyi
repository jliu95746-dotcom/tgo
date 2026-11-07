# Project-used surface, verified against oss2 2.19.1 api.py/models.py.
from collections.abc import Mapping
from typing import BinaryIO
from . import models

class Auth:
    def __init__(
        self,
        access_key_id: str,
        access_key_secret: str,
    ) -> None: ...

class Bucket:
    def __init__(
        self,
        auth: Auth,
        endpoint: str,
        bucket_name: str,
    ) -> None: ...
    def get_object(self, key: str) -> models.GetObjectResult: ...
    def put_object(
        self,
        key: str,
        data: BinaryIO,
        headers: Mapping[str, str] | None = ...,
    ) -> object: ...
    def delete_object(self, key: str) -> object: ...
    def object_exists(self, key: str) -> bool: ...
