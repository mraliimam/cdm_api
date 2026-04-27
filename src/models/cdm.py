"""
CDM-owned tables (written by cdm_api).

CdmAllocation        — one row per allocation decision (predicted + actual metrics)
CdmEffortBaseline    — per-workfile effort baseline cache
CdmProvisionJob      — async tracking of L4 audio provisioning jobs

The detailed per-parameter rationale lives in CdmAllocationDecision
(allocation_decision.py) and per-evaluator-on-user history lives in
CdmEvaluatorPerformance (evaluator_performance.py).

`db` is imported from src.extensions to avoid circular imports between
app.py and the model definitions.
"""

from datetime import datetime

from src.extensions import db
from src import constants


class CdmEffortBaseline(db.Model):
    """Per-workfile effort baseline derived from historical completion data."""

    __tablename__ = 'CC_EFFORT_BASELINE'

    ID               = db.Column(db.Integer,   primary_key=True, autoincrement=True)
    IAP_WORKFILE_ID  = db.Column(db.Integer,   nullable=False, unique=True)
    STAGE            = db.Column(db.String(5), nullable=True)
    SAMPLE_COUNT     = db.Column(db.Integer,   nullable=True)
    TOTAL_MINS_DATA  = db.Column(db.Float,     nullable=True)
    BASELINE_EFFORT  = db.Column(db.Float,     nullable=True)
    LAST_UPDATED_DTS = db.Column(db.DateTime,  nullable=True, default=datetime.utcnow)

    def to_json(self) -> dict:
        return {
            'id':               self.ID,
            'iap_workfile_id':  self.IAP_WORKFILE_ID,
            'stage':            self.STAGE,
            'sample_count':     self.SAMPLE_COUNT,
            'total_mins_data':  self.TOTAL_MINS_DATA,
            'baseline_effort':  self.BASELINE_EFFORT,
            'last_updated_dts': self.LAST_UPDATED_DTS.isoformat() if self.LAST_UPDATED_DTS else None,
        }


