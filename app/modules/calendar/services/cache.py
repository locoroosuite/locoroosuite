from app.config import DATA_DIR

CACHE_DIR = DATA_DIR / "caches"
CACHE_DIR.mkdir(exist_ok=True)


def get_cache_path(account):
    filename = f"customer_{account.customer_id}_account_{account.id}_calendar.db"
    return str(CACHE_DIR / filename)
