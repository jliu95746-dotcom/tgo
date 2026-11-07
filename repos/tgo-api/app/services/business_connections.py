"""Revisioned company connection configuration and credential ownership."""

from datetime import datetime, timezone
import hashlib
import hmac
import json
from typing import cast
from uuid import UUID, uuid4

from pydantic import JsonValue, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import ConflictError, NotFoundError, TGOAPIException
from app.models.business_connection import BusinessConnection
from app.schemas.business_connections import (
    BusinessConnectionCreate,
    BusinessConnectionUpdate,
    BusinessConnectionResponse,
    BusinessHTTPConfiguration,
    BusinessMerchantMapping,
    BusinessConnectionCredentials,
    BusinessCapability,
    ConnectionMode,
)
from app.services.business_connection_vault import (
    CredentialVault,
    credential_values,
)
from app.services.business_connection_proofs import current_capabilities

_CUSTOMER_READ_FIELDS = frozenset(
    {
        "customer_profile_path",
        "membership_path",
        "customer_segment_field",
        "customer_tags_field",
        "membership_tier_field",
        "membership_points_field",
    }
)


def _find(
    db: Session, project: UUID, connection: UUID, *, lock: bool = False
) -> BusinessConnection:
    statement = select(BusinessConnection).where(
        BusinessConnection.id == connection,
        BusinessConnection.project_id == project,
    )
    if lock:
        statement = statement.with_for_update()
    row = db.execute(
        statement.execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("Business connection")
    return row


def _proof_current(db: Session, row: BusinessConnection) -> bool:
    if (
        row.mode != "http"
        or row.verified_configuration_revision != row.configuration_revision
        or not row.last_tested_at
        or not row.last_tested_by
        or not set(row.capabilities).issubset(row.verified_capabilities)
    ):
        return False
    return set(row.capabilities).issubset(current_capabilities(db, row))


def _view(db: Session, row: BusinessConnection) -> BusinessConnectionResponse:
    readable = True
    if row.credential_ciphertext:
        try:
            CredentialVault().decrypt(
                row.credential_ciphertext, row.project_id, row.id
            )
        except TGOAPIException:
            readable = False
    tested = _proof_current(db, row)
    state = {
        "project": str(row.project_id),
        "id": str(row.id),
        "configuration": row.configuration_revision,
        "authorization": row.authorization_revision,
        "enabled": row.enabled,
        "readable": readable,
        "tested": tested,
    }
    revision = hmac.new(
        settings.SECRET_KEY.encode(),
        json.dumps(state, sort_keys=True).encode(),
        hashlib.sha256,
    ).hexdigest()
    return BusinessConnectionResponse(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        mode=cast(ConnectionMode, row.mode),
        capabilities=cast(list[BusinessCapability], list(row.capabilities)),
        configuration=BusinessHTTPConfiguration.model_validate(
            row.configuration
        )
        if row.configuration is not None
        else None,
        credentials_configured=row.credential_ciphertext is not None,
        enabled=row.enabled,
        revision=row.revision,
        configuration_revision=row.configuration_revision,
        authorization_revision=row.authorization_revision,
        resource_revision=revision,
        test_status="unavailable"
        if not readable
        else "passed"
        if tested
        else "not_tested",
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def get(
    db: Session, project: UUID, connection: UUID
) -> BusinessConnectionResponse:
    return _view(db, _find(db, project, connection))


def creation_result(
    db: Session, project: UUID, request: UUID
) -> BusinessConnectionResponse:
    row = db.scalar(
        select(BusinessConnection)
        .where(
            BusinessConnection.project_id == project,
            BusinessConnection.request_id == request,
        )
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise NotFoundError("Business connection creation request")
    return _view(db, row)


def list_connections(
    db: Session, project: UUID, *, limit: int = 100, offset: int = 0
) -> list[BusinessConnectionResponse]:
    rows = (
        db.execute(
            select(BusinessConnection)
            .where(BusinessConnection.project_id == project)
            .order_by(BusinessConnection.created_at, BusinessConnection.id)
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return [_view(db, row) for row in rows]


def _request_digest(
    request: BusinessConnectionCreate,
    *,
    legacy_mapping: bool = False,
    legacy_customer: bool = False,
) -> str:
    data = request.model_dump(mode="json", exclude={"credentials"})
    if legacy_mapping and request.configuration is not None:
        configuration = data["configuration"]
        for name in BusinessMerchantMapping.model_fields:
            if name not in {"success_field", "success_value"}:
                configuration.pop(name, None)
    if legacy_customer and request.configuration is not None:
        configuration = data["configuration"]
        for name in _CUSTOMER_READ_FIELDS:
            configuration.pop(name, None)
    data["credentials"] = (
        credential_values(request.credentials)
        if request.credentials is not None
        else None
    )
    raw = json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hmac.new(
        settings.SECRET_KEY.encode(),
        b"business-connection-create:" + raw,
        hashlib.sha256,
    ).hexdigest()


def _matches_creation_digest(
    saved: str, request: BusinessConnectionCreate, current: str
) -> bool:
    if hmac.compare_digest(saved, current):
        return True
    if request.configuration is None:
        return False
    customer_defaults = {
        name: BusinessHTTPConfiguration.model_fields[name].default
        for name in _CUSTOMER_READ_FIELDS
    }
    if any(
        getattr(request.configuration, name) != value
        for name, value in customer_defaults.items()
    ):
        return False
    if hmac.compare_digest(
        saved, _request_digest(request, legacy_customer=True)
    ):
        return True
    defaults = BusinessMerchantMapping().model_dump(
        exclude={"success_field", "success_value"}
    )
    if any(
        getattr(request.configuration, name) != value
        for name, value in defaults.items()
    ):
        return False
    # Only default-valued additions can replay an immutable pre-mapping
    # request.
    return hmac.compare_digest(
        saved,
        _request_digest(request, legacy_mapping=True, legacy_customer=True),
    )


def create(
    db: Session, project: UUID, actor: UUID, request: BusinessConnectionCreate
) -> BusinessConnectionResponse:
    digest = _request_digest(request)
    previous = db.execute(
        select(BusinessConnection).where(
            BusinessConnection.project_id == project,
            BusinessConnection.request_id == request.request_id,
        )
    ).scalar_one_or_none()
    if previous is not None:
        if not _matches_creation_digest(
            previous.request_digest, request, digest
        ):
            raise ConflictError("原创建请求内容已经变化，请使用新的请求编号")
        return _view(db, previous)
    connection_id = uuid4()
    ciphertext = (
        CredentialVault().encrypt(request.credentials, project, connection_id)
        if request.credentials is not None
        else None
    )
    row = BusinessConnection(
        id=connection_id,
        project_id=project,
        created_by=actor,
        updated_by=actor,
        request_id=request.request_id,
        request_digest=digest,
        name=request.name,
        mode=request.mode,
        capabilities=list(request.capabilities),
        configuration=cast(
            dict[str, JsonValue], request.configuration.model_dump(mode="json")
        )
        if request.configuration is not None
        else None,
        credential_ciphertext=ciphertext,
        enabled=False,
        revision=1,
        configuration_revision=1,
        authorization_revision=1,
        verified_capabilities=[],
    )
    db.add(row)
    try:
        db.commit()
        db.refresh(row)
        return _view(db, row)
    except IntegrityError:
        db.rollback()
        previous = db.execute(
            select(BusinessConnection).where(
                BusinessConnection.project_id == project,
                BusinessConnection.request_id == request.request_id,
            )
        ).scalar_one_or_none()
        if previous is None or not _matches_creation_digest(
            previous.request_digest, request, digest
        ):
            raise ConflictError("业务连接创建发生冲突，请查询原请求") from None
        return _view(db, previous)


def update(
    db: Session,
    project: UUID,
    actor: UUID,
    connection: UUID,
    request: BusinessConnectionUpdate,
) -> BusinessConnectionResponse:
    row = _find(db, project, connection, lock=True)
    if row.revision != request.expected_revision:
        raise ConflictError("连接已被修改，请读取当前版本后再操作")
    for required in ("name", "mode", "capabilities", "enabled"):
        if (
            required in request.model_fields_set
            and getattr(request, required) is None
        ):
            raise TGOAPIException(
                "此配置字段不能设为空", code="VALIDATION_ERROR", status_code=422
            )
    configuration_fields = {
        "mode",
        "configuration",
        "capabilities",
        "credentials",
    }
    changed_configuration = bool(
        request.model_fields_set & configuration_fields
    )
    next_ciphertext = row.credential_ciphertext
    next_configuration = row.configuration
    next_capabilities = row.capabilities
    next_mode = row.mode
    business_changed = False
    if changed_configuration:
        next_mode = request.mode or row.mode
        configured = (
            request.configuration
            if "configuration" in request.model_fields_set
            else row.configuration
        )
        supplied = (
            request.credentials
            if "credentials" in request.model_fields_set
            else (
                CredentialVault().decrypt(
                    row.credential_ciphertext, project, connection
                )
                if row.credential_ciphertext
                else None
            )
        )
        if next_mode == "demo":
            if "configuration" not in request.model_fields_set:
                configured = None
            if "credentials" not in request.model_fields_set:
                supplied = None
        try:
            validated = BusinessConnectionCreate(
                request_id=row.request_id,
                name=request.name or row.name,
                mode=cast(ConnectionMode, next_mode),
                capabilities=cast(
                    list[BusinessCapability],
                    request.capabilities or row.capabilities,
                ),
                configuration=BusinessHTTPConfiguration.model_validate(
                    configured
                )
                if configured is not None
                else None,
                credentials=supplied,
            )
        except ValidationError:
            raise TGOAPIException(
                "请核对业务连接配置及对应鉴权凭证",
                code="VALIDATION_ERROR",
                status_code=422,
            ) from None
        next_configuration = (
            cast(
                dict[str, JsonValue],
                validated.configuration.model_dump(mode="json"),
            )
            if validated.configuration is not None
            else None
        )
        next_capabilities = list(validated.capabilities)
        business_changed = (
            next_configuration != row.configuration
            or set(next_capabilities) != set(row.capabilities)
            or next_mode != row.mode
            or "credentials" in request.model_fields_set
        )
        if "credentials" in request.model_fields_set or next_mode != row.mode:
            next_ciphertext = (
                CredentialVault().encrypt(
                    validated.credentials, project, connection
                )
                if validated.credentials is not None
                else None
            )
    if request.enabled is True:
        if (
            business_changed
            or not _proof_current(db, row)
            or _view(db, row).test_status != "passed"
        ):
            raise ConflictError("请先完成当前连接全部业务能力的真实测试，再启用")
    name_changed = request.name is not None and request.name != row.name
    enabled_changed = (
        request.enabled is not None and request.enabled != row.enabled
    )
    if not (name_changed or enabled_changed or business_changed):
        db.rollback()
        return get(db, project, connection)
    if business_changed:
        row.mode = next_mode
        row.configuration = next_configuration
        row.capabilities = next_capabilities
        row.credential_ciphertext = next_ciphertext
        row.configuration_revision += 1
        row.verified_configuration_revision = None
        row.verified_capabilities = []
        row.last_tested_at = None
        row.last_tested_by = None
    if business_changed or enabled_changed:
        row.authorization_revision += 1
    if request.name is not None:
        row.name = request.name
    if request.enabled is not None:
        row.enabled = request.enabled
    row.revision += 1
    row.updated_by = actor
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return _view(db, row)


def resolve(
    db: Session, project: UUID, connection: UUID, *, expected_revision: int
) -> tuple[BusinessConnectionResponse, BusinessConnectionCredentials | None]:
    (
        'Private, exact-version configuration resolution; does not grant '
        'execution.'
    )
    row = _find(db, project, connection)
    if row.revision != expected_revision:
        raise ConflictError("原连接配置已变化，请重新读取")
    credentials = (
        CredentialVault().decrypt(
            row.credential_ciphertext, project, connection
        )
        if row.credential_ciphertext
        else None
    )
    return _view(db, row), credentials
