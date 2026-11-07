"""Negotiate one connection's screen scope; this does not publish business
access."""

import hashlib

from pydantic import ValidationError

from app.schemas.device_screen_policy import (
    ScreenGrantStatus,
    ScreenPolicy,
    ScreenPolicyAck,
)
from app.services.tcp_connection_manager import (
    TcpDeviceConnection,
    tcp_connection_manager,
)


def screen_policy_digest(policy: ScreenPolicy) -> str:
    lines = [
        "tgo-screen-policy-v1",
        str(policy.revision),
        policy.grant_id,
        str(policy.max_actions),
        str(policy.allow_click).lower(),
        str(policy.allow_text).lower(),
        str(len(policy.click_regions)),
    ]
    lines.extend(
        f"{r.x},{r.y},{r.width},{r.height}" for r in policy.click_regions
    )
    return hashlib.sha256(
        ("\n".join(lines) + "\n").encode("utf-8")
    ).hexdigest()


def validate_local_screen_scope(
    local: ScreenGrantStatus, policy: ScreenPolicy
) -> bool:
    if (
        not local.active
        or local.grant_id != policy.grant_id
        or not local.used_actions < policy.max_actions <= local.max_actions
        or (policy.allow_click and not local.allow_click)
        or (policy.allow_text and not local.allow_text)
        or policy.digest != screen_policy_digest(policy)
    ):
        return False
    return all(
        any(
            r.x >= parent.x
            and r.y >= parent.y
            and r.x + r.width <= parent.x + parent.width
            and r.y + r.height <= parent.y + parent.height
            for parent in local.click_regions
        )
        for r in policy.click_regions
    )


async def apply_screen_policy(
    connection: TcpDeviceConnection, policy: ScreenPolicy
) -> bool:
    (
        "Caller holds the device lifecycle lock. "
        "Acknowledgement is not a test pass."
        "\n\n    Old clients report "
        "method-not-found and remain pending. "
        "Neither reconnect\n    nor a failed/late "
        "acknowledgement can reactivate the local"
        " grant.\n    "
    )
    connection.applied_screen_policy_digest = None
    connection.screen_grant = None
    instance = connection.connection_instance_id

    def current() -> bool:
        return (
            tcp_connection_manager.get_connection(connection.agent_id)
            is connection
            and connection.connection_instance_id == instance
            and getattr(connection, "desired_read_policy_digest", None) is None
        )

    previous = connection.desired_screen_policy
    if (
        not current()
        or policy.digest != screen_policy_digest(policy)
        or (
            previous is not None
            and (
                previous.revision > policy.revision
                or (
                    previous.revision == policy.revision
                    and previous.digest != policy.digest
                )
            )
        )
    ):
        return False
    connection.desired_screen_policy = policy.model_copy(deep=True)
    response = await connection.send_request("screen/status", {}, timeout=10)
    try:
        local = ScreenGrantStatus.model_validate(response)
    except (ValidationError, TypeError, ValueError):
        return False
    if not current() or not validate_local_screen_scope(local, policy):
        return False
    response = await connection.send_request(
        "screen/policy/apply",
        policy.model_dump(mode="json"),
        timeout=10,
    )
    try:
        ack = ScreenPolicyAck.model_validate(response)
    except (ValidationError, TypeError, ValueError):
        return False
    if (
        not current()
        or connection.desired_screen_policy != policy
        or ack.revision != policy.revision
        or ack.digest != policy.digest
        or ack.grant_id != policy.grant_id
    ):
        return False
    connection.screen_grant = local
    connection.applied_screen_policy_digest = policy.digest
    return True
