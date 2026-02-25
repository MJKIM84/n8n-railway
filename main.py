from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from datetime import datetime, timedelta
import io
import json

app = FastAPI(title="YouTube Automation - Stock Data API")


# ============================================================
# 1. 한국 증시 데이터 수집 (pykrx)
# ============================================================
@app.get("/api/kr-market")
async def get_kr_market_data(days: int = 5):
    """한국 증시 데이터 (KOSPI/KOSDAQ 지수 + 주요 종목)"""
    try:
        from pykrx import stock as krx

        today = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days + 10)).strftime("%Y%m%d")

        # KOSPI / KOSDAQ 지수
        kospi = krx.get_index_ohlcv(start, today, "1001")  # KOSPI
        kosdaq = krx.get_index_ohlcv(start, today, "2001")  # KOSDAQ

        # 주요 종목 시세 (삼성전자, SK하이닉스, 현대차, NAVER, 카카오)
        major_tickers = {
            "005930": "삼성전자",
            "000660": "SK하이닉스",
            "005380": "현대차",
            "035420": "NAVER",
            "035720": "카카오",
        }

        stocks = {}
        for ticker, name in major_tickers.items():
            df = krx.get_market_ohlcv(start, today, ticker)
            if not df.empty:
                latest = df.iloc[-1]
                stocks[name] = {
                    "ticker": ticker,
                    "close": int(latest["종가"]),
                    "change": int(latest["종가"] - df.iloc[-2]["종가"]) if len(df) > 1 else 0,
                    "change_pct": round(((latest["종가"] / df.iloc[-2]["종가"]) - 1) * 100, 2) if len(df) > 1 else 0,
                    "volume": int(latest["거래량"]),
                }

        # 투자자별 순매수 (최근 거래일)
        investor_data = {}
        try:
            inv = krx.get_market_trading_value_by_investor(today, today, "KOSPI")
            if not inv.empty:
                investor_data = {
                    "외국인": int(inv.loc["외국인합계", "순매수"] if "외국인합계" in inv.index else 0),
                    "기관": int(inv.loc["기관합계", "순매수"] if "기관합계" in inv.index else 0),
                    "개인": int(inv.loc["개인", "순매수"] if "개인" in inv.index else 0),
                }
        except Exception:
            pass

        kospi_latest = kospi.iloc[-1] if not kospi.empty else None
        kosdaq_latest = kosdaq.iloc[-1] if not kosdaq.empty else None

        return {
            "date": today,
            "kospi": {
                "close": float(kospi_latest["종가"]) if kospi_latest is not None else None,
                "change_pct": round(((kospi_latest["종가"] / kospi.iloc[-2]["종가"]) - 1) * 100, 2) if kospi_latest is not None and len(kospi) > 1 else None,
            },
            "kosdaq": {
                "close": float(kosdaq_latest["종가"]) if kosdaq_latest is not None else None,
                "change_pct": round(((kosdaq_latest["종가"] / kosdaq.iloc[-2]["종가"]) - 1) * 100, 2) if kosdaq_latest is not None and len(kosdaq) > 1 else None,
            },
            "major_stocks": stocks,
            "investor_flow": investor_data,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 2. 미국 증시 데이터 수집 (yfinance)
# ============================================================
@app.get("/api/us-market")
async def get_us_market_data(days: int = 5):
    """미국 증시 데이터 (S&P500, NASDAQ + 주요 빅테크)"""
    try:
        import yfinance as yf

        # 주요 지수
        indices = {"^GSPC": "S&P500", "^IXIC": "NASDAQ", "^DJI": "DOW"}
        index_data = {}
        for symbol, name in indices.items():
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period=f"{days}d")
            if not hist.empty:
                latest = hist.iloc[-1]
                prev = hist.iloc[-2] if len(hist) > 1 else latest
                index_data[name] = {
                    "close": round(float(latest["Close"]), 2),
                    "change_pct": round(((latest["Close"] / prev["Close"]) - 1) * 100, 2),
                }

        # 빅테크 종목
        tech_symbols = {
            "AAPL": "Apple",
            "MSFT": "Microsoft",
            "NVDA": "NVIDIA",
            "TSLA": "Tesla",
            "GOOGL": "Google",
            "AMZN": "Amazon",
            "META": "Meta",
        }

        stocks = {}
        for symbol, name in tech_symbols.items():
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period=f"{days}d")
            if not hist.empty:
                latest = hist.iloc[-1]
                prev = hist.iloc[-2] if len(hist) > 1 else latest
                stocks[name] = {
                    "symbol": symbol,
                    "close": round(float(latest["Close"]), 2),
                    "change_pct": round(((latest["Close"] / prev["Close"]) - 1) * 100, 2),
                    "volume": int(latest["Volume"]),
                }

        return {
            "indices": index_data,
            "major_stocks": stocks,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 3. 캔들스틱 차트 생성 (mplfinance)
# ============================================================
class ChartRequest(BaseModel):
    symbol: str  # 종목코드 (한국: "005930", 미국: "AAPL")
    market: str = "kr"  # "kr" or "us"
    days: int = 30
    ma: list[int] = [5, 20, 60]


@app.post("/api/chart")
async def generate_chart(req: ChartRequest):
    """캔들스틱 차트 이미지 생성 (PNG)"""
    try:
        import pandas as pd
        import mplfinance as mpf
        import matplotlib
        matplotlib.use("Agg")

        if req.market == "kr":
            from pykrx import stock as krx
            today = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=req.days + 15)).strftime("%Y%m%d")
            df = krx.get_market_ohlcv(start, today, req.symbol)
            df.index.name = "Date"
            df = df.rename(columns={
                "시가": "Open", "고가": "High", "저가": "Low",
                "종가": "Close", "거래량": "Volume"
            })
        else:
            import yfinance as yf
            ticker = yf.Ticker(req.symbol)
            df = ticker.history(period=f"{req.days}d")

        df = df.tail(req.days)

        if df.empty:
            raise HTTPException(status_code=404, detail="데이터 없음")

        # 차트 스타일 설정
        mc = mpf.make_marketcolors(
            up="red", down="blue", edge="inherit",
            wick="inherit", volume="in",
        )
        style = mpf.make_mpf_style(marketcolors=mc, gridstyle="-", gridcolor="#e0e0e0")

        buf = io.BytesIO()
        mpf.plot(
            df, type="candle", style=style,
            volume=True, mav=tuple(req.ma),
            title=f"{req.symbol} ({req.market.upper()})",
            savefig=dict(fname=buf, dpi=150, bbox_inches="tight"),
        )
        buf.seek(0)

        return StreamingResponse(buf, media_type="image/png")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 4. 환율 및 원자재 데이터
