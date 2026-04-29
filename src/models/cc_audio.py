from app import db
from datetime import datetime, timedelta, timezone

import constants

class CCAudio(db.Model):
    __tablename__ = 'CC_AUDIO'
    ID = db.Column(db.Integer, primary_key=True)
    UPLOADER_ID = db.Column(db.Integer, nullable=True)
    SUPERVISOR_ID = db.Column(db.Integer, nullable=True)
    SUPERVISOR_AUDIO_ID = db.Column(db.Integer, nullable=True)
    UPLOAD_DATE = db.Column(db.Date, nullable=True, default=datetime.utcnow)
    AUDIO_KEY = db.Column(db.String(250), nullable=True)
    AUDIO_LENGTH = db.Column(db.String(100), nullable=True)
    FILEPATH = db.Column(db.String(400), nullable=True)
    REPEATS_PAUSES_STUTTER_LEVEL = db.Column(db.String(100), nullable=True)
    AUDIO_SOURCE = db.Column(db.String(50), nullable=True)
    MISTAKE_LEVEL = db.Column(db.String(50), nullable=True)
    AUDIO_ISSUES_LEVEL = db.Column(db.String(50), nullable=True)
    RECITATION_SPEED = db.Column(db.String(50), nullable=True)
    VOICE_PITCH = db.Column(db.String(50), nullable=True)
    VOICE_LEVEL = db.Column(db.String(50), nullable=True)
    VOICE_CLARITY = db.Column(db.String(50), nullable=True)
    UNKNOWN_USER_FL = db.Column(db.String(1), nullable=True)
    UNKNOWN_USER_ID_1 = db.Column(db.Integer, nullable=True)
    UNKNOWN_USER_ID_2 = db.Column(db.Integer, nullable=True)
    UNKNOWN_USER_ID_3 = db.Column(db.Integer, nullable=True)
    STUDENT_FL = db.Column(db.String(1), nullable=True)
    AUDIO_CLIPPED_BEG_FL = db.Column(db.String(1), nullable=True)
    AUDIO_CLIPPED_END_FL = db.Column(db.String(1), nullable=True)
    BACKGROUND_NOISE_LEVEL = db.Column(db.String(50), nullable=True)
    WHISPER_FL = db.Column(db.String(1), nullable=True)
    STUDENT_ID_1 = db.Column(db.Integer, nullable=True)
    STUDENT_ID_2 = db.Column(db.Integer, nullable=True)
    STUDENT_ID_3 = db.Column(db.Integer, nullable=True)
    TEACHER_FL = db.Column(db.String(1), nullable=True)
    TEACHER_ID = db.Column(db.Integer, nullable=True)
    PRO_RECITER_FL = db.Column(db.String(1), nullable=True)
    ETL_PROCESSED_FL = db.Column(db.String(1), nullable=True)
    PRO_RECITER_ID = db.Column(db.Integer, nullable=True)
    ETL_ADD_DTS = db.Column(db.Date, nullable=True, default=datetime.utcnow)
    ETL_ROW_PROCESS_DTS = db.Column(db.Date, nullable=True, default=datetime.utcnow)
    SCORE = db.Column(db.Integer, nullable=True)
    SURAH_SCORE = db.Column(db.Integer, nullable=True)
    META_SCORE = db.Column(db.Integer, nullable=True)
    PROFILE_SCORE = db.Column(db.Integer, nullable=True)
    IS_SURAH_CHANGED = db.Column(db.Boolean, nullable=True)
    MISTAKES = db.Column(db.String(1000), nullable=True)
    IS_IGNORED = db.Column(db.Boolean, nullable=True)
    IS_SUBMITTED = db.Column(db.Boolean, nullable=True)
    DURATION = db.Column(db.String, nullable=True)
    BASE_EFFORT_MINUTE           = db.Column(db.Float,       nullable=True)
    CDM_ELIGIBLE_FL              = db.Column(db.Boolean,     nullable=True)
    DIFFICULTY_SCORE             = db.Column(db.Float,       nullable=True)
    DIFFICULTY_LEVEL             = db.Column(db.String(10),  nullable=True)



    def __repr__(self):
        return '<CCAudio %r>' % self.ID
    @property
    def primary_user_id(self) -> int:
        """Return the most-specific 'who reads this audio' identifier we have."""
        return (
            self.UNKNOWN_USER_ID_1
            or self.STUDENT_ID_1
            or self.UPLOADER_ID
            or 0
        )

    @property
    def safe_audio_length_seconds(self) -> float:
        """AUDIO_LENGTH coerced to float seconds, with fallback to DURATION."""
        for raw in (self.AUDIO_LENGTH, self.DURATION):
            if raw is None:
                continue
            try:
                v = float(raw)
                if v > 0:
                    return v
            except (TypeError, ValueError):
                continue
        return 0.0
    def to_json(self):
        def get_user(model, user_id):
            if not user_id:
                return None
            record = model.query.get(user_id)
            return record.to_json() if record else None

        return {
            'id': self.ID,
            'uploader_id': self.UPLOADER_ID,
            'supervisor_id': self.SUPERVISOR_ID,
            'supervisor_audio_id': self.SUPERVISOR_AUDIO_ID,
            'upload_date': self.UPLOAD_DATE.isoformat() if self.UPLOAD_DATE else None,
            'audio_key': self.AUDIO_KEY,
            'audio_length': self.AUDIO_LENGTH,
            'filepath': self.FILEPATH,
            'repeats_pauses_stutter_level': self.REPEATS_PAUSES_STUTTER_LEVEL,
            'audio_source': self.AUDIO_SOURCE,
            'mistake_level': self.MISTAKE_LEVEL,
            'audio_issues_level': self.AUDIO_ISSUES_LEVEL,
            'recitation_speed': self.RECITATION_SPEED,
            'voice_pitch': self.VOICE_PITCH,
            'voice_level': self.VOICE_LEVEL,
            'voice_clarity': self.VOICE_CLARITY,
            'unknown_user_fl': self.UNKNOWN_USER_FL,
            'unknown_user_id_1': self.UNKNOWN_USER_ID_1,
            'unknown_user_id_2': self.UNKNOWN_USER_ID_2,
            'unknown_user_id_3': self.UNKNOWN_USER_ID_3,
            'student_fl': self.STUDENT_FL,
            'audio_clipped_beg_fl': self.AUDIO_CLIPPED_BEG_FL,
            'audio_clipped_end_fl': self.AUDIO_CLIPPED_END_FL,
            'background_noise_level': self.BACKGROUND_NOISE_LEVEL,
            'whisper_fl': self.WHISPER_FL,
            'student_id_1': self.STUDENT_ID_1,
            'student_id_2': self.STUDENT_ID_2,
            'student_id_3': self.STUDENT_ID_3,
            'teacher_fl': self.TEACHER_FL,
            'teacher_id': self.TEACHER_ID,
            'pro_reciter_fl': self.PRO_RECITER_FL,
            'pro_reciter_id': self.PRO_RECITER_ID,
            'etl_add_dts': self.ETL_ADD_DTS.isoformat() if self.ETL_ADD_DTS else None,
            'etl_row_process_dts': self.ETL_ROW_PROCESS_DTS.isoformat() if self.ETL_ROW_PROCESS_DTS else None,
            'score': self.SCORE,
            'surah_score': self.SURAH_SCORE,
            'meta_score': self.META_SCORE,
            'profile_score': self.PROFILE_SCORE,
            'mistakes': self.MISTAKES,
            'is_ignored': self.IS_IGNORED,
            'duration': self.DURATION,
            'is_surah_changed': self.IS_SURAH_CHANGED,
            'is_submitted': self.IS_SUBMITTED,
            'unknown_user_1': get_user(CCUnknownUser, self.UNKNOWN_USER_ID_1),
            'unknown_user_2': get_user(CCUnknownUser, self.UNKNOWN_USER_ID_2),
            'unknown_user_3': get_user(CCUnknownUser, self.UNKNOWN_USER_ID_3),
            'student_1': get_user(CCStudent, self.STUDENT_ID_1),
            'student_2': get_user(CCStudent, self.STUDENT_ID_2),
            'student_3': get_user(CCStudent, self.STUDENT_ID_3),
            'teacher': get_user(CCTeacher, self.TEACHER_ID),
            'pro_reciter': get_user(CCProreciter, self.PRO_RECITER_ID),
            'difficulty_score':             self.DIFFICULTY_SCORE,
            'base_effort_minute':           self.BASE_EFFORT_MINUTE,
            'difficulty_level':             self.DIFFICULTY_LEVEL,
            'etl_processed_fl':             self.ETL_PROCESSED_FL,
            



        }


