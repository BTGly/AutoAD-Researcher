"""InternalValidationStep functions for deterministic post-patch validation.

Four functions matching the InternalValidationStep.validation_function Literal:
  - ast_parse: validate Python syntax of payloads
  - diff_integrity: verify proposed diff matches applied content
  - path_containment: ensure all touched paths are in approved scope
  - before_after_identity: verify unchanged files weren't modified
"""
