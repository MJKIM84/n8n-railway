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
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# 기존 고정 종목 목록 (헤드라인 추출 비교용)
US_FIXED_TICKERS = {"AAPL", "MSFT", "NVDA", "TSLA", "GOOGL", "AMZN", "META", "AMD", "AVGO"}
KR_FIXED_NAMES = {
    "삼성전자", "SK하이닉스", "LG에너지솔루션", "현대차", "NAVER",
    "카카오", "삼성SDI", "삼성바이오로직스", "셀트리온", "POSCO홀딩스",
}

# 한국 주요 기업명 → 티커 코드 매핑 (헤드라인 추출 기업 데이터 조회용)
KR_NAME_TO_TICKER = {
    "삼성전자": "005930", "SK하이닉스": "000660", "LG에너지솔루션": "373220",
    "현대차": "005380", "현대자동차": "005380", "NAVER": "035420", "네이버": "035420",
    "카카오": "035720", "삼성SDI": "006400", "삼성바이오로직스": "207940",
    "셀트리온": "068270", "POSCO홀딩스": "005490", "포스코홀딩스": "005490",
    "KB금융": "105560", "신한지주": "055550", "하나금융지주": "086790",
    "우리금융지주": "316140", "기아": "000270", "현대모비스": "012330",
    "LG화학": "051910", "LG전자": "066570", "삼성물산": "028260",
    "두산에너빌리티": "034020", "HD현대중공업": "329180", "한화에어로스페이스": "012450",
    "두산밥캣": "241560", "에코프로": "086520", "에코프로비엠": "247540",
    "포스코퓨처엠": "003670", "HMM": "011200", "대한항공": "003490",
    "SK이노베이션": "096770", "SK텔레콤": "017670", "KT": "030200",
    "LG유플러스": "032640", "현대글로비스": "086280", "삼성SDS": "018260",
    "롯데케미칼": "011170", "한국전력": "015760", "CJ제일제당": "097950",
    "아모레퍼시픽": "090430", "LG생활건강": "051900", "엔씨소프트": "036570",
    "크래프톤": "259960", "넷마블": "251270", "카카오뱅크": "323410",
    "카카오페이": "377300", "HD현대": "267250", "한화오션": "042660",
    "한국항공우주": "047810", "현대건설": "000720", "삼성엔지니어링": "028050",
    "SK바이오팜": "326030", "유한양행": "000100", "셀트리온헬스케어": "091990",
    "GS건설": "006360", "현대제철": "004020", "OCI홀딩스": "456040",
    "한화솔루션": "009830", "롯데에너지머티리얼즈": "020150",
}


# ============================================================
# 1. 한국 증시 데이터 수집 (pykrx)
# ============================================================
def _patch_pykrx_index_name():
    """pykrx가 야간에 KRX 지수명 API 빈 응답을 받아 크래시하는 버그 패치.
    OHLCV 데이터는 정상 수집되므로 지수명 조회 실패만 무시하면 됨."""
    try:
        import pykrx.stock.stock_api as _sa
        if getattr(_sa, "_index_name_patched", False):
            return
        _orig = _sa.get_index_ticker_name
        def _safe_get_index_ticker_name(ticker):
            try:
                return _orig(ticker)
            except Exception:
                return ticker  # 실패 시 티커 코드 자체를 이름으로 사용
        _sa.get_index_ticker_name = _safe_get_index_ticker_name
        _sa._index_name_patched = True
    except Exception:
        pass

_patch_pykrx_index_name()


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
        if data and "data" in data and isinstance(data["data"], list) and len(data["data"]) > 0:
            try:
                r = data["data"][0].get("attributes", {}).get("ratings", {})
                ratings.append({
                    "symbol": symbol,
                    "wall_street": round(r.get("sellSideRating", 0), 2) if r.get("sellSideRating") else "",
                    "quant": round(r.get("quantRating", 0), 2) if r.get("quantRating") else "",
                    "authors": round(r.get("authorsRating", 0), 2) if r.get("authorsRating") else "",
                })
            except (KeyError, IndexError):
                continue

    # 2) 트렌딩 마켓 뉴스
    trending = []
    data = _sa_get("/news/v2/list", {"category": "market-news::all", "size": 10})
    if data and "data" in data:
        for article in data["data"][:10]:
            try:
                attrs = article.get("attributes", {})
                trending.append({
                    "title": attrs.get("title", ""),
                    "publish_on": attrs.get("publishOn", ""),
                })
            except (KeyError, IndexError):
                continue

    return {
        "ratings": ratings,
        "trending": trending,
    }


