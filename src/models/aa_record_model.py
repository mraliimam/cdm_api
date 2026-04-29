from datetime import datetime

from src.extensions import db
from src import constants


class AARecord(db.Model):
    __tablename__ = 'AA_RECORD'
    __table_args__ = {'implicit_returning': False}
    ID = db.Column(db.Integer, primary_key=True)
    USER_ID = db.Column(db.String(50), nullable=True)
    WORKFILE_ID = db.Column(db.String(50), nullable=True)
    RECORD_NAME =   db.Column(db.String(255), nullable=True)
    RECORD_LENGTH =  db.Column(db.String(255), nullable=True)
    RECORD_FILEPATH = db.Column(db.String(4000), nullable=True)
    MODEL_PRED_TRANSCRIPTION = db.Column(db.String(8000), nullable=True)
    RAW_TRANSCRIPTION = db.Column(db.String(8000), nullable=True)
    FE_TRANSCRIPTION = db.Column(db.String(4000), nullable=True)
    PR_TR_1 = db.Column(db.String(4000, collation='utf8mb4_general_ci'), nullable=True)
    NON_STANDARD_OPERATION_FL = db.Column(db.String(1), nullable=True)
    RECITER_AGE_GROUP = db.Column(db.String(50), nullable=True)
    RECITER_GENDER = db.Column(db.String(50), nullable=True)
    RECITER_LOCATION = db.Column(db.String(255), nullable=True)
    RECITER_DIALECT = db.Column(db.String(255), nullable=True)
    RECITER_PACE = db.Column(db.String(50), nullable=True)
    RECITER_VOICE_PITCH = db.Column(db.String(100), nullable=True)
    RECITER_VOICE_LEVEL = db.Column(db.String(100), nullable=True)
    RECITER_VOICE_CLARITY = db.Column(db.String(100), nullable=True)
    RECORD_BACKGROUND_NOISE = db.Column(db.String(255), nullable=True)
    RECORD_AUDIO_ISSUES_LEVEL = db.Column(db.String(100), nullable=True)
    WHISPER_FL = db.Column(db.String(1), nullable=True)
    REPEATS_PAUSE_STUTTER_LEVEL = db.Column(db.String(100), nullable=True)
    ALL_TAJWEED_RULES_OBSERVED_FL = db.Column(db.String(1), nullable=True)
    TAJWEED_TRANSCRIPTION = db.Column(db.String(255), nullable=True)
    EASY_TO_UNDERSTAND_WORDS_FL = db.Column(db.String(1), nullable=True)
    SENTENCE_ID_1 = db.Column(db.String(255), nullable=True)
    SENTENCE_ID_2 = db.Column(db.String(255), nullable=True)
    SENTENCE_ID_3 = db.Column(db.String(255), nullable=True)
    SENTENCE_ID_4 = db.Column(db.String(255), nullable=True)
    COMMENTS = db.Column(db.String(4000), nullable=True)
    SEGMENT_START_TIME = db.Column(db.String(100), nullable=True)
    SEGMENT_END_TIME = db.Column(db.String(100), nullable=True)
    UNKNOWN_USER_FL = db.Column(db.String(1), nullable=True)
    UNKNOWN_USER_ID_1 = db.Column(db.Integer, nullable=True)
    UNKNOWN_USER_ID_2 = db.Column(db.Integer, nullable=True)
    UNKNOWN_USER_ID_3 = db.Column(db.Integer, nullable=True)
    STUDENT_FL = db.Column(db.String(1), nullable=True)
    STUDENT_ID_1 = db.Column(db.Integer, nullable=True)
    STUDENT_ID_2 = db.Column(db.Integer, nullable=True)
    STUDENT_ID_3 = db.Column(db.Integer, nullable=True)
    TEACHER_FL = db.Column(db.String(1), nullable=True)
    TEACHER_ID = db.Column(db.Integer, nullable=True)
    PRO_RECITER_FL = db.Column(db.String(1), nullable=True)
    PRO_RECITER_ID = db.Column(db.Integer, nullable=True)
    ROW_PROC_DTS = db.Column(db.Date, nullable=True,  default=datetime.utcnow)
    ETL_Active_FL = db.Column(db.String(1), nullable=True)
    ALIGN_DATA = db.Column(db.String(1000), nullable=True)
    STAGE = db.Column(db.String(100), nullable=True)
    NO_OF_MISTAKE= db.Column(db.String(100), nullable=True)
    SATISFICATION_LEVEL=db.Column(db.Integer, nullable=True)
    SATISFICATION_FEEDBACK=db.Column(db.String(10000), nullable=True)
    L3_PROCESSED_FL = db.Column(db.String(1), nullable=True)
    CDM_L3_STATUS     = db.Column(db.String(20), nullable=True)
    CDM_L3_ROUTED_DTS = db.Column(db.DateTime,   nullable=True)





    def __repr__(self):
        return '<AARECORD %r>' % self.ID


    def to_json(self):

        def get_user(model, user_id):
            if not user_id:
                return None
            record = model.query.get(user_id)
            return record.to_json() if record else None

        return {
            'USER_ID' :self.USER_ID,
            'WORKFILE_ID' :self.WORKFILE_ID,
            'RECORD_NAME' :  self.RECORD_NAME,
            'RECORD_LENGTH' : self.RECORD_LENGTH,
            'RECORD_FILEPATH' :self.RECORD_FILEPATH,
            'MODEL_PRED_TRANSCRIPTION' :self.MODEL_PRED_TRANSCRIPTION,
            'RAW_TRANSCRIPTION' :self.RAW_TRANSCRIPTION,
            'FE_TRANSCRIPTION' : self.FE_TRANSCRIPTION,
            'PR_TR_1' :self.PR_TR_1,
            'NON_STANDARD_OPERATION_FL' :self.NON_STANDARD_OPERATION_FL,
            'RECITER_AGE_GROUP' :self.RECITER_AGE_GROUP,
            'RECITER_GENDER' :self.RECITER_GENDER,
            'RECITER_LOCATION' :self.RECITER_LOCATION,
            'RECITER_DIALECT' :self.RECITER_DIALECT,
            'RECITER_PACE' :self.RECITER_PACE,
            'RECITER_VOICE_PITCH' :self.RECITER_VOICE_PITCH,
            'RECITER_VOICE_LEVEL' :self.RECITER_VOICE_LEVEL,
            'RECITER_VOICE_CLARITY' :self.RECITER_VOICE_CLARITY,
            'RECORD_BACKGROUND_NOISE' :self.RECORD_BACKGROUND_NOISE,
            'RECORD_AUDIO_ISSUES_LEVEL' :self.RECORD_AUDIO_ISSUES_LEVEL,
            'WHISPER_FL' :self.WHISPER_FL,
            'ALL_TAJWEED_RULES_OBSERVED_FL' :self.ALL_TAJWEED_RULES_OBSERVED_FL,
            'TAJWEED_TRANSCRIPTION' :self.TAJWEED_TRANSCRIPTION,
            'EASY_TO_UNDERSTAND_WORDS_FL' :self.EASY_TO_UNDERSTAND_WORDS_FL,
            'SENTENCE_ID_1' :self.SENTENCE_ID_1,
            'SENTENCE_ID_2' :self.SENTENCE_ID_2,
            'SENTENCE_ID_3' :self.SENTENCE_ID_3,
            'SENTENCE_ID_4' :self.SENTENCE_ID_4,
            'COMMENTS' :self.COMMENTS,
            'REPEATS_PAUSE_STUTTER_LEVEL':self.REPEATS_PAUSE_STUTTER_LEVEL,
            'SEGMENT_START_TIME': self.SEGMENT_START_TIME,
            'SEGMENT_END_TIME': self.SEGMENT_END_TIME,
            'UNKNOWN_USER_FL' :self.UNKNOWN_USER_FL,
            'UNKNOWN_USER_ID_1' : self.UNKNOWN_USER_ID_1,
            'UNKNOWN_USER_ID_2' : self.UNKNOWN_USER_ID_2,
            'UNKNOWN_USER_ID_3' : self.UNKNOWN_USER_ID_3,
            'STUDENT_FL' :self.STUDENT_FL,
            'STUDENT_ID_1' : self.STUDENT_ID_1,
            'STUDENT_ID_2' : self.STUDENT_ID_2,
            'STUDENT_ID_3' : self.STUDENT_ID_3,
            'TEACHER_FL' :self.TEACHER_FL,
            'TEACHER_ID' : self.TEACHER_ID,
            'PRO_RECITER_FL' :self.PRO_RECITER_FL,
            'PRO_RECITER_ID' : self.PRO_RECITER_ID,
            'ROW_PROC_DTS' : self.ROW_PROC_DTS.isoformat(),
            'ETL_Active_FL' :self.ETL_Active_FL, 
            'ALIGN_DATA': self.ALIGN_DATA,
            'STAGE': self.STAGE,
            'unknown_user_1': get_user(AAUnknownUser, self.UNKNOWN_USER_ID_1),
            'unknown_user_2': get_user(AAUnknownUser, self.UNKNOWN_USER_ID_2),
            'unknown_user_3': get_user(AAUnknownUser, self.UNKNOWN_USER_ID_3),
            'student_1': get_user(AAStudent, self.STUDENT_ID_1),
            'student_2': get_user(AAStudent, self.STUDENT_ID_2),
            'student_3': get_user(AAStudent, self.STUDENT_ID_3),
            'teacher': get_user(AATeacher, self.TEACHER_ID),
            'pro_reciter': get_user(AAPRORECITER, self.PRO_RECITER_ID),
            'no_of_mistake':self.NO_OF_MISTAKE,
            'satisfication_level':self.SATISFICATION_LEVEL,
            'satisfication_feedback':self.SATISFICATION_FEEDBACK
           
        }
    

    
