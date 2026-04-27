"""
CDM Allocation Blueprint — IAP-facing REST API

Routes
------
POST /cdm/request-files       On-Demand: assign 2 workfiles to one IAP evaluator
POST /cdm/scheduled-run       Scheduled: batch assign 8-10 workfiles per evaluator
POST /cdm/complete            Log actual metrics when an evaluator finishes a file
GET  /cdm/decisions           Admin decision log (filterable by stage/evaluator/date)
GET  /cdm/evaluation          Algorithm evaluation — predicted vs actual trends
POST /cdm/recompute-baselines Admin: refresh CC_EFFORT_BASELINE table

All evaluator data is fetched from AA_IAP_USERS by evaluator_id.
All candidate files are fetched from AA_IAP_WORKFILE filtered by STAGE (L3/L4).
"""

import datetime

from flask import Blueprint, request, jsonify
from marshmallow import ValidationError

from src.models import CdmAllocation, CdmAllocationDecision, CdmEvaluatorPerformance
from src.schemas import (
    CdmRequestFileSchema, CdmScheduledRunSchema, CdmCompleteSchema,
)
from src.services import cdm_allocation_service, cdm_effort_service

cdm_allocation_api = Blueprint('cdm_allocation_api', __name__)


# ---------------------------------------------------------------------------
# POST /cdm/request-files  — On-Demand mode
# ---------------------------------------------------------------------------

@cdm_allocation_api.route('/cdm/request-files', methods=['POST'])
def request_files():
    """
    IAP calls this when an evaluator clicks "Request File".
    Returns 2 CDM-allocated workfiles for the evaluator.

    Request body (JSON):
      evaluator_id        int    required  — AA_IAP_USERS.ID
      stage               str    required  — 'L3' or 'L4'
      effort_bias_factor  float  optional  — default 1.0 (v1 normalizer)
      num_files           int    optional  — default 2
    """
    try:
        data = CdmRequestFileSchema().load(request.get_json() or {})
    except ValidationError as err:
        return jsonify({'errors': err.messages}), 400

    try:
        assignments = cdm_allocation_service.allocate_on_demand(
            evaluator_id       = data['evaluator_id'],
            stage              = data['stage'],
            n_files            = data.get('num_files', 2),
            effort_bias_factor = data.get('effort_bias_factor', 1.0),
        )
    except Exception as err:
        return jsonify({'error': str(err)}), 500

    if not assignments:
        return jsonify({
            'message':      'No eligible workfiles available for this stage.',
            'evaluator_id': data['evaluator_id'],
            'stage':        data['stage'],
            'assignments':  [],
        }), 200

    return jsonify({
        'evaluator_id': data['evaluator_id'],
        'stage':        data['stage'],
        'assignments':  assignments,
    }), 200


# ---------------------------------------------------------------------------
# POST /cdm/scheduled-run  — Scheduled mode
# ---------------------------------------------------------------------------

@cdm_allocation_api.route('/cdm/scheduled-run', methods=['POST'])
def scheduled_run():
    """
    Triggered daily (or manually) to assign 8-10 workfiles to every evaluator
    in the list — targeting 100% capacity utilisation.

    Request body (JSON):
      stage              str          required  — 'L3' or 'L4'
      evaluator_ids      list[int]    required  — AA_IAP_USERS.ID values
      files_per_user     int          optional  — default 9  (range 8-10)
      effort_bias_factor float        optional  — default 1.0
    """
    try:
        data = CdmScheduledRunSchema().load(request.get_json() or {})
    except ValidationError as err:
        return jsonify({'errors': err.messages}), 400

    try:
        result = cdm_allocation_service.allocate_scheduled(
            evaluator_ids      = data['evaluator_ids'],
            stage              = data['stage'],
            files_per_user     = data.get('files_per_user', 9),
            effort_bias_factor = data.get('effort_bias_factor', 1.0),
        )
    except Exception as err:
        return jsonify({'error': str(err)}), 500

    return jsonify(result), 200


# ---------------------------------------------------------------------------
# POST /cdm/complete  — log actual metrics
# ---------------------------------------------------------------------------

