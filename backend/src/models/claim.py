from sqlalchemy.orm import Mapped,mapped_column
from sqlalchemy import String,Text,ForeignKey,Integer,DateTime,func
from datetime import datetime
from ..config.database import Base
class Claim(Base):
    """一次无单号对账的认领请求。

    依据是提交时被固定的配对集合，结论是合并全部已认领依据后重算的配对结果。
    两者同一条记录同时生效；idempotency_key 保证断线重试不会重复认领。
    """
    __tablename__='claims'
    id:Mapped[int]=mapped_column(primary_key=True)
    batch_id:Mapped[int]=mapped_column(ForeignKey('batches.id'),index=True)
    idempotency_key:Mapped[str]=mapped_column(String(64),unique=True)
    actor:Mapped[str]=mapped_column(String(120))
    pairs:Mapped[str]=mapped_column(Text)
    status:Mapped[str]=mapped_column(String(20))
    conclusion:Mapped[str]=mapped_column(Text)
    created_at:Mapped[datetime]=mapped_column(DateTime,server_default=func.now())
