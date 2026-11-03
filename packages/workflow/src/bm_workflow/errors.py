"""Stable, payload-free errors at public boundaries."""


class WorkflowError(Exception):
    code = "workflow_error"


class DefinitionError(WorkflowError, ValueError):
    code = "invalid_definition"


class InputValidationError(WorkflowError, ValueError):
    code = "invalid_input"


class WorkflowNotFoundError(WorkflowError, LookupError):
    code = "workflow_not_found"


class TransientError(WorkflowError):
    """The host explicitly classifies an operation failure as retryable."""

    code = "transient_error"


class ExecutionError(WorkflowError):
    def __init__(
        self, code: str, node_id: str | None = None, error_type: str | None = None
    ) -> None:
        self.code = code
        self.node_id = node_id
        self.error_type = error_type
        super().__init__(code)
