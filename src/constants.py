"""
Shared constants and lookup helpers for cdm_api.

Centralises:
  • String classifications used by audio quality params
    (mistake_level, background_noise_level, …) so the same vocabulary
    drives both effort/difficulty calculations and admin rationale.
  • Weighting profile for the BASE_EFFORT_MINUTE / DIFFICULTY_SCORE
    formula shared between hms_api (write) and cdm_api (read fallback).
  • Default values used whenever a CC_AUDIO field is NULL — so the
    allocator never crashes on incomplete data.
  • Duration bucket helper.

The `score_value(...)` family converts the textual labels HMS stores
(e.g. "Average", "very slow", "loud") into a 0.0–1.0 numeric "severity"
that is suitable for weighted summation. 0.0 means easiest / cleanest /
most evaluator-friendly, 1.0 means hardest / worst.
"""

from typing import Optional


HMS_TYPE = 'hms'

# ---------------------------------------------------------------------------
# Difficulty / level vocabularies
# ---------------------------------------------------------------------------

DIFFICULTY_LEVELS = ('low', 'medium', 'hard')

# Generic 0–1 severity map. Used as a fallback when a column-specific map
# below does not contain the supplied label.
GENERIC_LEVEL_VALUES: dict = {
    'none':      0.0,
    'low':       0.15,
    'minimal':   0.15,
    'minor':     0.20,
    'good':      0.20,
    'clean':     0.10,
    'normal':    0.30,
    'average':   0.50,
    'medium':    0.50,
    'moderate':  0.55,
    'high':      0.80,
    'major':     0.85,
    'severe':    0.95,
    'extreme':   1.0,
    'critical':  1.0,
}

# Per-column scoring tables. Lower-cased keys, 0.0 (easy) → 1.0 (hardest).

MISTAKE_LEVEL_VALUES: dict = {
    'none': 0.0, 'minor': 0.25, 'low': 0.25,
    'average': 0.55, 'medium': 0.55,
    'major': 0.80, 'high': 0.80,
    'severe': 1.0,
}

BACKGROUND_NOISE_LEVEL_VALUES: dict = {
    'none': 0.0, 'low': 0.20, 'minimal': 0.20,
    'average': 0.50, 'medium': 0.50, 'moderate': 0.55,
    'high': 0.80, 'severe': 0.95, 'extreme': 1.0,
}

REPEATS_PAUSES_STUTTER_LEVEL_VALUES: dict = {
    'none': 0.0, 'low': 0.20,
    'average': 0.50, 'medium': 0.50,
    'high': 0.80, 'severe': 0.95,
}

AUDIO_ISSUES_LEVEL_VALUES: dict = {
    'none': 0.0, 'low': 0.20, 'minor': 0.20,
    'average': 0.50, 'medium': 0.50,
    'high': 0.80, 'major': 0.85,
    'severe': 1.0,
}

# Recitation speed: both very fast and very slow are harder to evaluate.
RECITATION_SPEED_VALUES: dict = {
    'very slow': 0.85,
    'slow':      0.55,
    'normal':    0.20,
    'medium':    0.30,
    'fast':      0.55,
    'very fast': 0.90,
}

# Voice clarity: clear voices are easy, unclear voices are hard.
VOICE_CLARITY_VALUES: dict = {
    'very clear': 0.0, 'clear': 0.10, 'good': 0.15,
    'average': 0.45, 'medium': 0.45, 'normal': 0.30,
    'not clear': 0.80, 'unclear': 0.80, 'muffled': 0.85,
    'distorted': 0.95,
}

# Voice pitch: extreme pitches make recognition harder.
VOICE_PITCH_VALUES: dict = {
    'normal': 0.20, 'medium': 0.20, 'average': 0.25,
    'deep-man': 0.55, 'deep-woman': 0.55,
    'high-man': 0.55, 'high-woman': 0.55,
    'child': 0.65,
    'very-deep': 0.80, 'very-high': 0.80,
}

# Audio source: synthetic / poor sources are harder than studio.
AUDIO_SOURCE_VALUES: dict = {
    'studio':   0.10,
    'manual':   0.30,
    'website':  0.40,
    'youtube':  0.60,
    'whatsapp': 0.70,
    'phone':    0.75,
    'unknown':  0.55,
}

DEFAULT_LEVEL_SEVERITY = 0.50  # used whenever a label cannot be mapped


# ---------------------------------------------------------------------------
# BASE_EFFORT_MINUTE / DIFFICULTY_SCORE formula weights
# ---------------------------------------------------------------------------
#
# Each weight describes how much the column contributes to the per-minute
# difficulty multiplier on top of the raw audio length.
#
# Sum of weights == 1.0 so that the weighted severity is a clean 0–1
# probability before we project it onto the difficulty score (0–100) and
# the per-minute effort multiplier (0.7 – 1.7).

