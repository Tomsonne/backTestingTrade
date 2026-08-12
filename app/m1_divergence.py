from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import pandas as pd
from .indicators import indicator_matrix

@dataclass
class LabelEvent:
    bar_index:int; time:pd.Timestamp; direction:str; count:int; a_entry_index:int|None=None; b_entry_index:int|None=None

def _ph(a,i,lb,rb):
    if i-lb<0 or i+rb>=len(a): return False
    c=a[i]; left=a[i-lb:i]; right=a[i+1:i+rb+1]
    return (not len(left) or np.all(c>=left)) and (not len(right) or np.all(c>=right)) and (not len(left) or np.any(c>left))

def _pl(a,i,lb,rb):
    if i-lb<0 or i+rb>=len(a): return False
    c=a[i]; left=a[i-lb:i]; right=a[i+1:i+rb+1]
    return (not len(left) or np.all(c<=left)) and (not len(right) or np.all(c<=right)) and (not len(left) or np.any(c<left))

def _line_clear(values,current_i,old_i,current_value,old_value,mode):
    n=current_i-old_i
    if n<=1:return True
    diff=(current_value-old_value)/n; line=current_value-diff
    for k in range(1,n):
        v=values[current_i-k]
        if mode=="above" and v>line:return False
        if mode=="below" and v<line:return False
        line-=diff
    return True

def _ind_clear(arr,current_i,old_i,mode):
    cur,old=arr[current_i],arr[old_i]
    if np.isnan(cur) or np.isnan(old):return False
    n=current_i-old_i
    if n<=1:return True
    diff=(cur-old)/n; line=cur-diff
    for k in range(1,n):
        v=arr[current_i-k]
        if np.isnan(v):return False
        if mode=="above" and v>line:return False
        if mode=="below" and v<line:return False
        line-=diff
    return True

def build_label_events(df,lb=5,rb=5,showlimit=1,check_cut_through=True):
    if len(df)<100:return []
    im=indicator_matrix(df); names=list(im.columns); inds={n:im[n].to_numpy(float) for n in names}
    high,low,close=df.mid_h.to_numpy(float),df.mid_l.to_numpy(float),df.mid_c.to_numpy(float); times=df.index
    last_ph=last_pl=None; events=[]
    for i in range(max(lb,rb),len(df)):
        center=i-rb
        if center>=lb and _ph(high,center,lb,rb):last_ph=center
        if center>=lb and _pl(low,center,lb,rb):last_pl=center
        if last_ph is not None and i-lb>=0 and high[i]>=np.max(high[i-lb:i+1]) and high[i]>high[last_ph]:
            if _line_clear(close,i,last_ph,high[i],high[last_ph],"above"):
                count=0
                for n in names:
                    a=inds[n]
                    if np.isnan(a[last_ph]) or np.isnan(a[i]):continue
                    if a[last_ph]>a[i] and (not check_cut_through or _ind_clear(a,i,last_ph,"above")):count+=1
                if count>=showlimit:events.append(LabelEvent(i,times[i],"short",int(count)))
        if last_pl is not None and i-lb>=0 and low[i]<=np.min(low[i-lb:i+1]) and low[i]<low[last_pl]:
            if _line_clear(close,i,last_pl,low[i],low[last_pl],"below"):
                count=0
                for n in names:
                    a=inds[n]
                    if np.isnan(a[last_pl]) or np.isnan(a[i]):continue
                    if a[last_pl]<a[i] and (not check_cut_through or _ind_clear(a,i,last_pl,"below")):count+=1
                if count>=showlimit:events.append(LabelEvent(i,times[i],"long",int(count)))
    dirs={}
    for e in events:dirs.setdefault(e.bar_index,set()).add(e.direction)
    for e in events:
        if e.bar_index+2<len(df) and e.direction not in dirs.get(e.bar_index+1,set()):e.a_entry_index=e.bar_index+2
        ok=_ph(high,e.bar_index,lb,rb) if e.direction=="short" else _pl(low,e.bar_index,lb,rb)
        if ok and e.bar_index+rb+1<len(df):e.b_entry_index=e.bar_index+rb+1
    return events