class CdmAllocation(db.Model):
    """One row per file assigned to an evaluator — the admin decision log."""

    __tablename__ = 'CC_CDM_ALLOCATION'

    ID                     = db.Column(db.Integer,    primary_key=True, autoincrement=True)
    EVALUATOR_ID           = db.Column(db.Integer,    nullable=False)
    IAP_WORKFILE_ID        = db.Column(db.Integer,    nullable=True)
    CCAUDIO_ID             = db.Column(db.Integer,    nullable=True)
    USER_ID                = db.Column(db.Integer,    nullable=True)   # student / uploader the audio belongs to
    CDM_MODE               = db.Column(db.String(20), nullable=True)
    STAGE                  = db.Column(db.String(5),  nullable=True)
    ALLOCATION_SCORE       = db.Column(db.Float,      nullable=True)
    PREDICTED_ACCURACY     = db.Column(db.Float,      nullable=True)
    ACTUAL_ACCURACY        = db.Column(db.Float,      nullable=True)
    PREDICTED_SATISFACTION = db.Column(db.Float,      nullable=True)
    ACTUAL_SATISFACTION    = db.Column(db.Float,      nullable=True)
    PREDICTED_EFFORT_MINS  = db.Column(db.Float,      nullable=True)
    ACTUAL_EFFORT_MINS     = db.Column(db.Float,      nullable=True)
    DIFFICULTY_SCORE       = db.Column(db.Float,      nullable=True)
    DIFFICULTY_LEVEL       = db.Column(db.String(10), nullable=True)
    BIAS_FACTOR            = db.Column(db.Float,      nullable=True, default=1.0)
    RATIONALE              = db.Column(db.String(2000), nullable=True)
    STATUS                 = db.Column(db.String(20), nullable=True, default='pending')
    ALLOCATED_DTS          = db.Column(db.DateTime,   nullable=True, default=datetime.utcnow)
    COMPLETED_DTS          = db.Column(db.DateTime,   nullable=True)
    ETL_ADD_DTS            = db.Column(db.DateTime,   nullable=True, default=datetime.utcnow)

    def to_json(self) -> dict:
        return {
            'id':                     self.ID,
            'evaluator_id':           self.EVALUATOR_ID,
            'iap_workfile_id':        self.IAP_WORKFILE_ID,
            'ccaudio_id':             self.CCAUDIO_ID,
            'user_id':                self.USER_ID,
            'cdm_mode':               self.CDM_MODE,
            'stage':                  self.STAGE,
            'allocation_score':       self.ALLOCATION_SCORE,
            'predicted_accuracy':     self.PREDICTED_ACCURACY,
            'actual_accuracy':        self.ACTUAL_ACCURACY,
            'predicted_satisfaction': self.PREDICTED_SATISFACTION,
            'actual_satisfaction':    self.ACTUAL_SATISFACTION,
            'predicted_effort_mins':  self.PREDICTED_EFFORT_MINS,
            'actual_effort_mins':     self.ACTUAL_EFFORT_MINS,
            'difficulty_score':       self.DIFFICULTY_SCORE,
            'difficulty_level':       self.DIFFICULTY_LEVEL or constants.difficulty_label(self.DIFFICULTY_SCORE),
            'bias_factor':            self.BIAS_FACTOR,
            'rationale':              self.RATIONALE,
            'status':                 self.STATUS,
            'allocated_dts':          self.ALLOCATED_DTS.isoformat() if self.ALLOCATED_DTS else None,
            'completed_dts':          self.COMPLETED_DTS.isoformat() if self.COMPLETED_DTS else None,
        }

    def to_performance_dict(self) -> dict:
        """
        Map to the performance.csv schema expected by on_demand_allocator.fit().
        Defaults are filled in for incomplete rows so training never crashes.
        """
        return {
            'performance_id':       self.ID,
            'evaluator_id':         self.EVALUATOR_ID,
            'sample_id':            self.IAP_WORKFILE_ID or self.CCAUDIO_ID or self.ID,
            'user_id':              self.USER_ID or 0,
            'difficulty_score':     self.DIFFICULTY_SCORE if self.DIFFICULTY_SCORE is not None else 50.0,
            'difficulty_level':     self.DIFFICULTY_LEVEL or constants.difficulty_label(self.DIFFICULTY_SCORE),
            'actual_accuracy':      self.ACTUAL_ACCURACY     if self.ACTUAL_ACCURACY     is not None else 0.85,
            'satisfaction_level':   self.ACTUAL_SATISFACTION if self.ACTUAL_SATISFACTION is not None else 0.75,
            'effort_minutes_spent': self.ACTUAL_EFFORT_MINS  if self.ACTUAL_EFFORT_MINS  is not None else 5.0,
            'quality_score':        self.ALLOCATION_SCORE    if self.ALLOCATION_SCORE    is not None else 0.0,
            'timestamp':            self.COMPLETED_DTS.strftime('%Y-%m-%d %H:%M:%S') if self.COMPLETED_DTS else '',
            'hour_of_day':          self.COMPLETED_DTS.hour    if self.COMPLETED_DTS else 9,
            'day_of_week':          self.COMPLETED_DTS.weekday() if self.COMPLETED_DTS else 0,
            'sequence_number':      self.ID,
        }


class CdmProvisionJob(db.Model):
    """
    Tracks an async L4 provisioning job.

    Created when POST /request_workfiles is called so the API can return
    202 + job_id within the API Gateway 29-second timeout. The actual
    audio download / convert / upload runs in a background Lambda
    invocation and updates STATUS + RESULT_JSON when done.
    """

    __tablename__ = 'CC_CDM_PROVISION_JOB'

    ID           = db.Column(db.Integer,    primary_key=True, autoincrement=True)
    EVALUATOR_ID = db.Column(db.Integer,    nullable=False)
    STAGE        = db.Column(db.String(5),  nullable=False)
    N_FILES      = db.Column(db.Integer,    nullable=False, default=2)
    BIAS_FACTOR  = db.Column(db.Float,      nullable=False, default=1.0)
    STATUS       = db.Column(db.String(20), nullable=False, default='pending')
    RESULT_JSON  = db.Column(db.Text,       nullable=True)
    ERROR_MSG    = db.Column(db.String(500), nullable=True)
    CREATED_DTS  = db.Column(db.DateTime,   nullable=False, default=datetime.utcnow)
    UPDATED_DTS  = db.Column(db.DateTime,   nullable=False, default=datetime.utcnow,
                             onupdate=datetime.utcnow)

    def to_json(self) -> dict:
        import json as _json
        return {
            'job_id':       self.ID,
            'evaluator_id': self.EVALUATOR_ID,
            'stage':        self.STAGE,
            'status':       self.STATUS,
            'result':       _json.loads(self.RESULT_JSON) if self.RESULT_JSON else None,
            'error':        self.ERROR_MSG,
            'created_dts':  self.CREATED_DTS.isoformat() if self.CREATED_DTS else None,
            'updated_dts':  self.UPDATED_DTS.isoformat() if self.UPDATED_DTS else None,
        }
