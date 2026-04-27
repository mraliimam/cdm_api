class AAIAPWORKFILE(db.Model):
    __tablename__ = 'AA_IAP_WORKFILE'
    ID = db.Column(db.Integer, primary_key=True)
    WORKFILE_NAME = db.Column(db.String(50), nullable=True)
    WORKFILE_STATUS = db.Column(db.String(50), nullable=True)
    ASSIGNED_USER_ID = db.Column(db.Integer, db.ForeignKey('AA_IAP_USERS.ID'), nullable=True, )
    AUDIO_FILEPATH = db.Column(db.String(50), nullable=True)
    AUDIO_STATUS = db.Column(db.String(50), nullable=True)
    AUDIO_KEY = db.Column(db.String(200), nullable=True)
    FILESAVE_FILEPATH = db.Column(db.String(50), nullable=True)
    FILESAVE_STATUS = db.Column(db.String(50), nullable=True)
    MODELPRED_FILEPATH = db.Column(db.String(50), nullable=True)
    MODELPRED_STATUS = db.Column(db.String(50), nullable=True)
    STAGE = db.Column(db.String(50), nullable=True)
    CC_AUDIO_ID = db.Column(db.String(50), nullable=True)
    AA_RECORD_ID = db.Column(db.String(50), nullable=True)
    TEST_STATIC_ID = db.Column(db.String(50), nullable=True)
    LAST_MOVED_DT = db.Column(db.String(50), nullable=True, default=datetime.utcnow)
    LAST_MOVED_BY = db.Column(db.String(50), nullable=True)
    CURR_ROW_FL = db.Column(db.String(1), nullable=True)
    TO_DO_MOVED_DTS = db.Column(db.String(50), nullable=True)
    IN_PROGRESS_MOVED_DTS = db.Column(db.String(50), nullable=True)
    COMPLETED_MOVED_DTS = db.Column(db.String(50), nullable=True)

    user = relationship('AAIAPUSERS', backref='AAIAPWORKFILE')

    def to_json(self):
        ccaudio = CCAudio.query.filter_by(ID = self.CC_AUDIO_ID).first()
        duration = constants.classify_duration(float(ccaudio.DURATION) if ccaudio else None) if ccaudio else None
        return {
            'id': self.ID,
            'WORKFILE_NAME': self.WORKFILE_NAME,
            'AUDIO_FILEPATH': self.split_path(self.AUDIO_FILEPATH),
            'MODELPRED_FILEPATH': self.split_path(self.MODELPRED_FILEPATH),
            'FILESAVE_FILEPATH': self.split_path(self.FILESAVE_FILEPATH),
            'user_id': self.ASSIGNED_USER_ID,
            'date': str(self.LAST_MOVED_DT),
            'duration': duration,
            'CC_AUDIO_ID':self.CC_AUDIO_ID
        }

    def get_json(self):
        return {
            'id': self.ID,
            'AUDIO_FILEPATH': self.split_path(self.AUDIO_FILEPATH),
            'MODELPRED_FILEPATH': self.split_path(self.MODELPRED_FILEPATH),
            'FILESAVE_FILEPATH': self.split_path(self.FILESAVE_FILEPATH),
        }
    




    
class CCAudio(db.Model):
    __tablename__ = 'CC_AUDIO'
    id = db.Column(db.Integer, primary_key=True)
    UPLOADER_ID = db.Column(db.Integer, nullable=True)
    UPLOAD_DATE =   db.Column(db.Date, nullable=True,  default=datetime.datetime.utcnow)
    AUDIO_KEY =  db.Column(db.String(50), nullable=True)
    FILEPATH = db.Column(db.String(50), nullable=True)
    SURAH_1 = db.Column(db.String(50), nullable=True)
    SURAH_2 = db.Column(db.String(50), nullable=True)
    SURAH_3 = db.Column(db.String(50), nullable=True)
    SURAH_4 = db.Column(db.String(50), nullable=True)
    SURAH_5 = db.Column(db.String(50), nullable=True)
    AUDIO_SOURCE = db.Column(db.String(50), nullable=True)
    MISTAKE_LEVEL = db.Column(db.String(50), nullable=True)
    AUDIO_ISSUES_LEVEL = db.Column(db.String(50), nullable=True)
    RECITATION_SPEED = db.Column(db.String(50), nullable=True)
    VOICE_PITCH = db.Column(db.String(50), nullable=True)
    VOICE_LEVEL = db.Column(db.String(50), nullable=True)
    UNKNOWN_USER_FL = db.Column(db.Integer, nullable=True)
    UNKNOWN_USER_ID_1 = db.Column(db.Integer, nullable=True)
    UNKNOWN_USER_ID_2 = db.Column(db.Integer, nullable=True)
    UNKNOWN_USER_ID_3 = db.Column(db.Integer, nullable=True)
    STUDENT_FL = db.Column(db.Integer, nullable=True)
    STUDENT_ID_1 = db.Column(db.Integer, nullable=True)
    STUDENT_ID_2 = db.Column(db.Integer, nullable=True)
    STUDENT_ID_3 = db.Column(db.Integer, nullable=True)
    TEACHER_FL = db.Column(db.Integer, nullable=True)
    TEACHER_ID = db.Column(db.Integer, nullable=True)
    PRO_RECITER_FL = db.Column(db.Integer, nullable=True)
    PRO_RECITER_ID = db.Column(db.Integer, nullable=True)
    ETL_ADD_DTS = db.Column(db.String(50), nullable=True)
    ETL_ROW_PROCESS_DTS = db.Column(db.String(50), nullable=True)
    

    def __repr__(self):
        return '<Item %r>' % self.name