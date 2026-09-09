from __future__ import annotations
from dataclasses import asdict,dataclass
from datetime import datetime,timezone
import logging
import numpy as np
import pandas as pd
from .config import Settings
from .data.base import DataCoverageError,MarketDataProvider,utc_timestamp
from .data.factory import create_provider
from .dxy import DXY_COMPONENTS,direct_dxy,synthetic_dxy
from .m1_divergence import build_label_events
from .sessions import build_session_instances,session_slice
from .zones import build_zones,find_active_zone
from .indicators import indicator_matrix

PAIRS=("EUR_USD","GBP_USD")
LOGGER=logging.getLogger(__name__)

@dataclass
class Candidate:
    variant:str; pair:str; direction:str; trade_date:str; session:str; previous_session:str; break_time:str; label_time:str; label_count:int; entry_time:str; entry_price:float; zone_tf:str; zone_bottom:float; zone_top:float; zone_formed_at:str; dxy_prev_high:float; dxy_prev_low:float; pair_prev_high:float; pair_prev_low:float; execution_price_mode:str="mid"; entry_spread_pips:float=0.0
    break_level:float|None=None; break_price:float|None=None; dxy_at_break:float|None=None
    divergence_confirmed:bool=True; trigger_timeframe:str="M1"; indicators_diverged:tuple[str,...]=()
    active_zones:tuple[dict,...]=(); trace:tuple[dict,...]=(); entry_bid:float|None=None; entry_ask:float|None=None
    additional_spread_pips:float=0.0; slippage_pips:float=0.0
    label_available_time:str|None=None
<<<<<<< HEAD
    data_quality_status:str|None=None
    missing_data_events:tuple[dict,...]=()
    setup_id:str|None=None
=======
>>>>>>> 853804b008cb85b1a2c913966f2c28e9a257535a

def _mid(raw,volume_mode="legacy_no_volume"):
    out=raw[["mid_o","mid_h","mid_l","mid_c"]].copy()
    if volume_mode=="dukascopy_volume":
        source="bid_volume" if "bid_volume" in raw.columns else "volume"
        out["volume"]=pd.to_numeric(raw[source],errors="coerce") if source in raw.columns else np.nan
    else:out["volume"]=np.nan
    return out
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

def _entry_price(raw,t,direction,mode="mid",spread_pips=0.0):
    row=raw.loc[t]
    if mode=="bid_ask" and all(column in raw.columns for column in ("bid_o","ask_o")):
        bid,ask=float(row.bid_o),float(row.ask_o)
        if not np.isnan(bid) and not np.isnan(ask):
            return (ask if direction=="long" else bid),(ask-bid)/.0001,"bid_ask"
    mid=float(row.mid_o);half=(spread_pips*.0001)/2
    return (mid+half if direction=="long" else mid-half),spread_pips,"mid"

def _spread(settings,pair):
    return settings.eurusd_spread_pips if pair=="EUR_USD" else settings.gbpusd_spread_pips

def generate_candidates(settings,pair,raw,dxy):
    mid=_mid(raw,settings.volume_mode)
    if mid.empty:return []
    zones={tf:build_zones(mid,tf,settings.htf_anchor_timezone,settings.imbalance_min_adr_pct,settings.adr_length) for tf in ("H2","H4")}
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
                        entry,observed_spread,actual_mode=_entry_price(raw,et,direction,settings.execution_price_mode,_spread(settings,pair))
                        out.append(Candidate(variant,pair,direction,cur.trade_date.isoformat(),cur.name,prev.name,bt.isoformat(),e.time.isoformat(),e.count,et.isoformat(),entry,tf,z.bottom,z.top,z.formed_at.isoformat(),dh,dl,ph,pl,actual_mode,observed_spread))
                        break
    return out

def _sl_tp(settings,pair):
    return (settings.eurusd_sl_pips,settings.eurusd_tp_pips) if pair=="EUR_USD" else (settings.gbpusd_sl_pips,settings.gbpusd_tp_pips)

