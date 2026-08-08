"""Domain-level errors shared without depending on adapters or interfaces."""


class WorkflowDomainError(RuntimeError):
    """Base class for rejected domain operations."""


class StoreError(WorkflowDomainError):
    """An event violates the permanent journal contract."""


class CatalogError(WorkflowDomainError):
    """A research-catalog value violates a domain rule."""
