"""
CC_CDM_ALLOCATION_DECISION — per-parameter explanation of one allocation.

When the allocator picks a workfile for an evaluator we persist a row in
CC_CDM_ALLOCATION (the headline decision row) AND one row PER decision
parameter in CC_CDM_ALLOCATION_DECISION so that admins can answer the
question "why was this file allocated to this user?".

Each row captures:
  • the parameter / signal name (e.g. AUDIO_LENGTH, MISTAKE_LEVEL, FAIRNESS)
  • the raw value read from the source table (e.g. "Average")
  • the numeric severity / weight projected onto the formula (0–1)
  • a category (audio_quality / evaluator_state / fairness / prediction / …)
  • a short human-readable reason explaining the parameter's effect
  • optional weight & contribution numbers from the weighted formula

Because this is a long-form audit log it intentionally over-stores: the
admin UI can group rows by category, sort by contribution, and surface
the highest-impact parameters without re-deriving anything.
"""

from datetime import datetime

from src.extensions import db


# Decision categories (kept as plain strings so SQL admin tools can filter easily)
CATEGORY_AUDIO_QUALITY      = 'audio_quality'
CATEGORY_EVALUATOR_STATE    = 'evaluator_state'
CATEGORY_EVALUATOR_HISTORY  = 'evaluator_history'
CATEGORY_USER_PROFILE       = 'user_profile'
CATEGORY_FAIRNESS           = 'fairness'
CATEGORY_TIMING             = 'timing'
CATEGORY_CONSTRAINT         = 'constraint'
CATEGORY_PREDICTION         = 'prediction'
CATEGORY_SCORE              = 'score'


class CdmAllocationDecision(db.Model):
    """One parameter-level rationale row for a CC_CDM_ALLOCATION decision."""

    __tablename__ = 'CC_CDM_ALLOCATION_DECISION'

    ID             = db.Column(db.Integer,    primary_key=True, autoincrement=True)
    ALLOCATION_ID  = db.Column(db.Integer,    nullable=False, index=True)   # FK CC_CDM_ALLOCATION.ID
    EVALUATOR_ID   = db.Column(db.Integer,    nullable=True,  index=True)
    CCAUDIO_ID     = db.Column(db.Integer,    nullable=True,  index=True)
    IAP_WORKFILE_ID = db.Column(db.Integer,   nullable=True)
    STAGE          = db.Column(db.String(5),  nullable=True)

    CATEGORY       = db.Column(db.String(40), nullable=False)   # e.g. 'audio_quality'
    PARAMETER      = db.Column(db.String(80), nullable=False)   # e.g. 'MISTAKE_LEVEL'
    RAW_VALUE      = db.Column(db.String(255), nullable=True)   # e.g. 'Average'
    NUMERIC_VALUE  = db.Column(db.Float,      nullable=True)    # severity / score in 0–1
    WEIGHT         = db.Column(db.Float,      nullable=True)    # weight in formula
    CONTRIBUTION   = db.Column(db.Float,      nullable=True)    # weight * severity
    REASON         = db.Column(db.String(500), nullable=True)   # short human label

    CREATED_DTS    = db.Column(db.DateTime,   nullable=False, default=datetime.utcnow)

    def to_json(self) -> dict:
        return {
            'id':              self.ID,
            'allocation_id':   self.ALLOCATION_ID,
            'evaluator_id':    self.EVALUATOR_ID,
            'ccaudio_id':      self.CCAUDIO_ID,
            'iap_workfile_id': self.IAP_WORKFILE_ID,
            'stage':           self.STAGE,
            'category':        self.CATEGORY,
            'parameter':       self.PARAMETER,
            'raw_value':       self.RAW_VALUE,
            'numeric_value':   self.NUMERIC_VALUE,
            'weight':          self.WEIGHT,
            'contribution':    self.CONTRIBUTION,
            'reason':          self.REASON,
            'created_dts':     self.CREATED_DTS.isoformat() if self.CREATED_DTS else None,
        }

    @classmethod
    def bulk_record(cls, allocation_id: int, decisions: list,
                    evaluator_id: int = None, ccaudio_id: int = None,
                    iap_workfile_id: int = None, stage: str = None) -> list:
        """
        Create N CdmAllocationDecision rows from a list of plain dicts.

        Each dict may contain: category, parameter, raw_value, numeric_value,
        weight, contribution, reason.

        Returns the created (un-flushed) ORM objects so the caller can
        commit them inside the same transaction as the parent allocation.
        """
        rows = []
        for d in decisions:
            row = cls(
                ALLOCATION_ID   = allocation_id,
                EVALUATOR_ID    = evaluator_id,
                CCAUDIO_ID      = ccaudio_id,
                IAP_WORKFILE_ID = iap_workfile_id,
                STAGE           = stage,
                CATEGORY        = d.get('category')  or 'general',
                PARAMETER       = (d.get('parameter') or '')[:80],
                RAW_VALUE       = (str(d['raw_value'])[:255] if d.get('raw_value') is not None else None),
                NUMERIC_VALUE   = d.get('numeric_value'),
                WEIGHT          = d.get('weight'),
                CONTRIBUTION    = d.get('contribution'),
                REASON          = (d.get('reason') or '')[:500] or None,
            )
            db.session.add(row)
            rows.append(row)
        return rows