def simulate_trade(settings,c,raw):
<<<<<<< HEAD
    if settings.data_validation_mode == "trace":
        from .research.gap_execution import simulate_trace_trade
        return simulate_trace_trade(settings,c,raw,_simulate_observed_trade,getattr(settings,"gap_catalog",None))
    return _simulate_observed_trade(settings,c,raw)

def _simulate_observed_trade(settings,c,raw):
=======
>>>>>>> 853804b008cb85b1a2c913966f2c28e9a257535a
    et=pd.Timestamp(c.entry_time); entry=c.entry_price; slp,tpp=_sl_tp(settings,c.pair); pip=.0001; spread=c.entry_spread_pips; half=spread*pip/2
    extra_half=c.additional_spread_pips*pip/2; slippage=c.slippage_pips*pip
    if c.direction=="long":sl,tp=entry-slp*pip,entry+tpp*pip
    else:sl,tp=entry+slp*pip,entry-tpp*pip
    x=raw[(raw.index>=et)&(raw.index<=et+pd.Timedelta(minutes=settings.max_hold_minutes))]
    outcome="TIMEOUT"; xt=et; xp=entry
    for t,row in x.iterrows():
        use_bid_ask=c.execution_price_mode=="bid_ask" and all(name in raw.columns for name in ("bid_l","bid_h","bid_c","ask_l","ask_h","ask_c"))
        if c.direction=="long" and use_bid_ask:
            lo=float(row.bid_l)-extra_half-slippage;hi=float(row.bid_h)-extra_half-slippage;hit_sl=lo<=sl;hit_tp=hi>=tp
        elif c.direction=="long":
            lo=float(row.mid_l)-half;hi=float(row.mid_h)-half;hit_sl=lo<=sl;hit_tp=hi>=tp
        elif use_bid_ask:
            lo=float(row.ask_l)+extra_half+slippage;hi=float(row.ask_h)+extra_half+slippage;hit_sl=hi>=sl;hit_tp=lo<=tp
        else:
            lo=float(row.mid_l)+half; hi=float(row.mid_h)+half; hit_sl=hi>=sl; hit_tp=lo<=tp
        if hit_sl and hit_tp:
            if settings.same_bar_policy=="tp_first":hit_sl=False
            else:hit_tp=False
        if hit_sl:outcome="LOSS";xt=t;xp=sl;break
        if hit_tp:outcome="WIN";xt=t;xp=tp;break
    if outcome=="TIMEOUT" and not x.empty:
        xt=x.index[-1]; row=x.iloc[-1]
        use_bid_ask=c.execution_price_mode=="bid_ask" and "bid_c" in raw.columns and "ask_c" in raw.columns
        if c.direction=="long":xp=(float(row.bid_c)-extra_half-slippage) if use_bid_ask else float(row.mid_c)-half;pnl=(xp-entry)/pip
        else:xp=(float(row.ask_c)+extra_half+slippage) if use_bid_ask else float(row.mid_c)+half;pnl=(entry-xp)/pip
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
            t1.update(trade_number_day=1,risk_pct=risk,equity_before=equity,return_pct=t1["r_multiple"]*risk if t1["r_multiple"] is not None else None); equity*=1+(t1["return_pct"] or 0)/100;t1["equity_after"]=equity;trades.append(t1)
            if t1["outcome"]=="INDETERMINATE":break
            if t1["outcome"]!="LOSS":continue
            ex=pd.Timestamp(t1["exit_time"]); rem=[c for c in ds[1:] if pd.Timestamp(c.entry_time)>ex]
            if not rem:continue
            t2=simulate_trade(settings,rem[0],raw_by_pair[rem[0].pair]); risk=settings.second_trade_risk_pct
            t2.update(trade_number_day=2,risk_pct=risk,equity_before=equity,return_pct=t2["r_multiple"]*risk if t2["r_multiple"] is not None else None); equity*=1+(t2["return_pct"] or 0)/100;t2["equity_after"]=equity;trades.append(t2)
            if t2["outcome"]=="INDETERMINATE":break
    return trades

