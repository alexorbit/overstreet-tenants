"""Dashboard DB connection helper."""
from overstreet.infra import get_imoveis_db, get_memory_db


def get_db():
    """Retorna conexao SQLite de imoveis (read-write)."""
    return get_imoveis_db()


def get_mem():
    """Retorna conexao SQLite de memory."""
    return get_memory_db()


def init_dashboard_tables(db):
    """Cria tabelas auxiliares do dashboard se nao existirem (settings, etc)."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    db.commit()


# Settings helpers
def get_setting(db, key: str, default: str = "") -> str:
    try:
        row = db.execute(
            "SELECT value FROM app_settings WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else default
    except Exception:
        return default


def set_setting(db, key: str, value: str):
    db.execute(
        "INSERT INTO app_settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    db.commit()
