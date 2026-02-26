from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse, PlainTextResponse
from pydantic import BaseModel
from datetime import datetime, timedelta
import io
import json
import os

app = FastAPI(title="YouTube Automation - Stock Data API")

TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "")


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

        # 전종목 OHLCV (pykrx 1.2+: market 파라미터 제거)
        vol_df = None
        try:
            vol_df = krx.get_market_ohlcv(recent_date)
        except Exception:
            pass

        # 주요 대형주 (스토리 분석에 필수)
        major_kr_stocks = {}
        KR_MAJOR_TICKERS = {
            "005930": "삼성전자", "000660": "SK하이닉스",
            "373220": "LG에너지솔루션", "005380": "현대차",
            "035420": "NAVER", "035720": "카카오",
            "006400": "삼성SDI", "207940": "삼성바이오로직스",
            "068270": "셀트리온", "005490": "POSCO홀딩스",
        }
        if vol_df is not None and not vol_df.empty:
            for ticker_code, name in KR_MAJOR_TICKERS.items():
                try:
                    if ticker_code in vol_df.index:
                        row = vol_df.loc[ticker_code]
                        major_kr_stocks[name] = {
                            "ticker": ticker_code,
                            "close": int(row["종가"]),
                            "change_pct": round(float(row["등락률"]), 2),
                            "volume": int(row["거래량"]),
                            "market_cap": int(row["시가총액"]) if "시가총액" in vol_df.columns else None,
                        }
                except Exception:
                    continue

        # 거래대금 상위 10종목
        top_volume = []
        try:
            if vol_df is not None and not vol_df.empty and "거래량" in vol_df.columns:
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
            if vol_df is not None and not vol_df.empty and "등락률" in vol_df.columns:
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
            if vol_df is not None and not vol_df.empty and "등락률" in vol_df.columns:
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

        # 투자자별 순매수 (외국인 라벨 수정)
        investor_data = {}
        try:
            inv = krx.get_market_trading_value_by_investor(recent_date, recent_date, "KOSPI")
            if not inv.empty:
                for label in ["외국인", "기관합계", "개인"]:
                    if label in inv.index and "순매수" in inv.columns:
                        display_name = label.replace("합계", "")
                        investor_data[display_name] = int(inv.loc[label, "순매수"])
        except Exception:
            pass

        return {
            "date": recent_date,
            "kospi": kospi_result,
            "kosdaq": kosdaq_result,
            "major_stocks": major_kr_stocks,
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
# 5-2. Tavily 심층 뉴스 검색
# ============================================================
TAVILY_KEYWORDS = [
    "한국 증시 코스피 오늘",
    "미국 증시 나스닥 S&P500",
    "반도체 AI 엔비디아 SK하이닉스",
    "환율 원달러 금리 연준",
    "삼성전자 테슬라 실적",
]


@app.get("/api/tavily-news")
async def get_tavily_news():
    """Tavily Search API로 심층 뉴스 수집 (본문 요약 포함)"""
    if not TAVILY_API_KEY:
        return {"error": "TAVILY_API_KEY not set", "results": []}

    try:
        from tavily import TavilyClient

        client = TavilyClient(api_key=TAVILY_API_KEY)
        all_results = []
        seen_urls = set()

        for keyword in TAVILY_KEYWORDS:
            try:
                response = client.search(
                    query=keyword,
                    search_depth="basic",
                    topic="news",
                    days=1,
                    max_results=5,
                    include_answer=False,
                )
                for r in response.get("results", []):
                    if r["url"] not in seen_urls:
                        seen_urls.add(r["url"])
                        all_results.append({
                            "keyword": keyword,
                            "title": r.get("title", ""),
                            "content": r.get("content", ""),
                            "url": r.get("url", ""),
                        })
            except Exception:
                continue

        return {
            "keywords_used": TAVILY_KEYWORDS,
            "total_results": len(all_results),
            "results": all_results,
        }
    except Exception as e:
        return {"error": str(e), "results": []}


# ============================================================
# 5-3. Seeking Alpha 데이터 (RapidAPI)
# ============================================================
SA_SYMBOLS = ["NVDA", "AAPL", "MSFT", "TSLA", "GOOGL", "AMZN", "META", "AMD", "AVGO"]

RAPIDAPI_HEADERS = {
    "x-rapidapi-host": "seeking-alpha.p.rapidapi.com",
}


def _sa_get(endpoint: str, params: dict = None) -> dict | None:
    """Seeking Alpha API 호출 헬퍼"""
    if not RAPIDAPI_KEY:
        return None
    import requests
    headers = {**RAPIDAPI_HEADERS, "x-rapidapi-key": RAPIDAPI_KEY}
    try:
        resp = requests.get(
            f"https://seeking-alpha.p.rapidapi.com{endpoint}",
            headers=headers,
            params=params or {},
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


@app.get("/api/seeking-alpha")
async def get_seeking_alpha_data():
    """Seeking Alpha: 애널리스트 레이팅 + 실적 캘린더 + 인기 분석"""
    if not RAPIDAPI_KEY:
        return {"error": "RAPIDAPI_KEY not set", "ratings": [], "trending": []}

    # 1) 주요 종목 애널리스트 레이팅
    ratings = []
    for symbol in SA_SYMBOLS:
        data = _sa_get("/symbols/get-ratings", {"symbol": symbol})
        if data and "data" in data:
            try:
                attrs = data["data"][0]["attributes"] if isinstance(data["data"], list) else data["data"].get("attributes", {})
                ratings.append({
                    "symbol": symbol,
                    "analysts_rating": attrs.get("sellSideRating", ""),
                    "quant_rating": attrs.get("quantRating", ""),
                    "authors_rating": attrs.get("authorsRatingPro", attrs.get("authorsRating", "")),
                })
            except (KeyError, IndexError):
                continue

    # 2) 트렌딩 분석 기사
    trending = []
    data = _sa_get("/analysis/v2/list", {"category": "latest", "size": 10})
    if data and "data" in data:
        for article in data["data"][:10]:
            try:
                attrs = article.get("attributes", {})
                trending.append({
                    "title": attrs.get("title", ""),
                    "summary": (attrs.get("summary", "") or attrs.get("teaser", ""))[:200],
                    "publish_on": attrs.get("publishOn", ""),
                })
            except (KeyError, IndexError):
                continue

    return {
        "ratings": ratings,
        "trending": trending,
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
        tavily = await get_tavily_news()
        sa = await get_seeking_alpha_data()

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

        if kr.get("major_stocks"):
            lines.append("### 한국 주요 대형주")
            for name, data in kr["major_stocks"].items():
                arrow = "▲" if data["change_pct"] > 0 else "▼" if data["change_pct"] < 0 else "─"
                lines.append(f"- {name}({data['ticker']}): {data['close']:,}원 ({arrow}{abs(data['change_pct'])}%)")
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

        # ── 뉴스 헤드라인 (Google News RSS) ──
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

        # ── Tavily 심층 뉴스 ──
        if tavily.get("results"):
            lines.append("## 5. 심층 뉴스 분석 (Tavily)")
            current_keyword = ""
            for item in tavily["results"]:
                if item["keyword"] != current_keyword:
                    current_keyword = item["keyword"]
                    lines.append(f"\n### [{current_keyword}]")
                lines.append(f"- **{item['title']}**")
                if item.get("content"):
                    lines.append(f"  > {item['content'][:300]}")
            lines.append("")

        # ── Seeking Alpha 애널리스트 데이터 ──
        if sa.get("ratings"):
            lines.append("## 6. 애널리스트 레이팅 (Seeking Alpha)")
            for r in sa["ratings"]:
                parts = [f"**{r['symbol']}**"]
                if r.get("analysts_rating"):
                    parts.append(f"월가: {r['analysts_rating']:.2f}" if isinstance(r["analysts_rating"], (int, float)) else f"월가: {r['analysts_rating']}")
                if r.get("quant_rating"):
                    parts.append(f"퀀트: {r['quant_rating']:.2f}" if isinstance(r["quant_rating"], (int, float)) else f"퀀트: {r['quant_rating']}")
                lines.append(f"- {' | '.join(parts)}")
            lines.append("- (1=Strong Sell, 3=Hold, 5=Strong Buy)")
            lines.append("")

        if sa.get("trending"):
            lines.append("## 7. 트렌딩 분석 (Seeking Alpha)")
            for article in sa["trending"]:
                lines.append(f"- **{article['title']}**")
                if article.get("summary"):
                    lines.append(f"  > {article['summary']}")
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