def summarize(trades,start_eq):
    out=[]
    for v in ("A.0","A.1","B.0","B.1"):
        x=[t for t in trades if t["variant"]==v and t["outcome"]!="INDETERMINATE"]
        if not x:
            out.append({"variant":v,"trades":0,"wins":0,"losses":0,"win_rate":None,"total_r":0.0,"profit_factor":None,"net_return_pct":0.0,"max_drawdown_pct":0.0,"eurusd_trades":0,"gbpusd_trades":0});continue
        wins=sum(t["outcome"]=="WIN" for t in x); losses=sum(t["outcome"]=="LOSS" for t in x); decided=wins+losses; pos=sum(max(0,t["r_multiple"]) for t in x); neg=abs(sum(min(0,t["r_multiple"]) for t in x))
        final=x[-1]["equity_after"]; peak=start_eq; mdd=0
        for t in x:
            eq=t["equity_after"];peak=max(peak,eq);mdd=min(mdd,(eq/peak-1)*100)
        out.append({"variant":v,"trades":len(x),"wins":wins,"losses":losses,"win_rate":100*wins/decided if decided else None,"total_r":sum(t["r_multiple"] for t in x),"profit_factor":pos/neg if neg else None,"net_return_pct":100*(final/start_eq-1),"max_drawdown_pct":abs(mdd),"eurusd_trades":sum(t["pair"]=="EUR_USD" for t in x),"gbpusd_trades":sum(t["pair"]=="GBP_USD" for t in x)})
    return out

def _strategy_timestamp(value,settings):
    ts=pd.Timestamp(value)
    if ts.tzinfo is None:ts=ts.tz_localize(settings.timezone,ambiguous="raise",nonexistent="raise")
    return ts.tz_convert("UTC")

def _synthetic_dxy_from_provider(provider,raw,start,end):
    comps={}
    for symbol in DXY_COMPONENTS:
        comps[symbol]=raw[symbol].copy() if symbol in raw else provider.get(symbol,start,end,"1min")
    return synthetic_dxy(comps)

def run_backtest(settings:Settings,provider:MarketDataProvider|None=None,now=None):
    settings.ensure_dirs();provider=provider or create_provider(settings)
    current=utc_timestamp(now or datetime.now(timezone.utc))
    start=_strategy_timestamp(settings.backtest_start,settings)
    end=_strategy_timestamp(settings.backtest_end,settings) if settings.backtest_end else current.floor("min")
    if start>=end:raise ValueError("BACKTEST_START must be earlier than BACKTEST_END")
<<<<<<< HEAD
    if hasattr(provider,"validation_mode"):provider.validation_mode=settings.data_validation_mode
    if settings.data_validation_mode=="trace":
        from .data.gaps import GapCatalog,TraceProvider
        from .research.data_quality import annotate_candidate,quality_report
        settings.gap_catalog=GapCatalog(start,end)
        provider=TraceProvider(provider,settings.gap_catalog)
=======
>>>>>>> 853804b008cb85b1a2c913966f2c28e9a257535a
    raw={p:provider.get(p,start,end,"1min") for p in PAIRS}
    dxy_actual_source="synthetic"; revision_symbols=list(PAIRS)
    if settings.dxy_source=="dukascopy_direct" and getattr(provider,"name","")=="dukascopy":
        try:
            dxy=direct_dxy(provider.get("DXY",start,end,"1min"));dxy_actual_source="dukascopy_direct";revision_symbols.append("DXY")
        except DataCoverageError as exc:
            LOGGER.warning("Direct Dukascopy DXY unavailable, trying synthetic fallback: %s",exc)
            dxy=_synthetic_dxy_from_provider(provider,raw,start,end);revision_symbols.extend(DXY_COMPONENTS)
    else:dxy=_synthetic_dxy_from_provider(provider,raw,start,end);revision_symbols.extend(DXY_COMPONENTS)
    candidates=[]