class AAUnknownUser(db.Model):
    __tablename__ = 'AA_UNKNOWN_USER'
    ID = db.Column(db.Integer, primary_key=True)
    UPLOADER_ID = db.Column(db.Integer, nullable=True)
    AGE = db.Column(db.String(50), nullable=True)
    USER_LOCATION= db.Column(db.String(50), nullable=True)
    GENDER =  db.Column(db.String(50), nullable=True) 
    USUAL_MISTAKE_LEVEL= db.Column(db.String(50), nullable=True)
    #DIALECT = db.Column(db.String(50), nullable=True)
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
            "type": constants.IAP_TYPE
        }

    def return_to_json(self):
        return {
            'id': self.ID,
            'UNK_NAME': self.UNK_NAME
           
        }
    


class AAStudent(db.Model):
    __tablename__ = 'AA_STUDENT'
    ID = db.Column(db.Integer, primary_key=True)
    FIRST_NAME = db.Column(db.String(50),nullable=True) 
    LAST_NAME = db.Column(db.String(50), nullable=True)
    UPLOADER_ID = db.Column(db.Integer, nullable=True)
    EDUCATION_LEVEL = db.Column(db.String(50), nullable=True)
    STUDENT_COUNTRY = db.Column(db.String(50), nullable=True)
    STUDENT_GENDER = db.Column(db.String(50), nullable=True)
    STUDENT_AGE = db.Column(db.String(50), nullable=True) 
    #ISLAM_SECT = db.Column(db.String(50), nullable=True) 
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
            "type": constants.IAP_TYPE
        }

    def return_to_json(self):
        return {
            'id': self.ID,
            'first_name': self.FIRST_NAME,
            'last_name': self.LAST_NAME
        }
    


