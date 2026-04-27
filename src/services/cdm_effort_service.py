"""
CDM Effort Minutes Service

Computes BASE_EFFORT_MINUTE for AA_IAP_WORKFILE entries using historical
completion data stored in CC_CDM_ALLOCATION.  Results are cached in
CC_EFFORT_BASELINE (keyed by IAP_WORKFILE_ID) so the allocator can use
them without re-querying all history on every request.

Baseline algorithm
------------------
  1. Find completed CC_CDM_ALLOCATION rows that reference the SAME CC_AUDIO_ID
     (the same underlying audio file reviewed in other workfiles).
  2. Use the median of their ACTUAL_EFFORT_MINS (robust to outliers).
     Requires at least MIN_SAMPLES completions; otherwise fall back.
  3. Fall back: stage-wide median (all L3 or all L4 completions).
  4. Final fall back: hard-coded stage default.
  5. Multiply by the caller-supplied bias_factor (v1 temporary normalizer).

Stage defaults (no data):  L4 → 5.0 min  |  L3 → 2.0 min  |  other → 4.0 min
Stage floors:              L4 → 3.0 min  |  L3 → 1.0 min  |  other → 1.0 min
"""

import datetime
import statistics
from typing import Optional
from src.extensions import db
from src.models.cdm import CdmAllocation, CdmEffortBaseline


MIN_SAMPLES = 3

_STAGE_DEFAULTS = {'L4': 5.0, 'L3': 2.0}
_STAGE_FLOORS   = {'L4': 3.0, 'L3': 1.0}
_FALLBACK_DEFAULT = 4.0
_FALLBACK_FLOOR   = 1.0


def _stage_default(stage: Optional[str]) -> float:
    return _STAGE_DEFAULTS.get(stage or '', _FALLBACK_DEFAULT)


def _stage_floor(stage: Optional[str]) -> float:
    return _STAGE_FLOORS.get(stage or '', _FALLBACK_FLOOR)


def _stage_wide_median(stage: str) -> Optional[float]:
    """Median of all completed ACTUAL_EFFORT_MINS for a given stage (L3/L4)."""
    rows = (
        CdmAllocation.query
        .filter(
            CdmAllocation.STATUS == 'completed',
            CdmAllocation.STAGE == stage,
            CdmAllocation.ACTUAL_EFFORT_MINS.isnot(None),
        )
        .with_entities(CdmAllocation.ACTUAL_EFFORT_MINS)
        .all()
    )
    values = [r[0] for r in rows if r[0] and r[0] > 0]
    if len(values) >= MIN_SAMPLES:
        return statistics.median(values)
    return None


def compute_baseline(
    iap_workfile_id: int,
    stage: Optional[str],
    cc_audio_id: Optional[int] = None,
    bias_factor: float = 1.0,
) -> float:
    """
    Compute and cache the effort-minute baseline for a single AA_IAP_WORKFILE.

    Looks up prior completions by CC_AUDIO_ID (same audio in other workfiles)
    to reuse real measured data.  Falls back to stage-wide median or default.

    Returns the final baseline after bias_factor is applied.
    Stores the raw (pre-bias) median in CC_EFFORT_BASELINE for admin inspection.
    """
    # 1. Per-audio completions (same audio reviewed in other workfiles)
    values = []
    if cc_audio_id is not None:
        rows = (
            CdmAllocation.query
            .filter(
                CdmAllocation.CCAUDIO_ID == cc_audio_id,
                CdmAllocation.STATUS == 'completed',
                CdmAllocation.ACTUAL_EFFORT_MINS.isnot(None),
            )
            .with_entities(CdmAllocation.ACTUAL_EFFORT_MINS)
            .all()
        )
        values = [r[0] for r in rows if r[0] and r[0] > 0]

    if len(values) >= MIN_SAMPLES:
        baseline_raw = statistics.median(values)
    else:
        # 2. Stage-wide fallback
        baseline_raw = _stage_wide_median(stage or '') or _stage_default(stage)

    # 3. Apply floor
    baseline_raw = max(baseline_raw, _stage_floor(stage))

    # 4. Upsert into CC_EFFORT_BASELINE (store raw median, not biased value)
    record = CdmEffortBaseline.query.filter_by(IAP_WORKFILE_ID=iap_workfile_id).first()
    if record is None:
        record = CdmEffortBaseline(IAP_WORKFILE_ID=iap_workfile_id, STAGE=stage)
        db.session.add(record)

    record.STAGE            = stage
    record.SAMPLE_COUNT     = len(values)
    record.TOTAL_MINS_DATA  = round(sum(values), 4) if values else None
    record.BASELINE_EFFORT  = round(baseline_raw, 4)
    record.LAST_UPDATED_DTS = datetime.datetime.utcnow()
    db.session.commit()

    return round(baseline_raw * bias_factor, 4)


def get_baseline(
    iap_workfile_id: int,
    stage: Optional[str],
    cc_audio_id: Optional[int] = None,
    bias_factor: float = 1.0,
) -> float:
    """
    Hot path: return cached baseline for a workfile, computing it if missing.
    Called by the allocator for every candidate workfile.
    """
    record = CdmEffortBaseline.query.filter_by(IAP_WORKFILE_ID=iap_workfile_id).first()
    if record and record.BASELINE_EFFORT is not None:
        return round(record.BASELINE_EFFORT * bias_factor, 4)
    return compute_baseline(iap_workfile_id, stage, cc_audio_id, bias_factor)


def recompute_all_baselines(stage: Optional[str] = None) -> dict:
    """
    Batch-refresh CC_EFFORT_BASELINE for all workfiles that have at least one
    completed allocation.  Called by POST /cdm/recompute-baselines.

    Returns { refreshed, total_distinct_files }.
    """
    query = (
        db.session.query(
            CdmAllocation.IAP_WORKFILE_ID,
            CdmAllocation.STAGE,
            CdmAllocation.CCAUDIO_ID,
        )
        .filter(CdmAllocation.STATUS == 'completed')
        .distinct()
    )
    if stage:
        query = query.filter(CdmAllocation.STAGE == stage)

    rows = query.all()
    refreshed = 0
    for wf_id, stg, cc_id in rows:
        if wf_id is None:
            continue
        try:
            compute_baseline(wf_id, stg, cc_id)
            refreshed += 1
        except Exception:
            pass

    return {'refreshed': refreshed, 'total_distinct_files': len(rows)}
