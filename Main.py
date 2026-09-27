from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
import sqlite3
import hashlib
import time
import os

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_PATH = "pastes.db"
BASE62_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pastes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            slug        TEXT    UNIQUE NOT NULL,
            title       TEXT    NOT NULL,
            content     TEXT    NOT NULL,
            language    TEXT    NOT NULL,
            visibility  TEXT    NOT NULL,
            password    TEXT,
            burn_after  INTEGER NOT NULL,
            views       INTEGER DEFAULT 0,
            created_at  INTEGER NOT NULL,
            expires_at  INTEGER
        )
    """)
    conn.commit()
    conn.close()

init_db()

def encode_base62(num: int) -> str:
    if num == 0: return BASE62_ALPHABET[0]
    arr = []
    while num:
        num, rem = divmod(num, 62)
        arr.append(BASE62_ALPHABET[rem])
    arr.reverse()
    return ''.join(arr)

def get_expiry_time(expiry_str: str) -> Optional[int]:
    now = int(time.time())
    mapping = {"10m": 600, "1h": 3600, "1d": 86400, "never": None}
    seconds = mapping.get(expiry_str)
    return now + seconds if seconds else None

class PasteCreate(BaseModel):
    title: str
    content: str
    language: str
    visibility: str
    expiry: str
    password: Optional[str] = None
    burn_after_read: bool

@app.get("/")
def serve_frontend():
    return FileResponse("index.html")

@app.post("/api/pastes")
def create_paste(paste: PasteCreate, request: Request):
    if not paste.content.strip():
        raise HTTPException(status_code=400, detail="Paste content is empty")
    slug = encode_base62(int(time.time() * 1000000))
    exp = get_expiry_time(paste.expiry)
    pw = hashlib.sha256(paste.password.encode()).hexdigest() if paste.password else None
    conn = get_db()
    conn.execute(
        """INSERT INTO pastes (slug, title, content, language, visibility, password, burn_after, created_at, expires_at) 
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (slug, paste.title, paste.content, paste.language, paste.visibility, pw, (1 if paste.burn_after_read else 0), int(time.time()), exp)
    )
    conn.commit()
    conn.close()
    base_url = str(request.base_url).rstrip('/')
    return {"slug": slug, "link": f"{base_url}/#paste/{slug}"}

@app.get("/api/pastes/{slug}")
def get_paste(slug: str, password: Optional[str] = None):
    conn = get_db()
    row = conn.execute("SELECT * FROM pastes WHERE slug=?", (slug,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, detail="Paste not found")
    p = dict(row)
    now = int(time.time())
    if p["expires_at"] and now > p["expires_at"]:
        conn.execute("DELETE FROM pastes WHERE slug=?", (slug,))
        conn.commit(); conn.close()
        raise HTTPException(410, detail="This paste has expired")
    if p["password"]:
        if not password or hashlib.sha256(password.encode()).hexdigest() != p["password"]:
            conn.close()
            return {"protected": True, "slug": slug}
    res = {k: v for k, v in p.items() if k != "password"}
    if p["burn_after"]:
        conn.execute("DELETE FROM pastes WHERE slug=?", (slug,))
    else:
        conn.execute("UPDATE pastes SET views = views + 1 WHERE slug=?", (slug,))
    conn.commit(); conn.close()
    return res

@app.get("/api/pastes")
def list_pastes():
    conn = get_db()
    now = int(time.time())
    rows = conn.execute(
        """SELECT slug, title, language, views FROM pastes 
           WHERE visibility='public' AND (expires_at IS NULL OR expires_at > ?) 
           ORDER BY created_at DESC""", (now,)
    ).fetchall()
    conn.close()
    return {"pastes": [dict(r) for r in rows]}
