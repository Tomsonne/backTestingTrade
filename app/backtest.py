from __future__ import annotations
from dataclasses import asdict,dataclass
from datetime import datetime,timedelta,timezone
import pandas as pd
from .config import Settings
from .data_twelvedata import TwelveDataClient,CandleCache
from .dxy import DXY_COMPONENTS,synthetic_dxy
from .m1_divergence import build_label_events
from .sessions import build_session_instances,session_slice
from .zones import build_zones,find_active_zone
from .indicators import indicator_matrix

PAIRS=("EUR_USD","GBP_USD")

@dataclass
class Candidate:
    variant:str; pair:str; direction:str; trade_date:str; session:str; previous_session:str; break_time:str; label_time:str; label_count:int; entry_time:str; entry_price:float; zone_tf:str; zone_bottom:float; zone_top:float; zone_formed_at:str; dxy_prev_high:float; dxy_prev_low:float; pair_prev_high:float; pair_prev_low:float

def _mid(raw):return raw[["mid_o","mid_h","mid_l","mid_c","volume"]].copy()
def _slice(df,s):return session_slice(df,s)
def _ext(df,s,dxy=False):
    x=_slice(df,s)
    if x.empty:return None
    return (float(x.high.max()),float(x.low.min())) if dxy else (float(x.mid_h.max()),float(x.mid_l.min()))

def _dxy_nonconfirm(dxy,cur,t,direction,prev_high,prev_low):
    x=dxy[(dxy.index>=cur.start)&(dxy.index<t)]
    if x.empty:return True
    return float(x.low.min())>=prev_low if direction=="short" else float(x.high.max())<=prev_high

def _breaks(pair_df,cur,prev_high,prev_low):
    x=_slice(pair_df,cur); out={}; hb=x[x.mid_h>prev_high]; lb=x[x.mid_l<prev_low]
    if not hb.empty:out["short"]=hb.index[0]
    if not lb.empty:out["long"]=lb.index[0]
    return out

def _entry_price(raw,t,direction,spread_pips=0.0):
    # Twelve Data fournit OHLC agrégé sans historique bid/ask. On applique un
    # spread optionnel symétrique, désactivé par défaut plutôt que d'en inventer un.
    mid=float(raw.loc[t].mid_o); half=(spread_pips*0.0001)/2
    return mid+half if direction=="long" else mid-half

def _spread(settings,pair):
    return settings.eurusd_spread_pips if pair=="EUR_USD" else settings.gbpusd_spread_pips

def generate_candidates(settings,pair,raw,dxy):
    mid=_mid(raw)
    if mid.empty:return []
    zones={tf:build_zones(mid,tf,settings.timezone,settings.imbalance_min_adr_pct,settings.adr_length) for tf in ("H2","H4")}
    labels=build_label_events(mid,settings.lb,settings.rb,settings.showlimit,settings.check_cut_through)
    bydir={d:[e for e in labels if e.direction==d] for d in ("short","long")}
    instances=build_session_instances(mid.index.min().to_pydatetime(),mid.index.max().to_pydatetime(),settings.sessions,settings.timezone)
    nonempty=[s for s in instances if not _slice(mid,s).empty and not _slice(dxy,s).empty]
    idx=mid.index; out=[]
    for k in range(1,len(nonempty)):
        cur,prev=nonempty[k],nonempty[k-1]
        if cur.trade_date.weekday()>=5:continue
        pe,de=_ext(mid,prev),_ext(dxy,prev,True)
        if pe is None or de is None:continue
        ph,pl=pe; dh,dl=de
        for direction,bt in _breaks(mid,cur,ph,pl).items():
            if not _dxy_nonconfirm(dxy,cur,bt+pd.Timedelta(minutes=1),direction,dh,dl):continue
            for letter in ("A","B"):
                for code,tf in (("0","H2"),("1","H4")):
                    variant=f"{letter}.{code}"
                    for e in bydir[direction]:
                        if e.time<bt:continue
                        ei=e.a_entry_index if letter=="A" else e.b_entry_index
                        if ei is None or ei>=len(mid):continue
                        et=idx[ei]
                        if et>=cur.end:break
                        if settings.invalidate_if_dxy_confirms_before_entry and not _dxy_nonconfirm(dxy,cur,et,direction,dh,dl):continue
                        mo=float(mid.iloc[ei].mid_o); z=find_active_zone(zones[tf],et,mo,direction,settings.zone_direction_match)
                        if z is None:continue
                        out.append(Candidate(variant,pair,direction,cur.trade_date.isoformat(),cur.name,prev.name,bt.isoformat(),e.time.isoformat(),e.count,et.isoformat(),_entry_price(raw,et,direction,_spread(settings,pair)),tf,z.bottom,z.top,z.formed_at.isoformat(),dh,dl,ph,pl))
                        break
    return out

