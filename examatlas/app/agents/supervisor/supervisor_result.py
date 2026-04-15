"""
app/agents/supervisor/supervisor_result.py

SupervisorResult wraps the final PipelineResult with the supervisor's
audit trail: execution plan, per-stage timings, validation reports,
conflict resolution report, and rollback history.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.agents.supervisor.conflict_resolver import ConflictReport
from app.agents.supervisor.execution_plan import ExecutionPlan
from app.agents.supervisor.validator import ValidationResult


@dataclass
class SupervisorResult:
    """
    Full result envelope from the OrchestratorSupervisor.

    Attributes:
        pipeline_result     The underlying PipelineResult (agents' output)
        plan                The ExecutionPlan that drove this run
        stage_validations   Per-stage ValidationResult objects
        final_validation    End-to-end FinalValidator output
        conflict_report     ConflictResolver audit report
        rollbacks_applied   List of (stage_name, rollback_note) tuples
        supervisor_ms       Total supervisor overhead in milliseconds
        degraded            True if any stage used a rollback strategy
        cross_domain        True if query spanned multiple domains
    """
    pipeline_result:    object                         # PipelineResult
    plan:               ExecutionPlan
    stage_validations:  dict[str, ValidationResult]   = field(default_factory=dict)
    final_validation:   ValidationResult | None     = None
    conflict_report:    ConflictReport | None        = None
    rollbacks_applied:  list[tuple[str, str]]           = field(default_factory=list)
    supervisor_ms:      int                             = 0
    degraded:           bool                            = False
    cross_domain:       bool                            = False

    def to_audit_dict(self) -> dict:
        """Serialisable audit trail — attached to the API response."""
        return {
            "supervisor_ms":   self.supervisor_ms,
            "degraded":        self.degraded,
            "cross_domain":    self.cross_domain,
            "domains":         [d.value for d in self.plan.domains],
            "plan":            self.plan.to_dict(),
            "stage_validations": {
                name: vr.to_dict()
                for name, vr in self.stage_validations.items()
            },
            "final_validation": self.final_validation.to_dict()
                                 if self.final_validation else None,
            "conflict_report":  self.conflict_report.to_dict()
                                 if self.conflict_report else None,
            "rollbacks":        [
                {"stage": s, "note": n}
                for s, n in self.rollbacks_applied
            ],
        }
