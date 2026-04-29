"""
CDM Allocation Service

Uses AA_IAP_WORKFILE (the real IAP candidate pool) as the source of files
to allocate, joined with CC_AUDIO for audio quality parameters.

Evaluator profiles are fetched from AA_IAP_USERS by ID.

Two allocation modes
--------------------
On-Demand  : one evaluator requests N files (default 2 per click).
Scheduled  : batch run assigns `files_per_user` (8-10) files to every
             evaluator in a list, targeting 100% capacity utilisation.

After allocation each assigned workfile is updated:
  ASSIGNED_USER_ID  ← evaluator_id
  TO_DO_MOVED_DTS   ← now
  LAST_MOVED_BY     ← 'CDM'

L3 / L4 separation is enforced via AA_IAP_WORKFILE.STAGE so the allocator
never mixes file tiers.

For each allocation we write three rows in the same transaction:
  1. CC_CDM_ALLOCATION                   (the headline allocation)
  2. CC_CDM_ALLOCATION_DECISION          (one row per decision parameter)
  3. CC_CDM_EVALUATOR_PERFORMANCE        (touched on /cdm/complete only)
"""

import datetime
import json
import logging
import os
import re
import subprocess
import tempfile
from typing import Tuple

import boto3
import botocore
import pandas as pd
from pydub import AudioSegment
from sqlalchemy import or_, text

from constants import classify_duration
import constants
from extensions import db
from models.aa_record_model import AARecord
from models.allocation_decision import CdmAllocationDecision
from models.cc_audio import CCAudio
from models.evaluator_performance import CdmEvaluatorPerformance
from models.users import AAIAPUSERS
from models.workfile import AAIAPWORKFILE
from models.cdm import CdmAllocation, _classify_duration
from services.on_demand_allocator import AllocationConfig, OnDemandAllocator
from services.cdm_workfile_service import (
    provision_l4_workfile,
    provision_l3_workfile_from_aa_record,
)
from services.l3_difficulty import compute_for_aa_record
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# L3 audio provisioning — direct replication of cdm_api/L3_job.py
#
# Mirrors:
#   • upload_if_not_exists(s3, bucket, local_path, s3_key)
#   • is_valid_audio(file_path)
#   • GetSplittedAudio(source_path, start, end)
#   • process_splitting_auto_segment(session, aa_record_id)
#   • the main `for row in df.iterrows()` loop that uploads the sliced
#     WAV + JSON stubs to the evaluator's ToDo folder and inserts a new
#     AA_IAP_WORKFILE row (STAGE='L3').
#   • the aa_workfile_master insert that links the parent AA_RECORD to
#     the newly created L3 review workfile.
#
# The helpers below are intentionally self-contained — nothing is
# imported from cdm_workfile_service. The flow is invoked inline from
# `allocate_on_demand` for stage='L3'.
# ---------------------------------------------------------------------------

_HMS_R2_BUCKET = os.environ.get('HMS_R2_BUCKET', 'rawaudio')
_R2_BUCKET     = os.environ.get('R2_BUCKET',     'dev-iap-data-operational')


def _s3_client():
    """Boto3 R2 client — same single-client pattern used in L3_job.py."""
    return boto3.client(
        's3',
        endpoint_url          = os.environ.get('R2_ENDPOINT'),
        aws_access_key_id     = os.environ.get('R2_ACCESS_KEY_ID'),
        aws_secret_access_key = os.environ.get('R2_SECRET_ACCESS_KEY'),
    )


