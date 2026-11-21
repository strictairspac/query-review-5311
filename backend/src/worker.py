import time,json,traceback
from pathlib import Path
from sqlalchemy import select
from .config.database import Session
from .models.batch import Batch
from .models.sample import Sample
from .models.evidence import Evidence
from .services.batch import queue
from .services.query_engine import compare
from .services.ticketless import build_evidence
heartbeat=Path('/artifacts/worker.heartbeat')
def run_one(batch_id):
    with Session() as db:
        batch=db.get(Batch,batch_id)
        if not batch or db.scalar(select(Evidence).where(Evidence.batch_id==batch_id)):return
        sample=db.get(Sample,batch.sample_id);batch.status='running';db.commit()
        try:
            result=compare(sample.original_sql,batch.candidate_sql)
            if batch.mode=='ticketless':
                try:
                    evidence=build_evidence(result['original'],result['candidate'])
                    result['ticketless']=evidence
                    batch.status=evidence['initial_status']
                except Exception as exc:
                    # 结果列不符合无单号对账口径时，保留严格比对结论，但标记失败并给出原因。
                    result['ticketless_error']=str(exc)[:500]
                    batch.status='failed'
            else:
                batch.status='passed' if result['equivalent'] else 'different'
        except Exception as exc:
            result={'error':str(exc)[:500]};batch.status='failed'
        db.add(Evidence(batch_id=batch_id,result=result));db.commit()
        Path(f'/artifacts/review-{batch_id}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
while True:
    try:
        heartbeat.write_text(str(time.time()))
        item=queue.blpop('query-review:jobs',timeout=2)
        if item:run_one(int(item[1]))
        else:
            with Session() as db:pending=list(db.scalars(select(Batch.id).where(Batch.status.in_(['queued','running']))))
            for ident in pending:run_one(ident)
    except Exception:traceback.print_exc();time.sleep(2)
