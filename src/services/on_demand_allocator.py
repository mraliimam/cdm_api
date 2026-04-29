"""
On-Demand Recording Allocation System (V1)

This module implements a single-step, constraint-aware allocation algorithm
that selects the best recording for an evaluator based on:
- Recent performance (last 1 hour)
- Weekly constraints (effort limits, accuracy targets)
- Fairness rules
- Multi-objective optimization (accuracy, satisfaction, productivity, stamina)

Key Design Decisions:
- Uses floating-point difficulty score (0-100) instead of strict categories
- Effort minutes combine student profile history + current audio parameters
- Provides clear rationale for each allocation decision
- Single-step optimization (not sequence-aware)
"""

import csv
import os
import pandas as pd
import numpy as np
from typing import Any, Dict, List, Tuple, Optional
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import defaultdict
import json

from services.audio_difficulty import compute_audio_difficulty


@dataclass
class AllocationConfig:
    """Configuration for on-demand allocation"""
    # Weights for multi-objective optimization
    weight_accuracy: float = 0.35
    weight_satisfaction: float = 0.25
    weight_productivity: float = 0.20
    weight_stamina: float = 0.10
    weight_fairness: float = 0.10
    
    # Constraint thresholds
    min_accuracy_target: float = 0.75  # Minimum accuracy to maintain
    max_effort_overage_ratio: float = 1.2  # Can exceed available effort by 20%
    
    # Recent performance window (hours)
    recent_performance_window_hours: float = 1.0
    
    # Fairness parameters
    ideal_difficulty_distribution: Dict[str, float] = field(default_factory=lambda: {
        'low': 0.33, 'medium': 0.33, 'hard': 0.34
    })
    fairness_tolerance: float = 0.15  # Max deviation from ideal distribution
    
    # Stamina parameters
    stamina_decay_rate: float = 0.1  # How quickly stamina builds/decays
    stamina_boost_threshold: float = 0.7  # Difficulty level that boosts stamina


@dataclass
class EvaluatorState:
    """Current state of an evaluator"""
    evaluator_id: int
    available_effort_minutes: float
    weekly_effort_used: float = 0.0
    weekly_effort_limit: float = 0.0
    weekly_accuracy_avg: float = 0.0
    weekly_accuracy_target: float = 0.75
    current_stamina: float = 0.5  # 0-1 scale
    recent_recordings: List[Dict] = field(default_factory=list)  # Last 1 hour
    difficulty_history: Dict[str, int] = field(default_factory=lambda: {'low': 0, 'medium': 0, 'hard': 0})
    total_recordings: int = 0


@dataclass
class RecordingCandidate:
    """A recording candidate for allocation"""
    sample_id: int
    user_id: int
    difficulty_score: float  # 0-100 floating point
    difficulty_level: str = 'medium'
    audio_params: Dict[str, float] = field(default_factory=dict)  # mistake_level, noise_level, etc.
    base_effort_minutes: float = 5.0
    recording_time: float = 5.0
    predicted_metrics: Dict[str, float] = field(default_factory=dict)
    # Per-parameter difficulty breakdown captured from the canonical formula —
    # used to populate the CC_CDM_ALLOCATION_DECISION table.
    difficulty_components: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class AllocationResult:
    """Result of an allocation decision"""
    selected_recording: RecordingCandidate
    evaluator_id: int
    score: float
    rationale: str
    constraint_violations: List[str] = field(default_factory=list)
    predicted_impact: Dict[str, float] = field(default_factory=dict)
    # Structured decision breakdown — list of dicts ready for
    # CdmAllocationDecision.bulk_record(). Populated by the allocator
    # alongside the human-readable rationale string.
    decision_breakdown: List[Dict[str, Any]] = field(default_factory=list)


class DataCollectionLogger:
    """
    Persistent logger for future ML training data.

    Appends one CSV row per completed recording to `session_log.csv`.
    Captures the signals called out in the CDM Future Plans:
      - time-of-day (hour_of_day, day_of_week)
      - sequence patterns (sequence_number)
      - satisfaction signals
      - effort variance
      - stamina (before/after)
      - boost-like behaviours (difficulty vs stamina change)

    The file is created with a header on first write and appended to on
    subsequent writes, so it survives across multiple process invocations.
    """

    FIELDS = [
        "timestamp",
        "hour_of_day",
        "day_of_week",
        "evaluator_id",
        "recording_id",
        "user_id",
        "difficulty_score",
        "effort_minutes",
        "accuracy",
        "satisfaction",
        "stamina_before",
        "stamina_after",
        "stamina_delta",
        "sequence_number",
        "weekly_effort_used",
        "weekly_accuracy_avg",
    ]

    def __init__(self, log_path: str = "session_log.csv"):
        self.log_path = log_path
        self._ensure_header()

    def _ensure_header(self):
        """Write header row if the file does not yet exist."""
        if not os.path.exists(self.log_path):
            with open(self.log_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=self.FIELDS)
                writer.writeheader()

    def log(
        self,
        timestamp: datetime,
        evaluator_id: int,
        recording_id: int,
        user_id: int,
        difficulty_score: float,
        effort_minutes: float,
        accuracy: float,
        satisfaction: float,
        stamina_before: float,
        stamina_after: float,
        sequence_number: int,
        weekly_effort_used: float,
        weekly_accuracy_avg: float,
    ):
        """Append one session event row to the log file."""
        row = {
            "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "hour_of_day": timestamp.hour,
            "day_of_week": timestamp.weekday(),
            "evaluator_id": evaluator_id,
            "recording_id": recording_id,
            "user_id": user_id,
            "difficulty_score": round(difficulty_score, 2),
            "effort_minutes": round(effort_minutes, 2),
            "accuracy": round(accuracy, 4),
            "satisfaction": round(satisfaction, 4),
            "stamina_before": round(stamina_before, 4),
            "stamina_after": round(stamina_after, 4),
            "stamina_delta": round(stamina_after - stamina_before, 4),
            "sequence_number": sequence_number,
            "weekly_effort_used": round(weekly_effort_used, 2),
            "weekly_accuracy_avg": round(weekly_accuracy_avg, 4),
        }
        with open(self.log_path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=self.FIELDS)
            writer.writerow(row)


