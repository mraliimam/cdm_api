"""
cdm_api Marshmallow schemas package.

Schemas are split per concern:
  • requests        — validate incoming API payloads
  • models          — serialise ORM model instances to JSON
  • allocation      — schemas tied to CC_CDM_ALLOCATION (extends models)
"""

from src.schemas.requests import (
    CdmRequestFileSchema,
    CdmScheduledRunSchema,
    CdmCompleteSchema,
    CdmDecisionsQuerySchema,
)
from src.schemas.models import (
    IapUserSchema,
    IapWorkfileSchema,
    CdmAudioSchema,
    CdmEffortBaselineSchema,
)
from src.schemas.allocation import (
    CdmAllocationSchema,
    CdmAllocationDecisionSchema,
    CdmEvaluatorPerformanceSchema,
)

__all__ = [
    'CdmRequestFileSchema',
    'CdmScheduledRunSchema',
    'CdmCompleteSchema',
    'CdmDecisionsQuerySchema',
    'IapUserSchema',
    'IapWorkfileSchema',
    'CdmAudioSchema',
    'CdmEffortBaselineSchema',
    'CdmAllocationSchema',
    'CdmAllocationDecisionSchema',
    'CdmEvaluatorPerformanceSchema',
]
