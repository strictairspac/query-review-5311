import json
from fastapi import APIRouter,Depends
from sqlalchemy import select
from ..config.database import get_db
from ..schemas.batch import BatchInput,ClaimInput
from ..services.auth import current_user,writer
from ..services.batch import list_batches,create_batch,detail
from ..services.claim import claims_for,merged_pairs,state_for
from ..services.claim import create_claim as submit_claim
from ..models.evidence import Evidence
router=APIRouter(prefix='/batches')
@router.get('')
def listing(db=Depends(get_db),user=Depends(current_user)):return list_batches(db)
@router.post('',status_code=202)
def create(data:BatchInput,db=Depends(get_db),user=Depends(writer)):return create_batch(db,data)
@router.get('/{batch_id}')
def one(batch_id:int,db=Depends(get_db),user=Depends(current_user)):
    item=detail(db,batch_id)
    evidence=db.scalar(select(Evidence).where(Evidence.batch_id==batch_id))
    block=(evidence.result or {}).get('ticketless') if evidence else None
    # 认领依据与结论都来自库中记录；批次状态也是合并后的当前值。
    item['claims']=claims_for(db,batch_id)
    if block:
        state=state_for(evidence.result,merged_pairs(db,batch_id))
        item['ticketless']=state
        for field in ('tolerance','rule','left_columns','right_columns','left_rows','right_rows','initial_status'):
            item['ticketless'][field]=block.get(field)
    return item
@router.get('/{batch_id}/claims')
def listing_claims(batch_id:int,db=Depends(get_db),user=Depends(current_user)):
    detail(db,batch_id)
    return claims_for(db,batch_id)
@router.post('/{batch_id}/claims',status_code=201)
def claim(batch_id:int,data:ClaimInput,db=Depends(get_db),user=Depends(writer)):
    return submit_claim(db,batch_id,data,user.email)