@cdm_allocation_api.route('/cdm/complete', methods=['POST'])
def complete_allocation():
    """
    Called by IAP when an evaluator finishes reviewing a workfile.
    Stores actual accuracy, satisfaction, and effort; triggers baseline refresh.

    Request body (JSON):
      allocation_id       int    required
      actual_accuracy     float  required  (0.0–1.0)
      actual_satisfaction float  required  (0.0–1.0)
      actual_effort_mins  float  required
    """
    try:
        data = CdmCompleteSchema().load(request.get_json() or {})
    except ValidationError as err:
        return jsonify({'errors': err.messages}), 400

    allocation = CdmAllocation.query.get(data['allocation_id'])
    if not allocation:
        return jsonify({'error': 'Allocation not found.'}), 404

    if allocation.STATUS == 'completed':
        return jsonify({'error': 'Allocation already marked completed.'}), 409

    try:
        # Single transaction:
        #   1. Updates CC_CDM_ALLOCATION (actual metrics + completed status)
        #   2. Upserts CC_CDM_EVALUATOR_PERFORMANCE so the next allocation
        #      for this (evaluator, user) sees the latest pair history.
        completion = cdm_allocation_service.complete_allocation(
            allocation_id        = data['allocation_id'],
            actual_accuracy      = data['actual_accuracy'],
            actual_satisfaction  = data['actual_satisfaction'],
            actual_effort_mins   = data['actual_effort_mins'],
        )
    except ValueError as err:
        return jsonify({'error': str(err)}), 404
    except Exception as err:
        return jsonify({'error': str(err)}), 500

    # Refresh baseline for this workfile — best-effort, never fails the response
    try:
        if allocation.IAP_WORKFILE_ID:
            cdm_effort_service.compute_baseline(
                iap_workfile_id = allocation.IAP_WORKFILE_ID,
                stage           = allocation.STAGE,
                cc_audio_id     = allocation.CCAUDIO_ID,
            )
    except Exception:
        pass

    return jsonify({
        'message':              'Allocation completed successfully.',
        'allocation_id':        allocation.ID,
        'status':               'completed',
        'evaluator_performance': completion.get('evaluator_performance'),
    }), 200


# ---------------------------------------------------------------------------
# GET /cdm/decisions  — admin decision log
# ---------------------------------------------------------------------------

