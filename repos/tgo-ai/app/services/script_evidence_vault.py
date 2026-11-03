"""Scope-bound encryption for explicitly retained test bodies."""

import base64
import json
import os
from typing import Literal
from uuid import UUID

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import settings
from app.exceptions import ConflictError

EvidencePurpose = Literal["case", "result"]


class ScriptEvidenceVault:
    def __init__(self, secret: str | None = None) -> None:
        configured = settings.script_trial_evidence_key
        self._secret = (
            secret
            if secret is not None
            else (
                configured.get_secret_value()
                if configured
                else settings.secret_key
            )
        )

    def _cipher(self) -> AESGCM:
        if len(self._secret) < 32 or self._secret.startswith("your-"):
            raise ConflictError("请先配置服务端证据加密密钥")
        key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=b"tgo:script-trial-evidence:aes-gcm:v1",
        ).derive(self._secret.encode("utf-8"))
        return AESGCM(key)

    @staticmethod
    def _scope(
        project: UUID,
        actor: UUID,
        agent: UUID,
        record: UUID,
        purpose: EvidencePurpose,
    ) -> bytes:
        return json.dumps(
            [
                "script-evidence-v1",
                str(project),
                str(actor),
                str(agent),
                str(record),
                purpose,
            ],
            separators=(",", ":"),
        ).encode()

    def encrypt(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        record: UUID,
        purpose: EvidencePurpose,
        plaintext: str,
    ) -> str:
        raw = plaintext.encode("utf-8")
        if len(raw) > 131072:
            raise ConflictError("证据正文超过保存范围")
        nonce = os.urandom(12)
        encrypted = self._cipher().encrypt(
            nonce, raw, self._scope(project, actor, agent, record, purpose)
        )
        return "v1:" + base64.b64encode(nonce + encrypted).decode("ascii")

    def decrypt(
        self,
        project: UUID,
        actor: UUID,
        agent: UUID,
        record: UUID,
        purpose: EvidencePurpose,
        ciphertext: str,
    ) -> str:
        try:
            if not ciphertext.startswith("v1:") or len(ciphertext) > 200000:
                raise ValueError("Invalid evidence format")
            encoded = base64.b64decode(ciphertext[3:], validate=True)
            if len(encoded) < 28:
                raise ValueError("Invalid encrypted evidence")
            raw = self._cipher().decrypt(
                encoded[:12],
                encoded[12:],
                self._scope(project, actor, agent, record, purpose),
            )
            if len(raw) > 131072:
                raise ValueError("Invalid evidence size")
            return raw.decode("utf-8")
        except Exception:
            raise ConflictError("证据内容无法解密或范围已变化") from None