class CCProreciter(db.Model):
    __tablename__ = 'CC_PRO_RECITER'
    ID = db.Column(db.Integer, primary_key=True)
    RECITER_NAME = db.Column(db.String(50), nullable=True)
    QIRAT = db.Column(db.Integer, nullable=True)
    ISLAM_SECT = db.Column(db.String(100), nullable=True)

    def to_json(self):
        return {
            'id': self.ID,
            'reciter_name': self.RECITER_NAME,
            'qirat': self.QIRAT,
            'type': constants.HMS_TYPE
        }
class CCTeacher(db.Model):
    __tablename__ = 'CC_TEACHER'

    ID = db.Column(db.Integer, primary_key=True)
    FIRST_NAME = db.Column(db.String(50), nullable=True)
    LAST_NAME = db.Column(db.String(50), nullable=True)
    UPLOADER_ID = db.Column(db.String(50), nullable=True)
    YEARS_OF_EXPERIENCE = db.Column(db.String(50), nullable=True)
    TEACHER_LOCATION = db.Column(db.String(50), nullable=True)
    GENDER = db.Column(db.String(50), nullable=True)
    AGE = db.Column(db.String(50), nullable=True)
    IJAZA_TYPE = db.Column(db.Integer, nullable=True)
    NUMBER_OF_STUDENTS_TAUGHT = db.Column(db.String(50), nullable=True)
    IJAZA_EARNED_DT = db.Column(db.String(50), nullable=True)
    QIRAT_TYPE = db.Column(db.Integer, nullable=True)
    EDUCATION_SCHOOL = db.Column(db.String(50), nullable=True)
    ISLAM_SECT = db.Column(db.String(50), nullable=True)
    QURAN_CERTIFICATION = db.Column(db.String(5), nullable=True)

    def to_json(self):
        return {
            "id": self.ID,
            "first_name": self.FIRST_NAME,
            "last_name": self.LAST_NAME,
            "uploader_id": self.UPLOADER_ID,
            "years_of_experience": self.YEARS_OF_EXPERIENCE,
            "teacher_location": self.TEACHER_LOCATION,
            "gender": self.GENDER,
            "age": self.AGE,
            "ijaza_type": self.IJAZA_TYPE,
            "number_of_students_taught": self.NUMBER_OF_STUDENTS_TAUGHT,
            "ijaza_earned_dt": self.IJAZA_EARNED_DT,
            "qirat_type": self.QIRAT_TYPE,
            "education_school": self.EDUCATION_SCHOOL,
            "islam_sect": self.ISLAM_SECT,
            "quran_certification": self.QURAN_CERTIFICATION,
            'type': constants.HMS_TYPE
        }


