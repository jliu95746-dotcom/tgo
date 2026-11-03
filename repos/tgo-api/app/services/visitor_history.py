"""Validate downstream history ownership before releasing any message contents."""

from app.core.exceptions import TGOAPIException
from app.schemas.visitor_data_access import VisitorDataAccess
from app.schemas.wukongim import WuKongIMChannelMessageSyncResponse
from app.services.visitor_data_access import authorize_visitor_data_channel


def require_owned_history_batch(
    access: VisitorDataAccess,
    channel_id: str,
    channel_type: int,
    result: WuKongIMChannelMessageSyncResponse,
) -> None:
    authorize_visitor_data_channel(access,channel_id,channel_type)
    if any(message.channel_id != channel_id or message.channel_type != channel_type
           for message in result.messages):
        raise TGOAPIException(
            "历史消息来源未通过核验，请稍后重试。",
            "HISTORY_SCOPE_MISMATCH",status_code=502,
        )
