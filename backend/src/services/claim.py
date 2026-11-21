import json
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from ..models.batch import Batch
from ..models.claim import Claim
from ..models.evidence import Evidence
from .ticketless import ClaimError, evaluate_public

CLAIMABLE_STATUSES=('pending_claim',)


def serialize(claim):
    return {
        'id': claim.id,
        'batch_id': claim.batch_id,
        'actor': claim.actor,
        'idempotency_key': claim.idempotency_key,
        'pairs': json.loads(claim.pairs),
        'status': claim.status,
        'conclusion': json.loads(claim.conclusion),
        'created_at': str(claim.created_at),
    }


def merged_pairs(db, batch_id):
    pairs = []
    for claim in db.scalars(select(Claim).where(Claim.batch_id == batch_id).order_by(Claim.id)):
        pairs.extend(json.loads(claim.pairs))
    return pairs


def claims_for(db, batch_id):
    return [serialize(c) for c in db.scalars(
        select(Claim).where(Claim.batch_id == batch_id).order_by(Claim.id)
    )]


def state_for(result, pairs):
    """合并全部认领后的当前结论。

    正常入库路径不会出现“已入库认领合并后无解”，但为防并发写坏数据或人工改库，
    这里把合并无解作为可展示的冲突结论返回，而不是让查询报错。
    """
    block = result.get('ticketless')
    if not block:
        return None
    try:
        return evaluate_public(block, pairs)
    except ClaimError as exc:
        return {
            'status': 'claim_conflict',
            'matching_count': None,
            'matching': None,
            'example_matchings': [],
            'candidate_classes': [],
            'candidates_truncated': False,
            'pair_count': None,
            'claims': [list(p) for p in pairs],
            'issue': f'已入库认领合并后无法配齐：{exc}；请重新认领',
        }


def create_claim(db, batch_id, data, actor):
    """在批次行锁内合并已有认领与本次认领，再重算结论。

    idempotency_key 命中时直接返回原来入库的结论，不再新增认领。
    """
    key = data.idempotency_key.strip()
    batch = db.scalar(select(Batch).where(Batch.id == batch_id).with_for_update())
    if not batch:
        raise HTTPException(404, '批次不存在')
    if batch.mode != 'ticketless':
        raise HTTPException(409, '该批次为精确比对，不支持无单号认领')

    existing = db.scalar(select(Claim).where(Claim.idempotency_key == key))
    if existing:
        if existing.batch_id != batch_id:
            raise HTTPException(409, '该幂等键已用于其他批次，请重新发起认领')
        return serialize(existing)

    if batch.status == 'failed':
        raise HTTPException(409, '批次执行失败，没有可认领的配对依据')
    if batch.status == 'different':
        raise HTTPException(409, '该批次无法配齐，没有可认领的全配方案')
    if batch.status == 'passed':
        raise HTTPException(409, '该批次已唯一配齐，无需再认领')
    if batch.status not in CLAIMABLE_STATUSES:
        raise HTTPException(409, '批次尚未完成，请等待 worker 执行')

    evidence = db.scalar(select(Evidence).where(Evidence.batch_id == batch_id))
    if not evidence:
        raise HTTPException(409, '证据尚未生成，请等待 worker 完成')
    result = evidence.result or {}
    block = result.get('ticketless')
    if not block:
        raise HTTPException(409, '该批次没有无单号对账依据')

    pairs = [list(p) for p in (data.pairs or [])]
    if not pairs:
        raise HTTPException(422, '至少选择一对要认领的配对')
    merged = merged_pairs(db, batch_id) + pairs
    try:
        state = evaluate_public(block, merged)
    except ClaimError as exc:
        raise HTTPException(409, f'合并已有认领后无法配齐：{exc}；请重新选择要认领的配对')
    except ValueError as exc:
        raise HTTPException(422, str(exc))

    if state['status'] == 'different':
        raise HTTPException(
            409,
            f"合并已有认领后无法配齐：{state['issue']}；请重新选择要认领的配对",
        )

    conclusion = {
        'status': state['status'],
        'matching_count': state['matching_count'],
        'pair_count': state['pair_count'],
        'matching': state['matching'],
        'issue': state['issue'],
        'merged_basis': merged,
        'exact_comparison_unchanged': True,
    }
    claim = Claim(
        batch_id=batch_id,
        idempotency_key=key,
        actor=actor,
        pairs=json.dumps(pairs, ensure_ascii=False),
        status=state['status'],
        conclusion=json.dumps(conclusion, ensure_ascii=False),
    )
    batch.status = state['status']
    db.add(claim)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(Claim).where(Claim.idempotency_key == key))
        if existing:
            return serialize(existing)
        raise HTTPException(409, '认领冲突，请重试')
    db.refresh(claim)
    return serialize(claim)
