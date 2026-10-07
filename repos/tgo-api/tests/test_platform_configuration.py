import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4
from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas import PlatformCreate
from app.services.platform_configuration import is_platform_configured


@pytest.mark.parametrize('name', ['', '   ', 'x' * 101])
def test_new_channel_requires_meaningful_name(name):
    with pytest.raises(ValidationError):
        PlatformCreate(type='wecom', name=name)


def test_name_is_trimmed():
    assert PlatformCreate(type='website', name=' 品牌官网 ').name == '品牌官网'


def test_empty_wecom_is_not_configured():
    assert not is_platform_configured('wecom', {})
    assert not is_platform_configured('wecom', {'corp_id': 'test'})
    assert is_platform_configured('wecom', {
        'corp_id': 'test', 'app_secret': 'test', 'token': 'test', 'encoding_aes_key': 'test',
    })
    assert is_platform_configured('wecom', {
        'corp_id': 'test', 'kf_secret': 'test', 'token': 'test', 'encoding_aes_key': 'test',
    })


def test_website_needs_no_external_credentials():
    assert is_platform_configured('website', {})


def test_unknown_channels_do_not_claim_configured():
    assert not is_platform_configured('unknown', {'anything': 'value'})


def test_email_configuration_supports_shared_credentials():
    config = {'imap_host': 'test', 'imap_username': 'test', 'imap_password': 'test', 'smtp_host': 'test'}
    assert is_platform_configured('email', config)
    assert not is_platform_configured('email', {**config, 'use_same_credentials': False})
    assert not is_platform_configured('email', {})


@pytest.mark.asyncio
async def test_duplicate_channel_name_is_rejected_before_insert():
    from app.api.v1.endpoints.platforms import create_platform
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = ('existing-id',)
    with pytest.raises(HTTPException) as error:
        await create_platform(
            PlatformCreate(type='website', name='现有渠道'), db=db,
            current_user=SimpleNamespace(project_id=uuid4(), username='test'),
        )
    assert error.value.status_code == 409
    db.add.assert_not_called()
