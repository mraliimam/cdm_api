

from app import db
from datetime import datetime, timedelta, timezone

import constants
from models.cc_audio import CCAudio
from sqlalchemy.orm import relationship


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
    USER_STAGE = db.Column(db.String(50), nullable=True)
    CC_AUDIO_ID = db.Column(db.String(50), nullable=True)
    AA_RECORD_ID = db.Column(db.String(50), nullable=True)
    TEST_STATIC_ID = db.Column(db.Integer, nullable=True)
    LAST_MOVED_DT = db.Column(db.String(50), nullable=True, default=datetime.utcnow)
    LAST_MOVED_BY = db.Column(db.String(50), nullable=True)
    CURR_ROW_FL = db.Column(db.String(1), nullable=True)
    TO_DO_MOVED_DTS = db.Column(db.String(50), nullable=True)
    IN_PROGRESS_MOVED_DTS = db.Column(db.String(50), nullable=True)
    COMPLETED_MOVED_DTS = db.Column(db.String(50), nullable=True)
    REVIEW_FL           = db.Column(db.String(1),  nullable=True)   # 'Y'/'N' review flag
    ALLOCATION_ID       = db.Column(db.Integer, nullable=True)

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
            'test_static_id':self.TEST_STATIC_ID,
            'date': str(self.LAST_MOVED_DT),
            'duration': duration,
            'CC_AUDIO_ID':self.CC_AUDIO_ID,
            'user_stage':self.USER_STAGE,
            'ALLOCATION_ID':self.ALLOCATION_ID
        }

    def get_json(self):
        return {
            'id': self.ID,
            'AUDIO_FILEPATH': self.split_path(self.AUDIO_FILEPATH),
            'MODELPRED_FILEPATH': self.split_path(self.MODELPRED_FILEPATH),
            'FILESAVE_FILEPATH': self.split_path(self.FILESAVE_FILEPATH),
        }

    def split_path(self, path):
        spl_Path = path.split("\\")

        if len(spl_Path) >= 5:
            unx_path = '\\'.join(spl_Path[:5])
        else:
            unx_path = path

        return unx_path
    def to_recording_dict(self, cc_audio=None) -> dict:
        """
        Map to the schema expected by on_demand_allocator.py.
        Audio quality params come from the linked CdmAudio row.
        """
        def _f(val, default=0.5):
            try:
                return float(val) if val is not None else default
            except (ValueError, TypeError):
                return default

        base_effort = 5.0  # fallback
        difficulty  = None
        difficulty_level = 'medium'
        recording_time = 5.0
        user_id = 0
        audio_params = {
            'mistake_level': 0.5,
            'background_noise_level': 0.5,
            'repeats_pauses_stutter_level': 0.5,
            'audio_issues_level': 0.5,
            'recitation_speed': 0.5,
        }

        if cc_audio:
            base_effort      = cc_audio.BASE_EFFORT_MINUTE or 5.0
            difficulty       = cc_audio.DIFFICULTY_SCORE
            difficulty_level = cc_audio.DIFFICULTY_LEVEL or 'medium'
            recording_time   = _f(cc_audio.DURATION, 5.0)
            user_id = cc_audio.UNKNOWN_USER_ID_1 or cc_audio.STUDENT_ID_1 or 0
            audio_params = {
                'mistake_level':              _f(cc_audio.MISTAKE_LEVEL),
                'background_noise_level':     _f(cc_audio.BACKGROUND_NOISE_LEVEL),
                'repeats_pauses_stutter_level': _f(cc_audio.REPEATS_PAUSES_STUTTER_LEVEL),
                'audio_issues_level':         _f(cc_audio.AUDIO_ISSUES_LEVEL),
                'recitation_speed':           _f(cc_audio.RECITATION_SPEED),
            }

        # L3 files are smaller → lower default effort
        if self.STAGE == 'L3' and base_effort == 5.0:
            base_effort = 2.0

        return {
            'sample_id':          self.ID,           # workfile ID (not CC_AUDIO.ID)
            'user_id':            user_id,
            'base_effort_minute': base_effort,
            'recording_time':     recording_time,
            'difficulty_score':   difficulty,
            'difficulty_level':   difficulty_level,
            **audio_params,
        }

    def to_response_extras(self, cc_audio=None) -> dict:
        """
        Return all workfile + CC_AUDIO fields needed to build the API response.
        These are kept out of the allocator DataFrame to avoid pandas type
        coercion (nested dicts, None→NaN, etc.).
        """
        return {
            '_workfile_name':     self.WORKFILE_NAME,
            '_audio_filepath':    self.AUDIO_FILEPATH,
            '_filesave_filepath': self.FILESAVE_FILEPATH,
            '_modelpred_filepath': self.MODELPRED_FILEPATH,
            '_stage':             self.STAGE,
            '_cc_audio_id':       self.CC_AUDIO_ID,
            '_last_moved_dt':     str(self.LAST_MOVED_DT) if self.LAST_MOVED_DT else None,
            '_test_static_id':    self.TEST_STATIC_ID,
            '_user_stage':        self.USER_STAGE,
            '_duration_label':    _classify_duration(cc_audio.DURATION if cc_audio else None),
            '_audio_data':        None,   # populated lazily for selected items only
        }
    

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