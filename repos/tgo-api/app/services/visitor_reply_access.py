"A reply number is a locator; only its original visitor can stop that \
reply."

from sqlalchemy.orm import Session

from app.core.exceptions import TGOAPIException
from app.schemas.ai_runs import ReplyRun
from app.schemas.visitor_data_access import VisitorDataAccess
from app.services.visitor_data_access import (
    authorize_visitor_data_channel,
    reauthorize_visitor_data,
)


def require_visitor_reply_access(
    db: Session,
    access: VisitorDataAccess,
    item: ReplyRun,
    client_msg_no: str,
) -> None:
    reauthorize_visitor_data(db, access)
    if (
        item.project_id != str(access.project_id)
        or item.client_msg_no != client_msg_no
        or item.channel_type != 251
    ):
        raise TGOAPIException("回复不存在", "NOT_FOUND", status_code=404)
    try:
        authorize_visitor_data_channel(
            access, item.channel_id, item.channel_type
        )
    except TGOAPIException:
        raise TGOAPIException("回复不存在", "NOT_FOUND", status_code=404) from None
