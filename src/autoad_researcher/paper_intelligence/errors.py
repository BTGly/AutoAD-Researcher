"""Paper Intelligence exceptions."""


class PaperIntelligenceContractError(ValueError):
    """Raised when a Paper Intelligence contract cannot be satisfied."""


class PaperSourceError(PaperIntelligenceContractError):
    """Raised when the paper source is invalid or missing."""


class PaperParseError(PaperIntelligenceContractError):
    """Raised when PDF parsing fails."""


class PaperEvidenceError(PaperIntelligenceContractError):
    """Raised when evidence validation fails."""


class PaperBudgetError(PaperIntelligenceContractError):
    """Raised when the agent budget is exhausted."""


class PaperRepairExhaustedError(PaperIntelligenceContractError):
    """Raised when the repair loop exceeds its budget."""


class PaperValidationError(PaperIntelligenceContractError):
    """Raised when a validation gate fails."""
