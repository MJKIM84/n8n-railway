from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse, PlainTextResponse
from pydantic import BaseModel
from datetime import datetime, timedelta
import io
import json
import os

app = FastAPI(title="YouTube Automation - Stock Data API")


# ============================================================
# 1. 한국 증시 데이터 수집 (pykrx)
# ============================================================
@app.get("/api/kr-market")
async def get_kr_market_data(days: int = 5):
    """한국 증시 데이터 (KOSPI/KOSDAQ 지수 + 주요 종목 + 거래대금/등락률 상위)"""
    try:
        from pykrx import stock as krx

        today = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days + 10)).strftime("%Y%m%d")

        # KOSPI / KOSDAQ 지수
        kospi = krx.get_index_ohlcv(start, today, "1001")
        kosdaq = krx.get_index_ohlcv(start, today, "2001")

        # 지수 데이터
        kospi_result = None
        if not kospi.empty and len(kospi) > 1:
            kospi_result = {
                "close": round(float(kospi.iloc[-1]["종가"]), 2),
                "prev_close": round(float(kospi.iloc[-2]["종가"]), 2),
                "change_pct": round(((kospi.iloc[-1]["종가"] / kospi.iloc[-2]["종가"]) - 1) * 100, 2),
                "volume": int(kospi.iloc[-1]["거래량"]),
            }

        kosdaq_result = None
        if not kosdaq.empty and len(kosdaq) > 1:
            kosdaq_result = {
                "close": round(float(kosdaq.iloc[-1]["종가"]), 2),
                "prev_close": round(float(kosdaq.iloc[-2]["종가"]), 2),
                "change_pct": round(((kosdaq.iloc[-1]["종가"] / kosdaq.iloc[-2]["종가"]) - 1) * 100, 2),
                "volume": int(kosdaq.iloc[-1]["거래량"]),
            }

        # 최근 거래일
        recent_date = kospi.index[-1].strftime("%Y%m%d") if not kospi.empty else today

        # 거래대금 상위 10종목
        top_volume = []
        try:
            vol_df = krx.get_market_ohlcv(recent_date, recent_date, market="KOSPI")
            if not vol_df.empty and "거래량" in vol_df.columns:
                vol_sorted = vol_df.nlargest(10, "거래량")
                for ticker_code in vol_sorted.index:
                    name = krx.get_market_ticker_name(ticker_code)
                    row = vol_sorted.loc[ticker_code]
                    top_volume.append({
                        "ticker": ticker_code,
                        "name": name,
                        "close": int(row["종가"]),
                        "change_pct": round(float(row["등락률"]), 2),
                        "volume": int(row["거래량"]),
                    })
        except Exception:
            pass

        # 등락률 상위 10종목 (상승)
        top_gainers = []
        try:
            if not vol_df.empty and "등락률" in vol_df.columns:
                gain_sorted = vol_df.nlargest(10, "등락률")
                for ticker_code in gain_sorted.index:
                    name = krx.get_market_ticker_name(ticker_code)
                    row = gain_sorted.loc[ticker_code]
                    top_gainers.append({
                        "ticker": ticker_code,
                        "name": name,
                        "close": int(row["종가"]),
                        "change_pct": round(float(row["등락률"]), 2),
                        "volume": int(row["거래량"]),
                    })
        except Exception:
            pass

        # 등락률 하위 10종목 (하락)
        top_losers = []
        try:
            if not vol_df.empty and "등락률" in vol_df.columns:
                loss_sorted = vol_df.nsmallest(10, "등락률")
                for ticker_code in loss_sorted.index:
                    name = krx.get_market_ticker_name(ticker_code)
                    row = loss_sorted.loc[ticker_code]
                    top_losers.append({
                        "ticker": ticker_code,
                        "name": name,
                        "close": int(row["종가"]),
                        "change_pct": round(float(row["등락률"]), 2),
                        "volume": int(row["거래량"]),
                    })
        except Exception:
            pass

        # 투자자별 순매수
        investor_data = {}
        try:
            inv = krx.get_market_trading_value_by_investor(recent_date, recent_date, "KOSPI")
            if not inv.empty:
                for label in ["외국인합계", "기관합계", "개인"]:
                    if label in inv.index and "순매수" in inv.columns:
                        investor_data[label.replace("합계", "")] = int(inv.loc[label, "순매수"])
        except Exception:
            pass

        return {
            "date": recent_date,
            "kospi": kospi_result,
            "kosdaq": kosdaq_result,
            "top_volume": top_volume,
            "top_gainers": top_gainers,
            "top_losers": top_losers,
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

        period = f"{max(days, 5)}d"

        indices = {"^GSPC": "S&P500", "^IXIC": "NASDAQ", "^DJI": "DOW"}
        index_data = {}
        for symbol, name in indices.items():
            try:
                ticker = yf.Ticker(symbol)
                hist = ticker.history(period=period)
                if not hist.empty and len(hist) > 1:
                    latest = hist.iloc[-1]
                    prev = hist.iloc[-2]
                    index_data[name] = {
                        "close": round(float(latest["Close"]), 2),
                        "prev_close": round(float(prev["Close"]), 2),
                        "change_pct": round(((float(latest["Close"]) / float(prev["Close"])) - 1) * 100, 2),
                        "volume": int(latest["Volume"]),
                    }
            except Exception:
                continue

        tech_symbols = {
            "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "NVIDIA",
            "TSLA": "Tesla", "GOOGL": "Google", "AMZN": "Amazon",
            "META": "Meta", "AMD": "AMD", "AVGO": "Broadcom",
        }

        stocks = {}
        for symbol, name in tech_symbols.items():
            try:
                ticker = yf.Ticker(symbol)
                hist = ticker.history(period=period)
                if not hist.empty and len(hist) > 1:
                    latest = hist.iloc[-1]
                    prev = hist.iloc[-2]
                    stocks[name] = {
                        "symbol": symbol,
                        "close": round(float(latest["Close"]), 2),
                        "prev_close": round(float(prev["Close"]), 2),
                        "change_pct": round(((float(latest["Close"]) / float(prev["Close"])) - 1) * 100, 2),
                        "volume": int(latest["Volume"]),
                    }
            except Exception:
                continue

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
    symbol: str
    market: str = "kr"
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

        df = df[["Open", "High", "Low", "Close", "Volume"]]
        df = df.tail(req.days)

        if df.empty:
            raise HTTPException(status_code=404, detail="데이터 없음")

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
            "CL=F": "WTI_Oil",
            "BTC-USD": "Bitcoin",
            "^VIX": "VIX_공포지수",
            "^TNX": "미국10년국채금리",
        }

        result = {}
        for symbol, name in symbols.items():
            try:
                ticker = yf.Ticker(symbol)
                hist = ticker.history(period="5d")
                if not hist.empty and len(hist) > 1:
                    latest = hist.iloc[-1]
                    prev = hist.iloc[-2]
                    result[name] = {
                        "price": round(float(latest["Close"]), 2),
                        "change_pct": round(((float(latest["Close"]) / float(prev["Close"])) - 1) * 100, 2),
                    }
            except Exception:
                continue

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 5. 뉴스 헤드라인 수집 (고정 키워드 기반)
# ============================================================
NEWS_KEYWORDS = [
    "금리", "인플레이션", "반도체", "실적발표", "외국인 매수",
    "AI 인공지능", "환율", "유가", "연준 Fed", "코스피",
    "나스닥", "삼성전자", "SK하이닉스", "테슬라", "엔비디아",
]


