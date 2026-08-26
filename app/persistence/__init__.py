"""Database management"""
from .db_manager import DatabaseManager
def get_database():
    from app.config import get_config
    cfg = get_config()
    return DatabaseManager(cfg.database_path)