class AATeacher(db.Model):
    __tablename__ = 'AA_TEACHER'
    ID = db.Column(db.Integer, primary_key=True)
    FIRST_NAME = db.Column(db.String(50), nullable=True)
    LAST_NAME = db.Column(db.String(50), nullable=True)
    UPLOADER_ID = db.Column(db.String(50), nullable=True)
    YEARS_OF_EXPERIENCE = db.Column(db.String(50), nullable=True)
    TEACHER_LOCATION = db.Column(db.String(50), nullable=True)
    GENDER = db.Column(db.String(50), nullable=True)
    AGE = db.Column(db.String(50), nullable=True)
    IJAZA_TYPE = db.Column(db.String(50), nullable=True)
    NUMBER_OF_STUDENTS_TAUGHT = db.Column(db.String(50), nullable=True)
    IJAZA_EARNED_DT = db.Column(db.String(50), nullable=True)
    QIRAT_TYPE = db.Column(db.String(50), nullable=True)
    EDUCATION_SCHOOL = db.Column(db.String(50), nullable=True)
    ISLAM_SECT = db.Column(db.String(50), nullable=True)

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
            "type": constants.IAP_TYPE
        }

    def return_to_json(self):
        return {
            'id': self.ID,
            'first_name': self.FIRST_NAME,
            'last_name': self.LAST_NAME
        }


class AAPRORECITER(db.Model):
    __tablename__ = 'AA_PRO_RECITER'
    ID =  db.Column(db.Integer, primary_key=True)
    RECITER_NAME = db.Column(db.String(50), nullable=True)
    QIRAT = db.Column(db.String(50), nullable=True)
    ETL_ROW_ADD_DTS = db.Column(db.Date, nullable=True,  default=datetime.utcnow)

    def to_json(self):
        return {
            "id": self.ID,
            "reciter_name": self.RECITER_NAME,
            "qirat": self.QIRAT,
            "type": constants.IAP_TYPE
        }
