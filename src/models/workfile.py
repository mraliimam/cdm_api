"""
AA_IAP_WORKFILE — IAP's per-evaluator queue.

CDM treats AA_IAP_WORKFILE rows as the candidate pool for L3 stages and
as the destination for L4 audio chunks created by cdm_workfile_service.

`to_recording_dict` and `to_response_extras` produce the shape expected
by the on_demand_allocator and by the API response builder respectively.
"""

from datetime import datetime

from sqlalchemy.orm import relationship

from src.extensions import db
from src import constants
from src.models.cc_audio import CCAudio


def _f(val, default=0.5) -> float:
    """Coerce a value to float with a fallback default."""
    if val is None:
        return default
    try:
        v = float(val)
        if v != v:  # NaN
            return default
        return v
    except (TypeError, ValueError):
        return default


class AAIAPWORKFILE(db.Model):
    __tablename__ = 'AA_IAP_WORKFILE'
    ID                    = db.Column(db.Integer,    primary_key=True)
    WORKFILE_NAME         = db.Column(db.String(50), nullable=True)
    WORKFILE_STATUS       = db.Column(db.String(50), nullable=True)
    ASSIGNED_USER_ID      = db.Column(db.Integer,    db.ForeignKey('AA_IAP_USERS.ID'), nullable=True)
    AUDIO_FILEPATH        = db.Column(db.String(50), nullable=True)
    AUDIO_STATUS          = db.Column(db.String(50), nullable=True)
    AUDIO_KEY             = db.Column(db.String(200), nullable=True)
    FILESAVE_FILEPATH     = db.Column(db.String(50), nullable=True)
    FILESAVE_STATUS       = db.Column(db.String(50), nullable=True)
    MODELPRED_FILEPATH    = db.Column(db.String(50), nullable=True)
    MODELPRED_STATUS      = db.Column(db.String(50), nullable=True)
    STAGE                 = db.Column(db.String(50), nullable=True)
    USER_STAGE            = db.Column(db.String(50), nullable=True)
    CC_AUDIO_ID           = db.Column(db.String(50), nullable=True)
    AA_RECORD_ID          = db.Column(db.String(50), nullable=True)
    TEST_STATIC_ID        = db.Column(db.Integer,    nullable=True)
    LAST_MOVED_DT         = db.Column(db.String(50), nullable=True, default=datetime.utcnow)
    LAST_MOVED_BY         = db.Column(db.String(50), nullable=True)
    CURR_ROW_FL           = db.Column(db.String(1),  nullable=True)
    TO_DO_MOVED_DTS       = db.Column(db.String(50), nullable=True)
    IN_PROGRESS_MOVED_DTS = db.Column(db.String(50), nullable=True)
    COMPLETED_MOVED_DTS   = db.Column(db.String(50), nullable=True)
    REVIEW_FL             = db.Column(db.String(1),  nullable=True)
    ALLOCATION_ID         = db.Column(db.Integer,    nullable=True)
    IS_FILE_UPLOAD        = db.Column(db.String(1),  nullable=True)

    user = relationship('AAIAPUSERS', backref='AAIAPWORKFILE')

    # ------------------------------------------------------------------
    # JSON helpers
    # ------------------------------------------------------------------

    def to_json(self) -> dict:
        ccaudio = (
            CCAudio.query.filter_by(ID=self.CC_AUDIO_ID).first()
            if self.CC_AUDIO_ID else None
        )
        duration_label = constants.classify_duration(
            ccaudio.DURATION if ccaudio else None
        )
        return {
            'id':                  self.ID,
            'WORKFILE_NAME':       self.WORKFILE_NAME,
            'AUDIO_FILEPATH':      self._split_path(self.AUDIO_FILEPATH),
            'MODELPRED_FILEPATH':  self._split_path(self.MODELPRED_FILEPATH),
            'FILESAVE_FILEPATH':   self._split_path(self.FILESAVE_FILEPATH),
            'user_id':             self.ASSIGNED_USER_ID,
            'test_static_id':      self.TEST_STATIC_ID,
            'date':                str(self.LAST_MOVED_DT) if self.LAST_MOVED_DT else None,
            'duration':            duration_label,
            'CC_AUDIO_ID':         self.CC_AUDIO_ID,
            'user_stage':          self.USER_STAGE,
        }

    def get_json(self) -> dict:
        return {
            'id':                 self.ID,
            'AUDIO_FILEPATH':     self._split_path(self.AUDIO_FILEPATH),
            'MODELPRED_FILEPATH': self._split_path(self.MODELPRED_FILEPATH),
            'FILESAVE_FILEPATH':  self._split_path(self.FILESAVE_FILEPATH),
        }

    @staticmethod
    def _split_path(path):
        if not path:
            return path
        spl = path.split('\\')
        return '\\'.join(spl[:5]) if len(spl) >= 5 else path

    # ------------------------------------------------------------------
    # Allocator-facing helpers
    # ------------------------------------------------------------------

    def to_recording_dict(self, cc_audio: CCAudio = None) -> dict:
        """
        Map to the schema expected by on_demand_allocator.py.
        Audio quality params come from the linked CCAudio row.

        All values fall back to safe defaults so an incomplete CC_AUDIO row
        still produces a usable candidate.
        """
        # Fallback / nominal values for incomplete data
        base_effort      = 5.0
        difficulty       = None
        difficulty_level = 'medium'
        recording_time   = 5.0
        user_id          = 0
        audio_params = {
            'mistake_level':                0.5,
            'background_noise_level':       0.5,
            'repeats_pauses_stutter_level': 0.5,
            'audio_issues_level':           0.5,
            'recitation_speed':             0.5,
            'voice_pitch':                  0.5,
            'voice_clarity':                0.5,
            'audio_source':                 0.5,
        }

        if cc_audio:
            base_effort = (cc_audio.BASE_EFFORT_MINUTE
                           if cc_audio.BASE_EFFORT_MINUTE is not None
                           else 5.0)
            difficulty       = cc_audio.DIFFICULTY_SCORE
            difficulty_level = cc_audio.DIFFICULTY_LEVEL or 'medium'
            recording_time   = _f(cc_audio.DURATION, 5.0)
            user_id          = cc_audio.primary_user_id
            # Use the canonical scoring tables to convert string labels to 0–1
            audio_params = {
                'mistake_level':                constants.score_value('MISTAKE_LEVEL', cc_audio.MISTAKE_LEVEL),
                'background_noise_level':       constants.score_value('BACKGROUND_NOISE_LEVEL', cc_audio.BACKGROUND_NOISE_LEVEL),
                'repeats_pauses_stutter_level': constants.score_value('REPEATS_PAUSES_STUTTER_LEVEL', cc_audio.REPEATS_PAUSES_STUTTER_LEVEL),
                'audio_issues_level':           constants.score_value('AUDIO_ISSUES_LEVEL', cc_audio.AUDIO_ISSUES_LEVEL),
                'recitation_speed':             constants.score_value('RECITATION_SPEED', cc_audio.RECITATION_SPEED),
                'voice_pitch':                  constants.score_value('VOICE_PITCH', cc_audio.VOICE_PITCH),
                'voice_clarity':                constants.score_value('VOICE_CLARITY', cc_audio.VOICE_CLARITY),
                'audio_source':                 constants.score_value('AUDIO_SOURCE', cc_audio.AUDIO_SOURCE),
            }

        # L3 files are smaller → lower default effort
        if self.STAGE == 'L3' and base_effort == 5.0:
            base_effort = 2.0

        return {
            'sample_id':          self.ID,
            'user_id':            user_id,
            'base_effort_minute': base_effort,
            'recording_time':     recording_time,
            'difficulty_score':   difficulty,
            'difficulty_level':   difficulty_level,
            'audio_length':       cc_audio.AUDIO_LENGTH if cc_audio else None,
            **audio_params,
        }

    def to_response_extras(self, cc_audio: CCAudio = None) -> dict:
        """Fields needed to build the API response, kept out of the DataFrame."""
        return {
            '_workfile_name':      self.WORKFILE_NAME,
            '_audio_filepath':     self.AUDIO_FILEPATH,
            '_filesave_filepath':  self.FILESAVE_FILEPATH,
            '_modelpred_filepath': self.MODELPRED_FILEPATH,
            '_stage':              self.STAGE,
            '_cc_audio_id':        self.CC_AUDIO_ID,
            '_last_moved_dt':      str(self.LAST_MOVED_DT) if self.LAST_MOVED_DT else None,
            '_test_static_id':     self.TEST_STATIC_ID,
            '_user_stage':         self.USER_STAGE,
            '_duration_label':     constants.classify_duration(cc_audio.DURATION) if cc_audio else None,
            '_audio_data':         cc_audio.to_audio_data_dict() if cc_audio else None,
        }
