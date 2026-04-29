"""
Audio difficulty + base-effort calculation.

This module owns the canonical formula that converts a CC_AUDIO row's
raw quality parameters into:

  • DIFFICULTY_SCORE   (0.0 – 100.0 floating point)
  • DIFFICULTY_LEVEL   ('low' / 'medium' / 'hard')
  • BASE_EFFORT_MINUTE (real minutes an evaluator is expected to spend)

The same function is intended to be used:
  • by hms_api when an audio is created or reviewed (writes back to CC_AUDIO);
  • by cdm_api as a fallback when CC_AUDIO already has the computed values
    stored — the function just re-derives them when needed.

Formula
-------
For each contributing parameter we read a raw label (e.g. 'Average',
'very slow') and project it to a 0–1 severity. We then take the weighted
sum using `DIFFICULTY_FEATURE_WEIGHTS` from constants.py.

  difficulty_severity = Σ weight_i * severity_i        ∈ [0, 1]
  difficulty_score    = difficulty_severity * 100      ∈ [0, 100]
  effort_multiplier   = lerp(EFFORT_MULTIPLIER_MIN,
                             EFFORT_MULTIPLIER_MAX,
                             difficulty_severity)      ∈ [0.7, 1.7]
  base_effort_minute  = clamp(audio_length_min * effort_multiplier,
                              BASE_EFFORT_FLOOR_MIN,
                              BASE_EFFORT_CEILING_MIN)

If `audio_length_seconds` is missing the formula falls back to a 5-minute
nominal recording length so callers always get a usable number.

Returns the full breakdown so callers can persist *both* the final
metric and the per-parameter contribution into a decision-log table.
"""

from typing import Any, Dict, Optional

import constants


_NOMINAL_LENGTH_SECONDS = 300.0   # used when AUDIO_LENGTH is missing
_DEFAULT_AUDIO_LENGTH_FOR_EFFORT = 5.0  # minutes (fallback)


def _safe_float(val, default: Optional[float] = None) -> Optional[float]:
    if val is None:
        return default
    try:
        f = float(val)
        if f != f:  # NaN check
            return default
        return f
    except (TypeError, ValueError):
        return default


def compute_audio_difficulty(audio_attrs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run the canonical difficulty/effort formula on a dict of CC_AUDIO
    field values (uppercase column names).

    Parameters
    ----------
    audio_attrs : dict-like with keys
        AUDIO_LENGTH, REPEATS_PAUSES_STUTTER_LEVEL, AUDIO_SOURCE,
        MISTAKE_LEVEL, AUDIO_ISSUES_LEVEL, RECITATION_SPEED,
        VOICE_PITCH, VOICE_CLARITY, BACKGROUND_NOISE_LEVEL

    Returns
    -------
    dict with keys
        difficulty_score        float  0–100
        difficulty_level        str    low/medium/hard
        base_effort_minute      float  minutes
        components              list of per-parameter contribution dicts
                                (used by the decision-log table)
        weighted_severity       float  0–1
        effort_multiplier       float  0.7–1.7
        audio_length_seconds    float  raw AUDIO_LENGTH value (or None)
    """
    weights = constants.DIFFICULTY_FEATURE_WEIGHTS
    components = []
    weighted_sum = 0.0

    for column, weight in weights.items():
        raw_value = audio_attrs.get(column)

        if column == 'AUDIO_LENGTH':
            severity = constants.audio_length_severity(raw_value)
        else:
            severity = constants.score_value(column, raw_value)

        contribution = round(weight * severity, 6)
        weighted_sum += contribution
        components.append({
            'column':      column,
            'raw_value':   raw_value,
            'severity':    round(severity, 4),
            'weight':      round(weight, 4),
            'contribution': contribution,
        })

    weighted_sum = max(0.0, min(1.0, weighted_sum))

    difficulty_score = round(weighted_sum * 100.0, 2)
    difficulty_level = constants.difficulty_label(difficulty_score)

    # Effort = audio_length_minutes * effort_multiplier
    audio_length_secs = _safe_float(audio_attrs.get('AUDIO_LENGTH'))
    if audio_length_secs is None:
        audio_length_secs = _safe_float(audio_attrs.get('DURATION'))
    if audio_length_secs is None or audio_length_secs <= 0:
        # Fallback: assume nominal length so we still produce sane minutes
        audio_length_secs = _NOMINAL_LENGTH_SECONDS

    audio_length_minutes = max(_DEFAULT_AUDIO_LENGTH_FOR_EFFORT,
                               audio_length_secs / 60.0)

    span = constants.EFFORT_MULTIPLIER_MAX - constants.EFFORT_MULTIPLIER_MIN
    effort_multiplier = constants.EFFORT_MULTIPLIER_MIN + weighted_sum * span

    base_effort_minute = audio_length_minutes * effort_multiplier
    base_effort_minute = max(constants.BASE_EFFORT_FLOOR_MIN,
                             min(constants.BASE_EFFORT_CEILING_MIN,
                                 base_effort_minute))

    return {
        'difficulty_score':     difficulty_score,
        'difficulty_level':     difficulty_level,
        'base_effort_minute':   round(base_effort_minute, 4),
        'components':           components,
        'weighted_severity':    round(weighted_sum, 4),
        'effort_multiplier':    round(effort_multiplier, 4),
        'audio_length_seconds': audio_length_secs,
    }


def compute_for_model(cc_audio) -> Dict[str, Any]:
    """
    Convenience wrapper that pulls the relevant attributes off a CC_AUDIO
    ORM instance.  Works with both the cdm_api `CCAudio` model and the
    hms_api one — only the column names matter.
    """
    attrs = {column: getattr(cc_audio, column, None) for column in (
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
    )}
    return compute_audio_difficulty(attrs)
