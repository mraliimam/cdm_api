"""
Serialisation schemas for the simple "table" models — IAP users, IAP
workfiles, CC_AUDIO and the effort-baseline cache.

Field names mirror the keys returned by each model's `to_json()` method
so admin tools can rely on a stable shape.
"""

from marshmallow import Schema, fields


class IapUserSchema(Schema):
    """Serialises an AAIAPUSERS row (AA_IAP_USERS)."""
    id              = fields.Integer(dump_default=None)
    user_name       = fields.String(dump_default=None)
    first_name      = fields.String(dump_default=None)
    last_name       = fields.String(dump_default=None)
    date_added      = fields.String(dump_default=None)
    status          = fields.String(dump_default=None)
    session_status  = fields.String(dump_default=None)
    user_stage      = fields.String(dump_default=None)
    payment_ratio   = fields.Float(dump_default=None)
    cdm_available_effort_minute = fields.Float(dump_default=None)
    cdm_weekly_effort_limit     = fields.Float(dump_default=None)
    cdm_accuracy_target         = fields.Float(dump_default=None)
    cdm_skill_level             = fields.String(dump_default=None)
    cdm_experience_years        = fields.Integer(dump_default=None)
    cdm_is_active_fl            = fields.String(dump_default=None)


class IapWorkfileSchema(Schema):
    """Serialises an AAIAPWORKFILE row (AA_IAP_WORKFILE)."""
    id                  = fields.Integer(dump_default=None)
    WORKFILE_NAME       = fields.String(dump_default=None)
    AUDIO_FILEPATH      = fields.String(dump_default=None)
    MODELPRED_FILEPATH  = fields.String(dump_default=None)
    FILESAVE_FILEPATH   = fields.String(dump_default=None)
    user_id             = fields.Integer(dump_default=None)
    test_static_id      = fields.Integer(dump_default=None)
    date                = fields.String(dump_default=None)
    duration            = fields.String(dump_default=None)
    CC_AUDIO_ID         = fields.String(dump_default=None)
    user_stage          = fields.String(dump_default=None)


class CdmAudioSchema(Schema):
    """Serialises a CCAudio row (CC_AUDIO, CDM-relevant slice)."""
    id           = fields.Integer(dump_default=None)
    filepath     = fields.String(dump_default=None)
    audio_key    = fields.String(dump_default=None)
    audio_source = fields.String(dump_default=None)
    duration     = fields.String(dump_default=None)
    audio_length = fields.String(dump_default=None)
    audio_type   = fields.String(dump_default=None)
    status       = fields.String(dump_default=None)
    mistake_level                = fields.String(dump_default=None)
    background_noise_level       = fields.String(dump_default=None)
    repeats_pauses_stutter_level = fields.String(dump_default=None)
    audio_issues_level           = fields.String(dump_default=None)
    recitation_speed             = fields.String(dump_default=None)
    voice_pitch                  = fields.String(dump_default=None)
    voice_clarity                = fields.String(dump_default=None)
    voice_level                  = fields.String(dump_default=None)
    whisper_fl                   = fields.String(dump_default=None)
    audio_clipped_beg_fl         = fields.String(dump_default=None)
    audio_clipped_end_fl         = fields.String(dump_default=None)
    score                        = fields.Float(dump_default=None)
    surah_score                  = fields.Float(dump_default=None)
    profile_score                = fields.Float(dump_default=None)
    meta_score                   = fields.Float(dump_default=None)
    difficulty_score             = fields.Float(dump_default=None)
    difficulty_level             = fields.String(dump_default=None)
    base_effort_minute           = fields.Float(dump_default=None)
    cdm_eligible_fl              = fields.Boolean(dump_default=None)
    uploader_id                  = fields.String(dump_default=None)
    website_user_id              = fields.Integer(dump_default=None)
    unknown_user_id_1            = fields.Integer(dump_default=None)
    student_id_1                 = fields.Integer(dump_default=None)
    supervisor_id                = fields.Integer(dump_default=None)


class CdmEffortBaselineSchema(Schema):
    """Serialises a CdmEffortBaseline row (CC_EFFORT_BASELINE)."""
    id               = fields.Integer(dump_default=None)
    iap_workfile_id  = fields.Integer(dump_default=None)
    stage            = fields.String(dump_default=None)
    sample_count     = fields.Integer(dump_default=None)
    total_mins_data  = fields.Float(dump_default=None)
    baseline_effort  = fields.Float(dump_default=None)
    last_updated_dts = fields.String(dump_default=None)
