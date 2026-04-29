from marshmallow import Schema, fields, validate
from app import db
from datetime import datetime, timedelta, timezone


class CdmRequestFileSchema(Schema):
    """
    On-Demand mode — one IAP user clicks 'Request File'.
    The evaluator profile is fetched from AA_IAP_USERS by evaluator_id.
    """
    evaluator_id       = fields.Integer(required=True)         # AA_IAP_USERS.ID
    stage              = fields.String(required=True,
                             validate=validate.OneOf(['L3', 'L4']))
    num_files          = fields.Integer(load_default=2,
                             validate=validate.Range(min=1, max=10))
    effort_bias_factor = fields.Float(load_default=1.0)   
class CdmCompleteSchema(Schema):
    """Mark a CDM-allocated workfile as completed; log actual measured metrics."""
    allocation_id       = fields.Integer(required=True)
    actual_accuracy     = fields.Float(required=True)
    actual_satisfaction = fields.Float(required=True)
    actual_effort_mins  = fields.Float(required=True)


class CdmEffortBaseline(db.Model):
    """Per-workfile effort baseline derived from historical completion data."""
    __tablename__ = 'CC_EFFORT_BASELINE'

    ID               = db.Column(db.Integer,  primary_key=True, autoincrement=True)
    IAP_WORKFILE_ID  = db.Column(db.Integer,  nullable=False, unique=True)
    STAGE            = db.Column(db.String(5), nullable=True)
    SAMPLE_COUNT     = db.Column(db.Integer,  nullable=True)
    TOTAL_MINS_DATA  = db.Column(db.Float,    nullable=True)
    BASELINE_EFFORT  = db.Column(db.Float,    nullable=True)
    LAST_UPDATED_DTS = db.Column(db.DateTime, nullable=True, default=datetime.utcnow)

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
    AA_RECORD_ID           = db.Column(db.Integer,    nullable=True)
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
            'id':                    self.ID,
            'evaluator_id':          self.EVALUATOR_ID,
            'iap_workfile_id':       self.IAP_WORKFILE_ID,
            'ccaudio_id':            self.CCAUDIO_ID,
            'aa_record_id':          self.AA_RECORD_ID,
            'cdm_mode':              self.CDM_MODE,
            'stage':                 self.STAGE,
            'user_id':              self.USER_ID,
            'allocation_score':      self.ALLOCATION_SCORE,
            'predicted_accuracy':    self.PREDICTED_ACCURACY,
            'actual_accuracy':       self.ACTUAL_ACCURACY,
            'predicted_satisfaction': self.PREDICTED_SATISFACTION,
            'actual_satisfaction':   self.ACTUAL_SATISFACTION,
            'predicted_effort_mins': self.PREDICTED_EFFORT_MINS,
            'actual_effort_mins':    self.ACTUAL_EFFORT_MINS,
            'difficulty_score':      self.DIFFICULTY_SCORE,
            'bias_factor':           self.BIAS_FACTOR,
            'rationale':             self.RATIONALE,
            'status':                self.STATUS,
            'allocated_dts':         self.ALLOCATED_DTS.isoformat() if self.ALLOCATED_DTS else None,
            'completed_dts':         self.COMPLETED_DTS.isoformat() if self.COMPLETED_DTS else None,
        }

    def to_performance_dict(self) -> dict:
        """Map to the performance.csv schema expected by on_demand_allocator."""
        return {
            'performance_id':     self.ID,
            'evaluator_id':       self.EVALUATOR_ID,
            'sample_id':          self.IAP_WORKFILE_ID or self.CCAUDIO_ID or self.ID,
            'user_id':            0,
            'difficulty_score':   self.DIFFICULTY_SCORE or 50.0,
            'difficulty_level':   _difficulty_label(self.DIFFICULTY_SCORE),
            'actual_accuracy':    self.ACTUAL_ACCURACY or 0.85,
            'satisfaction_level': self.ACTUAL_SATISFACTION or 0.75,
            'effort_minutes_spent': self.ACTUAL_EFFORT_MINS or 5.0,
            'quality_score':      self.ALLOCATION_SCORE or 0.0,
            'timestamp':          self.COMPLETED_DTS.strftime('%Y-%m-%d %H:%M:%S') if self.COMPLETED_DTS else '',
            'hour_of_day':        self.COMPLETED_DTS.hour if self.COMPLETED_DTS else 9,
            'day_of_week':        self.COMPLETED_DTS.weekday() if self.COMPLETED_DTS else 0,
            'sequence_number':    self.ID,
        }

class CdmProvisionJob(db.Model):
    """
    Tracks an async L4 provisioning job.

    Created immediately when POST /request_workfiles is called so the API
    can return 202 + job_id within the API Gateway 29-second timeout.
    The actual audio download / convert / upload runs in a background Lambda
    invocation and updates STATUS + RESULT_JSON when done.
    """
    __tablename__ = 'CC_CDM_PROVISION_JOB'

    ID             = db.Column(db.Integer,    primary_key=True, autoincrement=True)
    EVALUATOR_ID   = db.Column(db.Integer,    nullable=False)
    STAGE          = db.Column(db.String(5),  nullable=False)
    N_FILES        = db.Column(db.Integer,    nullable=False, default=2)
    BIAS_FACTOR    = db.Column(db.Float,      nullable=False, default=1.0)
    # pending → processing → done | failed
    STATUS         = db.Column(db.String(20), nullable=False, default='pending')
    RESULT_JSON    = db.Column(db.Text,       nullable=True)   # serialised assignments list
    ERROR_MSG      = db.Column(db.String(500), nullable=True)
    CREATED_DTS    = db.Column(db.DateTime,   nullable=False, default=datetime.utcnow)
    UPDATED_DTS    = db.Column(db.DateTime,   nullable=False, default=datetime.utcnow,
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


def _difficulty_label(score) -> str:
    if score is None:
        return 'medium'
    if score < 40:
        return 'low'
    if score < 70:
        return 'medium'
    return 'hard'

def _classify_duration(duration_str) -> str:
    """Classify a raw duration string (seconds) into short / medium / long."""
    try:
        secs = float(duration_str)
    except (TypeError, ValueError):
        return None
    if secs < 60:
        return 'short'
    if secs <= 360:
        return 'medium'
    return 'long'