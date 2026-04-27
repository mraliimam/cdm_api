class AAIAPUSERS(db.Model):
    """
    AA_IAP_USERS — IAP evaluator accounts.

    IAP columns
    -----------
    Core user-management columns owned by the IAP project.
    Do not rename or remove these without coordinating with the IAP team.

    CDM columns  (CDM_*)
    --------------------
    Added by the CDM service to store per-evaluator capacity targets directly
    on the user row.  All CDM columns are nullable; the CDM service falls back
    to USER_STAGE-derived defaults when they are NULL.

    CDM_AVAILABLE_EFFORT_MINUTE
        Minutes of annotation work the evaluator can accept right now.
        Used by OnDemandAllocator to enforce effort constraints per allocation.

    CDM_WEEKLY_EFFORT_LIMIT
        Hard cap on total effort minutes per week.
        OnDemandAllocator checks this before assigning a new workfile and skips
        the evaluator if the cap would be exceeded.

    CDM_ACCURACY_TARGET
        Minimum acceptable weekly accuracy score (float 0–1, e.g. 0.80).
        Allocations that would push the running weekly average below this
        threshold are deprioritised (constraint violation in the allocator).

    CDM_SKILL_LEVEL
        Human-readable skill label included in allocation rationale strings.
        Expected values: junior | intermediate | senior | lead | admin.

    CDM_EXPERIENCE_YEARS
        Integer years of experience.  Used as a tiebreaker in allocation
        scoring when two candidates have otherwise equal scores.

    CDM_IS_ACTIVE_FL
        'Y' / 'N' flag.  The CDM service only allocates workfiles to
        evaluators where CDM_IS_ACTIVE_FL = 'Y'.  Defaults to 'Y' so that
        existing rows remain eligible after the column is added.
    """
    __tablename__ = 'AA_IAP_USERS'

    # ---- IAP columns ----------------------------------------------------------
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

    # ---- CDM columns ----------------------------------------------------------
    CDM_AVAILABLE_EFFORT_MINUTE = db.Column(db.Float,      nullable=True)
    CDM_WEEKLY_EFFORT_LIMIT     = db.Column(db.Float,      nullable=True)
    CDM_ACCURACY_TARGET         = db.Column(db.Float,      nullable=True)
    CDM_SKILL_LEVEL             = db.Column(db.String(50), nullable=True)
    CDM_EXPERIENCE_YEARS        = db.Column(db.Integer,    nullable=True)
    CDM_IS_ACTIVE_FL            = db.Column(db.String(1),  nullable=True, default='Y')

    def to_json(self):
        return {
            'id':             self.ID,
            'user_name':      self.USERNAME,
            'date_added':     self.DATE_ADDED,
            'status':         self.STATUS,
            'session_status': self.SESSION_STATUS,
            'first_name':     self.FIRST_NAME,
            'last_name':      self.LAST_NAME,
            'user_stage':     self.USER_STAGE,
            'payment_ratio':  self.PAYMENT_RATIO,
            # CDM fields
            'cdm_available_effort_minute': self.CDM_AVAILABLE_EFFORT_MINUTE,
            'cdm_weekly_effort_limit':     self.CDM_WEEKLY_EFFORT_LIMIT,
            'cdm_accuracy_target':         self.CDM_ACCURACY_TARGET,
            'cdm_skill_level':             self.CDM_SKILL_LEVEL,
            'cdm_experience_years':        self.CDM_EXPERIENCE_YEARS,
            'cdm_is_active_fl':            self.CDM_IS_ACTIVE_FL,
        }