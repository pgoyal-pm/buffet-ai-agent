#!/usr/bin/env python3
"""Stock Tracker API - Persistent Watchlist + Live Prices"""
import os, json, sqlite3, urllib.request
from datetime import datetime
from typing import Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

DB = "/data/watchlist.db"

@asynccontextmanager
async def lifespan(app):
    _init_db()
    yield

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class TickerItem(BaseModel):
    ticker: str
    quantity: Optional[int] = None
    buy_price: Optional[float] = None

def _db():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; return c

def _init_db():
    c = _db(); cur = c.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS watchlist(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT UNIQUE NOT NULL,
        quantity REAL DEFAULT 0,
        buy_price REAL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS price_cache(
        ticker TEXT PRIMARY KEY,
        price REAL NOT NULL,
        change_percent REAL DEFAULT 0,
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    c.commit(); c.close()

@app.get("/api/watchlist")
def get_watchlist():
    try:
        c = _db()
        items = [dict(r) for r in c.execute("SELECT * FROM watchlist ORDER BY updated_at DESC").fetchall()]
        c.close()
        for i in items:
            p = _db().execute("SELECT * FROM price_cache WHERE ticker=?",(i["ticker"],)).fetchone()
            if p: i["price"]=dict(p)["price"]; i["change_percent"]=dict(p)["change_percent"]
            else: i["price"]=None; i["change_percent"]=None
        return {"watchlist":items}
    except Exception as e: raise HTTPException(500,str(e))

@app.post("/api/watchlist")
def add_ticker(item: TickerItem):
    try:
        c = _db()
        ex = c.execute("SELECT id FROM watchlist WHERE ticker=?", (item.ticker.upper(),)).fetchone()
        if ex:
            c.execute("UPDATE watchlist SET quantity=?,buy_price=?,updated_at=CURRENT_TIMESTAMP WHERE ticker=?",
                (item.quantity or 0, item.buy_price or 0, item.ticker.upper()))
        else:
            c.execute("INSERT INTO watchlist(ticker,quantity,buy_price) VALUES (?,?,?)",
                (item.ticker.upper(), item.quantity or 0, item.buy_price or 0))
        c.commit(); c.close()
        return {"success":True,"message":f"{item.ticker.upper()} added"}
    except Exception as e: raise HTTPException(500,str(e))

@app.delete("/api/watchlist/{ticker}")
def remove_ticker(ticker:str):
    try:
        c = _db(); cur = c.execute("DELETE FROM watchlist WHERE ticker=?", (ticker.upper(),)); d=cur.rowcount
        c.commit(); c.close()
        if not d: raise HTTPException(404,"Ticker not found")
        return {"success":True,"message":f"{ticker.upper()} removed"}
    except HTTPException: raise
    except Exception as e: raise HTTPException(500,str(e))

@app.get("/api/refresh")
def refresh_prices():
    try:
        c = _db()
        tickers = [r["ticker"] for r in c.execute("SELECT DISTINCT ticker FROM watchlist").fetchall()]
        c.close()
        if not tickers: return {"prices":{}}
        symbols = ",".join(tickers)
        req = urllib.request.Request(
            f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={symbols}",
            headers={"User-Agent":"Mozilla/5.0 (compatible; StockTracker/1.0)"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp: data=json.loads(resp.read())
        results={}
        for q in data.get("quoteResponse",{}).get("result",[]):
            sym=q.get("symbol","").upper()
            pd=q.get("regularMarketPrice",{})
            pv=pd.get("fmt") if isinstance(pd,dict) else pd
            cp=pd.get("percentChange",0) if isinstance(pd,dict) else 0
            if pv and sym in tickers:
                if isinstance(pv,str): pv=pv.replace("$","").replace(",","")
                results[sym]={"price":float(pv),"change_percent":round(cp*100,2)}
                dc=_db(); dc.execute(
                    "INSERT OR REPLACE INTO price_cache(ticker,price,change_percent,last_updated) VALUES (?,?,?,CURRENT_TIMESTAMP)",
                    (sym,float(pv),round(cp*100,2))); dc.commit(); dc.close()
        return {"prices":results}
    except Exception as e: raise HTTPException(500,f"Failed to fetch prices: {str(e)}")

@app.get("/api/prices")
def get_all_prices():
    try:
        c = _db()
        prices = {r["ticker"]:dict(r) for r in c.execute("SELECT * FROM price_cache").fetchall()}
        c.close()
        return {"prices":prices}
    except Exception as e: raise HTTPException(500,str(e))

if __name__=="__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
else:
    os.makedirs("/data", exist_ok=True)