# ============================================================
# 6-0. 헤드라인 기반 동적 기업 추출 헬퍼 함수들
# ============================================================
async def extract_companies_from_headlines(headlines: list) -> dict:
    """Claude Haiku로 헤드라인에서 고정 목록에 없는 신규 기업 추출"""
    if not ANTHROPIC_API_KEY or not headlines:
        return {"us_tickers": [], "kr_companies": []}

    headline_text = "\n".join([f"- {item['headline']}" for item in headlines[:60]])
    fixed_us = ", ".join(sorted(US_FIXED_TICKERS))
    fixed_kr = ", ".join(sorted(KR_FIXED_NAMES))

    prompt = f"""다음 뉴스 헤드라인에서 언급된 기업들을 추출해줘.

헤드라인:
{headline_text}

아래 JSON 형식으로만 응답해줘 (설명 없이):
{{
  "us_tickers": ["TICKER1", "TICKER2"],
  "kr_companies": ["회사명1", "회사명2"]
}}

규칙:
- 미국 기업은 주식 티커 심볼로 표시 (대문자, 예: PLTR, SMCI, INTC, ARM)
- 한국 기업은 공식 한글 회사명으로 표시 (예: 두산에너빌리티, 한화에어로스페이스)
- 지수(S&P500, 코스피 등), 국가, 섹터명은 제외
- 명확히 언급된 기업만 포함 (추측 금지)
- 아래 이미 처리되는 기업은 제외:
  미국: {fixed_us}
  한국: {fixed_kr}"""

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        # 코드블록 제거
        if "```" in text:
            parts = text.split("```")
            text = parts[1] if len(parts) > 1 else text
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text)
        return {
            "us_tickers": [t for t in result.get("us_tickers", []) if t not in US_FIXED_TICKERS],
            "kr_companies": [c for c in result.get("kr_companies", []) if c not in KR_FIXED_NAMES],
        }
    except Exception:
        return {"us_tickers": [], "kr_companies": []}


async def fetch_extra_us_stocks(tickers: list) -> dict:
    """헤드라인 추출 추가 미국 종목 주가 수집 (최대 5개)"""
    if not tickers:
        return {}
    import yfinance as yf

    stocks = {}
    for symbol in tickers[:5]:
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="5d")
            if not hist.empty and len(hist) > 1:
                latest = hist.iloc[-1]
                prev = hist.iloc[-2]
                try:
                    name = ticker.fast_info.company_name or symbol
                except Exception:
                    name = symbol
                stocks[name] = {
                    "symbol": symbol,
                    "close": round(float(latest["Close"]), 2),
                    "prev_close": round(float(prev["Close"]), 2),
                    "change_pct": round(((float(latest["Close"]) / float(prev["Close"])) - 1) * 100, 2),
                    "volume": int(latest["Volume"]),
                }
        except Exception:
            continue
    return stocks


async def fetch_extra_kr_stocks(company_names: list) -> dict:
    """헤드라인 추출 추가 한국 종목 주가 수집 (최대 5개, KR_NAME_TO_TICKER 기반)"""
    if not company_names:
        return {}
    from pykrx import stock as krx

    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=15)).strftime("%Y%m%d")

    stocks = {}
    for name in company_names[:5]:
        ticker_code = KR_NAME_TO_TICKER.get(name)
        if not ticker_code:
            continue
        try:
            df = krx.get_market_ohlcv(start, end, ticker_code)
            if df is not None and not df.empty and len(df) >= 1:
                row = df.iloc[-1]
                prev_row = df.iloc[-2] if len(df) > 1 else row
                change_pct = (
                    round(((float(row["종가"]) / float(prev_row["종가"])) - 1) * 100, 2)
                    if len(df) > 1 else 0
                )
                stocks[name] = {
                    "ticker": ticker_code,
                    "close": int(row["종가"]),
                    "change_pct": change_pct,
                    "volume": int(row["거래량"]),
                }
        except Exception:
            continue
    return stocks


async def fetch_extra_tavily(names: list) -> list:
    """헤드라인 추출 기업들의 Tavily 뉴스 추가 수집 (최대 3개 기업)"""
    if not names or not TAVILY_API_KEY:
        return []
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=TAVILY_API_KEY)
        results = []
        for name in names[:3]:
            try:
                response = client.search(
                    query=f"{name} 주가 뉴스 최신",
                    search_depth="basic",
                    topic="news",
                    days=2,
                    max_results=3,
                    include_answer=False,
                )
                for r in response.get("results", []):
                    results.append({
                        "keyword": f"[추출기업] {name}",
                        "title": r.get("title", ""),
                        "content": r.get("content", ""),
                        "url": r.get("url", ""),
                    })
            except Exception:
                continue
        return results
    except Exception:
        return []


async def fetch_extra_sa_ratings(tickers: list) -> list:
    """헤드라인 추출 미국 종목 Seeking Alpha 레이팅 (최대 3개)"""
    if not tickers or not RAPIDAPI_KEY:
        return []
    ratings = []
    for symbol in tickers[:3]:
        data = _sa_get("/symbols/get-ratings", {"symbol": symbol})
        if data and "data" in data and isinstance(data["data"], list) and len(data["data"]) > 0:
            try:
                r = data["data"][0].get("attributes", {}).get("ratings", {})
                ratings.append({
                    "symbol": symbol,
                    "wall_street": round(r.get("sellSideRating", 0), 2) if r.get("sellSideRating") else "",
                    "quant": round(r.get("quantRating", 0), 2) if r.get("quantRating") else "",
                    "authors": round(r.get("authorsRating", 0), 2) if r.get("authorsRating") else "",
                })
            except Exception:
                continue
    return ratings


