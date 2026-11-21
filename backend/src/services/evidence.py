from sqlalchemy import select
from fastapi import HTTPException
from ..models.evidence import Evidence
from ..models.batch import Batch
from .claim import claims_for,merged_pairs,state_for

def summary(batch,x):
    result=x.result or {}
    block=result.get('ticketless')
    return {
        'id':x.id,
        'batch_id':x.batch_id,
        'equivalent':result.get('equivalent'),
        'error':bool(result.get('error')),
        'ticketless_error':bool(result.get('ticketless_error')),
        'ticketless_status':block.get('initial_status') if block else None,
        'batch_status':batch.status if batch else None,
    }

def list_evidence(db):
    items=[]
    for x in db.scalars(select(Evidence).order_by(Evidence.id.desc())):
        items.append(summary(db.get(Batch,x.batch_id),x))
    return items

def get_evidence(db,batch_id):
    x=db.scalar(select(Evidence).where(Evidence.batch_id==batch_id))
    if not x:raise HTTPException(404,'证据尚未生成，请等待 worker 完成')
    batch=db.get(Batch,batch_id)
    result=dict(x.result or {})
    if result.get('ticketless'):
        # 证据保存的是初始结论；页面展示合并全部认领后的当前结论。
        state=state_for(result,merged_pairs(db,batch_id))
        result['ticketless']=dict(result['ticketless'],**state)
        result['ticketless']['claims']=claims_for(db,batch_id)
        result['ticketless']['batch_status']=batch.status if batch else None
    return {'id':x.id,'batch_id':batch_id,'result':result}
