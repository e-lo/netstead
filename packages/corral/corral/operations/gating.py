"""Operation gating — threshold check + approval block (architecture §6.5).

The gating layer is a thin primitive: it consults an
:class:`~corral.operations.cost_model.OperationCost`, compares
``est_seconds()`` against two thresholds, and either passes the cost
through, logs an estimate, or raises :class:`ApprovalRequired`.

Behaviour by band:

* ``est < estimate_threshold_s`` → silent pass-through.
* ``estimate_threshold_s <= est < approval_threshold_s`` → log estimate,
  return.
* ``est >= approval_threshold_s`` and ``approve=False`` →
  :class:`ApprovalRequired`.
* ``est >= approval_threshold_s`` and ``approve=True`` → log estimate,
  return.

User-facing prompts are *not* this layer's responsibility — the CLI
(Phase 4 task 4.7) catches :class:`ApprovalRequired`, asks the user,
and re-calls with ``approve=True`` (or callers set ``approve=True``
unconditionally via ``--yes`` / ``CORRAL_AUTO_APPROVE=1``).
"""

from __future__ import annotations

import logging

from .cost_model import OperationCost

logger = logging.getLogger(__name__)


class ApprovalRequired(Exception):
    """Raised when an op exceeds the approval threshold and ``approve=False``.

    The CLI / MCP / notebook surface catches this, prompts the user
    (or honours ``--yes`` / ``CORRAL_AUTO_APPROVE=1``), and re-calls
    the gated function with ``approve=True``.
    """

    def __init__(self, cost: OperationCost, threshold_s: float):
        """Record the offending cost + threshold and build a human message."""
        self.cost = cost
        self.threshold_s = threshold_s
        super().__init__(
            f"operation {cost.op_name!r} estimated at "
            f"{cost.est_seconds():.1f}s exceeds approval threshold "
            f"{threshold_s:.1f}s; pass approve=True to proceed",
        )


def gate(
    op_cost: OperationCost,
    *,
    approve: bool = False,
    estimate_threshold_s: float = 30.0,
    approval_threshold_s: float = 180.0,
) -> OperationCost:
    """Check whether an op should proceed; emit estimate or block on approval.

    Args:
        op_cost: The cost record to inspect.
        approve: If ``True``, bypass the approval block. The CLI sets this
            from ``--yes`` or ``CORRAL_AUTO_APPROVE=1``; programmatic
            callers pass it explicitly.
        estimate_threshold_s: Estimates at or above this value are logged.
        approval_threshold_s: Estimates at or above this value require
            ``approve=True``.

    Returns:
        The same ``op_cost`` (returned for fluent chaining at call sites).

    Raises:
        ApprovalRequired: If ``op_cost.est_seconds() >= approval_threshold_s``
            and ``approve`` is ``False``.

    Examples:
        Run a cheap op without prompting::

            >>> from corral.operations import OperationCost, gate
            >>> _ = gate(OperationCost(op_name="read", n_rows=1_000, fmt="parquet"))
    """
    est = op_cost.est_seconds()

    if est >= approval_threshold_s and not approve:
        raise ApprovalRequired(op_cost, approval_threshold_s)

    if est >= estimate_threshold_s:
        logger.info(
            "operation %s estimate %.1fs (n_rows=%d, n_tables=%d, fmt=%s)",
            op_cost.op_name,
            est,
            op_cost.n_rows,
            op_cost.n_tables,
            op_cost.fmt,
        )

    return op_cost