DIFFICULTY_FEATURE_WEIGHTS: dict = {
    'AUDIO_LENGTH':                 0.05,
    'REPEATS_PAUSES_STUTTER_LEVEL': 0.15,
    'AUDIO_SOURCE':                 0.07,
    'MISTAKE_LEVEL':                0.18,
    'AUDIO_ISSUES_LEVEL':           0.13,
    'RECITATION_SPEED':             0.12,
    'VOICE_PITCH':                  0.07,
    'VOICE_CLARITY':                0.13,
    'BACKGROUND_NOISE_LEVEL':       0.10,
}

# Per-minute effort range — 0.7 (clean recording) … 1.7 (very hard recording)
EFFORT_MULTIPLIER_MIN = 0.7
EFFORT_MULTIPLIER_MAX = 1.7

# Floor / ceiling for the final BASE_EFFORT_MINUTE result.
BASE_EFFORT_FLOOR_MIN   = 1.0   # minutes
BASE_EFFORT_CEILING_MIN = 60.0  # minutes


# ---------------------------------------------------------------------------
# Audio length scoring (long audios are more demanding per minute)
# ---------------------------------------------------------------------------

def audio_length_severity(length_seconds: Optional[float]) -> float:
    """
    Map an audio length in seconds to a 0–1 difficulty severity.
    < 60s     → 0.10
    60–180s   → 0.30
    180–360s  → 0.55
    360–720s  → 0.80
    > 720s    → 1.0
    """
    if length_seconds is None:
        return DEFAULT_LEVEL_SEVERITY
    try:
        secs = float(length_seconds)
    except (TypeError, ValueError):
        return DEFAULT_LEVEL_SEVERITY
    if secs < 60:
        return 0.10
    if secs < 180:
        return 0.30
    if secs < 360:
        return 0.55
    if secs < 720:
        return 0.80
    return 1.0


# ---------------------------------------------------------------------------
# Generic label → severity helper
# ---------------------------------------------------------------------------

_COLUMN_VALUE_MAPS = {
    'MISTAKE_LEVEL':                MISTAKE_LEVEL_VALUES,
    'BACKGROUND_NOISE_LEVEL':       BACKGROUND_NOISE_LEVEL_VALUES,
    'REPEATS_PAUSES_STUTTER_LEVEL': REPEATS_PAUSES_STUTTER_LEVEL_VALUES,
    'AUDIO_ISSUES_LEVEL':           AUDIO_ISSUES_LEVEL_VALUES,
    'RECITATION_SPEED':             RECITATION_SPEED_VALUES,
    'VOICE_PITCH':                  VOICE_PITCH_VALUES,
    'VOICE_CLARITY':                VOICE_CLARITY_VALUES,
    'AUDIO_SOURCE':                 AUDIO_SOURCE_VALUES,
}


def score_value(column: str, raw_value) -> float:
    """
    Convert a raw CC_AUDIO label (e.g. 'Average', 'very slow') to a numeric
    severity in [0, 1] for the named column.

    Falls back to `GENERIC_LEVEL_VALUES` then to `DEFAULT_LEVEL_SEVERITY`
    (0.5) so the formula is robust against typos and new labels.
    """
    if raw_value is None:
        return DEFAULT_LEVEL_SEVERITY
    # Numeric values (already 0–1) pass through directly
    if isinstance(raw_value, (int, float)):
        try:
            v = float(raw_value)
            if 0.0 <= v <= 1.0:
                return v
        except (TypeError, ValueError):
            pass
    key = str(raw_value).strip().lower()
    if not key or key in ('nan', 'none', 'null'):
        return DEFAULT_LEVEL_SEVERITY
    table = _COLUMN_VALUE_MAPS.get(column, {})
    if key in table:
        return table[key]
    if key in GENERIC_LEVEL_VALUES:
        return GENERIC_LEVEL_VALUES[key]
    return DEFAULT_LEVEL_SEVERITY


# ---------------------------------------------------------------------------
# Duration bucket
# ---------------------------------------------------------------------------

DURATIONS_SECONDS_RANGES = {
    'short':  {'start': 0,   'end': 60},
    'medium': {'start': 60,  'end': 360},
    'long':   {'start': 360, 'end': 99999},
}


def classify_duration(duration_str) -> Optional[str]:
    """Classify a raw duration (seconds) into 'short' / 'medium' / 'long'."""
    if duration_str is None:
        return None
    try:
        secs = float(duration_str)
    except (TypeError, ValueError):
        return None
    if secs < 60:
        return 'short'
    if secs <= 360:
        return 'medium'
    return 'long'


def difficulty_label(score) -> str:
    """Map a 0–100 difficulty score to one of {'low', 'medium', 'hard'}."""
    if score is None:
        return 'medium'
    try:
        s = float(score)
    except (TypeError, ValueError):
        return 'medium'
    if s < 40:
        return 'low'
    if s < 70:
        return 'medium'
    return 'hard'