# ============================================================
# 6. 일일 피드 생성 (Markdown 텍스트 - 복사해서 LLM에 붙여넣기용)
# ============================================================
@app.get("/api/daily-feed", response_class=PlainTextResponse)
async def get_daily_feed():
    """모든 데이터를 수집하여 LLM 입력용 Markdown 텍스트로 병합"""
    import asyncio

    try:
        # ── STEP 1: 뉴스 헤드라인 먼저 수집 ──
        news = await get_news_headlines()

        # ── STEP 2: Haiku 기업 추출 + 기존 데이터 병렬 수집 ──
        (
            extra_companies,
            kr, us, forex, tavily, sa,
        ) = await asyncio.gather(
            extract_companies_from_headlines(news.get("headlines", [])),
            get_kr_market_data(),
            get_us_market_data(),
            get_forex_data(),
            get_tavily_news(),
            get_seeking_alpha_data(),
            return_exceptions=True,
        )

        # 예외 처리 (각 수집 실패 시 빈 값으로 폴백)
        if isinstance(extra_companies, Exception): extra_companies = {"us_tickers": [], "kr_companies": []}
        if isinstance(kr, Exception): kr = {}
        if isinstance(us, Exception): us = {}
        if isinstance(forex, Exception): forex = {}
        if isinstance(tavily, Exception): tavily = {"results": []}
        if isinstance(sa, Exception): sa = {"ratings": [], "trending": []}

        extra_us_tickers = extra_companies.get("us_tickers", [])
        extra_kr_names = extra_companies.get("kr_companies", [])
        all_extra_names = extra_us_tickers + extra_kr_names

        # ── STEP 3: 추가 기업 데이터 병렬 수집 ──
        (
            extra_us_stocks,
            extra_kr_stocks,
            extra_tavily_results,
            extra_sa_ratings,
        ) = await asyncio.gather(
            fetch_extra_us_stocks(extra_us_tickers),
            fetch_extra_kr_stocks(extra_kr_names),
            fetch_extra_tavily(all_extra_names),
            fetch_extra_sa_ratings(extra_us_tickers),
            return_exceptions=True,
        )

        if isinstance(extra_us_stocks, Exception): extra_us_stocks = {}
        if isinstance(extra_kr_stocks, Exception): extra_kr_stocks = {}
        if isinstance(extra_tavily_results, Exception): extra_tavily_results = []
        if isinstance(extra_sa_ratings, Exception): extra_sa_ratings = []

        # ── STEP 4: Markdown 생성 ──
        today_str = datetime.now().strftime("%Y년 %m월 %d일 %H:%M")
        lines = []

        lines.append(f"# 일일 경제 브리핑 데이터 ({today_str} 기준)")
        lines.append("")

        # 헤드라인 추출 기업 요약 (상단 노출)
        if all_extra_names:
            lines.append(f"> 💡 헤드라인에서 추출된 추가 기업: {', '.join(all_extra_names)}")
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

        if extra_us_stocks:
            lines.append("### 헤드라인 언급 추가 미국 종목")
            for name, data in extra_us_stocks.items():
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

        if extra_kr_stocks:
            lines.append("### 헤드라인 언급 추가 한국 종목")
            for name, data in extra_kr_stocks.items():
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

        # ── Tavily 심층 뉴스 (고정 + 추가 기업) ──
        all_tavily_results = list(tavily.get("results", [])) + list(extra_tavily_results)
        if all_tavily_results:
            lines.append("## 5. 심층 뉴스 분석 (Tavily)")
            current_keyword = ""
            for item in all_tavily_results:
                if item["keyword"] != current_keyword:
                    current_keyword = item["keyword"]
                    lines.append(f"\n### [{current_keyword}]")
                lines.append(f"- **{item['title']}**")
                if item.get("content"):
                    lines.append(f"  > {item['content'][:300]}")
            lines.append("")

        # ── Seeking Alpha 애널리스트 데이터 (고정 + 추가 종목) ──
        all_ratings = list(sa.get("ratings", [])) + list(extra_sa_ratings)
        if all_ratings:
            lines.append("## 6. 애널리스트 레이팅 (Seeking Alpha)")
            for r in all_ratings:
                parts = [f"**{r['symbol']}**"]
                if r.get("wall_street"):
                    parts.append(f"월가: {r['wall_street']}")
                if r.get("quant"):
                    parts.append(f"퀀트: {r['quant']}")
                if r.get("authors"):
                    parts.append(f"SA분석가: {r['authors']}")
                lines.append(f"- {' | '.join(parts)}")
            lines.append("- (1=Strong Sell, 3=Hold, 5=Strong Buy)")
            lines.append("")

        if sa.get("trending"):
            lines.append("## 7. 마켓 뉴스 (Seeking Alpha)")
            for article in sa["trending"]:
                lines.append(f"- {article['title']}")
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
