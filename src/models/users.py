"""
AA_IAP_USERS — IAP evaluator accounts (extended with CDM_* capacity columns).

CDM columns (CDM_*) are additional columns added to AA_IAP_USERS by the
CDM service so per-evaluator capacity targets can be stored and read
directly without having to re-derive them from USER_STAGE on every call.

When a CDM column is NULL the value falls back to a USER_STAGE-based default
to guarantee the allocator always has a number to work with.
"""

from datetime import datetime

from src.extensions import db


class AAIAPUSERS(db.Model):
    __tablename__ = 'AA_IAP_USERS'

    ID                = db.Column(db.Integer,    primary_key=True)
    USERNAME          = db.Column(db.String(50), nullable=True)
    PASSWORD          = db.Column(db.String(200), nullable=True)
    DATE_ADDED        = db.Column(db.String(50), nullable=True, default=datetime.utcnow)
    STATUS            = db.Column(db.String(50), nullable=True)
    FIRST_NAME        = db.Column(db.String(50), nullable=True)
    LAST_NAME         = db.Column(db.String(50), nullable=True)
    ACCESS_TO_PROD_FL = db.Column(db.String(1),  nullable=True)
    FOLDER_SETUP_FL   = db.Column(db.String(1),  nullable=True)
    USER_STAGE        = db.Column(db.String(50), nullable=True)
    SESSION_STATUS    = db.Column(db.String(50), nullable=True, default='LOGGED_OUT')
    LAST_ACTIVITY     = db.Column(db.String(50), nullable=True, default=datetime.utcnow)
    LOG_IN_DTS        = db.Column(db.String(50), nullable=True, default=datetime.utcnow)
    PAYMENT_RATIO     = db.Column(db.Float,      nullable=True)

    # CDM capacity columns
    CDM_AVAILABLE_EFFORT_MINUTE = db.Column(db.Float,      nullable=True)
    CDM_WEEKLY_EFFORT_LIMIT     = db.Column(db.Float,      nullable=True)
    CDM_ACCURACY_TARGET         = db.Column(db.Float,      nullable=True)
    CDM_SKILL_LEVEL             = db.Column(db.String(50), nullable=True)
    CDM_EXPERIENCE_YEARS        = db.Column(db.Integer,    nullable=True)
    CDM_IS_ACTIVE_FL            = db.Column(db.String(1),  nullable=True, default='Y')

    # Stage-based fallback defaults (used when CDM columns are NULL)
    _STAGE_CAPACITY: dict = {
        'L1':   {'effort': 20.0, 'limit': 120.0, 'accuracy': 0.70, 'skill': 'junior',       'exp': 0},
        'L2':   {'effort': 25.0, 'limit': 150.0, 'accuracy': 0.73, 'skill': 'junior',       'exp': 1},
        'L3':   {'effort': 30.0, 'limit': 180.0, 'accuracy': 0.75, 'skill': 'intermediate', 'exp': 1},
        'L4':   {'effort': 35.0, 'limit': 200.0, 'accuracy': 0.80, 'skill': 'senior',       'exp': 2},
        'TEAM': {'effort': 40.0, 'limit': 240.0, 'accuracy': 0.85, 'skill': 'lead',         'exp': 3},
        'ADMI': {'effort': 40.0, 'limit': 240.0, 'accuracy': 0.90, 'skill': 'admin',        'exp': 5},
    }
    _DEFAULT_CAPACITY: dict = {
        'effort': 30.0, 'limit': 200.0, 'accuracy': 0.75,
        'skill': 'intermediate', 'exp': 1,
    }

    def _stage_defaults(self) -> dict:
        stage = (self.USER_STAGE or '').upper()
        for prefix, caps in self._STAGE_CAPACITY.items():
            if stage.startswith(prefix):
                return caps
        return self._DEFAULT_CAPACITY

    def to_json(self) -> dict:
        return {
            'id':             self.ID,
            'user_name':      self.USERNAME,
            'date_added':     str(self.DATE_ADDED) if self.DATE_ADDED else None,
            'status':         self.STATUS,
            'session_status': self.SESSION_STATUS,
            'first_name':     self.FIRST_NAME,
            'last_name':      self.LAST_NAME,
            'user_stage':     self.USER_STAGE,
            'payment_ratio':  self.PAYMENT_RATIO,
            'cdm_available_effort_minute': self.CDM_AVAILABLE_EFFORT_MINUTE,
            'cdm_weekly_effort_limit':     self.CDM_WEEKLY_EFFORT_LIMIT,
            'cdm_accuracy_target':         self.CDM_ACCURACY_TARGET,
            'cdm_skill_level':             self.CDM_SKILL_LEVEL,
            'cdm_experience_years':        self.CDM_EXPERIENCE_YEARS,
            'cdm_is_active_fl':            self.CDM_IS_ACTIVE_FL,
        }

    def to_evaluator_dict(self) -> dict:
        """
        Map to the schema expected by on_demand_allocator.py.
        CDM columns take precedence; USER_STAGE-based defaults fill any NULLs.
        """
        d = self._stage_defaults()
        return {
            'evaluator_id':            self.ID,
            'available_effort_minute': self.CDM_AVAILABLE_EFFORT_MINUTE if self.CDM_AVAILABLE_EFFORT_MINUTE is not None else d['effort'],
            'weekly_effort_limit':     self.CDM_WEEKLY_EFFORT_LIMIT     if self.CDM_WEEKLY_EFFORT_LIMIT     is not None else d['limit'],
            'accuracy_target':         self.CDM_ACCURACY_TARGET         if self.CDM_ACCURACY_TARGET         is not None else d['accuracy'],
            'skill_level':             self.CDM_SKILL_LEVEL             if self.CDM_SKILL_LEVEL             is not None else d['skill'],
            'experience_years':        self.CDM_EXPERIENCE_YEARS        if self.CDM_EXPERIENCE_YEARS        is not None else d['exp'],
        }
