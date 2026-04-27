"""
Schemas for CDM allocation, decision-log, and evaluator-performance rows.
"""

from marshmallow import Schema, fields


class CdmAllocationSchema(Schema):
    """Serialises a CdmAllocation row (CC_CDM_ALLOCATION)."""
    id                     = fields.Integer(dump_default=None)
    evaluator_id           = fields.Integer(dump_default=None)
    iap_workfile_id        = fields.Integer(dump_default=None)
    ccaudio_id             = fields.Integer(dump_default=None)
    user_id                = fields.Integer(dump_default=None)
    cdm_mode               = fields.String(dump_default=None)
    stage                  = fields.String(dump_default=None)
    allocation_score       = fields.Float(dump_default=None)
    predicted_accuracy     = fields.Float(dump_default=None)
    actual_accuracy        = fields.Float(dump_default=None)
    predicted_satisfaction = fields.Float(dump_default=None)
    actual_satisfaction    = fields.Float(dump_default=None)
    predicted_effort_mins  = fields.Float(dump_default=None)
    actual_effort_mins     = fields.Float(dump_default=None)
    difficulty_score       = fields.Float(dump_default=None)
    difficulty_level       = fields.String(dump_default=None)
    bias_factor            = fields.Float(dump_default=None)
    rationale              = fields.String(dump_default=None)
    status                 = fields.String(dump_default=None)
    allocated_dts          = fields.String(dump_default=None)
    completed_dts          = fields.String(dump_default=None)


class CdmAllocationDecisionSchema(Schema):
    """Serialises a CdmAllocationDecision row (CC_CDM_ALLOCATION_DECISION)."""
    id              = fields.Integer(dump_default=None)
    allocation_id   = fields.Integer(dump_default=None)
    evaluator_id    = fields.Integer(dump_default=None)
    ccaudio_id      = fields.Integer(dump_default=None)
    iap_workfile_id = fields.Integer(dump_default=None)
    stage           = fields.String(dump_default=None)
    category        = fields.String(dump_default=None)
    parameter       = fields.String(dump_default=None)
    raw_value       = fields.String(dump_default=None)
    numeric_value   = fields.Float(dump_default=None)
    weight          = fields.Float(dump_default=None)
    contribution    = fields.Float(dump_default=None)
    reason          = fields.String(dump_default=None)
    created_dts     = fields.String(dump_default=None)


class CdmEvaluatorPerformanceSchema(Schema):
    """Serialises a CdmEvaluatorPerformance row (CC_CDM_EVALUATOR_PERFORMANCE)."""
    id                   = fields.Integer(dump_default=None)
    evaluator_id         = fields.Integer(dump_default=None)
    user_id              = fields.Integer(dump_default=None)
    sample_count         = fields.Integer(dump_default=None)
    avg_accuracy         = fields.Float(dump_default=None)
    avg_satisfaction     = fields.Float(dump_default=None)
    avg_effort_minutes   = fields.Float(dump_default=None)
    avg_difficulty_score = fields.Float(dump_default=None)
    last_accuracy        = fields.Float(dump_default=None)
    last_satisfaction    = fields.Float(dump_default=None)
    last_effort_minutes  = fields.Float(dump_default=None)
    last_completed_dts   = fields.String(dump_default=None)
    first_seen_dts       = fields.String(dump_default=None)
    updated_dts          = fields.String(dump_default=None)