@app.get("/api/news")
async def get_news_headlines():
    """고정 키워드 기반 뉴스 헤드라인 수집 (RSS/웹 크롤링)"""
    import urllib.request
    import xml.etree.ElementTree as ET
    from urllib.parse import quote

    all_news = []

    for keyword in NEWS_KEYWORDS:
        try:
            encoded = quote(keyword)
            url = f"https://news.google.com/rss/search?q={encoded}+when:1d&hl=ko&gl=KR&ceid=KR:ko"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                xml_data = resp.read()

            root = ET.fromstring(xml_data)
            items = root.findall(".//item")

            for item in items[:3]:  # 키워드당 최대 3개
                title = item.find("title")
                pub_date = item.find("pubDate")
                source = item.find("source")

                if title is not None and title.text:
                    all_news.append({
                        "keyword": keyword,
                        "headline": title.text.strip(),
                        "source": source.text.strip() if source is not None and source.text else "",
                        "date": pub_date.text.strip() if pub_date is not None and pub_date.text else "",
                    })
        except Exception:
            continue

    # 중복 헤드라인 제거
    seen = set()
    unique_news = []
    for item in all_news:
        if item["headline"] not in seen:
            seen.add(item["headline"])
            unique_news.append(item)

    return {
        "keywords_used": NEWS_KEYWORDS,
        "total_headlines": len(unique_news),
        "headlines": unique_news,
    }


