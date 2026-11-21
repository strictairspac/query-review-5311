from pydantic import BaseModel
class EvidenceSummary(BaseModel):
    id:int
    batch_id:int
    equivalent:bool|None=None
    error:bool=False
    ticketless_error:bool=False
    ticketless_status:str|None=None
    batch_status:str|None=None

class EvidenceOutput(BaseModel):
    id:int
    batch_id:int
    result:dict
