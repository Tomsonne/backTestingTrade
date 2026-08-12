from __future__ import annotations
import numpy as np
import pandas as pd

PRICE_INDICATORS = ("RSI", "MACD", "MACD_HIST", "MOMENTUM", "CCI", "STOCH", "DIOSC")
VOLUME_INDICATORS = ("OBV", "VWMACD", "CMF", "MFI")

def pine_ema(s: pd.Series, n: int) -> pd.Series:
    arr=s.astype(float).to_numpy(); out=np.full(len(arr),np.nan)
    valid=np.where(~np.isnan(arr))[0]
    if len(valid)<n:return pd.Series(out,index=s.index)
    first=int(valid[0]); seed_end=first+n
    if seed_end>len(arr) or np.isnan(arr[first:seed_end]).any():return pd.Series(out,index=s.index)
    out[seed_end-1]=float(np.mean(arr[first:seed_end])); a=2/(n+1)
    for i in range(seed_end,len(arr)):
        out[i]=out[i-1] if np.isnan(arr[i]) else a*arr[i]+(1-a)*out[i-1]
    return pd.Series(out,index=s.index)

def rma(s: pd.Series,n:int)->pd.Series:
    arr=s.astype(float).to_numpy(); out=np.full(len(arr),np.nan)
    if len(arr)<n:return pd.Series(out,index=s.index)
    out[n-1]=np.nanmean(arr[:n]); a=1/n
    for i in range(n,len(arr)):
        out[i]=out[i-1] if np.isnan(arr[i]) else a*arr[i]+(1-a)*out[i-1]
    return pd.Series(out,index=s.index)

def rsi(c,n=14):
    d=c.diff(); g=d.clip(lower=0); l=(-d).clip(lower=0); ag=rma(g.fillna(0),n); al=rma(l.fillna(0),n)
    rs=ag/al.replace(0,np.nan); out=100-100/(1+rs); return out.where(al!=0,100.0)

def true_range(df):
    prev=df.mid_c.shift(1)
    return pd.concat([df.mid_h-df.mid_l,(df.mid_h-prev).abs(),(df.mid_l-prev).abs()],axis=1).max(axis=1)

def vwma(c,v,n):
    return (c*v).rolling(n,min_periods=n).sum()/v.rolling(n,min_periods=n).sum().replace(0,np.nan)

def mfi(source,volume,n=14):
    money=source*volume; d=source.diff(); pos=money.where(d>0,0.0); neg=money.where(d<0,0.0)
    ps=pos.rolling(n,min_periods=n).sum(); ns=neg.rolling(n,min_periods=n).sum(); ratio=ps/ns.replace(0,np.nan)
    out=100-100/(1+ratio); return out.where(ns!=0,100.0)

def _has_real_volume(df: pd.DataFrame) -> bool:
    if "volume" not in df.columns:
        return False
    v = pd.to_numeric(df["volume"], errors="coerce")
    return v.notna().any() and (v.fillna(0) != 0).any()

def indicator_matrix(df: pd.DataFrame) -> pd.DataFrame:
    c,h,l=df.mid_c,df.mid_h,df.mid_l; out=pd.DataFrame(index=df.index)
    out["RSI"]=rsi(c,14)
    e12,e26=pine_ema(c,12),pine_ema(c,26); macd=e12-e26; sig=pine_ema(macd,9)
    out["MACD"]=macd; out["MACD_HIST"]=macd-sig; out["MOMENTUM"]=c-c.shift(10)
    def cci_w(x):
        m=x.mean(); md=np.mean(np.abs(x-m)); return 0.0 if md==0 else (x[-1]-m)/(0.015*md)
    out["CCI"]=c.rolling(10,min_periods=10).apply(cci_w,raw=True)
    lo=l.rolling(14,min_periods=14).min(); hi=h.rolling(14,min_periods=14).max(); st=100*(c-lo)/(hi-lo).replace(0,np.nan)
    out["STOCH"]=st.rolling(3,min_periods=3).mean()
    di=h.diff()+l.diff(); tr=true_range(df); out["DIOSC"]=100*rma(di.fillna(0),14)/rma(tr.fillna(0),14).replace(0,np.nan)

    # L'API Twelve Data Forex /time_series n'inclut pas le volume. On n'invente
    # donc pas de tick-volume : ces quatre indicateurs sont seulement ajoutés
    # lorsqu'un fournisseur de données fournit réellement une colonne volume.
    if _has_real_volume(df):
        v=pd.to_numeric(df.volume,errors="coerce").fillna(0)
        out["OBV"]=(np.sign(c.diff()).fillna(0)*v).cumsum()
        out["VWMACD"]=vwma(c,v,12)-vwma(c,v,26)
        denom=(h-l).replace(0,np.nan); cmfm=((c-l)-(h-c))/denom; cmfv=cmfm*v
        out["CMF"]=cmfv.rolling(21,min_periods=21).mean()/v.rolling(21,min_periods=21).mean().replace(0,np.nan)
        out["MFI"]=mfi(c,v,14)
    return out
