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
import logging
import pandas as pd
from sqlalchemy import or_

from src import constants
from src.extensions import db
from src.models.cc_audio import CCAudio
from src.models.users import AAIAPUSERS
from src.models.workfile import AAIAPWORKFILE
from src.models.cdm import CdmAllocation
from src.models.allocation_decision import CdmAllocationDecision
from src.models.evaluator_performance import CdmEvaluatorPerformance
from src.services.on_demand_allocator import AllocationConfig, OnDemandAllocator
from src.services.cdm_workfile_service import provision_l4_workfile

logger = logging.getLogger(__name__)


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
        '_audio_data':         cc_audio.to_audio_data_dict(),
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
        '_audio_data':         cc_audio.to_audio_data_dict(),
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


def _build_recordings_df(stage: str, exclude_ids: list = None):
    """
    Return (recordings_df, extras_map) for the given stage.

    L3  — candidates are sourced from AA_IAP_WORKFILE (existing behaviour).
    L4  — candidates are sourced from CC_AUDIO; assignment is checked via
          AA_IAP_WORKFILE.ASSIGNED_USER_ID and CC_CDM_ALLOCATION.

    extras_map keys are always the sample_id used by the allocator:
      L3 → AAIAPWORKFILE.ID
      L4 → CCAudio.ID
    """
    if stage == 'L4':
        return _build_recordings_df_l4(exclude_audio_ids=exclude_ids)

    # ---- L3: source from AA_IAP_WORKFILE ---------------
    already_assigned = (
        db.session.query(CdmAllocation.IAP_WORKFILE_ID)
        .filter(
            CdmAllocation.STATUS.in_(['pending', 'completed']),
            CdmAllocation.STAGE == stage,
        )
        .subquery()
    )

    query = (
        AAIAPWORKFILE.query
        .filter(
            AAIAPWORKFILE.STAGE == stage,
            AAIAPWORKFILE.ASSIGNED_USER_ID.is_(None),
            ~AAIAPWORKFILE.ID.in_(already_assigned),
        )
    )
    if exclude_ids:
        query = query.filter(~AAIAPWORKFILE.ID.in_(exclude_ids))

    workfiles = query.all()
    if not workfiles:
        return pd.DataFrame(), {}

    cc_audio_ids = [
        int(w.CC_AUDIO_ID) for w in workfiles
        if w.CC_AUDIO_ID is not None
    ]
    audio_map = {}
    if cc_audio_ids:
        # Only pull in CC_AUDIO rows still eligible for CDM routing
        # (ETL_PROCESSED_FL is NULL or 'N'). Workfiles whose audio is
        # already 'Y' will be dropped below.
        audio_rows = (
            CCAudio.query
            .filter(CCAudio.ID.in_(cc_audio_ids), _audio_eligible_filter())
            .all()
        )
        audio_map = {a.ID: a for a in audio_rows}

    rows       = []
    extras_map = {}
    for wf in workfiles:
        cc_audio = audio_map.get(int(wf.CC_AUDIO_ID)) if wf.CC_AUDIO_ID else None
        # Skip workfiles whose audio has already been processed by CDM.
        # `cc_audio is None` here means either the workfile has no audio
        # or the audio was filtered out as ineligible.
        if wf.CC_AUDIO_ID is not None and cc_audio is None:
            continue
        rows.append(wf.to_recording_dict(cc_audio))
        extras = wf.to_response_extras(cc_audio)
        extras['_iap_workfile_id'] = wf.ID
        extras_map[wf.ID] = extras

    return pd.DataFrame(rows), extras_map


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
) -> tuple:
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
) -> int:
    """Insert one CC_CDM_ALLOCATION row (and return its primary-key ID)."""
    rec       = result.selected_recording
    predicted = rec.predicted_metrics or {}

    row = CdmAllocation(
        EVALUATOR_ID           = evaluator_id,
        IAP_WORKFILE_ID        = workfile_id,
        CCAUDIO_ID             = int(cc_audio_id) if cc_audio_id else None,
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

    evaluator_username = None
    if stage == 'L4':
        ev = AAIAPUSERS.query.get(evaluator_id)
        evaluator_username = ev.USERNAME if ev else str(evaluator_id)

    assignments = []
    excluded    = []

    for _ in range(n_files):
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
        else:
            iap_workfile_id = extras.get('_iap_workfile_id')
            allocation_id = _write_allocation_row(
                evaluator_id, iap_workfile_id, cc_audio_id,
                stage, 'on_demand', result, effort_bias_factor,
            )
            _set_workfile_allocation_id(iap_workfile_id, allocation_id)
            _assign_workfile(iap_workfile_id, evaluator_id, cc_audio_id=cc_audio_id)
            _write_decision_rows(
                allocation_id, evaluator_id, cc_audio_id, iap_workfile_id, stage, result,
            )
            _mark_audio_processed(cc_audio_id)
            entry = _serialize_result(result, extras, stage)
            entry['allocation_id'] = allocation_id
            assignments.append(entry)

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

    username_by_id: dict = {}
    if stage == 'L4':
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
            else:
                iap_workfile_id = extras.get('_iap_workfile_id')
                allocation_id = _write_allocation_row(
                    evaluator_id, iap_workfile_id, cc_audio_id,
                    stage, 'scheduled', result, effort_bias_factor,
                )
                _set_workfile_allocation_id(iap_workfile_id, allocation_id)
                _assign_workfile(iap_workfile_id, evaluator_id, cc_audio_id=cc_audio_id)
                _write_decision_rows(
                    allocation_id, evaluator_id, cc_audio_id, iap_workfile_id, stage, result,
                )
                _mark_audio_processed(cc_audio_id)
                entry = _serialize_result(result, extras, stage)
                entry['allocation_id'] = allocation_id
                eval_assignments.append(entry)

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
