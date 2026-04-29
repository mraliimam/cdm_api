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
"""

import datetime
import pandas as pd
from extensions import db
from models.cc_audio import CCAudio
from models.users import AAIAPUSERS
from models.workfile import AAIAPWORKFILE
from models.cdm import CdmAllocation
from services.on_demand_allocator import AllocationConfig, OnDemandAllocator




# ---------------------------------------------------------------------------
# DataFrame builders
# ---------------------------------------------------------------------------

def _build_recordings_df(stage: str, exclude_workfile_ids: list = None):
    """
    Query AA_IAP_WORKFILE for unassigned workfiles of the given stage,
    join with CC_AUDIO to attach audio quality params.

    Returns
    -------
    recordings_df : pd.DataFrame
        Allocator-facing columns only (sample_id, user_id, effort, audio params…).
        No nested Python objects — safe for pandas arithmetic and filtering.
    extras_map : dict  {sample_id (int) → extra_fields (dict)}
        All serialization fields (workfile paths, audio_data, dates…) kept
        outside the DataFrame to avoid pandas None→NaN and dict coercion issues.
    """
    already_assigned = (
        db.session.query(CdmAllocation.IAP_WORKFILE_ID)
        .filter(
            CdmAllocation.STATUS.in_(['pending', 'completed']),
            CdmAllocation.STAGE == stage,
        )
        .scalar_subquery()
    )

    query = (
        AAIAPWORKFILE.query
        .filter(
            AAIAPWORKFILE.STAGE == stage,
            AAIAPWORKFILE.ASSIGNED_USER_ID.is_(None),
            ~AAIAPWORKFILE.ID.in_(already_assigned),
        )
    )
    if exclude_workfile_ids:
        query = query.filter(~AAIAPWORKFILE.ID.in_(exclude_workfile_ids))

    workfiles = query.all()
    if not workfiles:
        return pd.DataFrame(), {}

    # Bulk-fetch CC_AUDIO rows for all linked CC_AUDIO_IDs in one query
    cc_audio_ids = [
        int(w.CC_AUDIO_ID) for w in workfiles
        if w.CC_AUDIO_ID is not None
    ]
    audio_map = {}
    if cc_audio_ids:
        audio_rows = CCAudio.query.filter(CCAudio.ID.in_(cc_audio_ids)).all()
        audio_map = {a.ID: a for a in audio_rows}

    rows       = []
    extras_map = {}
    for wf in workfiles:
        cc_audio = audio_map.get(int(wf.CC_AUDIO_ID)) if wf.CC_AUDIO_ID else None
        rows.append(wf.to_recording_dict(cc_audio))
        extras_map[wf.ID] = wf.to_response_extras(cc_audio)

    return pd.DataFrame(rows), extras_map


def _build_evaluator_df(evaluator_ids: list) -> pd.DataFrame:
    """
    Fetch AA_IAP_USERS rows and convert to the schema expected by
    OnDemandAllocator (mirrors version_1/evaluators.csv).

    Capacity fields (available_effort_minute, weekly_effort_limit,
    accuracy_target, skill_level, experience_years) are read from the
    CDM_* columns on AA_IAP_USERS.  When a CDM column is NULL the model
    falls back to a default derived from USER_STAGE.

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


# ---------------------------------------------------------------------------
# Core allocator bootstrap
# ---------------------------------------------------------------------------

def _run_allocator(
    evaluator_ids: list,
    stage: str,
    exclude_ids: list = None,
) -> tuple[OnDemandAllocator, pd.DataFrame, dict]:
    """
    Initialise and fit OnDemandAllocator with DB-backed DataFrames.
    Returns (allocator, recordings_df, extras_map).
    """
    recordings_df, extras_map = _build_recordings_df(stage, exclude_ids)
    evaluator_df              = _build_evaluator_df(evaluator_ids)
    performance_df            = _build_performance_df()
    users_df                  = _build_users_df(recordings_df)

    config    = AllocationConfig()
    allocator = OnDemandAllocator(config)

    allocator.recordings_df  = recordings_df
    allocator.evaluators_df  = evaluator_df
    allocator.users_df       = users_df
    allocator.performance_df = performance_df

    allocator._initialize_evaluator_states()
    if not performance_df.empty:
        allocator.fit()

    return allocator, recordings_df, extras_map


# ---------------------------------------------------------------------------
# Post-allocation workfile update
# ---------------------------------------------------------------------------

