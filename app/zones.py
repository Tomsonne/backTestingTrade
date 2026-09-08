from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
from .data.resampler import resample_market_data

@dataclass
class Zone:
    timeframe:str; direction:str; bottom:float; top:float; formed_at:pd.Timestamp; invalidated_at:pd.Timestamp|None; adr_pct:float

def resample_ohlcv(m1,rule,timezone):
    interval={"2h":"H2","4h":"H4","1h":"H1","5min":"M5"}.get(rule,rule)
    return resample_market_data(m1,interval,timezone)

def daily_adr_series(m1,timezone,length=15):
    local=m1.tz_convert(timezone)
    daily=local.resample("1D",origin="start_day",label="left",closed="left").agg({"mid_h":"max","mid_l":"min"}).dropna()
    return (daily.mid_h-daily.mid_l).shift(1).rolling(length,min_periods=length).mean()

def build_zones(m1,timeframe,timezone,min_adr_pct=.5,adr_length=15,mitigation_mode="wick"):
    hours={"H1":1,"H2":2,"H4":4}
    if timeframe not in hours:raise ValueError(f"Unsupported zone timeframe: {timeframe}")
    rule=f"{hours[timeframe]}h"; duration=pd.Timedelta(hours=hours[timeframe])
    htf=resample_ohlcv(m1,rule,timezone); adr=daily_adr_series(m1,timezone,adr_length)
    dates=htf.tz_convert(timezone).index.normalize(); adr_vals=adr.reindex(dates).to_numpy()
    o,h,l,c=[htf[x].to_numpy() for x in ("mid_o","mid_h","mid_l","mid_c")]; zones=[]
    def kind(i):return "up" if o[i]<c[i] else "down" if o[i]>c[i] else "doji"
    for i in range(2,len(htf)):
        a=adr_vals[i] if i<len(adr_vals) else np.nan
        if np.isnan(a) or a<=0:continue
        k0,k1,k2=kind(i),kind(i-1),kind(i-2)
        sb=l[i-2]-h[i]
        if k1=="down" and not(k2=="doji" and k0=="doji") and sb>0 and 100*sb/a>min_adr_pct:
            zones.append(Zone(timeframe,"bearish",float(h[i]),float(l[i-2]),htf.index[i]+duration,None,float(100*sb/a)))
        su=l[i]-h[i-2]
        if k1=="up" and not(k2=="doji" and k0=="doji") and su>0 and 100*su/a>min_adr_pct:
            zones.append(Zone(timeframe,"bullish",float(h[i-2]),float(l[i]),htf.index[i]+duration,None,float(100*su/a)))
    for z in zones:
        future=m1[m1.index>=z.formed_at]
        if mitigation_mode=="close":
            hit=future[future.mid_c>z.top] if z.direction=="bearish" else future[future.mid_c<z.bottom]
        elif mitigation_mode=="wick":
            hit=future[future.mid_h>z.top] if z.direction=="bearish" else future[future.mid_l<z.bottom]
        else:raise ValueError(f"Unsupported mitigation mode: {mitigation_mode}")
        if not hit.empty:z.invalidated_at=hit.index[0]
    return zones

def find_active_zone(zones,t,price,trade_direction,direction_match=True):
    wanted="bullish" if trade_direction=="long" else "bearish"; found=[]
    for z in zones:
        if direction_match and z.direction!=wanted:continue
        if z.formed_at>t:continue
        if z.invalidated_at is not None and t>=z.invalidated_at:continue
        if z.bottom<=price<=z.top:found.append(z)
    return max(found,key=lambda z:z.formed_at) if found else None
