"""Typed failures for the durable AutoAD control plane."""


class ControlPlaneError(RuntimeError):
    """Base class for control-plane failures."""


class ControlPlaneLockError(ControlPlaneError):
    """A run or event lock could not be acquired."""


class ControlPlaneLockReentryError(ControlPlaneLockError):
    """A public store method tried to reacquire an active run lock."""


class CorruptAuthoritativeStore(ControlPlaneError):
    """An authoritative control-plane file is malformed or inconsistent."""


class CorruptAuditProjection(ControlPlaneError):
    """The non-authoritative event projection is malformed."""


class IdempotencyConflict(ControlPlaneError):
    """An idempotency key was reused with a different request."""


class JobClaimFenceError(ControlPlaneError):
    """A worker operation no longer owns the claimed job attempt."""


class EventIdempotencyConflict(ControlPlaneError):
    """An event idempotency key was reused with different content."""


class ReadinessStaleError(ControlPlaneError):
    """A readiness snapshot no longer matches current materialization input."""