def _assign_workfile(workfile_id: int, evaluator_id: int):
    """
    Update AA_IAP_WORKFILE to mark the file as assigned by CDM.
    Sets ASSIGNED_USER_ID, TO_DO_MOVED_DTS, LAST_MOVED_BY.
    Status stays 'To Do' — the evaluator picks it up through the normal IAP flow.
    """
    now_str = datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    AAIAPWORKFILE.query.filter_by(ID=workfile_id).update({
        'ASSIGNED_USER_ID': evaluator_id,
        'TO_DO_MOVED_DTS':  now_str,
        'LAST_MOVED_BY':    'CDM',
        'CURR_ROW_FL':      'Y',
    })


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def _serialize_result(result, extras: dict, stage: str) -> dict:
    """
    Build the API response dict for one allocated workfile.

    Parameters
    ----------
    result  : AllocationResult  from OnDemandAllocator
    extras  : dict              from IapWorkfile.to_response_extras() — never
                                passed through pandas so types are preserved
    stage   : str               'L3' or 'L4'
    """
    rec       = result.selected_recording
    predicted = rec.predicted_metrics or {}
    return {
        # Workfile identity fields
        'id':                 rec.sample_id,
        'WORKFILE_NAME':      extras.get('_workfile_name'),
        'AUDIO_FILEPATH':     extras.get('_audio_filepath'),
        'FILESAVE_FILEPATH':  extras.get('_filesave_filepath'),
        'MODELPRED_FILEPATH': extras.get('_modelpred_filepath'),
        'CC_AUDIO_ID':        extras.get('_cc_audio_id'),
        'date':               extras.get('_last_moved_dt'),
        'duration':           extras.get('_duration_label'),
        'test_static_id':     extras.get('_test_static_id'),
        'user_id':            rec.user_id,
        'user_stage':         extras.get('_user_stage'),
        'stage':              stage,
        # Full CC_AUDIO recording parameters
        'audio_data':         extras.get('_audio_data'),
        # CDM allocation metadata
        'difficulty_score':        round(rec.difficulty_score, 2) if rec.difficulty_score else None,
        'difficulty_level':        _difficulty_label(rec.difficulty_score),
        'predicted_accuracy':      predicted.get('accuracy'),
        'predicted_satisfaction':  predicted.get('satisfaction'),
        'predicted_effort_mins':   predicted.get('effort_minutes'),
        'allocation_score':        round(result.score, 6),
        'rationale':               result.rationale,
        'constraint_violations':   result.constraint_violations,
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
    rec      = result.selected_recording
    predicted = rec.predicted_metrics or {}

    row = CdmAllocation(
        EVALUATOR_ID           = evaluator_id,
        IAP_WORKFILE_ID        = workfile_id,
        CCAUDIO_ID             = int(cc_audio_id) if cc_audio_id else None,
        CDM_MODE               = cdm_mode,
        STAGE                  = stage,
        ALLOCATION_SCORE       = round(result.score, 6),
        PREDICTED_ACCURACY     = predicted.get('accuracy'),
        PREDICTED_SATISFACTION = predicted.get('satisfaction'),
        PREDICTED_EFFORT_MINS  = predicted.get('effort_minutes'),
        DIFFICULTY_SCORE       = round(rec.difficulty_score, 2) if rec.difficulty_score else None,
        BIAS_FACTOR            = bias_factor,
        RATIONALE              = result.rationale,
        STATUS                 = 'pending',
        ALLOCATED_DTS          = datetime.datetime.utcnow(),
        ETL_ADD_DTS            = datetime.datetime.utcnow(),
    )
    db.session.add(row)
    db.session.flush()
    return row.ID


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

    Parameters
    ----------
    evaluator_id : int        AA_IAP_USERS.ID
    stage        : str        'L3' or 'L4'
    n_files      : int        Files to assign per request (default 2)
    effort_bias_factor: float v1 temporary normalizer

    Returns list of assignment dicts, each containing allocation_id + workfile info.
    """
    allocator, recordings_df, extras_map = _run_allocator([evaluator_id], stage)
    print(f'recordings_df {recordings_df}')

    if recordings_df.empty:
        return []

    assignments = []
    excluded    = []
    print(f'n_files {n_files}')

    for _ in range(n_files):
        result = allocator.allocate_recording(
            evaluator_id, exclude_recording_ids=excluded
        )
        print(f'result allcocator {result}')
        if result is None:
            break

        workfile_id = result.selected_recording.sample_id
        excluded.append(workfile_id)

        extras = extras_map.get(workfile_id, {})

        allocation_id = _write_allocation_row(
            evaluator_id, workfile_id, extras.get('_cc_audio_id'),
            stage, 'on_demand', result, effort_bias_factor,
        )
        _assign_workfile(workfile_id, evaluator_id)

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

    Parameters
    ----------
    evaluator_ids  : list of int   AA_IAP_USERS.ID values
    stage          : str           'L3' or 'L4'
    files_per_user : int           Target per evaluator (8-10; default 9)
    effort_bias_factor: float      v1 temporary normalizer

    Returns summary dict with total_assigned and per-evaluator breakdown.
    """
    allocator, recordings_df, extras_map = _run_allocator(evaluator_ids, stage)

    if recordings_df.empty:
        return {'total_assigned': 0, 'by_evaluator': [], 'stage': stage}

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

            workfile_id = result.selected_recording.sample_id
            excluded_for_eval.append(workfile_id)
            globally_excluded.append(workfile_id)

            extras = extras_map.get(workfile_id, {})

            allocation_id = _write_allocation_row(
                evaluator_id, workfile_id, extras.get('_cc_audio_id'),
                stage, 'scheduled', result, effort_bias_factor,
            )
            _assign_workfile(workfile_id, evaluator_id)

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


def _difficulty_label(score) -> str:
    if score is None:
        return 'medium'
    if score < 40:
        return 'low'
    if score < 70:
        return 'medium'
    return 'hard'
