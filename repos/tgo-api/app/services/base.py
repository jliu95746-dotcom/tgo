"""Base service class for business logic."""

from typing import Any, Dict, Generic, List, Optional, Type, TypeVar
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.database import Base
from app.core.exceptions import NotFoundError
from app.core.logging import get_logger

ModelType = TypeVar("ModelType", bound=Base)
CreateSchemaType = TypeVar("CreateSchemaType")
UpdateSchemaType = TypeVar("UpdateSchemaType")

logger = get_logger("services.base")


class BaseService(Generic[ModelType, CreateSchemaType, UpdateSchemaType]):
    """Base service class with common CRUD operations."""

    def __init__(self, model: Type[ModelType]):
        """Initialize service with model class."""
        self.model = model
        self.model_name = model.__name__

    def get(self, db: Session, id: Any) -> Optional[ModelType]:
        """Get a single record by ID."""
        return db.query(self.model).filter(self.model.id == id).first()

    def get_or_404(self, db: Session, id: Any) -> ModelType:
        """Get a single record by ID or raise 404."""
        obj = self.get(db, id)
        if not obj:
            raise NotFoundError(self.model_name, str(id))
        return obj

    def get_multi(
        self,
        db: Session,
        *,
        skip: int = 0,
        limit: int = 100,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[ModelType]:
        """Get multiple records with pagination and filtering."""
        query = db.query(self.model)

        # Apply filters

        if filters:
            for key, value in filters.items():
                if hasattr(self.model, key) and value is not None:
                    query = query.filter(getattr(self.model, key) == value)
        return query.offset(skip).limit(limit).all()

    def count(
        self,
        db: Session,
        filters: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Count records with optional filtering."""
        query = db.query(self.model)

        # Apply filters

        if filters:
            for key, value in filters.items():
                if hasattr(self.model, key) and value is not None:
                    query = query.filter(getattr(self.model, key) == value)
        return query.count()

    def create(self, db: Session, *, obj_in: CreateSchemaType) -> ModelType:
        """Create a new record."""
        obj_in_data = (
            obj_in.model_dump()
            if hasattr(obj_in, 'model_dump')
            else obj_in.dict()
        )
        db_obj = self.model(**obj_in_data)
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        logger.info(f"Created {self.model_name} with ID: {db_obj.id}")
        return db_obj

    def update(
        self,
        db: Session,
        *,
        db_obj: ModelType,
        obj_in: UpdateSchemaType,
    ) -> ModelType:
        """Update an existing record."""
        obj_data = (
            obj_in.model_dump(exclude_unset=True)
            if hasattr(obj_in, 'model_dump')
            else obj_in.dict(exclude_unset=True)
        )

        for field, value in obj_data.items():
            if hasattr(db_obj, field):
                setattr(db_obj, field, value)
        # Update timestamp if available

        if hasattr(db_obj, 'updated_at'):
            from datetime import datetime

            db_obj.updated_at = datetime.utcnow()
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        logger.info(f"Updated {self.model_name} with ID: {db_obj.id}")
        return db_obj

    def remove(self, db: Session, *, id: Any) -> ModelType:
        """Remove a record (hard delete)."""
        obj = db.query(self.model).get(id)
        if not obj:
            raise NotFoundError(self.model_name, str(id))
        db.delete(obj)
        db.commit()
        logger.info(f"Deleted {self.model_name} with ID: {id}")
        return obj

    def soft_delete(self, db: Session, *, id: Any) -> ModelType:
        """Soft delete a record (if model supports it)."""
        obj = db.query(self.model).get(id)
        if not obj:
            raise NotFoundError(self.model_name, str(id))
        if hasattr(obj, 'deleted_at'):
            from datetime import datetime

            obj.deleted_at = datetime.utcnow()
            if hasattr(obj, 'updated_at'):
                obj.updated_at = datetime.utcnow()
            db.add(obj)
            db.commit()
            db.refresh(obj)
            logger.info(f"Soft deleted {self.model_name} with ID: {id}")
            return obj
        else:
            # Fall back to hard delete if soft delete not supported

            return self.remove(db, id=id)

    def get_by_project(
        self,
        db: Session,
        *,
        project_id: UUID,
        skip: int = 0,
        limit: int = 100,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[ModelType]:
        """Get records filtered by project ID (for multi-tenant models)."""
        query = db.query(self.model)

        # Add project filter if model has project_id

        if hasattr(self.model, 'project_id'):
            query = query.filter(self.model.project_id == project_id)
        # Add soft delete filter if model supports it

        if hasattr(self.model, 'deleted_at'):
            query = query.filter(self.model.deleted_at.is_(None))
        # Apply additional filters

        if filters:
            for key, value in filters.items():
                if hasattr(self.model, key) and value is not None:
                    query = query.filter(getattr(self.model, key) == value)
        return query.offset(skip).limit(limit).all()

    def count_by_project(
        self,
        db: Session,
        *,
        project_id: UUID,
        filters: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Count records filtered by project ID."""
        query = db.query(self.model)

        # Add project filter if model has project_id

        if hasattr(self.model, 'project_id'):
            query = query.filter(self.model.project_id == project_id)
        # Add soft delete filter if model supports it

        if hasattr(self.model, 'deleted_at'):
            query = query.filter(self.model.deleted_at.is_(None))
        # Apply additional filters

        if filters:
            for key, value in filters.items():
                if hasattr(self.model, key) and value is not None:
                    query = query.filter(getattr(self.model, key) == value)
        return query.count()
