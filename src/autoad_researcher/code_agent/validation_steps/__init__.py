"""InternalValidationStep functions for deterministic post-patch validation.

Five functions matching the InternalValidationStep.step_id Literal:
  - ast_parse: validate Python syntax of payloads
  - before_after_identity: verify unchanged files weren't modified
  - diff_integrity: verify proposed diff matches applied content
  - import_declaration_scan: static import declaration scanner
  - path_containment: ensure all touched paths are in approved scope
"""
