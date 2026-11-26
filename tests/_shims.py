"""Simulations minimales de sqlalchemy et yt_dlp pour exécuter server.py sans réseau.

Installées SEULEMENT si les vrais modules sont absents (donc inutiles sur ta machine ou sur Render).
sqlalchemy est simulé avec sqlite3 : le SQL du serveur est exécuté pour de vrai par SQLite.
"""
import re
import sqlite3
import sys
import threading
import types


class _BP:
    def __init__(self, key, expanding=False):
        self.key, self.expanding = key, expanding


class _Text:
    def __init__(self, sql):
        self.sql, self.expanding = sql, set()

    def bindparams(self, *bps):
        for b in bps:
            if b.expanding:
                self.expanding.add(b.key)
        return self


class _Row(tuple):
    def __new__(cls, cols, vals):
        r = super().__new__(cls, vals)
        r._cols = list(cols)
        return r

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self[self._cols.index(name)]
        except ValueError:
            raise AttributeError(name)


class _Result:
    def __init__(self, cur):
        self.rowcount = cur.rowcount
        cols = [d[0] for d in cur.description] if cur.description else []
        self._rows = [_Row(cols, r) for r in cur.fetchall()] if cur.description else []

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class _Conn:
    def __init__(self, engine, tx):
        self.e, self.tx = engine, tx

    def __enter__(self):
        self.e.lock.acquire()
        if self.tx:
            self.e.db.execute("BEGIN")
        return self

    def __exit__(self, et, ev, tb):
        try:
            if self.tx:
                self.e.db.execute("ROLLBACK" if et else "COMMIT")
        finally:
            self.e.lock.release()
        return False

    def execute(self, t, params=None):
        sql, params = t.sql, dict(params or {})
        for key in t.expanding:
            vals = list(params.pop(key))
            names = []
            for i, v in enumerate(vals):
                params[f"{key}_{i}"] = v
                names.append(f":{key}_{i}")
            sql = re.sub(rf":{key}\b", "(" + (",".join(names) or "NULL") + ")", sql)
        return _Result(self.e.db.execute(sql, params))


class _Engine:
    def __init__(self, *a, **k):
        self.db = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
        self.lock = threading.RLock()

    def connect(self):
        return _Conn(self, False)

    def begin(self):
        return _Conn(self, True)


class _Inspector:
    def __init__(self, e):
        self.e = e

    def get_columns(self, table):
        with self.e.lock:
            return [{"name": r[1]} for r in self.e.db.execute(f"PRAGMA table_info({table})")]


class YoutubeDL:
    """Remplacée par chaque test qui en a besoin."""
    def __init__(self, opts=None):
        self.opts = opts or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=True):
        raise RuntimeError("yt_dlp simulé : aucun téléchargement possible dans les tests")


def install():
    try:
        import sqlalchemy  # noqa: F401
    except ImportError:
        sa = types.ModuleType("sqlalchemy")
        sa.create_engine = lambda *a, **k: _Engine()
        sa.text = _Text
        sa.inspect = _Inspector
        sa.bindparam = lambda key, value=None, expanding=False: _BP(key, expanding)
        exc = types.ModuleType("sqlalchemy.exc")
        exc.IntegrityError = sqlite3.IntegrityError
        sa.exc = exc
        sys.modules["sqlalchemy"], sys.modules["sqlalchemy.exc"] = sa, exc
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        m = types.ModuleType("yt_dlp")
        m.YoutubeDL = YoutubeDL
        sys.modules["yt_dlp"] = m