<<<<<<< HEAD
    for p in PAIRS:
        generated=generate_candidates(settings,p,raw[p],dxy)
        if settings.data_validation_mode=="trace":
            settings.gap_catalog.dxy_symbols=["DXY"] if dxy_actual_source=="dukascopy_direct" else list(DXY_COMPONENTS)
            instances=build_session_instances(start.to_pydatetime(),end.to_pydatetime(),settings.sessions,settings.timezone)
            instances=[s for s in instances if not _slice(raw[p],s).empty and not _slice(dxy,s).empty]
            for c in generated:
                k=next(i for i,s in enumerate(instances) if s.name==c.session and s.start<=pd.Timestamp(c.entry_time)<s.end)
                annotate_candidate(c,settings.gap_catalog,instances[k-1],instances[k],trigger=True,zones=True,dxy=True,history_index=raw[p].index)
        candidates.extend(generated)
=======
    for p in PAIRS:candidates.extend(generate_candidates(settings,p,raw[p],dxy))
>>>>>>> 853804b008cb85b1a2c913966f2c28e9a257535a
    trades=apply_money(settings,candidates,raw); summary=summarize(trades,settings.starting_equity)

    active_indicators=list(indicator_matrix(_mid(raw["EUR_USD"],settings.volume_mode)).columns) if not raw["EUR_USD"].empty else []
    revision = provider.data_revision(revision_symbols, start, end) if hasattr(provider, "data_revision") else None
    return {
        "generated_at":current.isoformat(),"start":start.isoformat(),"end":end.isoformat(),"timezone":settings.timezone,
        "data_provider":getattr(provider,"name",settings.data_provider),"dxy_source":dxy_actual_source,
        "execution_price_mode":settings.execution_price_mode,"volume_mode":settings.volume_mode,
        "data_revision":revision,
<<<<<<< HEAD
        **({"data_quality_report":quality_report(settings.gap_catalog.physical_gaps(),[asdict(c) for c in candidates],trades),
             "data_warnings":[{"instrument":symbol,"rows":report["rows"],"unexpected_missing_minutes":report["unexpected_missing_minutes"],"gap_event_count":len(report["unexpected_gaps"])} for symbol,report in settings.gap_catalog.reports.items() if not report["is_valid"]],
             "setup_quality":[asdict(c) for c in candidates]} if settings.data_validation_mode=="trace" else {}),
=======
>>>>>>> 853804b008cb85b1a2c913966f2c28e9a257535a
        "sessions":[{"name":s.name,"start":s.start.strftime("%H:%M"),"end":s.end.strftime("%H:%M")} for s in settings.sessions],
        "summary":summary,"trades":trades,"candidate_count":len(candidates),
        "candidate_counts":{
            variant:sum(candidate.variant==variant for candidate in candidates)
            for variant in ("A.0","A.1","B.0","B.1")
        },
        "candle_count":sum(len(frame) for frame in raw.values())+len(dxy),
        "active_m1_indicators":active_indicators,
        "assumptions":[
            f"Source de prix: {getattr(provider,'name',settings.data_provider)} M1; cache historique séparé du backtest.",
            "ASIA 23:00-06:00 reste provisoire et configurable; BLUE 07:00-11:00 et RED 12:00-16:00 sont conservées.",
            f"DXY utilisé: {dxy_actual_source}; le mode synthétique conserve la formule ICE existante.",
            "La divergence est invalidée si DXY casse finalement le niveau opposé avant l'entrée.",
            "A = label M1 fixé après 1 bougie; B = pivot M1 confirmé rb=5; .0 = H2; .1 = H4.",
            f"Mode volume: {settings.volume_mode}; aucun indicateur volume n'est réactivé silencieusement.",
            "Entrée à l'ouverture M1 suivant la confirmation, sans look-ahead.",
            "EURUSD 15/30 pips; GBPUSD 20/40 pips.",
            f"Exécution: {settings.execution_price_mode}; BUY à l'ASK/sortie au BID et SELL au BID/sortie à l'ASK quand disponibles.",
            "Risque 2% au 1er trade; win = fin de journée; loss => 2e trade possible à 1%.",
        ]
    }