def _sl_tp(settings,pair):
    return (settings.eurusd_sl_pips,settings.eurusd_tp_pips) if pair=="EUR_USD" else (settings.gbpusd_sl_pips,settings.gbpusd_tp_pips)

def simulate_trade(settings,c,raw):
    et=pd.Timestamp(c.entry_time); entry=c.entry_price; slp,tpp=_sl_tp(settings,c.pair); pip=.0001; spread=_spread(settings,c.pair); half=spread*pip/2
    if c.direction=="long":sl,tp=entry-slp*pip,entry+tpp*pip
    else:sl,tp=entry+slp*pip,entry-tpp*pip
    x=raw[(raw.index>=et)&(raw.index<=et+pd.Timedelta(minutes=settings.max_hold_minutes))]
    outcome="TIMEOUT"; xt=et; xp=entry
    for t,row in x.iterrows():
        # reconstruit un bid/ask approximatif seulement si l'utilisateur fournit un spread
        if c.direction=="long":
            lo=float(row.mid_l)-half; hi=float(row.mid_h)-half; hit_sl=lo<=sl; hit_tp=hi>=tp
        else:
            lo=float(row.mid_l)+half; hi=float(row.mid_h)+half; hit_sl=hi>=sl; hit_tp=lo<=tp
        if hit_sl and hit_tp:
            if settings.same_bar_policy=="tp_first":hit_sl=False
            else:hit_tp=False
        if hit_sl:outcome="LOSS";xt=t;xp=sl;break
        if hit_tp:outcome="WIN";xt=t;xp=tp;break
    if outcome=="TIMEOUT" and not x.empty:
        xt=x.index[-1]; row=x.iloc[-1]
        if c.direction=="long":xp=float(row.mid_c)-half; pnl=(xp-entry)/pip
        else:xp=float(row.mid_c)+half; pnl=(entry-xp)/pip
        r=pnl/slp
    elif outcome=="WIN":r=tpp/slp
    else:r=-1.0
    return {**asdict(c),"sl_price":sl,"tp_price":tp,"exit_time":xt.isoformat(),"exit_price":float(xp),"outcome":outcome,"r_multiple":float(r),"sl_pips":float(slp),"tp_pips":float(tpp),"spread_pips":float(spread)}

def apply_money(settings,candidates,raw_by_pair):
    rank={p:i for i,p in enumerate(settings.pair_priority)}; trades=[]
    for variant in ("A.0","A.1","B.0","B.1"):
        sub=sorted([c for c in candidates if c.variant==variant],key=lambda c:(c.trade_date,pd.Timestamp(c.entry_time),rank.get(c.pair,99)))
        equity=settings.starting_equity
        for day in sorted(set(c.trade_date for c in sub)):
            ds=[c for c in sub if c.trade_date==day]
            if not ds:continue
            t1=simulate_trade(settings,ds[0],raw_by_pair[ds[0].pair]); risk=settings.first_trade_risk_pct
            t1.update(trade_number_day=1,risk_pct=risk,equity_before=equity,return_pct=t1["r_multiple"]*risk); equity*=1+t1["return_pct"]/100;t1["equity_after"]=equity;trades.append(t1)
            if t1["outcome"]!="LOSS":continue
            ex=pd.Timestamp(t1["exit_time"]); rem=[c for c in ds[1:] if pd.Timestamp(c.entry_time)>ex]
            if not rem:continue
            t2=simulate_trade(settings,rem[0],raw_by_pair[rem[0].pair]); risk=settings.second_trade_risk_pct
            t2.update(trade_number_day=2,risk_pct=risk,equity_before=equity,return_pct=t2["r_multiple"]*risk); equity*=1+t2["return_pct"]/100;t2["equity_after"]=equity;trades.append(t2)
    return trades

