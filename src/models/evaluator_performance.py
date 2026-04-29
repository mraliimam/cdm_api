"""
CC_CDM_EVALUATOR_PERFORMANCE — historical evaluator-on-user performance.

Captures, per (evaluator, audio-uploader/student user) pair, the running
performance signals we use to predict future accuracy / satisfaction /
effort minutes for the SAME pair.  This is what makes the CDM effort
prediction "personalised" — instead of using only the evaluator's
overall numbers, we adjust by the evaluator's track record on this
specific user.

Two ways of using this table:
  1. As a write-side aggregate: every time POST /cdm/complete logs an
     allocation, we update the (evaluator_id, user_id) row with the
     latest measured numbers.
  2. As a read-side cache: the allocator queries it to derive
     personalised predictions — falling back to evaluator-wide and
     stage-wide defaults when no row exists yet.

Each row also stores the LAST measured values + a rolling sample count,
so the predictor can apply a confidence weight (more samples → trust the
specific pair more, fewer → trust the global evaluator profile more).
"""

from datetime import datetime

from extensions import db


class CdmEvaluatorPerformance(db.Model):
    """Per (evaluator, user) running performance aggregate."""

    __tablename__ = 'CC_CDM_EVALUATOR_PERFORMANCE'
    __table_args__ = (
        db.UniqueConstraint('EVALUATOR_ID', 'USER_ID', name='uq_eval_user'),
    )

    ID                   = db.Column(db.Integer,    primary_key=True, autoincrement=True)
    EVALUATOR_ID         = db.Column(db.Integer,    nullable=False, index=True)
    USER_ID              = db.Column(db.Integer,    nullable=False, index=True)

    # Running aggregates
    SAMPLE_COUNT         = db.Column(db.Integer,    nullable=False, default=0)
    AVG_ACCURACY         = db.Column(db.Float,      nullable=True)   # 0–1
    AVG_SATISFACTION     = db.Column(db.Float,      nullable=True)   # 0–1
    AVG_EFFORT_MINUTES   = db.Column(db.Float,      nullable=True)
    AVG_DIFFICULTY_SCORE = db.Column(db.Float,      nullable=True)   # 0–100

    # Last-seen values (handy for sanity checks in the admin UI)
    LAST_ACCURACY        = db.Column(db.Float,      nullable=True)
    LAST_SATISFACTION    = db.Column(db.Float,      nullable=True)
    LAST_EFFORT_MINUTES  = db.Column(db.Float,      nullable=True)
    LAST_COMPLETED_DTS   = db.Column(db.DateTime,   nullable=True)
    FIRST_SEEN_DTS       = db.Column(db.DateTime,   nullable=False, default=datetime.utcnow)
    UPDATED_DTS          = db.Column(db.DateTime,   nullable=False, default=datetime.utcnow,
                                     onupdate=datetime.utcnow)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @classmethod
    def get_or_create(cls, evaluator_id: int, user_id: int) -> 'CdmEvaluatorPerformance':
        rec = cls.query.filter_by(
            EVALUATOR_ID=evaluator_id, USER_ID=user_id
        ).first()
        if rec is None:
            rec = cls(EVALUATOR_ID=evaluator_id, USER_ID=user_id, SAMPLE_COUNT=0)
            db.session.add(rec)
        return rec

    @classmethod
    def lookup(cls, evaluator_id: int, user_id: int) -> 'CdmEvaluatorPerformance':
        """Return the row if one exists; otherwise None."""
        if not evaluator_id or not user_id:
            return None
        return cls.query.filter_by(
            EVALUATOR_ID=evaluator_id, USER_ID=user_id
        ).first()

    def update_with_completion(self, *, accuracy: float, satisfaction: float,
                               effort_minutes: float,
                               difficulty_score: float = None,
                               completed_dts: datetime = None) -> None:
        """
        Roll the new completion into the running averages.

        Uses an incremental average so we never have to scan history.
        Missing values keep their previous aggregate untouched.
        """
        n = self.SAMPLE_COUNT or 0
        new_n = n + 1

        def _running_avg(prev, new_val):
            if new_val is None:
                return prev
            if prev is None:
                return float(new_val)
            return (float(prev) * n + float(new_val)) / new_n

        self.AVG_ACCURACY         = _running_avg(self.AVG_ACCURACY,         accuracy)
        self.AVG_SATISFACTION     = _running_avg(self.AVG_SATISFACTION,     satisfaction)
        self.AVG_EFFORT_MINUTES   = _running_avg(self.AVG_EFFORT_MINUTES,   effort_minutes)
        self.AVG_DIFFICULTY_SCORE = _running_avg(self.AVG_DIFFICULTY_SCORE, difficulty_score)

        if accuracy is not None:
            self.LAST_ACCURACY = float(accuracy)
        if satisfaction is not None:
            self.LAST_SATISFACTION = float(satisfaction)
        if effort_minutes is not None:
            self.LAST_EFFORT_MINUTES = float(effort_minutes)

        self.LAST_COMPLETED_DTS = completed_dts or datetime.utcnow()
        self.SAMPLE_COUNT = new_n

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_json(self) -> dict:
        return {
            'id':                   self.ID,
            'evaluator_id':         self.EVALUATOR_ID,
            'user_id':              self.USER_ID,
            'sample_count':         self.SAMPLE_COUNT,
            'avg_accuracy':         self.AVG_ACCURACY,
            'avg_satisfaction':     self.AVG_SATISFACTION,
            'avg_effort_minutes':   self.AVG_EFFORT_MINUTES,
            'avg_difficulty_score': self.AVG_DIFFICULTY_SCORE,
            'last_accuracy':        self.LAST_ACCURACY,
            'last_satisfaction':    self.LAST_SATISFACTION,
            'last_effort_minutes':  self.LAST_EFFORT_MINUTES,
            'last_completed_dts':   self.LAST_COMPLETED_DTS.isoformat() if self.LAST_COMPLETED_DTS else None,
            'first_seen_dts':       self.FIRST_SEEN_DTS.isoformat() if self.FIRST_SEEN_DTS else None,
            'updated_dts':          self.UPDATED_DTS.isoformat() if self.UPDATED_DTS else None,
        }

    def to_predictor_dict(self) -> dict:
        """
        Compact dict used by on_demand_allocator to derive personalised
        predictions for an (evaluator, user) pair.
        """
        return {
            'sample_count':         self.SAMPLE_COUNT or 0,
            'avg_accuracy':         self.AVG_ACCURACY,
            'avg_satisfaction':     self.AVG_SATISFACTION,
            'avg_effort_minutes':   self.AVG_EFFORT_MINUTES,
            'avg_difficulty_score': self.AVG_DIFFICULTY_SCORE,
        }
