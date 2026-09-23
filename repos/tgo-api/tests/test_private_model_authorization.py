"""Model credentials are granted only after validating a live company reservation."""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from uuid import uuid4
import pytest
from fastapi import HTTPException

from app.api.internal.endpoints.ai_usage import authorize
from app.core.config import settings
from app.models import SystemSetup
from app.models.ai_usage import AIUsageReservation
from app.models.company_account import CompanyAccount
from app.schemas.ai_usage import UsageAuthorization
from app.schemas.platform_models import PlatformModelDefinition, StoredPlatformModel
from app.utils.crypto import encrypt_str


@pytest.mark.parametrize("valid", [False, True])
def test_private_credentials_require_valid_live_reservation(monkeypatch, valid):
    monkeypatch.setattr(settings, "SAAS_ENABLED", True)
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", True)
    project, reservation, lease = uuid4(), uuid4(), uuid4()
    expires = datetime.now(timezone.utc) + timedelta(minutes=10)
    company = CompanyAccount(project_id=project, status="trial", expires_at=expires)
    row = AIUsageReservation(
        id=reservation,
        project_id=project,
        lease_id=lease,
        status="reserved",
        expires_at=expires,
    )
    db = Mock()
    db.get.side_effect = [company, row if valid else None]
    stored = StoredPlatformModel(
        version=1,
        definition=PlatformModelDefinition(
            model="synthetic-model", provider_kind="openai"
        ),
        encrypted_api_key=encrypt_str("synthetic-private-model-key"),
    )
    db.scalar.return_value = SystemSetup(
        is_installed=True,
        config={"saas_platform_model": stored.model_dump(mode="json")},
    )
    payload = UsageAuthorization(
        project_id=project, reservation_id=reservation, lease_id=lease
    )
    if valid:
        result = authorize(payload, db)
        assert (
            result.platform_model.api_key.get_secret_value()
            == "synthetic-private-model-key"
        )
        assert (
            result.model_dump(mode="json")["platform_model"]["api_key"]
            == "synthetic-private-model-key"
        )
    else:
        with pytest.raises(HTTPException) as error:
            authorize(payload, db)
        assert error.value.status_code == 403
        db.scalar.assert_not_called()