@cdm_allocation_api.route('/cdm/decisions', methods=['GET'])
def get_decisions():
    """
    Returns CC_CDM_ALLOCATION rows so admins can audit each file assignment.

    Query parameters (all optional):
      evaluator_id  int
      stage         str   (L3 / L4)
      status        str   (pending / completed)
      from_date     str   ISO date  YYYY-MM-DD
      to_date       str   ISO date  YYYY-MM-DD
      page          int   default 1
      per_page      int   default 20  (max 100)
    """
    evaluator_id = request.args.get('evaluator_id', type=int)
    stage        = request.args.get('stage',        type=str)
    status       = request.args.get('status',       type=str)
    from_date    = request.args.get('from_date',    type=str)
    to_date      = request.args.get('to_date',      type=str)
    page         = request.args.get('page',         default=1,  type=int)
    per_page     = min(request.args.get('per_page', default=20, type=int), 100)

    query = CdmAllocation.query

    if evaluator_id:
        query = query.filter(CdmAllocation.EVALUATOR_ID == evaluator_id)
    if stage:
        query = query.filter(CdmAllocation.STAGE == stage)
    if status:
        query = query.filter(CdmAllocation.STATUS == status)
    if from_date:
        try:
            query = query.filter(
                CdmAllocation.ALLOCATED_DTS >= datetime.datetime.fromisoformat(from_date)
            )
        except ValueError:
            return jsonify({'error': 'Invalid from_date. Use YYYY-MM-DD.'}), 400
    if to_date:
        try:
            query = query.filter(
                CdmAllocation.ALLOCATED_DTS <= datetime.datetime.fromisoformat(to_date)
                + datetime.timedelta(days=1)
            )
        except ValueError:
            return jsonify({'error': 'Invalid to_date. Use YYYY-MM-DD.'}), 400

    total   = query.count()
    records = (
        query.order_by(CdmAllocation.ALLOCATED_DTS.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    return jsonify({
        'total':     total,
        'page':      page,
        'per_page':  per_page,
        'decisions': [r.to_json() for r in records],
    }), 200


# ---------------------------------------------------------------------------
# GET /cdm/decisions/<allocation_id>  — per-allocation parameter rationale
# ---------------------------------------------------------------------------

@cdm_allocation_api.route('/cdm/decisions/<int:allocation_id>', methods=['GET'])
def get_decision_breakdown(allocation_id: int):
    """
    Return the full per-parameter decision log for a single allocation.

    The response includes:
      • the headline CC_CDM_ALLOCATION row
      • all CC_CDM_ALLOCATION_DECISION rows grouped by category
        (audio_quality / evaluator_state / evaluator_history / fairness /
        prediction / constraint / score)

    This is what answers "why was this file allocated to this user?".
    """
    allocation = CdmAllocation.query.get(allocation_id)
    if allocation is None:
        return jsonify({'error': 'Allocation not found.'}), 404

    rows = (
        CdmAllocationDecision.query
        .filter_by(ALLOCATION_ID=allocation_id)
        .order_by(CdmAllocationDecision.CATEGORY, CdmAllocationDecision.ID)
        .all()
    )

    grouped: dict = {}
    for r in rows:
        grouped.setdefault(r.CATEGORY, []).append(r.to_json())

    pair_history = None
    if allocation.EVALUATOR_ID and allocation.USER_ID:
        rec = CdmEvaluatorPerformance.lookup(
            evaluator_id=allocation.EVALUATOR_ID, user_id=allocation.USER_ID
        )
        if rec:
            pair_history = rec.to_json()

    return jsonify({
        'allocation':              allocation.to_json(),
        'decisions_by_category':   grouped,
        'evaluator_pair_history':  pair_history,
        'total_decisions':         len(rows),
    }), 200


# ---------------------------------------------------------------------------
# GET /cdm/evaluation  — algorithm evaluation
# ---------------------------------------------------------------------------

@cdm_allocation_api.route('/cdm/evaluation', methods=['GET'])
def get_evaluation():
    """
    Evaluates whether CDM has improved accuracy, satisfaction, and momentum
    by comparing predicted vs actual metrics across completed allocations.

    Query parameters (all optional):
      stage       str   (L3 / L4)
      from_date   str   ISO date
      to_date     str   ISO date
    """
    stage     = request.args.get('stage',     type=str)
    from_date = request.args.get('from_date', type=str)
    to_date   = request.args.get('to_date',   type=str)

    query = CdmAllocation.query.filter(CdmAllocation.STATUS == 'completed')

    if stage:
        query = query.filter(CdmAllocation.STAGE == stage)
    if from_date:
        try:
            query = query.filter(
                CdmAllocation.COMPLETED_DTS >= datetime.datetime.fromisoformat(from_date)
            )
        except ValueError:
            return jsonify({'error': 'Invalid from_date.'}), 400
    if to_date:
        try:
            query = query.filter(
                CdmAllocation.COMPLETED_DTS <= datetime.datetime.fromisoformat(to_date)
                + datetime.timedelta(days=1)
            )
        except ValueError:
            return jsonify({'error': 'Invalid to_date.'}), 400

    rows = query.all()
    if not rows:
        return jsonify({'message': 'No completed allocations found for the given filters.'}), 200

    total = len(rows)

    def _avg(vals):
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals), 4) if vals else None

    avg_pred_acc  = _avg([r.PREDICTED_ACCURACY     for r in rows])
    avg_act_acc   = _avg([r.ACTUAL_ACCURACY         for r in rows])
    avg_pred_sat  = _avg([r.PREDICTED_SATISFACTION  for r in rows])
    avg_act_sat   = _avg([r.ACTUAL_SATISFACTION     for r in rows])
    avg_pred_eff  = _avg([r.PREDICTED_EFFORT_MINS   for r in rows])
    avg_act_eff   = _avg([r.ACTUAL_EFFORT_MINS      for r in rows])

    def _delta(a, b):
        return round(a - b, 4) if (a is not None and b is not None) else None

    # Utilisation = completed / total allocated
    total_q = CdmAllocation.query
    if stage:
        total_q = total_q.filter(CdmAllocation.STAGE == stage)
    utilisation = round(total / total_q.count(), 4) if total_q.count() else None

    # Weekly momentum
    buckets: dict = {}
    for r in rows:
        if r.COMPLETED_DTS:
            wk = r.COMPLETED_DTS.strftime('%Y-W%W')
            if wk not in buckets:
                buckets[wk] = {'count': 0, 'accuracy': [], 'satisfaction': []}
            buckets[wk]['count'] += 1
            if r.ACTUAL_ACCURACY     is not None: buckets[wk]['accuracy'].append(r.ACTUAL_ACCURACY)
            if r.ACTUAL_SATISFACTION is not None: buckets[wk]['satisfaction'].append(r.ACTUAL_SATISFACTION)

    momentum = [
        {
            'week':             k,
            'count':            v['count'],
            'avg_accuracy':     _avg(v['accuracy']),
            'avg_satisfaction': _avg(v['satisfaction']),
        }
        for k, v in sorted(buckets.items())
    ]

    return jsonify({
        'total_completed':  total,
        'utilisation_rate': utilisation,
        'accuracy': {
            'avg_predicted': avg_pred_acc,
            'avg_actual':    avg_act_acc,
            'delta':         _delta(avg_act_acc, avg_pred_acc),
        },
        'satisfaction': {
            'avg_predicted': avg_pred_sat,
            'avg_actual':    avg_act_sat,
            'delta':         _delta(avg_act_sat, avg_pred_sat),
        },
        'effort_mins': {
            'avg_predicted': avg_pred_eff,
            'avg_actual':    avg_act_eff,
            'delta':         _delta(avg_act_eff, avg_pred_eff),
        },
        'weekly_momentum': momentum,
    }), 200


# ---------------------------------------------------------------------------
# POST /cdm/recompute-baselines  — admin baseline refresh
# ---------------------------------------------------------------------------

@cdm_allocation_api.route('/cdm/recompute-baselines', methods=['POST'])
def recompute_baselines():
    """
    Admin: re-derives CC_EFFORT_BASELINE from all completed CC_CDM_ALLOCATION rows.

    Optional JSON body:
      stage  str  — limit refresh to 'L3' or 'L4' only
    """
    body  = request.get_json(silent=True) or {}
    stage = body.get('stage')

    try:
        summary = cdm_effort_service.recompute_all_baselines(stage=stage)
    except Exception as err:
        return jsonify({'error': str(err)}), 500

    return jsonify({
        'message': 'Baseline recomputation complete.',
        'stage':   stage or 'all',
        **summary,
    }), 200