class CCStudent(db.Model):
    __tablename__ = 'CC_STUDENT'
    ID = db.Column(db.Integer, primary_key=True)
    FIRST_NAME = db.Column(db.String(50), nullable=True)
    LAST_NAME = db.Column(db.String(50), nullable=True)
    UPLOADER_ID = db.Column(db.Integer, nullable=True)
    EDUCATION_LEVEL = db.Column(db.String(50), nullable=True)
    STUDENT_COUNTRY = db.Column(db.String(50), nullable=True)
    STUDENT_GENDER = db.Column(db.String(50), nullable=True)
    STUDENT_AGE = db.Column(db.Integer, nullable=True)
    READ_ARABIC_FL = db.Column(db.String(50), nullable=True)
    SPEAK_ARABIC_FL = db.Column(db.String(50), nullable=True)
    WRITE_ARABIC_FL = db.Column(db.String(50), nullable=True)
    SPEAKING_DISABILITY_FL = db.Column(db.String(50), nullable=True)
    NATIVE_LANGUAGE = db.Column(db.String(50), nullable=True)
    USUAL_MISTAKE_LEVEL = db.Column(db.String(50), nullable=True)
    TAJWEED_RULE_PERFORMANCE = db.Column(db.String(50), nullable=True)
    RECITATION_PER_WEEK = db.Column(db.String(50), nullable=True)
    STARTED_READING_QURAN_YEAR = db.Column(db.String(50), nullable=True)
    NUMBER_OF_SURAHS_MEMORIZED = db.Column(db.String(50), nullable=True)

    def to_json(self):
        return {
            "id": self.ID,
            "first_name": self.FIRST_NAME,
            "last_name": self.LAST_NAME,
            "uploader_id": self.UPLOADER_ID,
            "education_level": self.EDUCATION_LEVEL,
            "student_country": self.STUDENT_COUNTRY,
            "student_gender": self.STUDENT_GENDER,
            "student_age": self.STUDENT_AGE,
            "read_arabic_fl": self.READ_ARABIC_FL,
            "speak_arabic_fl": self.SPEAK_ARABIC_FL,
            "write_arabic_fl": self.WRITE_ARABIC_FL,
            "speaking_disability_fl": self.SPEAKING_DISABILITY_FL,
            "native_language": self.NATIVE_LANGUAGE,
            "usual_mistake_level": self.USUAL_MISTAKE_LEVEL,
            "tajweed_rule_performance": self.TAJWEED_RULE_PERFORMANCE,
            "recitation_per_week": self.RECITATION_PER_WEEK,
            "started_reading_quran_year": self.STARTED_READING_QURAN_YEAR,
            "number_of_surahs_memorized": self.NUMBER_OF_SURAHS_MEMORIZED,
            'type': constants.HMS_TYPE
        }

class CCUnknownUser(db.Model):
    __tablename__ = 'CC_UNKNOWN_USER'
    ID = db.Column(db.Integer, primary_key=True)
    UPLOADER_ID = db.Column(db.Integer, nullable=True)
    AGE = db.Column(db.String(50), nullable=True)
    USER_LOCATION = db.Column(db.String(50), nullable=True)
    GENDER = db.Column(db.String(50), nullable=True)
    USUAL_MISTAKE_LEVEL = db.Column(db.String(50), nullable=True)
    UNK_NAME = db.Column(db.String(50), nullable=True)

    def to_json(self):
        return {
            "id": self.ID,
            "uploader_id": self.UPLOADER_ID,
            "age": self.AGE,
            "user_location": self.USER_LOCATION,
            "gender": self.GENDER,
            "usual_mistake_level": self.USUAL_MISTAKE_LEVEL,
            "unk_name": self.UNK_NAME,
            'type': constants.HMS_TYPE
        }