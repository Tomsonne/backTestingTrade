from __future__ import annotations
from dataclasses import dataclass
from datetime import date,datetime,timedelta
from zoneinfo import ZoneInfo
import pandas as pd
from .config import SessionDef

@dataclass
class SessionInstance:
    name:str; start:pd.Timestamp; end:pd.Timestamp; trade_date:date

def build_session_instances(start,end,defs,timezone):
    tz=ZoneInfo(timezone); d0=start.astimezone(tz).date()-timedelta(days=4); d1=end.astimezone(tz).date()+timedelta(days=1); out=[]; d=d0
    while d<=d1:
        for s in defs:
            st=datetime.combine(d,s.start,tzinfo=tz); ed=d+timedelta(days=1) if s.overnight else d; en=datetime.combine(ed,s.end,tzinfo=tz)
            td=en.date() if s.overnight else st.date(); out.append(SessionInstance(s.name,pd.Timestamp(st).tz_convert("UTC"),pd.Timestamp(en).tz_convert("UTC"),td))
        d+=timedelta(days=1)
    return sorted(out,key=lambda x:x.start)

def session_slice(df,s):return df[(df.index>=s.start)&(df.index<s.end)]