class OnDemandAllocator:
    """
    On-demand allocation algorithm for assigning recordings to evaluators.
    
    This is a V1 implementation focusing on:
    - Single-step optimization (not sequence-aware)
    - Constraint-aware selection
    - Clear rationale generation
    - Multi-objective optimization
    """
    
    def __init__(self, config: AllocationConfig,
                 logger: Optional["DataCollectionLogger"] = None):
        self.config = config
        self.logger = logger
        self.recordings_df = None
        self.evaluators_df = None
        self.users_df = None
        self.performance_df = None

        # Evaluator states (tracked in memory)
        self.evaluator_states: Dict[int, EvaluatorState] = {}

        # Learned patterns
        self.evaluator_profiles: Dict[int, Dict] = {}
        self.user_impact_profiles: Dict[int, Dict] = {}
        self.evaluator_time_adjustments: Dict[int, float] = {}
        self.temporal_profiles: Dict[int, Dict] = {}

        # Per (evaluator_id, user_id) running performance from CC_CDM_EVALUATOR_PERFORMANCE.
        # Loaded from the DB by cdm_allocation_service before each allocation call.
        # Each value is the dict produced by CdmEvaluatorPerformance.to_predictor_dict():
        #   sample_count, avg_accuracy, avg_satisfaction,
        #   avg_effort_minutes, avg_difficulty_score
        self.pair_performance: Dict[Tuple[int, int], Dict[str, Any]] = {}
        
    def load_data(self, recordings_path: str, evaluators_path: str,
                 users_path: str, performance_path: str):
        """Load all datasets"""
        print("📂 Loading datasets...")
        self.recordings_df = pd.read_csv(recordings_path)
        self.evaluators_df = pd.read_csv(evaluators_path)
        self.users_df = pd.read_csv(users_path)
        self.performance_df = pd.read_csv(performance_path)
        
        print(f"✅ Loaded: {len(self.recordings_df):,} recordings, "
              f"{len(self.evaluators_df)} evaluators, "
              f"{len(self.users_df)} users, "
              f"{len(self.performance_df):,} performance records")
        
        # Initialize evaluator states
        self._initialize_evaluator_states()
        
    def _initialize_evaluator_states(self):
        """Initialize evaluator states from data"""
        for _, evaluator in self.evaluators_df.iterrows():
            eval_id = evaluator['evaluator_id']
            self.evaluator_states[eval_id] = EvaluatorState(
                evaluator_id=eval_id,
                available_effort_minutes=evaluator.get('available_effort_minute', 30.0),
                weekly_effort_limit=evaluator.get('weekly_effort_limit', 200.0),
                weekly_accuracy_target=evaluator.get('accuracy_target', 0.75)
            )
    
    def fit(self):
        """Learn patterns from historical performance data"""
        print("🧠 Learning patterns from historical data...")
        
        # Learn evaluator profiles
        for evaluator_id in self.evaluators_df['evaluator_id']:
            eval_perf = self.performance_df[self.performance_df['evaluator_id'] == evaluator_id]
            
            if len(eval_perf) > 0:
                # Performance by difficulty category (for backward compatibility)
                perf_by_diff = {}
                for diff in ['low', 'medium', 'hard']:
                    diff_perf = eval_perf[eval_perf['difficulty_level'] == diff]
                    if len(diff_perf) > 0:
                        perf_by_diff[diff] = {
                            'avg_accuracy': diff_perf['actual_accuracy'].mean(),
                            'avg_satisfaction': diff_perf['satisfaction_level'].mean(),
                            'avg_effort': diff_perf['effort_minutes_spent'].mean(),
                            'count': len(diff_perf)
                        }
                
                # Overall performance
                self.evaluator_profiles[evaluator_id] = {
                    'overall_accuracy': eval_perf['actual_accuracy'].mean(),
                    'overall_satisfaction': eval_perf['satisfaction_level'].mean(),
                    'overall_effort': eval_perf['effort_minutes_spent'].mean(),
                    'by_difficulty': perf_by_diff,
                    'total_recordings': len(eval_perf)
                }

                # Temporal profile (time-of-day and sequence effects)
                hourly_accuracy = {}
                hourly_effort_factor = {}
                if 'hour_of_day' in eval_perf.columns:
                    hourly_accuracy = {
                        int(h): float(v)
                        for h, v in eval_perf.groupby('hour_of_day')['actual_accuracy'].mean().to_dict().items()
                    }
                    avg_effort = max(1e-6, float(eval_perf['effort_minutes_spent'].mean()))
                    hourly_effort_factor = {
                        int(h): float(v / avg_effort)
                        for h, v in eval_perf.groupby('hour_of_day')['effort_minutes_spent'].mean().to_dict().items()
                    }

                sequence_accuracy_slope = 0.0
                if 'sequence_number' in eval_perf.columns:
                    seq_df = eval_perf[['sequence_number', 'actual_accuracy']].dropna()
                    if len(seq_df) >= 3 and seq_df['sequence_number'].nunique() >= 2:
                        slope, _ = np.polyfit(
                            seq_df['sequence_number'].astype(float),
                            seq_df['actual_accuracy'].astype(float),
                            1
                        )
                        sequence_accuracy_slope = float(slope)

                self.temporal_profiles[evaluator_id] = {
                    'hourly_accuracy': hourly_accuracy,
                    'hourly_effort_factor': hourly_effort_factor,
                    'sequence_accuracy_slope': sequence_accuracy_slope
                }
                
                # Time adjustment factor
                if 'base_effort_minute' in self.recordings_df.columns:
                    merged = eval_perf.merge(
                        self.recordings_df[['sample_id', 'base_effort_minute']],
                        on='sample_id', how='left'
                    )
                    merged = merged[merged['base_effort_minute'].notna()]
                    if len(merged) > 0:
                        time_ratio = (merged['effort_minutes_spent'] / merged['base_effort_minute']).mean()
                        self.evaluator_time_adjustments[evaluator_id] = time_ratio
                    else:
                        self.evaluator_time_adjustments[evaluator_id] = 1.0
                else:
                    self.evaluator_time_adjustments[evaluator_id] = 1.0
                
                # Update difficulty history and initialize weekly accuracy
                for _, perf in eval_perf.iterrows():
                    diff = perf.get('difficulty_level', 'medium')
                    if diff in self.evaluator_states[evaluator_id].difficulty_history:
                        self.evaluator_states[evaluator_id].difficulty_history[diff] += 1
                    self.evaluator_states[evaluator_id].total_recordings += 1
                
                # Initialize weekly accuracy average from historical data
                self.evaluator_states[evaluator_id].weekly_accuracy_avg = eval_perf['actual_accuracy'].mean()
            else:
                # Default for new evaluators
                self.evaluator_profiles[evaluator_id] = {
                    'overall_accuracy': 0.85,
                    'overall_satisfaction': 0.75,
                    'overall_effort': 5.0,
                    'by_difficulty': {},
                    'total_recordings': 0
                }
                self.evaluator_time_adjustments[evaluator_id] = 1.0
                self.temporal_profiles[evaluator_id] = {
                    'hourly_accuracy': {},
                    'hourly_effort_factor': {},
                    'sequence_accuracy_slope': 0.0
                }
        
        # Learn user impact profiles
        for user_id in self.users_df['user_id']:
            user_perf = self.performance_df[self.performance_df['user_id'] == user_id]
            if len(user_perf) > 0:
                self.user_impact_profiles[user_id] = {
                    'avg_accuracy_impact': user_perf['actual_accuracy'].mean(),
                    'avg_effort_impact': user_perf['effort_minutes_spent'].mean(),
                    'consistency': user_perf.groupby('evaluator_id')['actual_accuracy'].std().mean()
                }
        
        print(f"✅ Learned patterns for {len(self.evaluator_profiles)} evaluators")
    
    # ------------------------------------------------------------------
    # External hooks (called by cdm_allocation_service)
    # ------------------------------------------------------------------

    def set_pair_performance(self, pair_perf: Dict[Tuple[int, int], Dict[str, Any]]) -> None:
        """
        Inject per (evaluator_id, user_id) performance from
        CC_CDM_EVALUATOR_PERFORMANCE.  Empty dict is fine — the allocator
        falls back to evaluator-wide / global defaults.
        """
        self.pair_performance = pair_perf or {}

    def _pair_history(self, evaluator_id: int, user_id: int) -> Optional[Dict[str, Any]]:
        """Return the pair history dict if available, else None."""
        if not user_id:
            return None
        return self.pair_performance.get((evaluator_id, user_id))

    # ------------------------------------------------------------------
    # Difficulty score
    # ------------------------------------------------------------------

    def calculate_difficulty_score(self, recording: pd.Series) -> Tuple[float, str, List[Dict[str, Any]]]:
        """
        Calculate floating-point difficulty score (0-100) using the canonical
        weighted formula in `audio_difficulty.compute_audio_difficulty`.

        Returns
        -------
        (difficulty_score, difficulty_level, components)
            Where ``components`` is a list of per-parameter contribution dicts
            (column, raw_value, severity, weight, contribution) for use by the
            decision-log table.
        """
        # Prefer canonical difficulty_score if provided by upstream (HMS).
        canonical_score = recording.get('difficulty_score')
        if canonical_score is not None and pd.notna(canonical_score):
            score = float(np.clip(canonical_score, 0.0, 100.0))
            level = recording.get('difficulty_level') or _difficulty_label(score)
            # Even when score is canonical, derive components from raw params
            # so we can still log the "why" of every parameter.
            attrs = _recording_to_audio_attrs(recording)
            breakdown = compute_audio_difficulty(attrs)
            return score, level, breakdown['components']

        attrs = _recording_to_audio_attrs(recording)
        breakdown = compute_audio_difficulty(attrs)
        return (
            float(breakdown['difficulty_score']),
            breakdown['difficulty_level'],
            breakdown['components'],
        )
    
    def calculate_effort_minutes(self, evaluator_id: int, recording: pd.Series,
                                 difficulty_score: float) -> float:
        """
        Calculate total effort minutes combining:
        1. Student profile history (past performance with this user)
        2. Evaluator-on-this-user history from CC_CDM_EVALUATOR_PERFORMANCE
        3. Current audio-specific parameters

        Formula: Effort = base * difficulty_multiplier * evaluator_time_factor *
                          user_time_factor * pair_time_factor
        """
        user_id = recording.get('user_id')
        base_effort = recording.get('base_effort_minute', recording.get('recording_time', 3.0) * 2.0)
        if base_effort is None or (isinstance(base_effort, float) and pd.isna(base_effort)):
            base_effort = 5.0

        # Adjust by difficulty score — higher difficulty = more effort.
        difficulty_multiplier = 0.7 + (difficulty_score / 100.0) * 0.8  # Range: 0.7-1.5

        # Adjust by evaluator's historical time patterns
        time_adjustment = self.evaluator_time_adjustments.get(evaluator_id, 1.0)

        # Adjust by user profile impact (any evaluator who has worked this user)
        user_time_factor = 1.0
        if user_id and user_id in self.user_impact_profiles:
            user_impact = self.user_impact_profiles[user_id]
            profile = self.evaluator_profiles.get(evaluator_id, {})
            if profile.get('overall_effort', 0) > 0:
                user_time_factor = user_impact['avg_effort_impact'] / profile['overall_effort']

        # Adjust by *pair* history — this evaluator on this user.
        pair_time_factor = 1.0
        pair = self._pair_history(evaluator_id, int(user_id)) if user_id else None
        if pair and pair.get('avg_effort_minutes') is not None:
            pair_avg = float(pair['avg_effort_minutes'])
            base_estimate = float(base_effort) * difficulty_multiplier
            if base_estimate > 0:
                ratio = pair_avg / base_estimate
                # Confidence-weighted blend toward the pair-implied ratio
                conf = self._pair_confidence(pair)
                pair_time_factor = (1.0 - conf) + conf * ratio

        effort_minutes = float(base_effort) * difficulty_multiplier * time_adjustment * user_time_factor * pair_time_factor

        return float(np.clip(effort_minutes, 2.0, 60.0))
    
    def predict_metrics(self, evaluator_id: int, recording: pd.Series,
                       difficulty_score: float, effort_minutes: float,
                       current_hour: Optional[int] = None,
                       sequence_number: Optional[int] = None) -> Dict[str, float]:
        """
        Predict performance metrics for evaluator on this recording.

        Blending order (highest priority first):
          1. (evaluator, user) pair history from CC_CDM_EVALUATOR_PERFORMANCE
             — confidence weighted by sample_count.  This makes predictions
             "personalised": if this specific evaluator has worked on this
             user before, their prior accuracy / satisfaction directly
             influence the prediction.
          2. evaluator-wide overall_accuracy / overall_satisfaction from
             historical performance_df.
          3. user-wide avg_accuracy_impact / avg_effort_impact from
             performance_df (anybody who has worked this user).
          4. temporal calibration (time-of-day, sequence slope).
          5. fallback to global defaults (0.85 / 0.75) for new evaluators.
        """
        profile = self.evaluator_profiles.get(evaluator_id, {
            'overall_accuracy': 0.85,
            'overall_satisfaction': 0.75
        })

        user_id = recording.get('user_id')
        user_impact = None
        if user_id and user_id in self.user_impact_profiles:
            user_impact = self.user_impact_profiles[user_id]
        pair = self._pair_history(evaluator_id, int(user_id)) if user_id else None

        # ---------------- accuracy ----------------
        base_accuracy = profile.get('overall_accuracy', 0.85)
        difficulty_penalty = (difficulty_score / 100.0) * 0.15
        predicted_accuracy = base_accuracy - difficulty_penalty

        # User-wide impact (any evaluator) — modest blend.
        if user_impact:
            predicted_accuracy = predicted_accuracy * 0.7 + user_impact['avg_accuracy_impact'] * 0.3

        # Pair-level (evaluator on this user) takes precedence with confidence weight.
        pair_confidence = self._pair_confidence(pair)
        if pair and pair_confidence > 0 and pair.get('avg_accuracy') is not None:
            pair_acc = float(pair['avg_accuracy'])
            predicted_accuracy = (
                predicted_accuracy * (1.0 - pair_confidence)
                + pair_acc * pair_confidence
            )

        # Temporal calibration from historical time-of-day and sequence trends
        temporal = self.temporal_profiles.get(evaluator_id, {})
        if current_hour is None:
            current_hour = datetime.now().hour
        if sequence_number is None:
            sequence_number = self.evaluator_states[evaluator_id].total_recordings + 1

        hourly_accuracy = temporal.get('hourly_accuracy', {})
        if current_hour in hourly_accuracy:
            predicted_accuracy = predicted_accuracy * 0.85 + hourly_accuracy[current_hour] * 0.15

        sequence_slope = temporal.get('sequence_accuracy_slope', 0.0)
        seq_adjustment = np.clip(sequence_slope * min(sequence_number, 30), -0.03, 0.03)
        predicted_accuracy += float(seq_adjustment)

        predicted_accuracy = np.clip(predicted_accuracy, 0.5, 0.97)

        # ---------------- satisfaction ----------------
        base_satisfaction = profile.get('overall_satisfaction', 0.75)
        difficulty_penalty_sat = (difficulty_score / 100.0) * 0.10
        effort_penalty = min(0.1, effort_minutes / 30.0) * 0.1
        predicted_satisfaction = base_satisfaction - difficulty_penalty_sat - effort_penalty

        # Pair satisfaction blend
        if pair and pair_confidence > 0 and pair.get('avg_satisfaction') is not None:
            pair_sat = float(pair['avg_satisfaction'])
            predicted_satisfaction = (
                predicted_satisfaction * (1.0 - pair_confidence)
                + pair_sat * pair_confidence
            )

        # Temporal effort calibration: known slower hours slightly reduce satisfaction.
        hourly_effort_factor = temporal.get('hourly_effort_factor', {})
        hour_factor = hourly_effort_factor.get(current_hour, 1.0)
        if hour_factor > 1.0:
            predicted_satisfaction -= min(0.04, (hour_factor - 1.0) * 0.08)
        elif hour_factor < 0.95:
            predicted_satisfaction += min(0.02, (0.95 - hour_factor) * 0.05)

        predicted_satisfaction = np.clip(predicted_satisfaction, 0.3, 1.0)

        # ---------------- productivity ----------------
        predicted_productivity = 60.0 / max(1.0, effort_minutes)

        return {
            'accuracy':         round(float(predicted_accuracy), 3),
            'satisfaction':     round(float(predicted_satisfaction), 3),
            'productivity':     round(float(predicted_productivity), 2),
            'effort_minutes':   round(float(effort_minutes), 2),
            'pair_confidence':  round(float(pair_confidence), 3),
            'pair_sample_count': int(pair.get('sample_count', 0)) if pair else 0,
        }

    # ------------------------------------------------------------------
    # Pair-history confidence weighting
    # ------------------------------------------------------------------

    @staticmethod
    def _pair_confidence(pair_history: Optional[Dict[str, Any]]) -> float:
        """
        Confidence weight in [0, 0.6] derived from sample_count.

        We cap pair influence at 0.6 so the global / temporal signals can
        still steer the prediction even when the pair has many samples;
        capping prevents one outlier-heavy pair from drowning everything else.
        """
        if not pair_history:
            return 0.0
        n = pair_history.get('sample_count') or 0
        if n <= 0:
            return 0.0
        # Saturating curve: 1 sample → 0.16, 5 samples → 0.50, 10 samples → 0.60
        return float(min(0.6, n / (n + 4.0)))

    def get_evaluator_confidence(self, evaluator_id: int) -> float:
        """
        Estimate confidence in this evaluator's profile based on data volume.
        Low-data evaluators are down-weighted to reduce overconfident scoring.
        """
        n = self.evaluator_profiles.get(evaluator_id, {}).get('total_recordings', 0)
        confidence = np.log1p(max(0, n)) / np.log1p(30)
        return float(np.clip(confidence, 0.35, 1.0))

    def calculate_fatigue_index(self, evaluator_id: int) -> float:
        """
        Estimate fatigue using recent workload and stamina (0=no fatigue, 1=high).
        """
        state = self.evaluator_states[evaluator_id]
        recent = self.get_recent_performance(evaluator_id)
        count_component = min(1.0, recent['count'] / 6.0)
        effort_component = min(1.0, recent['avg_effort'] / 15.0)
        stamina_component = 1.0 - state.current_stamina
        fatigue = 0.45 * count_component + 0.35 * effort_component + 0.20 * stamina_component
        return float(np.clip(fatigue, 0.0, 1.0))
    
    def get_recent_performance(self, evaluator_id: int) -> Dict[str, float]:
        """Get performance metrics from last 1 hour"""
        state = self.evaluator_states[evaluator_id]
        recent = state.recent_recordings
        
        if not recent:
            return {
                'avg_accuracy': 0.85,
                'avg_satisfaction': 0.75,
                'avg_effort': 5.0,
                'count': 0
            }
        
        return {
            'avg_accuracy': np.mean([r.get('accuracy', 0.85) for r in recent]),
            'avg_satisfaction': np.mean([r.get('satisfaction', 0.75) for r in recent]),
            'avg_effort': np.mean([r.get('effort_minutes', 5.0) for r in recent]),
            'count': len(recent)
        }
    
    def calculate_fairness_score(self, evaluator_id: int, difficulty_score: float) -> float:
        """
        Calculate fairness score based on difficulty distribution.
        Uses difficulty score (0-100) to categorize into low/medium/hard for fairness tracking.
        """
        state = self.evaluator_states[evaluator_id]
        total = state.total_recordings
        
        if total == 0:
            return 1.0  # No history, no penalty
        
        # Categorize difficulty score for fairness tracking
        if difficulty_score < 40:
            category = 'low'
        elif difficulty_score < 70:
            category = 'medium'
        else:
            category = 'hard'
        
        # Current distribution
        current_dist = {
            'low': state.difficulty_history['low'] / total,
            'medium': state.difficulty_history['medium'] / total,
            'hard': state.difficulty_history['hard'] / total
        }
        
        # Ideal distribution
        ideal_dist = self.config.ideal_difficulty_distribution
        
        # Calculate deviation from ideal and apply tolerance band.
        deviation = abs(current_dist[category] - ideal_dist[category])
        tolerance = self.config.fairness_tolerance
        effective_deviation = max(0.0, deviation - tolerance)
        denominator = max(1e-6, 1.0 - tolerance)
        fairness_score = 1.0 - min(1.0, effective_deviation / denominator)
        return max(0.0, fairness_score)
    
    def calculate_stamina_impact(self, difficulty_score: float, current_stamina: float) -> float:
        """
        Calculate how this recording affects stamina.
        Higher difficulty can build stamina if stamina is low, but drain it if stamina is high.
        """
        # Normalize difficulty to 0-1
        norm_difficulty = difficulty_score / 100.0
        
        # If difficulty is above threshold, it can boost stamina
        if norm_difficulty >= self.config.stamina_boost_threshold:
            # Building stamina
            stamina_gain = (norm_difficulty - self.config.stamina_boost_threshold) * 0.3
            return min(1.0, current_stamina + stamina_gain)
        else:
            # Maintaining or slightly draining stamina
            stamina_loss = (self.config.stamina_boost_threshold - norm_difficulty) * 0.1
            return max(0.0, current_stamina - stamina_loss)
    
    def check_constraints(self, evaluator_id: int, recording: RecordingCandidate,
                         predicted_metrics: Dict[str, float]) -> Tuple[bool, List[str]]:
        """
        Check if allocation violates any constraints.
        Returns (is_valid, list_of_violations)
        """
        state = self.evaluator_states[evaluator_id]
        violations = []
        
        # Check available effort
        if predicted_metrics['effort_minutes'] > state.available_effort_minutes * self.config.max_effort_overage_ratio:
            violations.append(f"Effort ({predicted_metrics['effort_minutes']:.1f} min) exceeds available "
                            f"({state.available_effort_minutes:.1f} min)")
        
        # Check weekly effort limit
        if state.weekly_effort_limit > 0:
            if state.weekly_effort_used + predicted_metrics['effort_minutes'] > state.weekly_effort_limit:
                violations.append(f"Weekly effort limit would be exceeded "
                                f"({state.weekly_effort_used + predicted_metrics['effort_minutes']:.1f} > "
                                f"{state.weekly_effort_limit:.1f})")
        
        # Check accuracy target
        if predicted_metrics['accuracy'] < self.config.min_accuracy_target:
            violations.append(f"Predicted accuracy ({predicted_metrics['accuracy']:.3f}) below minimum "
                            f"({self.config.min_accuracy_target:.3f})")
        
        # Check weekly accuracy target
        if state.weekly_accuracy_target > 0:
            # Estimate new weekly average
            current_avg = state.weekly_accuracy_avg
            n_recordings = state.total_recordings
            
            # If no weekly average yet, use predicted accuracy directly
            if current_avg == 0.0 and n_recordings == 0:
                new_avg = predicted_metrics['accuracy']
            elif n_recordings > 0:
                # Use running average calculation
                if current_avg == 0.0:
                    # If avg is 0 but we have recordings, use predicted as estimate
                    new_avg = predicted_metrics['accuracy']
                else:
                    new_avg = (current_avg * n_recordings + predicted_metrics['accuracy']) / (n_recordings + 1)
            else:
                new_avg = predicted_metrics['accuracy']
            
            if new_avg < state.weekly_accuracy_target:
                violations.append(f"Weekly accuracy target would be missed "
                                f"(estimated: {new_avg:.3f} < {state.weekly_accuracy_target:.3f})")
        
        return len(violations) == 0, violations
    
    def compute_allocation_score(self, evaluator_id: int, recording: RecordingCandidate,
                                predicted_metrics: Dict[str, float]) -> float:
        """
        Compute multi-objective allocation score.
        
        Maximizes:
        - Accuracy
        - Satisfaction
        - Productivity
        - Stamina
        - Fairness
        
        Subject to constraints (checked separately).
        """
        state = self.evaluator_states[evaluator_id]
        
        # Accuracy component
        accuracy_score = self.config.weight_accuracy * predicted_metrics['accuracy']
        
        # Satisfaction component
        satisfaction_score = self.config.weight_satisfaction * predicted_metrics['satisfaction']
        
        # Productivity component (normalized)
        productivity_score = self.config.weight_productivity * min(1.0, predicted_metrics['productivity'] / 10.0)
        
        # Stamina component
        stamina_impact = self.calculate_stamina_impact(recording.difficulty_score, state.current_stamina)
        stamina_score = self.config.weight_stamina * stamina_impact
        
        # Fairness component
        fairness_score = self.calculate_fairness_score(evaluator_id, recording.difficulty_score)
        fairness_component = self.config.weight_fairness * fairness_score

        # Confidence and fatigue adjustments improve robustness under sparse data and workload spikes.
        confidence = self.get_evaluator_confidence(evaluator_id)
        fatigue_index = self.calculate_fatigue_index(evaluator_id)
        confidence_multiplier = 0.8 + 0.2 * confidence
        fatigue_multiplier = 1.0 - 0.15 * fatigue_index

        accuracy_score *= confidence_multiplier * fatigue_multiplier
        satisfaction_score *= (1.0 - 0.10 * fatigue_index)
        productivity_score *= (1.0 - 0.20 * fatigue_index)
        
        # Recent performance adjustment
        recent_perf = self.get_recent_performance(evaluator_id)
        if recent_perf['count'] > 0:
            # If recent performance is poor, boost score for easier recordings
            if recent_perf['avg_accuracy'] < 0.7:
                difficulty_penalty = (recording.difficulty_score / 100.0) * 0.1
                accuracy_score *= (1.0 - difficulty_penalty)
        
        # Combine all components
        total_score = (accuracy_score + satisfaction_score + productivity_score +
                      stamina_score + fairness_component)
        
        return total_score
    
    def generate_rationale(self, evaluator_id: int, recording: RecordingCandidate,
                          predicted_metrics: Dict[str, float], score: float,
                          constraint_violations: List[str]) -> str:
        """
        Generate human-readable rationale explaining why this recording was selected.
        """
        state = self.evaluator_states[evaluator_id]
        rationale_parts = []
        
        # Main selection reason
        rationale_parts.append(f"Selected Recording {recording.sample_id} (User {recording.user_id})")
        
        # Difficulty explanation
        if recording.difficulty_score < 40:
            diff_desc = "low difficulty"
        elif recording.difficulty_score < 70:
            diff_desc = "medium difficulty"
        else:
            diff_desc = "high difficulty"
        
        rationale_parts.append(f"Difficulty: {recording.difficulty_score:.1f}/100 ({diff_desc})")
        
        # Key metrics
        rationale_parts.append(f"Predicted Accuracy: {predicted_metrics['accuracy']:.1%}")
        rationale_parts.append(f"Predicted Satisfaction: {predicted_metrics['satisfaction']:.1%}")
        rationale_parts.append(f"Estimated Effort: {predicted_metrics['effort_minutes']:.1f} minutes")
        
        # Constraints status
        if constraint_violations:
            rationale_parts.append(f"⚠️ Constraints: {', '.join(constraint_violations)}")
        else:
            rationale_parts.append("✅ All constraints satisfied")
        
        # Fairness consideration
        fairness_score = self.calculate_fairness_score(evaluator_id, recording.difficulty_score)
        if fairness_score < 0.7:
            rationale_parts.append(f"Fairness: Adjusting difficulty distribution (score: {fairness_score:.2f})")
        
        # Recent performance consideration
        recent_perf = self.get_recent_performance(evaluator_id)
        if recent_perf['count'] > 0:
            if recent_perf['avg_accuracy'] < 0.7:
                rationale_parts.append(f"Recent Performance: Lower accuracy in last hour ({recent_perf['avg_accuracy']:.1%}), "
                                      f"selecting moderate difficulty to maintain performance")

        # Pair (evaluator-on-this-user) history
        pair = self._pair_history(evaluator_id, recording.user_id) if recording.user_id else None
        if pair:
            n = int(pair.get('sample_count') or 0)
            pair_acc = pair.get('avg_accuracy')
            pair_sat = pair.get('avg_satisfaction')
            pair_eff = pair.get('avg_effort_minutes')
            pieces = [f"Pair History: {n} prior file(s) on user {recording.user_id}"]
            if pair_acc is not None:
                pieces.append(f"avg acc {float(pair_acc):.1%}")
            if pair_sat is not None:
                pieces.append(f"avg sat {float(pair_sat):.1%}")
            if pair_eff is not None:
                pieces.append(f"avg effort {float(pair_eff):.1f}m")
            rationale_parts.append(", ".join(pieces))

        confidence = self.get_evaluator_confidence(evaluator_id)
        fatigue = self.calculate_fatigue_index(evaluator_id)
        rationale_parts.append(f"Profile Confidence: {confidence:.2f} | Fatigue Index: {fatigue:.2f}")
        
        # Weekly targets
        if state.weekly_effort_limit > 0:
            effort_remaining = state.weekly_effort_limit - state.weekly_effort_used
            rationale_parts.append(f"Weekly Effort: {state.weekly_effort_used:.1f}/{state.weekly_effort_limit:.1f} "
                                 f"minutes used ({effort_remaining:.1f} remaining)")
        
        # Overall score
        rationale_parts.append(f"Allocation Score: {score:.4f}")
        
        return " | ".join(rationale_parts)
    
    def allocate_recording(self, evaluator_id: int,
                          available_recording_ids: Optional[List[int]] = None,
                          exclude_recording_ids: Optional[List[int]] = None) -> Optional[AllocationResult]:
        """
        Allocate the best recording for an evaluator using on-demand algorithm.
        
        This is the main entry point for V1 allocation.
        """
        if evaluator_id not in self.evaluator_states:
            print(f"❌ Evaluator {evaluator_id} not found")
            return None
        
        # Filter recordings
        candidates_df = self.recordings_df.copy()
        
        if available_recording_ids:
            candidates_df = candidates_df[candidates_df['sample_id'].isin(available_recording_ids)]
        
        if exclude_recording_ids:
            candidates_df = candidates_df[~candidates_df['sample_id'].isin(exclude_recording_ids)]
        
        if len(candidates_df) == 0:
            print(f"❌ No available recordings for evaluator {evaluator_id}")
            return None
        
        # Score all candidates
        scored_candidates = []
        state = self.evaluator_states[evaluator_id]
        current_hour = datetime.now().hour
        next_sequence_number = state.total_recordings + 1
        
        for _, rec_row in candidates_df.iterrows():
            # Calculate difficulty score (0-100), level, and per-parameter breakdown
            difficulty_score, difficulty_level, difficulty_components = (
                self.calculate_difficulty_score(rec_row)
            )

            # Calculate effort minutes
            effort_minutes = self.calculate_effort_minutes(evaluator_id, rec_row, difficulty_score)

            # Create candidate
            candidate = RecordingCandidate(
                sample_id=rec_row['sample_id'],
                user_id=int(rec_row.get('user_id') or 0),
                difficulty_score=difficulty_score,
                difficulty_level=difficulty_level,
                audio_params={
                    'mistake_level': rec_row.get('mistake_level', 0.5),
                    'background_noise_level': rec_row.get('background_noise_level', 0.5),
                    'repeats_pauses_stutter_level': rec_row.get('repeats_pauses_stutter_level', 0.5),
                    'audio_issues_level': rec_row.get('audio_issues_level', 0.5),
                    'recitation_speed': rec_row.get('recitation_speed', 0.5)
                },
                base_effort_minutes=float(rec_row.get('base_effort_minute') or 5.0),
                recording_time=float(rec_row.get('recording_time') or 5.0),
                difficulty_components=difficulty_components,
            )
            
            # Predict metrics
            predicted_metrics = self.predict_metrics(
                evaluator_id,
                rec_row,
                difficulty_score,
                effort_minutes,
                current_hour=current_hour,
                sequence_number=next_sequence_number
            )
            candidate.predicted_metrics = predicted_metrics
            
            # Check constraints
            is_valid, violations = self.check_constraints(evaluator_id, candidate, predicted_metrics)
            
            # Compute score
            score = self.compute_allocation_score(evaluator_id, candidate, predicted_metrics)
            
            # Apply penalty for constraint violations
            if violations:
                score *= 0.5  # Heavy penalty
            
            scored_candidates.append({
                'candidate': candidate,
                'score': score,
                'violations': violations,
                'is_valid': is_valid
            })
        
        # Sort by score (highest first)
        scored_candidates.sort(key=lambda x: x['score'], reverse=True)
        
        # Select best candidate (prefer valid ones)
        selected = None
        for sc in scored_candidates:
            if sc['is_valid']:
                selected = sc
                break
        
        # If no valid candidate, still select best (with violations)
        if not selected and scored_candidates:
            selected = scored_candidates[0]
        
        if not selected:
            return None

        # Generate rationale
        rationale = self.generate_rationale(
            evaluator_id,
            selected['candidate'],
            selected['candidate'].predicted_metrics,
            selected['score'],
            selected['violations']
        )

        # Build a structured decision breakdown — one entry per signal.
        # The cdm_allocation_service writes these directly into
        # CC_CDM_ALLOCATION_DECISION via CdmAllocationDecision.bulk_record.
        decision_breakdown = self._build_decision_breakdown(
            evaluator_id=evaluator_id,
            candidate=selected['candidate'],
            score=selected['score'],
            violations=selected['violations'],
        )

        # Create result
        result = AllocationResult(
            selected_recording=selected['candidate'],
            evaluator_id=evaluator_id,
            score=selected['score'],
            rationale=rationale,
            constraint_violations=selected['violations'],
            predicted_impact=selected['candidate'].predicted_metrics,
            decision_breakdown=decision_breakdown,
        )

        return result

    # ------------------------------------------------------------------
    # Decision breakdown for CC_CDM_ALLOCATION_DECISION
    # ------------------------------------------------------------------

    def _build_decision_breakdown(self, evaluator_id: int,
                                   candidate: RecordingCandidate,
                                   score: float,
                                   violations: List[str]) -> List[Dict[str, Any]]:
        """
        Produce a list of dicts ready for CdmAllocationDecision.bulk_record.

        Each dict has keys:
          category, parameter, raw_value, numeric_value, weight, contribution, reason

        Categories include:
          • audio_quality      — one entry per CC_AUDIO weighted parameter
          • prediction         — accuracy / satisfaction / effort / productivity
          • evaluator_state    — stamina, fatigue, recent accuracy, weekly load
          • evaluator_history  — pair-history accuracy / satisfaction / sample_count
          • fairness           — distribution & fairness score
          • constraint         — any violated constraints
          • score              — final allocation score
        """
        rows: List[Dict[str, Any]] = []
        state = self.evaluator_states.get(evaluator_id)
        recent = self.get_recent_performance(evaluator_id) if state else None
        confidence = self.get_evaluator_confidence(evaluator_id) if state else None
        fatigue = self.calculate_fatigue_index(evaluator_id) if state else None
        fairness = self.calculate_fairness_score(evaluator_id, candidate.difficulty_score) if state else None
        pair = self._pair_history(evaluator_id, candidate.user_id) if candidate.user_id else None

        # 1. Audio quality components — already computed in
        #    candidate.difficulty_components.
        for comp in candidate.difficulty_components:
            rows.append({
                'category':       'audio_quality',
                'parameter':      comp['column'],
                'raw_value':      comp.get('raw_value'),
                'numeric_value':  comp.get('severity'),
                'weight':         comp.get('weight'),
                'contribution':   comp.get('contribution'),
                'reason':         f"Severity {comp.get('severity', 0):.2f} for "
                                  f"{comp['column']} (weight {comp.get('weight', 0):.2f}) "
                                  f"contributed {comp.get('contribution', 0):.3f} "
                                  f"to difficulty.",
            })

        # 2. Predictions
        pm = candidate.predicted_metrics or {}
        for key, label in [
            ('accuracy',         'PREDICTED_ACCURACY'),
            ('satisfaction',     'PREDICTED_SATISFACTION'),
            ('effort_minutes',   'PREDICTED_EFFORT_MINS'),
            ('productivity',     'PREDICTED_PRODUCTIVITY'),
        ]:
            if key in pm and pm[key] is not None:
                rows.append({
                    'category':      'prediction',
                    'parameter':     label,
                    'numeric_value': float(pm[key]),
                    'reason':        f"{label.replace('_', ' ').title()} = "
                                     f"{pm[key]} (blended from evaluator profile, "
                                     f"pair history & temporal calibration).",
                })

        # 3. Evaluator state
        if state is not None:
            rows.append({
                'category':       'evaluator_state',
                'parameter':      'CURRENT_STAMINA',
                'numeric_value':  float(state.current_stamina),
                'reason':         f"Stamina {state.current_stamina:.2f}/1.0 "
                                  f"(higher → more capacity for hard files).",
            })
            rows.append({
                'category':       'evaluator_state',
                'parameter':      'AVAILABLE_EFFORT_MINUTE',
                'numeric_value':  float(state.available_effort_minutes),
                'reason':         f"{state.available_effort_minutes:.1f} min capacity "
                                  f"vs predicted {pm.get('effort_minutes', 0):.1f} min effort.",
            })
            if state.weekly_effort_limit:
                rows.append({
                    'category':      'evaluator_state',
                    'parameter':     'WEEKLY_EFFORT_USED',
                    'numeric_value': float(state.weekly_effort_used),
                    'reason':        f"Used {state.weekly_effort_used:.1f}/"
                                     f"{state.weekly_effort_limit:.1f} weekly minutes.",
                })
            if confidence is not None:
                rows.append({
                    'category':      'evaluator_state',
                    'parameter':     'PROFILE_CONFIDENCE',
                    'numeric_value': float(confidence),
                    'reason':        f"Confidence {confidence:.2f}/1.0 in evaluator's "
                                     f"historical profile.",
                })
            if fatigue is not None:
                rows.append({
                    'category':      'evaluator_state',
                    'parameter':     'FATIGUE_INDEX',
                    'numeric_value': float(fatigue),
                    'reason':        f"Fatigue {fatigue:.2f}/1.0 — high values "
                                     f"penalise allocation score.",
                })
        if recent and recent.get('count', 0) > 0:
            rows.append({
                'category':      'evaluator_state',
                'parameter':     'RECENT_ACCURACY_1H',
                'numeric_value': float(recent['avg_accuracy']),
                'reason':        f"Last hour averaged {recent['avg_accuracy']:.2%} "
                                 f"accuracy across {recent['count']} files.",
            })

        # 4. Evaluator-on-this-user history
        if pair:
            rows.append({
                'category':      'evaluator_history',
                'parameter':     'PAIR_SAMPLE_COUNT',
                'raw_value':     candidate.user_id,
                'numeric_value': float(pair.get('sample_count') or 0),
                'reason':        f"Evaluator has completed "
                                 f"{int(pair.get('sample_count') or 0)} prior files "
                                 f"for user {candidate.user_id}.",
            })
            if pair.get('avg_accuracy') is not None:
                rows.append({
                    'category':      'evaluator_history',
                    'parameter':     'PAIR_AVG_ACCURACY',
                    'numeric_value': float(pair['avg_accuracy']),
                    'reason':        f"Past accuracy on this user: "
                                     f"{float(pair['avg_accuracy']):.2%}.",
                })
            if pair.get('avg_satisfaction') is not None:
                rows.append({
                    'category':      'evaluator_history',
                    'parameter':     'PAIR_AVG_SATISFACTION',
                    'numeric_value': float(pair['avg_satisfaction']),
                    'reason':        f"Past satisfaction on this user: "
                                     f"{float(pair['avg_satisfaction']):.2%}.",
                })
            if pair.get('avg_effort_minutes') is not None:
                rows.append({
                    'category':      'evaluator_history',
                    'parameter':     'PAIR_AVG_EFFORT_MIN',
                    'numeric_value': float(pair['avg_effort_minutes']),
                    'reason':        f"Past avg effort on this user: "
                                     f"{float(pair['avg_effort_minutes']):.1f} min.",
                })
            if pm.get('pair_confidence') is not None:
                rows.append({
                    'category':      'evaluator_history',
                    'parameter':     'PAIR_CONFIDENCE',
                    'numeric_value': float(pm['pair_confidence']),
                    'reason':        f"Pair-history confidence weight applied to "
                                     f"this prediction: {pm['pair_confidence']:.2f}.",
                })

        # 5. Fairness
        if fairness is not None:
            rows.append({
                'category':      'fairness',
                'parameter':     'FAIRNESS_SCORE',
                'numeric_value': float(fairness),
                'reason':        f"Fairness score {fairness:.2f} — distribution of "
                                 f"low/medium/hard files for this evaluator.",
            })

        # 6. Constraint violations
        for v in violations:
            rows.append({
                'category': 'constraint',
                'parameter': 'CONSTRAINT_VIOLATION',
                'raw_value': v,
                'reason':    v,
            })

        # 7. Final score
        rows.append({
            'category':      'score',
            'parameter':     'ALLOCATION_SCORE',
            'numeric_value': float(score),
            'reason':        f"Multi-objective score "
                             f"(accuracy/satisfaction/productivity/stamina/fairness) "
                             f"= {score:.4f}.",
        })

        return rows
    
    def record_allocation(self, evaluator_id: int, recording_id: int,
                         actual_metrics: Dict[str, float], timestamp: Optional[datetime] = None):
        """
        Record that an allocation was completed.
        Updates evaluator state for next allocation and writes a row to the
        DataCollectionLogger (if one was provided) for future ML training.
        """
        if timestamp is None:
            timestamp = datetime.now()

        state = self.evaluator_states[evaluator_id]

        # Capture stamina before update (needed for logger)
        stamina_before = state.current_stamina

        # Update recent recordings (last 1 hour)
        cutoff_time = timestamp - timedelta(hours=self.config.recent_performance_window_hours)
        state.recent_recordings = [
            r for r in state.recent_recordings
            if r.get('timestamp', timestamp) > cutoff_time
        ]

        # Add new recording to recent window
        state.recent_recordings.append({
            'recording_id': recording_id,
            'accuracy': actual_metrics.get('accuracy', 0.85),
            'satisfaction': actual_metrics.get('satisfaction', 0.75),
            'effort_minutes': actual_metrics.get('effort_minutes', 5.0),
            'timestamp': timestamp
        })

        # Update weekly stats
        state.weekly_effort_used += actual_metrics.get('effort_minutes', 0)

        # Update weekly accuracy (running average)
        n = state.total_recordings
        current_avg = state.weekly_accuracy_avg
        new_accuracy = actual_metrics.get('accuracy', 0.85)
        if n == 0:
            state.weekly_accuracy_avg = new_accuracy
        else:
            state.weekly_accuracy_avg = (current_avg * n + new_accuracy) / (n + 1)

        # Update difficulty history and stamina
        recording_rows = self.recordings_df[self.recordings_df['sample_id'] == recording_id]
        difficulty_score = 50.0  # fallback
        user_id = 0
        if len(recording_rows) > 0:
            rec = recording_rows.iloc[0]
            difficulty_score, _, _ = self.calculate_difficulty_score(rec)
            user_id = int(rec.get('user_id') or 0)
            if difficulty_score < 40:
                category = 'low'
            elif difficulty_score < 70:
                category = 'medium'
            else:
                category = 'hard'
            state.difficulty_history[category] += 1
            state.current_stamina = self.calculate_stamina_impact(difficulty_score, state.current_stamina)

        state.total_recordings += 1

        # Persistent data collection for future ML
        if self.logger is not None:
            self.logger.log(
                timestamp=timestamp,
                evaluator_id=evaluator_id,
                recording_id=recording_id,
                user_id=user_id,
                difficulty_score=difficulty_score,
                effort_minutes=actual_metrics.get('effort_minutes', 5.0),
                accuracy=actual_metrics.get('accuracy', 0.85),
                satisfaction=actual_metrics.get('satisfaction', 0.75),
                stamina_before=stamina_before,
                stamina_after=state.current_stamina,
                sequence_number=state.total_recordings,
                weekly_effort_used=state.weekly_effort_used,
                weekly_accuracy_avg=state.weekly_accuracy_avg,
            )
    
    def get_evaluator_state_summary(self, evaluator_id: int) -> Dict:
        """Get summary of evaluator's current state"""
        state = self.evaluator_states[evaluator_id]
        recent_perf = self.get_recent_performance(evaluator_id)
        
        return {
            'evaluator_id': evaluator_id,
            'available_effort_minutes': state.available_effort_minutes,
            'weekly_effort_used': state.weekly_effort_used,
            'weekly_effort_limit': state.weekly_effort_limit,
            'weekly_accuracy_avg': state.weekly_accuracy_avg,
            'weekly_accuracy_target': state.weekly_accuracy_target,
            'current_stamina': state.current_stamina,
            'recent_recordings_count': len(state.recent_recordings),
            'recent_accuracy': recent_perf['avg_accuracy'],
            'recent_satisfaction': recent_perf['avg_satisfaction'],
            'difficulty_distribution': dict(state.difficulty_history),
            'total_recordings': state.total_recordings
        }


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

