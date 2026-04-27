"""
cdm_api ORM models package.

Models are split into one file per logical table group to keep each module
small and focused.  Re-exporting them here lets callers do, e.g.

    from src.models import CCAudio, CdmAllocation, CdmAllocationDecision

without caring about the internal layout.

Modules
-------
cc_audio                CC_AUDIO + reference tables (read-only for cdm_api)
users                   AA_IAP_USERS (with CDM_* capacity columns)
workfile                AA_IAP_WORKFILE
cdm                     CC_CDM_ALLOCATION, CC_EFFORT_BASELINE, CC_CDM_PROVISION_JOB
allocation_decision     CC_CDM_ALLOCATION_DECISION (per-parameter rationale log)
evaluator_performance   CC_CDM_EVALUATOR_PERFORMANCE (per evaluator/user history)

`db` is intentionally re-exported as well so legacy code that does
`from src.models import db` keeps working — but the canonical import path
is `from src.extensions import db`.
"""

from src.extensions import db

from src.models.cc_audio import (
    CCAudio,
    CCProreciter,
    CCTeacher,
    CCStudent,
    CCUnknownUser,
)
from src.models.users import AAIAPUSERS
from src.models.workfile import AAIAPWORKFILE
from src.models.cdm import (
    CdmAllocation,
    CdmEffortBaseline,
    CdmProvisionJob,
)
from src.models.allocation_decision import CdmAllocationDecision
from src.models.evaluator_performance import CdmEvaluatorPerformance

__all__ = [
    'db',
    'CCAudio',
    'CCProreciter',
    'CCTeacher',
    'CCStudent',
    'CCUnknownUser',
    'AAIAPUSERS',
    'AAIAPWORKFILE',
    'CdmAllocation',
    'CdmEffortBaseline',
    'CdmProvisionJob',
    'CdmAllocationDecision',
    'CdmEvaluatorPerformance',
]
