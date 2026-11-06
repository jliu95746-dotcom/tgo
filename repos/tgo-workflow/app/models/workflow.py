from sqlalchemy import String, Text, JSON, Integer, ARRAY, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from datetime import datetime
from typing import Optional, List
from pydantic import JsonValue
from app.database import Base
import uuid


class Workflow(Base):
    __tablename__ = "wf_workflows"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    definition: Mapped[dict[str, JsonValue]] = mapped_column(
        JSON, nullable=False
    )
    status: Mapped[str] = mapped_column(String, default="draft")
    version: Mapped[int] = mapped_column(Integer, default=1)
    tags: Mapped[List[str]] = mapped_column(
        ARRAY(String).with_variant(JSON(), "sqlite"), default=[]
    )
    published_version: Mapped[int | None] = mapped_column(Integer)
    publication_revision: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    created_by: Mapped[Optional[str]] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
