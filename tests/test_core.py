import pandas as pd
import numpy as np
from app.dxy import synthetic_dxy, DXY_COMPONENTS
from app.indicators import indicator_matrix


def test_dxy_formula_direction():
    idx=pd.date_range("2026-01-01",periods=3,freq="1min",tz="UTC")
    base={"EUR_USD":1.10,"USD_JPY":150,"GBP_USD":1.27,"USD_CAD":1.35,"USD_SEK":10.5,"USD_CHF":0.90}
    frames={k:pd.DataFrame({"mid_c":[v,v,v]},index=idx) for k,v in base.items()}
    d=synthetic_dxy(frames)
    assert len(d)==3 and d.close.notna().all()


def test_price_only_indicators_when_volume_missing():
    idx=pd.date_range("2026-01-01",periods=120,freq="1min",tz="UTC")
    p=np.linspace(1.1,1.11,len(idx))+np.sin(np.arange(len(idx))/6)*0.0003
    df=pd.DataFrame({"mid_o":p,"mid_h":p+0.0002,"mid_l":p-0.0002,"mid_c":p+0.00005,"volume":np.nan},index=idx)
    m=indicator_matrix(df)
    assert "RSI" in m.columns
    assert "OBV" not in m.columns
    assert set(m.columns)=={"RSI","MACD","MACD_HIST","MOMENTUM","CCI","STOCH","DIOSC"}
