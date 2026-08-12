from __future__ import annotations
import pandas as pd
import numpy as np

DXY_COMPONENTS = {
    "EUR_USD": -0.576,
    "USD_JPY": 0.136,
    "GBP_USD": -0.119,
    "USD_CAD": 0.091,
    "USD_SEK": 0.042,
    "USD_CHF": 0.036,
}
DXY_CONSTANT = 50.14348112

def synthetic_dxy(component_frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """DXY synthétique ICE calculé sur les clôtures M1 synchronisées Twelve Data."""
    closes=[]
    for symbol in DXY_COMPONENTS:
        closes.append(component_frames[symbol]["mid_c"].rename(symbol))
    x=pd.concat(closes,axis=1,join="inner").dropna()
    value=pd.Series(DXY_CONSTANT,index=x.index,dtype=float)
    for symbol,exp in DXY_COMPONENTS.items():value=value*np.power(x[symbol],exp)
    out=pd.DataFrame(index=value.index);out["close"]=value;out["open"]=value.shift(1).fillna(value)
    out["high"]=out[["open","close"]].max(axis=1);out["low"]=out[["open","close"]].min(axis=1)
    return out