# Map between the lowercase keys we see in `pd.Series` rows (built by the
# cdm_allocation_service from AAIAPWORKFILE.to_recording_dict) and the
# uppercase column names expected by audio_difficulty.compute_audio_difficulty.
_RECORDING_COLUMN_MAP = {
    'AUDIO_LENGTH':                 'audio_length',
    'REPEATS_PAUSES_STUTTER_LEVEL': 'repeats_pauses_stutter_level',
    'AUDIO_SOURCE':                 'audio_source',
    'MISTAKE_LEVEL':                'mistake_level',
    'AUDIO_ISSUES_LEVEL':           'audio_issues_level',
    'RECITATION_SPEED':             'recitation_speed',
    'VOICE_PITCH':                  'voice_pitch',
    'VOICE_CLARITY':                'voice_clarity',
    'BACKGROUND_NOISE_LEVEL':       'background_noise_level',
    'DURATION':                     'recording_time',
}


def _recording_to_audio_attrs(recording) -> Dict[str, Any]:
    """
    Translate a single recording row (dict or pandas Series) into the
    UPPERCASE column dict expected by `audio_difficulty.compute_audio_difficulty`.
    """
    attrs: Dict[str, Any] = {}
    for upper, lower in _RECORDING_COLUMN_MAP.items():
        v = recording.get(lower)
        if v is None or (isinstance(v, float) and pd.isna(v)):
            v = None
        attrs[upper] = v
    return attrs


def _difficulty_label(score: float) -> str:
    """Map a 0–100 difficulty score to {'low', 'medium', 'hard'}."""
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


if __name__ == "__main__":
    # Example usage
    config = AllocationConfig()
    allocator = OnDemandAllocator(config)
    
    allocator.load_data(
        "recordings.csv",
        "evaluators.csv",
        "users.csv",
        "performance.csv"
    )
    
    allocator.fit()
    
    # Allocate recording for evaluator 1
    result = allocator.allocate_recording(1)
    
    if result:
        print("\n" + "="*80)
        print("ALLOCATION RESULT")
        print("="*80)
        print(f"Evaluator ID: {result.evaluator_id}")
        print(f"Selected Recording: {result.selected_recording.sample_id}")
        print(f"Difficulty Score: {result.selected_recording.difficulty_score:.1f}/100")
        print(f"Score: {result.score:.4f}")
        print(f"\nRationale:\n{result.rationale}")
        print(f"\nPredicted Impact:")
        for key, value in result.predicted_impact.items():
            print(f"  {key}: {value}")
        if result.constraint_violations:
            print(f"\n⚠️ Constraint Violations:")
            for violation in result.constraint_violations:
                print(f"  - {violation}")
    else:
        print("❌ No allocation possible")
