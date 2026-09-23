"""Short-lived attachment links issued after checking current authority."""

from datetime import datetime

from app.schemas.base import BaseSchema


class ChatFileAccessResponse(BaseSchema):
    access_url: str
    expires_at: datetime
