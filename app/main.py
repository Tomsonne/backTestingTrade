from __future__ import annotations
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI,HTTPException
from fastapi.responses import FileResponse
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from .config import Settings
from .jobs import execute
from .storage import load_result

settings=Settings();settings.ensure_dirs();app=FastAPI(title="RiseUp Backtester",version="0.2.0")
executor=ThreadPoolExecutor(max_workers=1);scheduler=None

@app.on_event("startup")
def startup():
    global scheduler
    if settings.enable_scheduler:
        scheduler=BackgroundScheduler(timezone=settings.timezone)
        scheduler.add_job(lambda:execute(settings),CronTrigger(day_of_week="mon-fri",hour="*",minute=settings.schedule_minute,timezone=settings.timezone),id="weekday_backtest",replace_existing=True,max_instances=1,coalesce=True)
        scheduler.start()
    if settings.auto_run_on_startup and settings.twelve_data_api_key:executor.submit(execute,settings)

@app.on_event("shutdown")
def shutdown():
    if scheduler:scheduler.shutdown(wait=False)

@app.get("/")
def index():return FileResponse(Path(__file__).parent/"static"/"index.html")

@app.get("/api/result")
def result():
    r=load_result(settings.db_path)
    return r or {"status":"empty","message":"Aucun backtest enregistré. Ajoute TWELVE_DATA_API_KEY au .env puis lance /api/run."}

@app.post("/api/run")
def run():
    if not settings.twelve_data_api_key:raise HTTPException(400,"TWELVE_DATA_API_KEY absente. Ajoute la clé côté serveur dans .env; ne la mets jamais dans le navigateur.")
    executor.submit(execute,settings);return {"status":"started"}

@app.get("/api/config")
def config():
    return {"provider":"Twelve Data","timezone":settings.timezone,"backtest_start":settings.backtest_start,"sessions":[{"name":s.name,"start":s.start.strftime("%H:%M"),"end":s.end.strftime("%H:%M")} for s in settings.sessions],"variants":{"A.0":"A + H2","A.1":"A + H4","B.0":"B + H2","B.1":"B + H4"},"scheduler":{"enabled":settings.enable_scheduler,"weekdays":"Mon-Fri","minute_each_hour":settings.schedule_minute}}