# ============================================================
@app.get("/api/forex")
async def get_forex_data():
    """원/달러 환율 및 주요 원자재 가격"""
    try:
        import yfinance as yf

        symbols = {
            "KRW=X": "USD/KRW",
            "GC=F": "Gold",
            "CL=F": "WTI Oil",
            "BTC-USD": "Bitcoin",
        }

        result = {}
        for symbol, name in symbols.items():
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="5d")
            if not hist.empty:
                latest = hist.iloc[-1]
                prev = hist.iloc[-2] if len(hist) > 1 else latest
                result[name] = {
                    "price": round(float(latest["Close"]), 2),
                    "change_pct": round(((latest["Close"] / prev["Close"]) - 1) * 100, 2),
                }

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 5. 통합 데이터 (n8n에서 한번에 호출)
# ============================================================
@app.get("/api/daily-briefing")
async def get_daily_briefing():
    """한국+미국 증시 + 환율 통합 데이터 (n8n 워크플로우 메인 엔드포인트)"""
    try:
        kr = await get_kr_market_data()
        us = await get_us_market_data()
        forex = await get_forex_data()

        return {
            "timestamp": datetime.now().isoformat(),
            "kr_market": kr,
            "us_market": us,
            "forex": forex,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# Health Check
# ============================================================
@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}