def _is_valid_audio(file_path: str) -> bool:
    """Replica of `is_valid_audio` used by L3_job.py."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_type", "-of",
             "default=noprint_wrappers=1:nokey=1", file_path],
            capture_output=True, text=True,
        )
        return result.returncode == 0 and "audio" in result.stdout
    except Exception as exc:
        logger.error("Audio validation error: %s", exc)
        return False


def _is_missing_object_error(exc: botocore.exceptions.ClientError) -> bool:
    """Return True for the different missing-object codes R2/S3 may emit."""
    error = exc.response.get('Error', {})
    code = str(error.get('Code', '')).strip().lower().replace(' ', '')
    status = exc.response.get('ResponseMetadata', {}).get('HTTPStatusCode')
    return status == 404 or code in {'404', 'nosuchkey', 'notfound'}


def _upload_if_not_exists(s3, bucket: str, local_path: str, s3_key: str) -> bool:
    """Replica of `upload_if_not_exists` from L3_job.py with R2-safe 404 handling."""
    try:
        s3.head_object(Bucket=bucket, Key=s3_key)
        return False
    except botocore.exceptions.ClientError as exc:
        if _is_missing_object_error(exc):
            s3.upload_file(local_path, bucket, s3_key)
            return True
        raise


def _get_splitted_audio(source_path: str,
                         segment_start_time: float,
                         segment_end_time: float) -> AudioSegment:
    """
    Replica of `GetSplittedAudio` used inside `process_splitting_auto_segment`
    in L3_job.py — returns the pydub AudioSegment slice between the two
    timestamps (in seconds). Caller exports it to disk.
    """
    audio = AudioSegment.from_file(source_path)
    start_ms = max(0, int(float(segment_start_time) * 1000))
    end_ms   = min(len(audio), int(float(segment_end_time) * 1000)) or len(audio)
    if end_ms <= start_ms:
        raise ValueError(
            f"Invalid segment range: start={segment_start_time}s "
            f"end={segment_end_time}s"
        )
    return audio[start_ms:end_ms]


def _process_splitting_auto_segment(aa_record_id) -> bool:
    """
    Direct replica of `process_splitting_auto_segment(session, aa_record_ids)`
    from L3_job.py.

      • Joins AA_RECORD ↔ AA_IAP_WORKFILE for the given record.
      • Strips `_partN` and the `.WKFL` extension off the parent
        WORKFILE_NAME to derive the audio basename.
      • Downloads the parent's converted WAV from HMS R2
        (`data/Dev/RawAudioFiles/ConvertedAudio/wav/<basename>.wav`),
        slices it to AA_RECORD's SEGMENT_START_TIME / SEGMENT_END_TIME,
        and uploads to HMS R2 at `data/Dev/AA_RECORD/L3_<rec.ID>.wav`
        only when the destination key is missing.

    Returns True when the AA_RECORD slice exists and validates in HMS R2;
    returns False when the source/slice is missing or invalid. Per-row
    exceptions are logged and swallowed (same try/except semantics as
    L3_job.py) so a single bad record cannot abort the surrounding
    allocation loop.
    """
    s3 = _s3_client()

    query = text("""
        SELECT A.ID, B.WORKFILE_NAME, A.USER_ID, A.SEGMENT_START_TIME,
               A.SEGMENT_END_TIME, B.AUDIO_KEY
        FROM AA_RECORD A
        INNER JOIN AA_IAP_WORKFILE B ON A.WORKFILE_ID = B.ID
        WHERE A.ID = :aa_record_id
    """)
    rows = db.session.execute(query, {'aa_record_id': aa_record_id}).mappings().all()
    if not rows:
        logger.warning(
            "_process_splitting_auto_segment: no AA_RECORD/workfile row found for %s",
            aa_record_id,
        )
        return False

    for row in rows:
        tmp_source_path = None
        tmp_dest_path = None
        try:
            audio_key_as_workfile_id = re.sub(
                r'_part\d+', '', (row['WORKFILE_NAME'] or '').split('.')[0]
            )
            if not audio_key_as_workfile_id:
                continue

            segment_start_time = float(row['SEGMENT_START_TIME']) if row['SEGMENT_START_TIME'] else 0.0
            segment_end_time   = float(row['SEGMENT_END_TIME'])   if row['SEGMENT_END_TIME']   else 0.0
            record_id          = row['ID']

            source_key = f"data/Dev/RawAudioFiles/ConvertedAudio/wav/{audio_key_as_workfile_id}.wav"
            dest_key   = f"data/Dev/AA_RECORD/L3_{record_id}.wav"
            logger.info("audio_key_as_workfile_id %s", audio_key_as_workfile_id)

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_source:
                s3.download_file(_HMS_R2_BUCKET, source_key, tmp_source.name)
                tmp_source_path = tmp_source.name

            file_to_export = _get_splitted_audio(
                tmp_source_path, segment_start_time, segment_end_time
            )

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_dest:
                file_to_export.export(tmp_dest.name, format="wav")
                tmp_dest_path = tmp_dest.name
                uploaded = _upload_if_not_exists(
                    s3, _HMS_R2_BUCKET, tmp_dest_path, dest_key
                )
                if uploaded:
                    logger.info("file uploaded to R2: %s", dest_key)
                else:
                    logger.info("file already present in R2: %s", dest_key)

            # Best-effort SPLIT_L4_STATUS bookkeeping (mirrors L3_job.py).
            # Wrapped because the column may not exist on every deployment.
            try:
                db.session.execute(
                    text("UPDATE AA_RECORD SET SPLIT_L4_STATUS = 'Running' WHERE ID = :record_id"),
                    {"record_id": record_id},
                )
            except Exception:
                logger.debug(
                    "SPLIT_L4_STATUS update skipped for AA_RECORD %s",
                    record_id, exc_info=True,
                )

            if not _is_valid_audio(tmp_dest_path):
                logger.warning("Invalid audio: %s", dest_key)
                return False

            for path in (tmp_source_path, tmp_dest_path):
                try:
                    os.remove(path)
                except OSError:
                    pass
            return True

        except Exception as exc:
            logger.warning(
                "_process_splitting_auto_segment: unable to process record %s: %s",
                row.get('ID'), exc,
            )
            for path in (tmp_source_path, tmp_dest_path):
                if not path:
                    continue
                try:
                    os.remove(path)
                except OSError:
                    pass
            return False

    return False



def _insert_aa_workfile_master(parent_aa_record_id, review_workfile_id) -> None:
    """
    Mirror the `aa_workfile_master` write from L3_job.py:

        INSERT INTO aa_workfile_master
            (PARENT_AA_RECORD_ID, REVIEW_WORKFILE_ID)
        VALUES (:PARENT_AA_RECORD_ID, :REVIEW_WORKFILE_ID)

    In L3_job.py, REVIEW_WORKFILE_ID is estimated with MAX(AA_IAP_WORKFILE.ID)+1
    before the raw AA_IAP_WORKFILE insert. Here we already have the flushed ORM
    ID, so we write the actual created workfile ID.
    """
    db.session.execute(
        text("""
            INSERT INTO aa_workfile_master (PARENT_AA_RECORD_ID, REVIEW_WORKFILE_ID)
            VALUES (:PARENT_AA_RECORD_ID, :REVIEW_WORKFILE_ID)
        """),
        {
            'PARENT_AA_RECORD_ID': str(parent_aa_record_id),
            'REVIEW_WORKFILE_ID':  str(review_workfile_id),
        },
    )


def _update_aa_record_relationship_l3_workfile(l4_aa_record_id, l3_workfile_id) -> None:
    """
    Mirror the AA_RECORD_RELATIONSHIPS update from L3_job.py:

        UPDATE AA_RECORD_RELATIONSHIPS
        SET L3_WORKFILE_ID = :l3_workfile_id
        WHERE L4_AA_RECORD_ID = :l4_aa_record_id
    """
    db.session.execute(
        text("""
            UPDATE AA_RECORD_RELATIONSHIPS
            SET L3_WORKFILE_ID = :l3_workfile_id
            WHERE L4_AA_RECORD_ID = :l4_aa_record_id
        """),
        {
            'l3_workfile_id':  l3_workfile_id,
            'l4_aa_record_id': l4_aa_record_id,
        },
    )


# CC_AUDIO.ETL_PROCESSED_FL convention:
#   'N' or NULL  → not yet picked up by CDM, eligible for allocation
#   'Y'          → already routed to an evaluator at least once; ineligible
ETL_PROCESSED_YES = 'Y'
ETL_PROCESSED_NO  = 'N'


def _audio_eligible_filter():
    """SQLAlchemy filter that keeps only CC_AUDIO rows still pending CDM pickup."""
    return or_(
        CCAudio.ETL_PROCESSED_FL.is_(None),
        CCAudio.ETL_PROCESSED_FL != ETL_PROCESSED_YES,
    )


def _mark_audio_processed(cc_audio_id) -> None:
    """
    Stamp CC_AUDIO.ETL_PROCESSED_FL = 'Y' once an audio has been successfully
    routed to an evaluator. Called inside the same transaction as the
    allocation/workfile writes so a rollback also un-marks the row.

    Used by the L4 path only. The L3 path must NOT touch CC_AUDIO — it
    flips state on AA_RECORD instead via `_mark_aa_record_routed`.
    """
    if not cc_audio_id:
        return
    CCAudio.query.filter_by(ID=cc_audio_id).update(
        {
            CCAudio.ETL_PROCESSED_FL: ETL_PROCESSED_YES,
            CCAudio.ETL_ROW_PROCESS_DTS: datetime.datetime.utcnow(),
        },
        synchronize_session=False,
    )


# AA_RECORD.L3_PROCESSED_FL convention:
#   'N'          → not yet routed to an L3 user, eligible for allocation
#   'Y'          → already routed by CDM at least once; ineligible
AA_RECORD_L3_PROCESSED_YES = 'Y'
AA_RECORD_L3_PROCESSED_NO  = 'N'
AA_RECORD_L3_ROUTED        = 'ROUTED'
AA_RECORD_L3_NOT_FOUND     = 'NOT_FOUND'
AA_RECORD_L3_INVALID_AUDIO = 'INVALID_AUDIO'


def _aa_record_eligible_filter():
    """SQLAlchemy filter that keeps only AA_RECORD rows with L3_PROCESSED_FL='N'."""
    return db.func.upper(db.func.trim(AARecord.L3_PROCESSED_FL)) == AA_RECORD_L3_PROCESSED_NO


def _is_aa_record_l3_pending(aa_record: AARecord) -> bool:
    """Return True only when the current AA_RECORD flag is explicitly 'N'."""
    flag = getattr(aa_record, 'L3_PROCESSED_FL', None)
    return str(flag).strip().upper() == AA_RECORD_L3_PROCESSED_NO


def _mark_aa_record_routed(aa_record_id) -> None:
    """
    Stamp AA_RECORD.L3_PROCESSED_FL = 'Y' once a record has been assigned
    to an L3 user.

    CDM_L3_STATUS / CDM_L3_ROUTED_DTS are also retained as internal CDM
    routing metadata for existing admin/debug views, but eligibility is driven
    strictly by L3_PROCESSED_FL.

    Called inside the same transaction as the allocation / workfile writes
    so a rollback also un-marks the row. This is the L3 counterpart to
    `_mark_audio_processed` — the L3 flow never updates CC_AUDIO.
    """
    if not aa_record_id:
        return
    AARecord.query.filter_by(ID=aa_record_id).update(
        {
            AARecord.L3_PROCESSED_FL:    AA_RECORD_L3_PROCESSED_YES,
            AARecord.CDM_L3_STATUS:     AA_RECORD_L3_ROUTED,
            AARecord.CDM_L3_ROUTED_DTS: datetime.datetime.utcnow(),
        },
        synchronize_session=False,
    )


def _mark_aa_record_l3_failed(aa_record_id, status: str = AA_RECORD_L3_NOT_FOUND) -> None:
    """
    Mark an AA_RECORD as no longer eligible for L3 allocation when its audio
    cannot be produced or validated.

    L3 eligibility is driven by L3_PROCESSED_FL='N', so setting it to 'Y'
    prevents the same broken record from being selected on every future
    request. CDM_L3_STATUS captures why it was skipped.
    """
    if not aa_record_id:
        return
    AARecord.query.filter_by(ID=aa_record_id).update(
        {
            AARecord.L3_PROCESSED_FL:    AA_RECORD_L3_PROCESSED_YES,
            AARecord.CDM_L3_STATUS:     status,
            AARecord.CDM_L3_ROUTED_DTS: datetime.datetime.utcnow(),
        },
        synchronize_session=False,
    )


# ---------------------------------------------------------------------------
# L4 CC_AUDIO helpers
# ---------------------------------------------------------------------------

def _f(val, default=0.5) -> float:
    """Coerce a raw label / number to a float in [0, 1] using the canonical scoring tables."""
    if isinstance(val, (int, float)):
        try:
            v = float(val)
            if 0.0 <= v <= 1.0:
                return v
        except (TypeError, ValueError):
            pass
    return default


def _audio_to_recording_dict(cc_audio: CCAudio) -> dict:
    """
    Build the allocator-facing recording row directly from a CC_AUDIO record.
    Used for L4 allocations where CC_AUDIO is the primary candidate source.
    sample_id is CC_AUDIO.ID (not an AAIAPWORKFILE ID).
    """
    return {
        'sample_id':                       cc_audio.ID,
        'user_id':                         cc_audio.primary_user_id,
        'base_effort_minute':              cc_audio.BASE_EFFORT_MINUTE or 5.0,
        'recording_time':                  cc_audio.safe_audio_length_seconds or 5.0,
        'difficulty_score':                cc_audio.DIFFICULTY_SCORE,
        'difficulty_level':                cc_audio.DIFFICULTY_LEVEL or 'medium',
        'audio_length':                    cc_audio.AUDIO_LENGTH,
        'mistake_level':                   constants.score_value('MISTAKE_LEVEL', cc_audio.MISTAKE_LEVEL),
        'background_noise_level':          constants.score_value('BACKGROUND_NOISE_LEVEL', cc_audio.BACKGROUND_NOISE_LEVEL),
        'repeats_pauses_stutter_level':    constants.score_value('REPEATS_PAUSES_STUTTER_LEVEL', cc_audio.REPEATS_PAUSES_STUTTER_LEVEL),
        'audio_issues_level':              constants.score_value('AUDIO_ISSUES_LEVEL', cc_audio.AUDIO_ISSUES_LEVEL),
        'recitation_speed':                constants.score_value('RECITATION_SPEED', cc_audio.RECITATION_SPEED),
        'voice_pitch':                     constants.score_value('VOICE_PITCH', cc_audio.VOICE_PITCH),
        'voice_clarity':                   constants.score_value('VOICE_CLARITY', cc_audio.VOICE_CLARITY),
        'audio_source':                    constants.score_value('AUDIO_SOURCE', cc_audio.AUDIO_SOURCE),
    }


def _audio_to_response_extras(cc_audio: CCAudio, iap_workfile=None) -> dict:
    """
    Build the extras dict for an L4 candidate sourced from CC_AUDIO.
    iap_workfile is the linked AA_IAP_WORKFILE row if one exists; may be None.
    """
    return {
        '_iap_workfile_id':    iap_workfile.ID if iap_workfile else None,
        '_workfile_name':      (iap_workfile.WORKFILE_NAME if iap_workfile
                                else cc_audio.AUDIO_KEY),
        '_audio_filepath':     (iap_workfile.AUDIO_FILEPATH if iap_workfile
                                else cc_audio.FILEPATH),
        '_filesave_filepath':  iap_workfile.FILESAVE_FILEPATH if iap_workfile else None,
        '_modelpred_filepath': iap_workfile.MODELPRED_FILEPATH if iap_workfile else None,
        '_stage':              'L4',
        '_cc_audio_id':        cc_audio.ID,
        '_last_moved_dt':      (str(iap_workfile.LAST_MOVED_DT)
                                if iap_workfile and iap_workfile.LAST_MOVED_DT else None),
        '_test_static_id':     iap_workfile.TEST_STATIC_ID if iap_workfile else None,
        '_user_stage':         iap_workfile.USER_STAGE if iap_workfile else None,
        '_duration_label':     constants.classify_duration(cc_audio.DURATION),
        '_audio_data':         None,   # populated lazily for selected items only
    }


def _workfile_to_extras(wf: AAIAPWORKFILE, cc_audio: CCAudio) -> dict:
    """
    Build the extras dict from a freshly provisioned L4 AAIAPWORKFILE.
    Used after provision_l4_workfile() so the API response is populated
    entirely from the real AA_IAP_WORKFILE row, not from CC_AUDIO fields.

    _user_id is set to ASSIGNED_USER_ID (the evaluator) so that
    _serialize_result returns the evaluator ID rather than the CC_AUDIO
    student/uploader ID.
    """
    return {
        '_iap_workfile_id':    wf.ID,
        '_user_id':            wf.ASSIGNED_USER_ID,   # evaluator_id
        '_workfile_name':      wf.WORKFILE_NAME,
        '_audio_filepath':     wf.AUDIO_FILEPATH,
        '_filesave_filepath':  wf.FILESAVE_FILEPATH,
        '_modelpred_filepath': wf.MODELPRED_FILEPATH,
        '_stage':              'L4',
        '_cc_audio_id':        cc_audio.ID,
        '_last_moved_dt':      str(wf.LAST_MOVED_DT) if wf.LAST_MOVED_DT else None,
        '_test_static_id':     wf.TEST_STATIC_ID,
        '_user_stage':         wf.USER_STAGE,
        '_duration_label':     constants.classify_duration(cc_audio.DURATION),
        '_audio_data':         cc_audio.to_json(),
    }


# ---------------------------------------------------------------------------
# DataFrame builders
# ---------------------------------------------------------------------------

def _build_recordings_df_l4(exclude_audio_ids: list = None):
    """
    L4 candidate pool — sourced directly from CC_AUDIO.

    A CC_AUDIO record is excluded when:
      • ETL_PROCESSED_FL is already 'Y' (already routed by CDM at some point), OR
      • CC_CDM_ALLOCATION has a pending/completed L4 row for that CCAUDIO_ID, OR
      • AA_IAP_WORKFILE has a row with that CC_AUDIO_ID whose ASSIGNED_USER_ID
        is already set (i.e. manually or previously assigned).

    sample_id in the returned DataFrame is CC_AUDIO.ID (not an AAIAPWORKFILE ID).
    extras_map carries _iap_workfile_id so callers can write back to AA_IAP_WORKFILE
    when a linked workfile row exists.
    """
    allocated_audio_ids = (
        db.session.query(CdmAllocation.CCAUDIO_ID)
        .filter(
            CdmAllocation.STATUS.in_(['pending', 'completed']),
            CdmAllocation.STAGE == 'L4',
            CdmAllocation.CCAUDIO_ID.isnot(None),
        )
    )
    assigned_audio_ids = (
        db.session.query(AAIAPWORKFILE.CC_AUDIO_ID)
        .filter(
            AAIAPWORKFILE.STAGE == 'L4',
            AAIAPWORKFILE.ASSIGNED_USER_ID.isnot(None),
            AAIAPWORKFILE.CC_AUDIO_ID.isnot(None),
        )
    )

    query = CCAudio.query.filter(
        _audio_eligible_filter(),
        ~CCAudio.ID.in_(allocated_audio_ids),
        ~CCAudio.ID.cast(db.String).in_(assigned_audio_ids),
    )
    if exclude_audio_ids:
        query = query.filter(~CCAudio.ID.in_(exclude_audio_ids))

    audio_rows = query.all()
    if not audio_rows:
        return pd.DataFrame(), {}

    audio_id_strs = [str(a.ID) for a in audio_rows]
    workfile_rows = (
        AAIAPWORKFILE.query
        .filter(
            AAIAPWORKFILE.STAGE == 'L4',
            AAIAPWORKFILE.CC_AUDIO_ID.in_(audio_id_strs),
        )
        .all()
    )
    workfile_by_audio = {int(wf.CC_AUDIO_ID): wf for wf in workfile_rows
                         if wf.CC_AUDIO_ID is not None}

    rows       = []
    extras_map = {}
    for audio in audio_rows:
        rows.append(_audio_to_recording_dict(audio))
        extras_map[audio.ID] = _audio_to_response_extras(
            audio, workfile_by_audio.get(audio.ID)
        )

    return pd.DataFrame(rows), extras_map


# ---------------------------------------------------------------------------
# L3 AA_RECORD helpers
# ---------------------------------------------------------------------------

def _aa_record_primary_user_id(rec: AARecord) -> int:
    """
    Pick the recitation-owner user_id from an AA_RECORD row.

    Priority mirrors CC_AUDIO.primary_user_id: student → unknown_user → teacher
    → pro_reciter → 0. Returns 0 (rather than None) so the allocator's
    user_quality_score lookup never receives NaN.
    """
    return (
        rec.STUDENT_ID_1
        or rec.UNKNOWN_USER_ID_1
        or rec.TEACHER_ID
        or rec.PRO_RECITER_ID
        or 0
    )


def _aa_record_to_recording_dict(rec: AARecord, parent_audio: CCAudio = None) -> dict:
    """
    Build the allocator-facing recording row from an AA_RECORD.
    Used for L3 allocations where AA_RECORD is the primary candidate source.
    sample_id is AA_RECORD.ID.
    """
    diff = compute_for_aa_record(rec, parent_audio=parent_audio)

    # Segment length (seconds) — fall back to parent CC_AUDIO duration so
    # the allocator never sees a zero/None recording_time.
    segment_secs = diff.get('segment_length_seconds')
    if not segment_secs and parent_audio is not None:
        try:
            segment_secs = float(parent_audio.DURATION) if parent_audio.DURATION else None
        except (TypeError, ValueError):
            segment_secs = None
    if not segment_secs:
        segment_secs = 60.0   # 1-minute neutral default

    # Audio-quality severities pulled directly from AA_RECORD's column
    # spellings, falling back to the parent CC_AUDIO when a column is
    # NULL on AA_RECORD itself.
    def _aa(col, parent_col=None):
        val = getattr(rec, col, None)
        if (val is None or val == '') and parent_audio is not None and parent_col:
            val = getattr(parent_audio, parent_col, None)
        return val

    return {
        'sample_id':                    rec.ID,
        'user_id':                      _aa_record_primary_user_id(rec),
        'base_effort_minute':           diff['base_effort_minute'],
        'recording_time':               segment_secs,
        'difficulty_score':             diff['difficulty_score'],
        'difficulty_level':             diff['difficulty_level'],
        'audio_length':                 _aa('RECORD_LENGTH', 'AUDIO_LENGTH'),
        'mistake_level':                constants.score_value(
            'MISTAKE_LEVEL',
            getattr(parent_audio, 'MISTAKE_LEVEL', None) if parent_audio else None),
        'background_noise_level':       constants.score_value(
            'BACKGROUND_NOISE_LEVEL',
            _aa('RECORD_BACKGROUND_NOISE', 'BACKGROUND_NOISE_LEVEL')),
        'repeats_pauses_stutter_level': constants.score_value(
            'REPEATS_PAUSES_STUTTER_LEVEL',
            _aa('REPEATS_PAUSE_STUTTER_LEVEL', 'REPEATS_PAUSES_STUTTER_LEVEL')),
        'audio_issues_level':           constants.score_value(
            'AUDIO_ISSUES_LEVEL',
            _aa('RECORD_AUDIO_ISSUES_LEVEL', 'AUDIO_ISSUES_LEVEL')),
        'recitation_speed':             constants.score_value(
            'RECITATION_SPEED',
            _aa('RECITER_PACE', 'RECITATION_SPEED')),
        'voice_pitch':                  constants.score_value(
            'VOICE_PITCH',
            _aa('RECITER_VOICE_PITCH', 'VOICE_PITCH')),
        'voice_clarity':                constants.score_value(
            'VOICE_CLARITY',
            _aa('RECITER_VOICE_CLARITY', 'VOICE_CLARITY')),
        'audio_source':                 constants.score_value(
            'AUDIO_SOURCE',
            getattr(parent_audio, 'AUDIO_SOURCE', None) if parent_audio else None),
    }


def _aa_record_to_response_extras(rec: AARecord, parent_audio: CCAudio = None) -> dict:
    """
    Build the extras dict for an L3 candidate sourced from AA_RECORD.

    The eventual AA_IAP_WORKFILE row will be created by
    `provision_l3_workfile_from_aa_record(...)` once allocation succeeds,
    so this dict carries the *future* file paths that the provisioning
    helper will assemble. Concrete WORKFILE_NAME / *_FILEPATH values are
    overwritten by `_workfile_to_extras_l3` after provisioning.
    """
    return {
        '_iap_workfile_id':    None,
        '_aa_record_id':       rec.ID,
        '_workfile_name':      rec.RECORD_NAME,
        '_audio_filepath':     rec.RECORD_FILEPATH,
        '_filesave_filepath':  None,
        '_modelpred_filepath': None,
        '_stage':              'L3',
        '_cc_audio_id':        (parent_audio.ID if parent_audio else
                                rec.WORKFILE_ID),  # AA_IAP_WORKFILE.ID of parent
        '_last_moved_dt':      (rec.ROW_PROC_DTS.isoformat()
                                if rec.ROW_PROC_DTS else None),
        '_test_static_id':     None,
        '_user_stage':         rec.STAGE,
        '_duration_label':     constants.classify_duration(
            getattr(parent_audio, 'DURATION', None) if parent_audio else None),
        '_audio_data':         None,
    }


def _workfile_to_extras_l3(wf: AAIAPWORKFILE,
                            rec: AARecord,
                            parent_audio: CCAudio = None) -> dict:
    """
    Refresh the extras dict from a freshly provisioned L3 AAIAPWORKFILE so
    the API response carries the real paths/IDs (mirrors `_workfile_to_extras`
    for L4).
    """
    return {
        '_iap_workfile_id':    wf.ID,
        '_aa_record_id':       rec.ID,
        '_user_id':            wf.ASSIGNED_USER_ID,
        '_workfile_name':      wf.WORKFILE_NAME,
        '_audio_filepath':     wf.AUDIO_FILEPATH,
        '_filesave_filepath':  wf.FILESAVE_FILEPATH,
        '_modelpred_filepath': wf.MODELPRED_FILEPATH,
        '_stage':              'L3',
        '_cc_audio_id':        wf.CC_AUDIO_ID,
        '_last_moved_dt':      str(wf.LAST_MOVED_DT) if wf.LAST_MOVED_DT else None,
        '_test_static_id':     wf.TEST_STATIC_ID,
        '_user_stage':         wf.USER_STAGE,
        '_duration_label':     constants.classify_duration(
            getattr(parent_audio, 'DURATION', None) if parent_audio else None),
        '_audio_data':         rec.to_json() if hasattr(rec, 'to_json') else None,
    }


def _build_recordings_df_l3_aa_record(exclude_record_ids: list = None):
    """
    L3 candidate pool — sourced directly from AA_RECORD.

    An AA_RECORD row is excluded when:
      • ETL_Active_FL != 'Y' (defensive — keeps disabled segments out), OR
      • L3_PROCESSED_FL != 'N' (only rows explicitly pending L3 pickup are
        eligible), OR
      • An L3 AA_IAP_WORKFILE has already been created for the record
        (row already routed to an L3 user previously), OR
      • PR_TR_1 / FE_TRANSCRIPTION are both empty (nothing for L3 to verify).

    Returns (DataFrame, extras_map) keyed by AA_RECORD.ID.

    NOTE: when `compute_for_aa_record` runs we want the parent CC_AUDIO so
    MISTAKE_LEVEL / AUDIO_SOURCE can fall back to the source recording's
    values. We bulk-load it via WORKFILE_ID → AA_IAP_WORKFILE → CC_AUDIO_ID.
    """
    # Records already routed to an L3 user via a previous allocation/workfile.
    routed_record_ids = (
        db.session.query(AAIAPWORKFILE.AA_RECORD_ID)
        .filter(
            AAIAPWORKFILE.STAGE == 'L3',
            AAIAPWORKFILE.AA_RECORD_ID.isnot(None),
        )
    )

    query = (
        AARecord.query
        .filter(
            or_(AARecord.ETL_Active_FL == 'Y',
                AARecord.ETL_Active_FL.is_(None)),
            _aa_record_eligible_filter(),
            ~AARecord.ID.cast(db.String).in_(routed_record_ids),
        )
    )
    if exclude_record_ids:
        query = query.filter(~AARecord.ID.in_(exclude_record_ids))

    records = query.all()
    if not records:
        return pd.DataFrame(), {}

    # Bulk-load parent AA_IAP_WORKFILE → CC_AUDIO so the per-record loop
    # below doesn't issue N+1 queries.
    parent_workfile_ids = [int(r.WORKFILE_ID) for r in records
                           if r.WORKFILE_ID and str(r.WORKFILE_ID).isdigit()]
    parent_workfile_map: dict = {}
    if parent_workfile_ids:
        for wf in AAIAPWORKFILE.query.filter(
            AAIAPWORKFILE.ID.in_(parent_workfile_ids)
        ).all():
            parent_workfile_map[wf.ID] = wf

    parent_audio_ids = [
        int(wf.CC_AUDIO_ID) for wf in parent_workfile_map.values()
        if wf.CC_AUDIO_ID and str(wf.CC_AUDIO_ID).isdigit()
    ]
    parent_audio_map: dict = {}
    if parent_audio_ids:
        for a in CCAudio.query.filter(CCAudio.ID.in_(parent_audio_ids)).all():
            parent_audio_map[a.ID] = a

    rows: list = []
    extras_map: dict = {}
    for rec in records:
        # Skip records with no L3-meaningful payload.
        if not (rec.PR_TR_1 or rec.FE_TRANSCRIPTION):
            continue

        parent_wf = parent_workfile_map.get(
            int(rec.WORKFILE_ID)) if rec.WORKFILE_ID and str(rec.WORKFILE_ID).isdigit() else None
        parent_audio = (parent_audio_map.get(int(parent_wf.CC_AUDIO_ID))
                        if parent_wf and parent_wf.CC_AUDIO_ID
                        and str(parent_wf.CC_AUDIO_ID).isdigit()
                        else None)

        rows.append(_aa_record_to_recording_dict(rec, parent_audio))
        extras_map[rec.ID] = _aa_record_to_response_extras(rec, parent_audio)
        # Stash the live ORM rows so the allocator can hand them straight
        # to provision_l3_workfile_from_aa_record without a second SELECT.
        extras_map[rec.ID]['_aa_record_obj']    = rec
        extras_map[rec.ID]['_parent_audio_obj'] = parent_audio

    return pd.DataFrame(rows), extras_map


def _build_recordings_df(stage: str, exclude_ids: list = None):
    """
    Return (recordings_df, extras_map) for the given stage.

    L3  — candidates are sourced from AA_RECORD; the assignment is created
          fresh by `provision_l3_workfile_from_aa_record` after allocation.
    L4  — candidates are sourced from CC_AUDIO; assignment is checked via
          AA_IAP_WORKFILE.ASSIGNED_USER_ID and CC_CDM_ALLOCATION.

    extras_map keys are always the sample_id used by the allocator:
      L3 → AARecord.ID
      L4 → CCAudio.ID
    """
    if stage == 'L4':
        return _build_recordings_df_l4(exclude_audio_ids=exclude_ids)

    if stage == 'L3':
        return _build_recordings_df_l3_aa_record(exclude_record_ids=exclude_ids)

    # Fallback: empty pool for any unknown stage so the allocator
    # downstream simply returns "no candidates" rather than blowing up.
    logger.warning("_build_recordings_df: unknown stage %r — returning empty pool", stage)
    return pd.DataFrame(), {}


def _build_evaluator_df(evaluator_ids: list) -> pd.DataFrame:
    """
    Fetch AA_IAP_USERS rows and convert to the schema expected by
    OnDemandAllocator.

    Capacity fields (available_effort_minute, weekly_effort_limit,
    accuracy_target, skill_level, experience_years) are read from the
    CDM_* columns on AA_IAP_USERS.  When a CDM column is NULL the model
    falls back to a default derived from USER_STAGE so the allocator
    always has usable numbers.

    Only evaluators with CDM_IS_ACTIVE_FL = 'Y' (or NULL, treated as 'Y')
    are included so inactive users are never allocated workfiles.
    """
    users = AAIAPUSERS.query.filter(
        AAIAPUSERS.ID.in_(evaluator_ids),
        db.or_(AAIAPUSERS.CDM_IS_ACTIVE_FL == 'Y', AAIAPUSERS.CDM_IS_ACTIVE_FL.is_(None)),
    ).all()
    if not users:
        return pd.DataFrame()
    return pd.DataFrame([u.to_evaluator_dict() for u in users])


def _build_performance_df() -> pd.DataFrame:
    """
    Build performance history from completed CC_CDM_ALLOCATION rows.
    This trains the allocator's per-evaluator accuracy/effort profiles.
    """
    completed = CdmAllocation.query.filter(CdmAllocation.STATUS == 'completed').all()
    if not completed:
        return pd.DataFrame()
    return pd.DataFrame([r.to_performance_dict() for r in completed])


def _build_users_df(recordings_df: pd.DataFrame) -> pd.DataFrame:
    """Minimal users DataFrame derived from the candidate pool."""
    if recordings_df.empty or 'user_id' not in recordings_df.columns:
        return pd.DataFrame(columns=['user_id', 'user_quality_score'])
    user_ids = recordings_df['user_id'].dropna().unique()
    return pd.DataFrame({'user_id': user_ids, 'user_quality_score': 0.75})


def _build_pair_performance(evaluator_ids: list,
                             recordings_df: pd.DataFrame) -> dict:
    """
    Bulk-load CC_CDM_EVALUATOR_PERFORMANCE rows for every (evaluator, user)
    combination present in the candidate pool — feeds Feature 3.

    Returns dict keyed by (evaluator_id, user_id) with each value being the
    CdmEvaluatorPerformance.to_predictor_dict() output.

    Empty dict means "no historical pairs" — the allocator falls back to
    evaluator-wide / global defaults.
    """
    if recordings_df.empty or 'user_id' not in recordings_df.columns:
        return {}
    user_ids = [int(uid) for uid in recordings_df['user_id'].dropna().unique() if uid]
    if not user_ids or not evaluator_ids:
        return {}

    rows = (
        CdmEvaluatorPerformance.query
        .filter(
            CdmEvaluatorPerformance.EVALUATOR_ID.in_(evaluator_ids),
            CdmEvaluatorPerformance.USER_ID.in_(user_ids),
        )
        .all()
    )
    return {(r.EVALUATOR_ID, r.USER_ID): r.to_predictor_dict() for r in rows}


# ---------------------------------------------------------------------------
# Core allocator bootstrap
# ---------------------------------------------------------------------------

def _run_allocator(
    evaluator_ids: list,
    stage: str,
    exclude_ids: list = None,
) -> Tuple["OnDemandAllocator", pd.DataFrame, dict]:
    """
    Initialise and fit OnDemandAllocator with DB-backed DataFrames.
    Returns (allocator, recordings_df, extras_map).
    """
    recordings_df, extras_map = _build_recordings_df(stage, exclude_ids=exclude_ids)
    evaluator_df              = _build_evaluator_df(evaluator_ids)
    performance_df            = _build_performance_df()
    users_df                  = _build_users_df(recordings_df)
    pair_perf                 = _build_pair_performance(evaluator_ids, recordings_df)

    config    = AllocationConfig()
    allocator = OnDemandAllocator(config)

    allocator.recordings_df  = recordings_df
    allocator.evaluators_df  = evaluator_df
    allocator.users_df       = users_df
    allocator.performance_df = performance_df

    allocator._initialize_evaluator_states()
    if not performance_df.empty:
        allocator.fit()

    allocator.set_pair_performance(pair_perf)

    logger.info(
        "Allocator bootstrapped: %d candidates, %d evaluators, %d completed perf rows, "
        "%d (evaluator,user) history rows",
        len(recordings_df), len(evaluator_df), len(performance_df), len(pair_perf),
    )

    return allocator, recordings_df, extras_map


# ---------------------------------------------------------------------------
# Post-allocation workfile update
# ---------------------------------------------------------------------------

def _assign_workfile(workfile_id: int, evaluator_id: int, cc_audio_id=None):
    """
    Update AA_IAP_WORKFILE to mark the file as assigned by CDM.
    Sets ASSIGNED_USER_ID, TO_DO_MOVED_DTS, LAST_MOVED_BY.
    Status stays 'To Do' — the evaluator picks it up through the normal IAP flow.

    For L3, workfile_id is the AA_IAP_WORKFILE.ID (direct lookup).
    For L4, workfile_id may be None; cc_audio_id is used to locate the row via
    CC_AUDIO_ID (the string FK column on AA_IAP_WORKFILE).
    """
    now_str = datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    update_vals = {
        'ASSIGNED_USER_ID': evaluator_id,
        'TO_DO_MOVED_DTS':  now_str,
        'LAST_MOVED_BY':    'CDM',
        'CURR_ROW_FL':      'Y',
    }
    if workfile_id:
        AAIAPWORKFILE.query.filter_by(ID=workfile_id).update(update_vals)
    elif cc_audio_id:
        AAIAPWORKFILE.query.filter(
            AAIAPWORKFILE.CC_AUDIO_ID == str(cc_audio_id),
            AAIAPWORKFILE.STAGE == 'L4',
        ).update(update_vals)


def _set_workfile_allocation_id(workfile_id: int, allocation_id: int):
    """Stamp ALLOCATION_ID on AA_IAP_WORKFILE after the CDM allocation row is written."""
    AAIAPWORKFILE.query.filter_by(ID=workfile_id).update({'ALLOCATION_ID': allocation_id})


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def _serialize_result(result, extras: dict, stage: str) -> dict:
    """
    Build the API response dict for one allocated workfile.

    Parameters
    ----------
    result  : AllocationResult  from OnDemandAllocator
    extras  : dict              from AAIAPWORKFILE.to_response_extras() — never
                                passed through pandas so types are preserved
    stage   : str               'L3' or 'L4'
    """
    rec       = result.selected_recording
    predicted = rec.predicted_metrics or {}
    score     = rec.difficulty_score
    return {
        'id':                 rec.sample_id,
        'WORKFILE_NAME':      extras.get('_workfile_name'),
        'AUDIO_FILEPATH':     extras.get('_audio_filepath'),
        'FILESAVE_FILEPATH':  extras.get('_filesave_filepath'),
        'MODELPRED_FILEPATH': extras.get('_modelpred_filepath'),
        'CC_AUDIO_ID':        extras.get('_cc_audio_id'),
        'date':               extras.get('_last_moved_dt'),
        'duration':           extras.get('_duration_label'),
        'test_static_id':     extras.get('_test_static_id'),
        'user_id':            extras.get('_user_id', rec.user_id),
        'user_stage':         extras.get('_user_stage'),
        'stage':              stage,
        'audio_data':         extras.get('_audio_data'),
        'difficulty_score':       round(score, 2) if score is not None else None,
        'difficulty_level':       rec.difficulty_level or constants.difficulty_label(score),
        'predicted_accuracy':     predicted.get('accuracy'),
        'predicted_satisfaction': predicted.get('satisfaction'),
        'predicted_effort_mins':  predicted.get('effort_minutes'),
        'pair_sample_count':      predicted.get('pair_sample_count', 0),
        'pair_confidence':        predicted.get('pair_confidence', 0.0),
        'allocation_score':       round(result.score, 6),
        'rationale':              result.rationale,
        'constraint_violations':  result.constraint_violations,
    }


def _write_allocation_row(
    evaluator_id: int,
    workfile_id: int,
    cc_audio_id,
    stage: str,
    cdm_mode: str,
    result,
    bias_factor: float,
    aa_record_id=None,
) -> int:
    """
    Insert one CC_CDM_ALLOCATION row (and return its primary-key ID).

    Predicted metrics + per-parameter rationale captured here:
      • PREDICTED_ACCURACY     ← result.predicted_metrics['accuracy']
      • PREDICTED_SATISFACTION ← result.predicted_metrics['satisfaction']
      • PREDICTED_EFFORT_MINS  ← result.predicted_metrics['effort_minutes']
      • RATIONALE              ← result.rationale (truncated to 2000 chars)

    Stage references:
      • STAGE = 'L4' → CCAUDIO_ID is set, AA_RECORD_ID is NULL
      • STAGE = 'L3' → AA_RECORD_ID is set; CCAUDIO_ID stays NULL because
        the L3 flow must not write to CC_AUDIO. The parent CC_AUDIO
        remains traceable via AA_IAP_WORKFILE.CC_AUDIO_ID on the linked
        workfile row.
    """
    rec       = result.selected_recording
    predicted = rec.predicted_metrics or {}

    # L3 must not couple to CC_AUDIO at the allocation level.
    ccaudio_fk  = None if stage == 'L3' else (int(cc_audio_id) if cc_audio_id else None)
    aa_record_fk = int(aa_record_id) if aa_record_id else None

    row = CdmAllocation(
        EVALUATOR_ID           = evaluator_id,
        IAP_WORKFILE_ID        = workfile_id,
        CCAUDIO_ID             = ccaudio_fk,
        AA_RECORD_ID           = aa_record_fk,
        USER_ID                = rec.user_id or None,
        CDM_MODE               = cdm_mode,
        STAGE                  = stage,
        ALLOCATION_SCORE       = round(result.score, 6),
        PREDICTED_ACCURACY     = predicted.get('accuracy'),
        PREDICTED_SATISFACTION = predicted.get('satisfaction'),
        PREDICTED_EFFORT_MINS  = predicted.get('effort_minutes'),
        DIFFICULTY_SCORE       = round(rec.difficulty_score, 2) if rec.difficulty_score is not None else None,
        DIFFICULTY_LEVEL       = rec.difficulty_level or constants.difficulty_label(rec.difficulty_score),
        BIAS_FACTOR            = bias_factor,
        RATIONALE              = (result.rationale or '')[:2000] or None,
        STATUS                 = 'pending',
        ALLOCATED_DTS          = datetime.datetime.utcnow(),
        ETL_ADD_DTS            = datetime.datetime.utcnow(),
    )
    db.session.add(row)
    db.session.flush()
    return row.ID


def _write_decision_rows(
    allocation_id: int,
    evaluator_id: int,
    cc_audio_id,
    iap_workfile_id,
    stage: str,
    result,
) -> int:
    """
    Persist the per-parameter rationale into CC_CDM_ALLOCATION_DECISION.

    Returns the number of rows inserted.  Errors are logged but never raise
    — losing a decision row should not block the allocation itself.
    """
    breakdown = getattr(result, 'decision_breakdown', None) or []
    if not breakdown:
        return 0
    try:
        rows = CdmAllocationDecision.bulk_record(
            allocation_id   = allocation_id,
            decisions       = breakdown,
            evaluator_id    = evaluator_id,
            ccaudio_id      = int(cc_audio_id) if cc_audio_id else None,
            iap_workfile_id = iap_workfile_id,
            stage           = stage,
        )
        db.session.flush()
        return len(rows)
    except Exception as exc:  # pragma: no cover — we never want to break allocation on log failure
        logger.warning(
            "Failed to persist %d allocation-decision rows for allocation %s: %s",
            len(breakdown), allocation_id, exc, exc_info=True,
        )
        return 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def allocate_on_demand(
    evaluator_id: int,
    stage: str,
    n_files: int = 2,
    effort_bias_factor: float = 1.0,
) -> list:
    """
    On-Demand mode: select the n_files best workfiles for one IAP evaluator.

    Returns list of assignment dicts, each containing allocation_id + workfile info.
    """
    allocator, recordings_df, extras_map = _run_allocator([evaluator_id], stage)
    if recordings_df.empty:
        return []

    # Both L3 and L4 provisioning hit R2 with the evaluator's USERNAME, so
    # resolve it up front for either stage.
    ev = AAIAPUSERS.query.get(evaluator_id)
    evaluator_username = ev.USERNAME if ev else str(evaluator_id)

    assignments = []
    excluded    = []

    # Keep trying additional candidates when one cannot be provisioned
    # (for example, L3 source audio missing in R2). `excluded` prevents
    # retrying the same candidate in this request, and the L3 failure marker
    # below prevents retrying broken records in future requests.
    while len(assignments) < n_files and len(excluded) < len(recordings_df):
        result = allocator.allocate_recording(
            evaluator_id, exclude_recording_ids=excluded
        )
        if result is None:
            break

        sample_id   = result.selected_recording.sample_id
        excluded.append(sample_id)
        extras      = extras_map.get(sample_id, {})
        cc_audio_id = extras.get('_cc_audio_id')

        if stage == 'L4':
            cc_audio = CCAudio.query.get(cc_audio_id)
            if cc_audio is None:
                logger.warning("allocate_on_demand: CC_AUDIO %s not found, skipping", cc_audio_id)
                continue

            try:
                new_workfiles = provision_l4_workfile(
                    cc_audio, evaluator_id, evaluator_username
                )
            except Exception as exc:
                logger.error(
                    "allocate_on_demand: L4 provisioning failed for CC_AUDIO %s: %s",
                    cc_audio_id, exc, exc_info=True,
                )
                continue

            for wf in new_workfiles:
                chunk_extras  = _workfile_to_extras(wf, cc_audio)
                allocation_id = _write_allocation_row(
                    evaluator_id, wf.ID, cc_audio_id,
                    stage, 'on_demand', result, effort_bias_factor,
                )
                _set_workfile_allocation_id(wf.ID, allocation_id)
                _write_decision_rows(
                    allocation_id, evaluator_id, cc_audio_id, wf.ID, stage, result,
                )
                entry = _serialize_result(result, chunk_extras, stage)
                entry['allocation_id'] = allocation_id
                assignments.append(entry)

            # All chunks for this CC_AUDIO have been allocated → flip the
            # ETL flag once so subsequent allocator runs skip it.
            _mark_audio_processed(cc_audio_id)

        elif stage == 'L3':
            aa_record    = extras.get('_aa_record_obj')
            parent_audio = extras.get('_parent_audio_obj')
            if aa_record is None:
                # Cold-cache fallback: re-fetch by sample_id (= AA_RECORD.ID)
                aa_record = AARecord.query.get(sample_id)
            if aa_record is None:
                logger.warning(
                    "allocate_on_demand: AA_RECORD %s not found, skipping", sample_id
                )
                continue
            db.session.refresh(aa_record)
            if not _is_aa_record_l3_pending(aa_record):
                logger.info(
                    "allocate_on_demand: AA_RECORD %s skipped because "
                    "L3_PROCESSED_FL=%r",
                    aa_record.ID, aa_record.L3_PROCESSED_FL,
                )
                continue

            # ----------------------------------------------------------- #
            # Direct inline replication of L3_job.py for this AA_RECORD.  #
            # No imports from cdm_workfile_service — every helper used    #
            # here is the L3_job.py-style one defined above in this file.#
            # ----------------------------------------------------------- #
            s3 = _s3_client()

            # 1. process_splitting_auto_segment(session, row['ID'])
            try:
                segment_ready = _process_splitting_auto_segment(aa_record.ID)
            except Exception as exc:
                logger.error(
                    "allocate_on_demand: L3 segment splitting failed for "
                    "AA_RECORD %s: %s", aa_record.ID, exc, exc_info=True,
                )
                continue
            if not segment_ready:
                logger.info(
                    "allocate_on_demand: L3 segment not ready for AA_RECORD %s; "
                    "marking NOT_FOUND and trying next candidate",
                    aa_record.ID,
                )
                _mark_aa_record_l3_failed(aa_record.ID, AA_RECORD_L3_NOT_FOUND)
                continue

            # 2. object_key + R2 existence check
            object_key = f"data/Dev/AA_RECORD/L3_{aa_record.ID}.wav"
            try:
                s3.head_object(Bucket=_HMS_R2_BUCKET, Key=object_key)
            except botocore.exceptions.ClientError as exc:
                if not _is_missing_object_error(exc):
                    raise
                logger.info("File not found in R2: %s", object_key)
                _mark_aa_record_l3_failed(aa_record.ID, AA_RECORD_L3_NOT_FOUND)
                continue

            # 3. Download to temp file and validate
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
                s3.download_file(_HMS_R2_BUCKET, object_key, tmp_file.name)
                local_path = tmp_file.name

            if not _is_valid_audio(local_path):
                logger.info("Invalid audio file: %s", object_key)
                try:
                    os.remove(local_path)
                except OSError:
                    pass
                _mark_aa_record_l3_failed(aa_record.ID, AA_RECORD_L3_INVALID_AUDIO)
                continue

            # 4. filename = basename without extension (L3_<rec.ID>)
            filename, _ext = os.path.splitext(os.path.basename(object_key))
            workfile_name  = filename

            # 5. Move to evaluator's ToDo folder + write empty stubs
            audio_s3_key     = f"data/Dev/ToDo/{evaluator_username}/Audio/{workfile_name}.WKFL"
            modelpred_s3_key = f"data/Dev/ToDo/{evaluator_username}/ModelPred/{workfile_name}.json"
            filesave_s3_key  = f"data/Dev/ToDo/{evaluator_username}/FileSave/{workfile_name}.json"

            s3.upload_file(local_path, _R2_BUCKET, audio_s3_key)
            logger.info("Uploaded file to s3://%s/%s", _R2_BUCKET, audio_s3_key)
            try:
                os.remove(local_path)
            except OSError:
                pass

            # ModelPred stub — { "data": [] } per L3_job.py.
            model_pred_path = f"/tmp/{workfile_name}_modelpred.json"
            with open(model_pred_path, "w", encoding="utf-8") as outfile:
                json.dump({"data": []}, outfile)
            s3.upload_file(model_pred_path, _R2_BUCKET, modelpred_s3_key)
            logger.info(
                "Uploaded model predictions to s3://%s/%s",
                _R2_BUCKET, modelpred_s3_key,
            )

            # FileSave stub — L3_job.py copies a remote FileSaveBase.json
            # template that does not exist in this repo. We fall back to
            # the same empty-data shape we use for ModelPred.
            filesave_path = f"/tmp/{workfile_name}_filesave.json"
            with open(filesave_path, "w", encoding="utf-8") as outfile:
                json.dump({"data": []}, outfile)
            s3.upload_file(filesave_path, _R2_BUCKET, filesave_s3_key)
            logger.info(
                "Uploaded FileSave JSON to s3://%s/%s",
                _R2_BUCKET, filesave_s3_key,
            )

            # 6. Insert AA_IAP_WORKFILE row (L3_job.py INSERT mirror).
            now_str = datetime.datetime.utcnow().strftime("%Y/%m/%d %H:%M:%S")
            new_wf = AAIAPWORKFILE(
                WORKFILE_NAME      = f"{workfile_name}.WKFL",
                WORKFILE_STATUS    = 'ToDo',
                ASSIGNED_USER_ID   = evaluator_id,
                AUDIO_FILEPATH     = audio_s3_key,
                AUDIO_STATUS       = 'ToDo',
                AUDIO_KEY          = 'obsolete',
                FILESAVE_FILEPATH  = filesave_s3_key,
                FILESAVE_STATUS    = 'ToDo',
                MODELPRED_FILEPATH = modelpred_s3_key,
                MODELPRED_STATUS   = 'ToDo',
                LAST_MOVED_DT      = now_str,
                LAST_MOVED_BY      = 'ETL',
                CURR_ROW_FL        = 'Y',
                STAGE              = 'L3',
                REVIEW_FL          = 'N',
                CC_AUDIO_ID        = None,
                AA_RECORD_ID       = str(aa_record.ID),
                TEST_STATIC_ID     = None,
                TO_DO_MOVED_DTS    = now_str,
            )
            db.session.add(new_wf)
            db.session.flush()
            _update_aa_record_relationship_l3_workfile(sample_id, new_wf.ID)
            _insert_aa_workfile_master(aa_record.ID, new_wf.ID)
            logger.info(
                "allocate_on_demand: created AA_IAP_WORKFILE ID=%s for AA_RECORD %s",
                new_wf.ID, aa_record.ID,
            )

            # ----------------------------------------------------------- #
            # CDM allocation bookkeeping (unchanged — not S3-related).    #
            # ----------------------------------------------------------- #
            chunk_extras  = _workfile_to_extras_l3(new_wf, aa_record, parent_audio)
            allocation_id = _write_allocation_row(
                evaluator_id, new_wf.ID, None,
                stage, 'on_demand', result, effort_bias_factor,
                aa_record_id=aa_record.ID,
            )
            _set_workfile_allocation_id(new_wf.ID, allocation_id)
            _write_decision_rows(
                allocation_id, evaluator_id, None, new_wf.ID, stage, result,
            )
            # L3 must NOT update CC_AUDIO. Routing state lives on AA_RECORD.
            _mark_aa_record_routed(aa_record.ID)

            entry = _serialize_result(result, chunk_extras, stage)
            entry['allocation_id'] = allocation_id
            entry['aa_record_id']  = aa_record.ID
            assignments.append(entry)

        else:
            logger.warning("allocate_on_demand: unsupported stage %r — skipping", stage)
            continue

    db.session.commit()
    return assignments


def allocate_scheduled(
    evaluator_ids: list,
    stage: str,
    files_per_user: int = 9,
    effort_bias_factor: float = 1.0,
) -> dict:
    """
    Scheduled mode: assign files_per_user workfiles to every evaluator,
    targeting 100% capacity utilisation across the pool.

    Returns summary dict with total_assigned and per-evaluator breakdown.
    """
    allocator, recordings_df, extras_map = _run_allocator(evaluator_ids, stage)

    if recordings_df.empty:
        return {'total_assigned': 0, 'by_evaluator': [], 'stage': stage}

    # Both L3 and L4 provisioning need the evaluator's USERNAME for R2.
    users = AAIAPUSERS.query.filter(AAIAPUSERS.ID.in_(evaluator_ids)).all()
    username_by_id = {u.ID: u.USERNAME or str(u.ID) for u in users}

    total_assigned    = 0
    by_evaluator      = []
    globally_excluded = []

    for evaluator_id in evaluator_ids:
        excluded_for_eval = list(globally_excluded)
        eval_assignments  = []

        for _ in range(files_per_user):
            result = allocator.allocate_recording(
                evaluator_id, exclude_recording_ids=excluded_for_eval
            )
            if result is None:
                break

            sample_id   = result.selected_recording.sample_id
            excluded_for_eval.append(sample_id)
            globally_excluded.append(sample_id)

            extras      = extras_map.get(sample_id, {})
            cc_audio_id = extras.get('_cc_audio_id')

            if stage == 'L4':
                cc_audio = CCAudio.query.get(cc_audio_id)
                if cc_audio is None:
                    logger.warning(
                        "allocate_scheduled: CC_AUDIO %s not found, skipping", cc_audio_id
                    )
                    continue

                username = username_by_id.get(evaluator_id, str(evaluator_id))
                try:
                    new_workfiles = provision_l4_workfile(cc_audio, evaluator_id, username)
                except Exception as exc:
                    logger.error(
                        "allocate_scheduled: L4 provisioning failed for CC_AUDIO %s "
                        "(evaluator %s): %s",
                        cc_audio_id, evaluator_id, exc, exc_info=True,
                    )
                    continue

                for wf in new_workfiles:
                    chunk_extras  = _workfile_to_extras(wf, cc_audio)
                    allocation_id = _write_allocation_row(
                        evaluator_id, wf.ID, cc_audio_id,
                        stage, 'scheduled', result, effort_bias_factor,
                    )
                    _set_workfile_allocation_id(wf.ID, allocation_id)
                    _write_decision_rows(
                        allocation_id, evaluator_id, cc_audio_id, wf.ID, stage, result,
                    )
                    entry = _serialize_result(result, chunk_extras, stage)
                    entry['allocation_id'] = allocation_id
                    eval_assignments.append(entry)

                # All chunks for this CC_AUDIO have been allocated → flag it.
                _mark_audio_processed(cc_audio_id)

            elif stage == 'L3':
                aa_record    = extras.get('_aa_record_obj')
                parent_audio = extras.get('_parent_audio_obj')
                if aa_record is None:
                    aa_record = AARecord.query.get(sample_id)
                if aa_record is None:
                    logger.warning(
                        "allocate_scheduled: AA_RECORD %s not found, skipping", sample_id
                    )
                    continue

                username = username_by_id.get(evaluator_id, str(evaluator_id))
                try:
                    new_wf = provision_l3_workfile_from_aa_record(
                        aa_record, evaluator_id, username,
                        parent_audio=parent_audio,
                    )
                except Exception as exc:
                    logger.error(
                        "allocate_scheduled: L3 provisioning failed for AA_RECORD %s "
                        "(evaluator %s): %s",
                        sample_id, evaluator_id, exc, exc_info=True,
                    )
                    continue

                chunk_extras  = _workfile_to_extras_l3(new_wf, aa_record, parent_audio)
                allocation_id = _write_allocation_row(
                    evaluator_id, new_wf.ID, None,
                    stage, 'scheduled', result, effort_bias_factor,
                    aa_record_id=aa_record.ID,
                )
                _set_workfile_allocation_id(new_wf.ID, allocation_id)
                _write_decision_rows(
                    allocation_id, evaluator_id, None, new_wf.ID, stage, result,
                )
                # L3 must NOT update CC_AUDIO. Routing state lives on AA_RECORD.
                _mark_aa_record_routed(aa_record.ID)

                entry = _serialize_result(result, chunk_extras, stage)
                entry['allocation_id'] = allocation_id
                entry['aa_record_id']  = aa_record.ID
                eval_assignments.append(entry)

            else:
                logger.warning(
                    "allocate_scheduled: unsupported stage %r — skipping", stage
                )
                continue

        db.session.commit()
        total_assigned += len(eval_assignments)
        by_evaluator.append({
            'evaluator_id':   evaluator_id,
            'assigned_count': len(eval_assignments),
            'files':          eval_assignments,
        })

    return {
        'total_assigned': total_assigned,
        'stage':          stage,
        'by_evaluator':   by_evaluator,
    }


# ---------------------------------------------------------------------------
# Completion hook (Feature 3 — write back evaluator-on-user history)
# ---------------------------------------------------------------------------

def complete_allocation(
    allocation_id: int,
    actual_accuracy: float,
    actual_satisfaction: float,
    actual_effort_mins: float,
) -> dict:
    """
    Mark a CC_CDM_ALLOCATION row as completed and roll the actual measured
    metrics into the per (evaluator, user) running aggregate in
    CC_CDM_EVALUATOR_PERFORMANCE.  This is what makes future predictions
    "personalised" — each evaluator's track record on each user is
    retained and used the next time the same pair is seen.
    """
    allocation = CdmAllocation.query.get(allocation_id)
    if allocation is None:
        raise ValueError(f"CdmAllocation {allocation_id} not found")

    now = datetime.datetime.utcnow()
    allocation.STATUS              = 'completed'
    allocation.ACTUAL_ACCURACY     = float(actual_accuracy)
    allocation.ACTUAL_SATISFACTION = float(actual_satisfaction)
    allocation.ACTUAL_EFFORT_MINS  = float(actual_effort_mins)
    allocation.COMPLETED_DTS       = now

    # Update CC_CDM_EVALUATOR_PERFORMANCE for the (evaluator, user) pair
    perf_row = None
    user_id = allocation.USER_ID
    if user_id is None and allocation.CCAUDIO_ID:
        # Backfill USER_ID from CC_AUDIO if it wasn't captured at allocation time
        cc_audio = CCAudio.query.get(allocation.CCAUDIO_ID)
        if cc_audio is not None:
            user_id = cc_audio.primary_user_id
            allocation.USER_ID = user_id

    if allocation.EVALUATOR_ID and user_id:
        perf_row = CdmEvaluatorPerformance.get_or_create(
            evaluator_id=allocation.EVALUATOR_ID, user_id=user_id,
        )
        perf_row.update_with_completion(
            accuracy        = float(actual_accuracy),
            satisfaction    = float(actual_satisfaction),
            effort_minutes  = float(actual_effort_mins),
            difficulty_score= allocation.DIFFICULTY_SCORE,
            completed_dts   = now,
        )

    db.session.commit()
    return {
        'allocation':           allocation.to_json(),
        'evaluator_performance': perf_row.to_json() if perf_row else None,
    }
