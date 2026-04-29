"""
L3 difficulty + base-effort calculation (AA_RECORD–sourced).

Background
----------
At the L3 stage candidate audio segments come from AA_RECORD instead of
CC_AUDIO. Each AA_RECORD row is a small, supervisor-corrected slice of
the parent CC_AUDIO produced during L4 evaluation.

Difficulty is computed from the **same CC_AUDIO-level audio-quality
parameters** the L4 path uses (`AUDIO_LENGTH`, `MISTAKE_LEVEL`,
`AUDIO_SOURCE`, `BACKGROUND_NOISE_LEVEL`, `REPEATS_PAUSES_STUTTER_LEVEL`,
`AUDIO_ISSUES_LEVEL`, `RECITATION_SPEED`, `VOICE_PITCH`,
`VOICE_CLARITY`). The PR_TR_1 mistake-tag column is intentionally NOT
used here.

Per-segment specifics
---------------------
AA_RECORD carries L4-corrected copies of some of these parameters under
slightly different column names (e.g. `RECITER_PACE`,
`REPEATS_PAUSE_STUTTER_LEVEL`, `RECORD_BACKGROUND_NOISE`, …). When an
AA_RECORD value is present we prefer it over the parent CC_AUDIO value
since it reflects the L4-supervisor-validated state of that specific
slice. AUDIO_LENGTH is always the segment length, not the parent's full
length, so the resulting `base_effort_minute` is correctly scaled to
the L3 chunk.

The actual weighted-sum maths lives in `services.audio_difficulty` —
this module just builds the right input dict and delegates, ensuring L3
and L4 share a single source of truth.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from services.audio_difficulty import compute_audio_difficulty


# AA_RECORD column names → canonical CC_AUDIO column names that
# `compute_audio_difficulty` consumes. Only columns that exist on
# AA_RECORD with different spelling are listed; everything else is
# read straight from the parent CC_AUDIO.
_AA_RECORD_COLUMN_ALIAS: Dict[str, str] = {
    'BACKGROUND_NOISE_LEVEL':       'RECORD_BACKGROUND_NOISE',
    'REPEATS_PAUSES_STUTTER_LEVEL': 'REPEATS_PAUSE_STUTTER_LEVEL',
    'AUDIO_ISSUES_LEVEL':           'RECORD_AUDIO_ISSUES_LEVEL',
    'RECITATION_SPEED':             'RECITER_PACE',
    'VOICE_PITCH':                  'RECITER_VOICE_PITCH',
    'VOICE_CLARITY':                'RECITER_VOICE_CLARITY',
}

# Parameters AA_RECORD does not carry natively — always sourced from
# the parent CC_AUDIO row.
_PARENT_ONLY_COLUMNS = ('MISTAKE_LEVEL', 'AUDIO_SOURCE')


def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        f = float(val)
        if f != f:
            return None
        return f
    except (TypeError, ValueError):
        return None


def _resolve_segment_seconds(record_attrs: Dict[str, Any]) -> Optional[float]:
    """
    Compute the L3 segment length (seconds) from AA_RECORD time fields.

    Priority:
      1. SEGMENT_END_TIME − SEGMENT_START_TIME   (preferred, exact slice)
      2. RECORD_LENGTH                           (free-form string fallback)
      3. None
    """
    start = _safe_float(record_attrs.get('SEGMENT_START_TIME'))
    end   = _safe_float(record_attrs.get('SEGMENT_END_TIME'))
    if start is not None and end is not None and end > start:
        return end - start
    return _safe_float(record_attrs.get('RECORD_LENGTH'))


def _build_audio_attrs(record_attrs: Dict[str, Any],
                        parent_audio_attrs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build the attrs dict expected by `compute_audio_difficulty`, taking
    the AA_RECORD value where one is present and falling back to the
    parent CC_AUDIO otherwise. AUDIO_LENGTH is always the segment length.
    """
    def _pick(canonical_col: str) -> Any:
        aa_col = _AA_RECORD_COLUMN_ALIAS.get(canonical_col, canonical_col)
        val    = record_attrs.get(aa_col)
        if val is None or val == '':
            val = parent_audio_attrs.get(canonical_col)
        return val

    attrs: Dict[str, Any] = {
        col: _pick(col) for col in (
            'REPEATS_PAUSES_STUTTER_LEVEL',
            'AUDIO_ISSUES_LEVEL',
            'RECITATION_SPEED',
            'VOICE_PITCH',
            'VOICE_CLARITY',
            'BACKGROUND_NOISE_LEVEL',
        )
    }
    # MISTAKE_LEVEL / AUDIO_SOURCE only ever exist on the parent.
    for col in _PARENT_ONLY_COLUMNS:
        attrs[col] = parent_audio_attrs.get(col)

    # AUDIO_LENGTH is the L3 segment length, not the parent's full audio
    # length, so base_effort_minute reflects the chunk a reviewer will
    # actually listen to.
    attrs['AUDIO_LENGTH'] = _resolve_segment_seconds(record_attrs)
    return attrs


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def compute_l3_difficulty(
    record_attrs: Dict[str, Any],
    parent_audio: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Compute the L3 difficulty bundle for one AA_RECORD row.

    Returns the same shape as `audio_difficulty.compute_audio_difficulty`
    plus `segment_length_seconds` so callers can persist the L3-specific
    duration alongside the parent's audio length.

    Parameters
    ----------
    record_attrs : dict
        AA_RECORD column values (uppercase column names). Read defensively;
        any missing column falls back to the parent CC_AUDIO.
    parent_audio : dict, optional
        CC_AUDIO column values for the parent recording. AA_RECORD does
        not carry MISTAKE_LEVEL / AUDIO_SOURCE so the parent is required
        for those two; everything else falls back gracefully.
    """
    parent_audio_attrs = dict(parent_audio or {})
    audio_attrs        = _build_audio_attrs(record_attrs, parent_audio_attrs)

    result = compute_audio_difficulty(audio_attrs)
    # `audio_length_seconds` here *is* the segment length; alias it for
    # clarity so consumers don't confuse it with the parent's full length.
    result['segment_length_seconds'] = result.get('audio_length_seconds')
    return result


# ---------------------------------------------------------------------------
# ORM convenience wrapper
# ---------------------------------------------------------------------------

_AA_RECORD_COLUMNS = (
    'SEGMENT_START_TIME',
    'SEGMENT_END_TIME',
    'RECORD_LENGTH',
    'RECORD_BACKGROUND_NOISE',
    'REPEATS_PAUSE_STUTTER_LEVEL',
    'RECORD_AUDIO_ISSUES_LEVEL',
    'RECITER_PACE',
    'RECITER_VOICE_PITCH',
    'RECITER_VOICE_CLARITY',
)

_CC_AUDIO_COLUMNS = (
    'AUDIO_LENGTH',
    'REPEATS_PAUSES_STUTTER_LEVEL',
    'AUDIO_SOURCE',
    'MISTAKE_LEVEL',
    'AUDIO_ISSUES_LEVEL',
    'RECITATION_SPEED',
    'VOICE_PITCH',
    'VOICE_CLARITY',
    'BACKGROUND_NOISE_LEVEL',
    'DURATION',
)


def compute_for_aa_record(aa_record, parent_audio=None) -> Dict[str, Any]:
    """
    Pull AA_RECORD attributes (and parent CC_AUDIO attributes if supplied)
    off ORM rows or dicts and run the L3 difficulty calculation.
    """
    record_attrs = {col: getattr(aa_record, col, None) for col in _AA_RECORD_COLUMNS}

    parent_attrs: Dict[str, Any] = {}
    if parent_audio is not None:
        if isinstance(parent_audio, dict):
            parent_attrs = parent_audio
        else:
            parent_attrs = {col: getattr(parent_audio, col, None)
                            for col in _CC_AUDIO_COLUMNS}

    return compute_l3_difficulty(record_attrs, parent_attrs)