def summarize(trades,start_eq):
    out=[]
    for v in ("A.0","A.1","B.0","B.1"):
        x=[t for t in trades if t["variant"]==v]
        if not x:
            out.append({"variant":v,"trades":0,"wins":0,"losses":0,"win_rate":None,"total_r":0.0,"profit_factor":None,"net_return_pct":0.0,"max_drawdown_pct":0.0,"eurusd_trades":0,"gbpusd_trades":0});continue
        wins=sum(t["outcome"]=="WIN" for t in x); losses=sum(t["outcome"]=="LOSS" for t in x); decided=wins+losses; pos=sum(max(0,t["r_multiple"]) for t in x); neg=abs(sum(min(0,t["r_multiple"]) for t in x))
        final=x[-1]["equity_after"]; peak=start_eq; mdd=0
        for t in x:
            eq=t["equity_after"];peak=max(peak,eq);mdd=min(mdd,(eq/peak-1)*100)
        out.append({"variant":v,"trades":len(x),"wins":wins,"losses":losses,"win_rate":100*wins/decided if decided else None,"total_r":sum(t["r_multiple"] for t in x),"profit_factor":pos/neg if neg else None,"net_return_pct":100*(final/start_eq-1),"max_drawdown_pct":abs(mdd),"eurusd_trades":sum(t["pair"]=="EUR_USD" for t in x),"gbpusd_trades":sum(t["pair"]=="GBP_USD" for t in x)})
    return out

def run_backtest(settings:Settings):
    settings.ensure_dirs(); client=TwelveDataClient(settings); cache=CandleCache(settings,client)
    start=datetime.fromisoformat(settings.backtest_start).replace(tzinfo=settings.tz).astimezone(timezone.utc); end=datetime.now(timezone.utc)-timedelta(minutes=2)
    raw={p:cache.get(p,start,end,"1min") for p in PAIRS}; comps={}
    for s in DXY_COMPONENTS:
        comps[s]=raw[s].copy() if s in raw else cache.get(s,start,end,"1min")
    dxy=synthetic_dxy(comps); candidates=[]
    for p in PAIRS:candidates.extend(generate_candidates(settings,p,raw[p],dxy))
    trades=apply_money(settings,candidates,raw); summary=summarize(trades,settings.starting_equity)

    active_indicators=list(indicator_matrix(_mid(raw["EUR_USD"])).columns) if not raw["EUR_USD"].empty else []
    return {
        "generated_at":datetime.now(timezone.utc).isoformat(),"start":start.isoformat(),"end":end.isoformat(),"timezone":settings.timezone,
        "sessions":[{"name":s.name,"start":s.start.strftime("%H:%M"),"end":s.end.strftime("%H:%M")} for s in settings.sessions],
        "summary":summary,"trades":trades,"candidate_count":len(candidates),"active_m1_indicators":active_indicators,
        "assumptions":[
            "Source de prix: Twelve Data Forex /time_series en 1 minute.",
            "ASIA 23:00-06:00 reste provisoire et configurable; BLUE 07:00-11:00 et RED 12:00-16:00 sont conservées.",
            "DXY synthétique ICE calculé à partir de EUR/USD, USD/JPY, GBP/USD, USD/CAD, USD/SEK et USD/CHF Twelve Data.",
            "La divergence est invalidée si DXY casse finalement le niveau opposé avant l'entrée.",
            "A = label M1 fixé après 1 bougie; B = pivot M1 confirmé rb=5; .0 = H2; .1 = H4.",
            "Twelve Data Forex n'inclut pas de volume dans /time_series: OBV, VW-MACD, CMF et MFI sont donc exclus plutôt que simulés.",
            "Le label M1 Twelve Data est ainsi basé sur RSI, MACD, MACD Hist, Momentum, CCI, Stoch et DIosc.",
            "Entrée à l'ouverture M1 suivant la confirmation, sans look-ahead.",
            "EURUSD 15/30 pips; GBPUSD 20/40 pips.",
            "Spread historique non fourni par /time_series: 0 pip par défaut, réglable dans .env; un spread nul rend le résultat plus optimiste.",
            "Risque 2% au 1er trade; win = fin de journée; loss => 2e trade possible à 1%.",
        ]
    }