# ============================================================
# 6. 일일 피드 생성 (Markdown 텍스트 - 복사해서 LLM에 붙여넣기용)
# ============================================================
@app.get("/api/daily-feed", response_class=PlainTextResponse)
async def get_daily_feed():
    """모든 데이터를 수집하여 LLM 입력용 Markdown 텍스트로 병합"""
    try:
        kr = await get_kr_market_data()
        us = await get_us_market_data()
        forex = await get_forex_data()
        news = await get_news_headlines()

        today_str = datetime.now().strftime("%Y년 %m월 %d일 %H:%M")
        lines = []

        lines.append(f"# 일일 경제 브리핑 데이터 ({today_str} 기준)")
        lines.append("")

        # ── 미국 증시 ──
        lines.append("## 1. 미국 증시 (간밤 마감)")
        if us.get("indices"):
            for name, data in us["indices"].items():
                arrow = "▲" if data["change_pct"] > 0 else "▼" if data["change_pct"] < 0 else "─"
                lines.append(f"- {name}: {data['close']:,.2f} ({arrow}{abs(data['change_pct'])}%)")
        lines.append("")

        if us.get("major_stocks"):
            lines.append("### 미국 주요 종목")
            for name, data in us["major_stocks"].items():
                arrow = "▲" if data["change_pct"] > 0 else "▼" if data["change_pct"] < 0 else "─"
                lines.append(f"- {name}({data['symbol']}): ${data['close']:,.2f} ({arrow}{abs(data['change_pct'])}%)")
        lines.append("")

        # ── 한국 증시 ──
        lines.append("## 2. 한국 증시 (전일 마감)")
        if kr.get("kospi"):
            k = kr["kospi"]
            arrow = "▲" if k["change_pct"] > 0 else "▼" if k["change_pct"] < 0 else "─"
            lines.append(f"- KOSPI: {k['close']:,.2f} ({arrow}{abs(k['change_pct'])}%)")
        if kr.get("kosdaq"):
            k = kr["kosdaq"]
            arrow = "▲" if k["change_pct"] > 0 else "▼" if k["change_pct"] < 0 else "─"
            lines.append(f"- KOSDAQ: {k['close']:,.2f} ({arrow}{abs(k['change_pct'])}%)")
        lines.append("")

        if kr.get("investor_flow"):
            lines.append("### 투자자별 순매수 (KOSPI)")
            for inv, val in kr["investor_flow"].items():
                arrow = "순매수" if val > 0 else "순매도"
                lines.append(f"- {inv}: {abs(val):,}원 ({arrow})")
            lines.append("")

        if kr.get("top_gainers"):
            lines.append("### 등락률 상위 (급등 종목)")
            for s in kr["top_gainers"][:7]:
                lines.append(f"- {s['name']}({s['ticker']}): {s['close']:,}원 (▲{abs(s['change_pct'])}%)")
            lines.append("")

        if kr.get("top_losers"):
            lines.append("### 등락률 하위 (급락 종목)")
            for s in kr["top_losers"][:7]:
                lines.append(f"- {s['name']}({s['ticker']}): {s['close']:,}원 (▼{abs(s['change_pct'])}%)")
            lines.append("")

        if kr.get("top_volume"):
            lines.append("### 거래대금 상위 (주목 종목)")
            for s in kr["top_volume"][:7]:
                arrow = "▲" if s["change_pct"] > 0 else "▼" if s["change_pct"] < 0 else "─"
                lines.append(f"- {s['name']}({s['ticker']}): {s['close']:,}원 ({arrow}{abs(s['change_pct'])}%) 거래량:{s['volume']:,}")
            lines.append("")

        # ── 환율/원자재 ──
        lines.append("## 3. 환율 및 주요 지표")
        if forex:
            for name, data in forex.items():
                arrow = "▲" if data["change_pct"] > 0 else "▼" if data["change_pct"] < 0 else "─"
                lines.append(f"- {name}: {data['price']:,.2f} ({arrow}{abs(data['change_pct'])}%)")
        lines.append("")

        # ── 뉴스 헤드라인 ──
        lines.append("## 4. 핵심 뉴스 헤드라인 (최근 24시간)")
        if news.get("headlines"):
            current_keyword = ""
            for item in news["headlines"]:
                if item["keyword"] != current_keyword:
                    current_keyword = item["keyword"]
                    lines.append(f"\n### [{current_keyword}]")
                source_str = f" ({item['source']})" if item["source"] else ""
                lines.append(f"- {item['headline']}{source_str}")
        lines.append("")

        return "\n".join(lines)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 7. 통합 JSON 데이터 (기존 호환)
# ============================================================
@app.get("/api/daily-briefing")
async def get_daily_briefing():
    """한국+미국 증시 + 환율 통합 JSON 데이터"""
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
