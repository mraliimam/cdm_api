"""
Request validation schemas for cdm_api endpoints.
"""

from marshmallow import Schema, fields, validate


class CdmRequestFileSchema(Schema):
    """
    On-Demand mode — one IAP user clicks 'Request File'.
    The evaluator profile is fetched from AA_IAP_USERS by evaluator_id.
    """
    evaluator_id       = fields.Integer(required=True)
    stage              = fields.String(required=True,
                             validate=validate.OneOf(['L3', 'L4']))
    num_files          = fields.Integer(load_default=2,
                             validate=validate.Range(min=1, max=10))
    effort_bias_factor = fields.Float(load_default=1.0)


class CdmScheduledRunSchema(Schema):
    """
    Scheduled mode — batch assigns files_per_user workfiles to a list of
    evaluators.  Evaluator profiles are fetched from AA_IAP_USERS.
    """
    stage              = fields.String(required=True,
                             validate=validate.OneOf(['L3', 'L4']))
    evaluator_ids      = fields.List(fields.Integer(), required=True)
    files_per_user     = fields.Integer(load_default=9)
    effort_bias_factor = fields.Float(load_default=1.0)


class CdmCompleteSchema(Schema):
    """Mark a CDM-allocated workfile as completed; log actual measured metrics."""
    allocation_id       = fields.Integer(required=True)
    actual_accuracy     = fields.Float(required=True)
    actual_satisfaction = fields.Float(required=True)
    actual_effort_mins  = fields.Float(required=True)


class CdmDecisionsQuerySchema(Schema):
    """Query-string validator for GET /cdm/decisions."""
    evaluator_id = fields.Integer(load_default=None)
    stage        = fields.String(load_default=None,
                       validate=validate.OneOf(['L3', 'L4']))
    status       = fields.String(load_default=None,
                       validate=validate.OneOf(['pending', 'completed']))
    from_date    = fields.String(load_default=None)
    to_date      = fields.String(load_default=None)
    page         = fields.Integer(load_default=1,
                       validate=validate.Range(min=1))
    per_page     = fields.Integer(load_default=20,
                       validate=validate.Range(min=1, max=100))
