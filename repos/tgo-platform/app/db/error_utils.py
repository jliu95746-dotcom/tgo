"""Database error classification helpers."""

from sqlalchemy.exc import IntegrityError


def is_expected_unique_violation(
    error: IntegrityError,
    constraint_name: str,
) -> bool:
    """Return true only for PostgreSQL unique violations on one constraint."""
    original = error.orig
    sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    diagnostic = getattr(original, "diag", None)
    actual_constraint = getattr(diagnostic, "constraint_name", None)
    if sqlstate != "23505":
        return False
    if actual_constraint is not None:
        return actual_constraint == constraint_name
    return constraint_name in str(original)
