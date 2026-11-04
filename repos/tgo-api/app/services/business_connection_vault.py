"""Authenticated credential encryption binds every payload to its owner."""

import base64
import binascii
import json
import os
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag
from pydantic import ValidationError

from app.core.config import settings
from app.core.exceptions import TGOAPIException
from app.schemas.business_connections import BusinessConnectionCredentials


def credential_values(credentials: BusinessConnectionCredentials) -> dict[str, str]:
    return {
        name: value.get_secret_value()
        for name, value in (
            ("token", credentials.token),
            ("username", credentials.username),
            ("password", credentials.password),
        )
        if value is not None
    }


class CredentialVault:
    def _key(self) -> bytes:
        try:
            configured = settings.BUSINESS_CONNECTION_CREDENTIAL_KEY
            if configured is None:
                raise ValueError
            key = base64.b64decode(
                configured.get_secret_value(), altchars=b"-_", validate=True
            )
            if len(key) != 32:
                raise ValueError
            return key
        except (ValueError, binascii.Error):
            raise TGOAPIException(
                "业务连接凭证保管尚未配置，请联系平台管理员",
                code="CREDENTIAL_VAULT_UNAVAILABLE",
                status_code=503,
            ) from None

    def _cipher(self) -> AESGCM:
        return AESGCM(self._key())

    @staticmethod
    def _scope(project: UUID, connection: UUID) -> bytes:
        return f"tgo-business-connection-v1:{project}:{connection}".encode()

    def encrypt(
        self,
        credentials: BusinessConnectionCredentials,
        project: UUID,
        connection: UUID,
    ) -> str:
        nonce = os.urandom(12)
        raw = json.dumps(
            credential_values(credentials), ensure_ascii=False, sort_keys=True
        ).encode()
        ciphertext = self._cipher().encrypt(
            nonce, raw, self._scope(project, connection)
        )
        return "v1." + base64.urlsafe_b64encode(nonce + ciphertext).decode()

    def decrypt(
        self, ciphertext: str, project: UUID, connection: UUID
    ) -> BusinessConnectionCredentials:
        try:
            if not ciphertext.startswith("v1."):
                raise ValueError
            payload = base64.b64decode(ciphertext[3:], altchars=b"-_", validate=True)
            raw = self._cipher().decrypt(
                payload[:12], payload[12:], self._scope(project, connection)
            )
            return BusinessConnectionCredentials.model_validate_json(raw)
        except TGOAPIException:
            raise
        except (ValueError, binascii.Error, ValidationError, InvalidTag):
            # Neither the upstream error nor decrypted bytes enter errors or logs.
            raise TGOAPIException(
                "连接凭证无法读取，请重新核对保管配置或更新凭证",
                code="CREDENTIAL_UNREADABLE",
                status_code=503,
            ) from None
