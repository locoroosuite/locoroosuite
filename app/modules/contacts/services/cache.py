
from app.config import DATA_DIR

CACHE_DIR = DATA_DIR / "caches"
CACHE_DIR.mkdir(exist_ok=True)


def get_cache_path(account):
    if account.cache_db_path:
        return account.cache_db_path
    filename = f"customer_{account.customer_id}_account_{account.id}.db"
    path = str(CACHE_DIR / filename)
    account.cache_db_path = path
    return path
