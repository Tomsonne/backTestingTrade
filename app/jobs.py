from threading import Lock
from .backtest import run_backtest
from .storage import save_result
_LOCK=Lock()
def execute(settings):
    if not _LOCK.acquire(blocking=False):return {"status":"already_running"}
    try:
        r=run_backtest(settings);save_result(settings.db_path,r);return {"status":"ok","generated_at":r["generated_at"]}
    finally:_LOCK.release()
