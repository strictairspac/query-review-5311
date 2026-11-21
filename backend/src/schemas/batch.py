from typing import Annotated
from pydantic import BaseModel,Field
class BatchInput(BaseModel):
    name:str=Field(min_length=2,max_length=120)
    sample_id:int=Field(gt=0)
    candidate_sql:str=Field(min_length=6,max_length=6000)
    mode:str=Field(default='exact')

Pair=Annotated[list[int],Field(min_length=2,max_length=2)]

class ClaimInput(BaseModel):
    # 每对恰好两个行号：左侧结果行号、右侧结果行号。
    pairs:list[Pair]=Field(min_length=1,max_length=200)
    idempotency_key:str=Field(min_length=8,max_length=64)
