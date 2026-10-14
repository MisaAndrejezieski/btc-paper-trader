"""Market history, public analyst coverage, and a transparent 24-hour estimate."""

import html
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from statistics import fmean, stdev

import requests


COINGECKO_RANGE_URL = (
    "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart/range"
)
COINGECKO_CHART_URL = (
    "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart"
    "?vs_currency=brl&days=1"
)
ANALYST_FEEDS = (
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
)

_ANALYST_MENTION = re.compile(
    r"\b(?:analysts?|strategists?|researchers?|research firms?)\b"
)
_NEGATED_ANALYST_MENTION = re.compile(
    r"\b(?:no|without|not|isn't|is not)\s+(?:an?\s+)?"
    r"(?:bitcoin\s+)?(?:analysts?|strategists?|researchers?)\b"
)
_BULLISH_TERMS = (
    "bullish", "rally", "surge", "breakout", "higher", "upside",
    "gains", "rise", "rises", "soars", "rebound", "accumulate",
)
_BEARISH_TERMS = (
    "bearish", "selloff", "sell-off", "downside", "lower", "drop",
    "falls", "falling", "decline", "plunge", "correction", "resistance",
)


@dataclass(frozen=True)
class AnalystArticle:
    publisher: str
    author: str
    title: str
    url: str
    published_at: datetime
    sentiment: float
    analyst_view: bool = False


@dataclass(frozen=True)
class ForecastPoint:
    timestamp: datetime
    expected_brl: float
    low_brl: float
    high_brl: float


def buscar_historico_btc_brl() -> list[tuple[datetime, float]]:
    """Fetch the latest 24 hours of BTC/BRL prices from CoinGecko."""
    response = requests.get(COINGECKO_CHART_URL, timeout=15)
    response.raise_for_status()
    data = response.json()
    prices = data["prices"]
    history = [
        (datetime.fromtimestamp(item[0] / 1000, timezone.utc), float(item[1]))
        for item in prices
        if len(item) >= 2 and math.isfinite(float(item[1])) and float(item[1]) > 0
    ]
    if len(history) < 2:
        raise ValueError("A API retornou histórico insuficiente para o gráfico.")
    return history


def buscar_historico_btc_brl_periodo(
    dias: int,
    now: datetime | None = None,
) -> list[tuple[datetime, float]]:
    """Fetch BTC/BRL history for one of the supported backtest periods."""
    if dias not in (7, 30, 90, 365):
        raise ValueError("O período precisa ser 7, 30, 90 ou 365 dias.")
    end = now or datetime.now(timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    end = end.astimezone(timezone.utc)
    start = end - timedelta(days=dias)
    response = requests.get(
        COINGECKO_RANGE_URL,
        params={
            "vs_currency": "brl",
            "from": int(start.timestamp()),
            "to": int(end.timestamp()),
        },
        timeout=30,
    )
    response.raise_for_status()
    prices = response.json()["prices"]
    history = [
        (datetime.fromtimestamp(item[0] / 1000, timezone.utc), float(item[1]))
        for item in prices
        if len(item) >= 2
        and math.isfinite(float(item[1]))
        and float(item[1]) > 0
    ]
    if len(history) < 2:
        raise ValueError("A API retornou dados insuficientes para o backtest.")
    return history


def _limpar_html(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", text or " "))


def _sentimento(text: str) -> float:
    texto = text.casefold()
    bullish = sum(
        len(re.findall(rf"\b{re.escape(term)}\b", texto))
        for term in _BULLISH_TERMS
    )
    bearish = sum(
        len(re.findall(rf"\b{re.escape(term)}\b", texto))
        for term in _BEARISH_TERMS
    )
    total = bullish + bearish
    return (bullish - bearish) / total if total else 0.0


def analisar_feed_analistas(
    xml_content: str,
    publisher: str,
    now: datetime | None = None,
) -> list[AnalystArticle]:
    """Keep recent BTC stories and mark whether they discuss analyst views."""
    agora = now or datetime.now(timezone.utc)
    if agora.tzinfo is None:
        agora = agora.replace(tzinfo=timezone.utc)

    root = ET.fromstring(xml_content)
    articles = []
    for item in root.findall(".//item"):
        title = _limpar_html(item.findtext("title", ""))
        description = _limpar_html(item.findtext("description", ""))
        content = f"{title} {description}".casefold()
        if "bitcoin" not in content and "btc" not in content:
            continue
        score = _sentimento(content)
        analyst_view = bool(
            _ANALYST_MENTION.search(content)
            and not _NEGATED_ANALYST_MENTION.search(content)
        )
        date_text = item.findtext("pubDate")
        if not date_text:
            continue
        try:
            published_at = parsedate_to_datetime(date_text)
        except (TypeError, ValueError, OverflowError):
            continue
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
        age_hours = (agora - published_at.astimezone(timezone.utc)).total_seconds() / 3600
        if age_hours < 0 or age_hours > 24:
            continue

        link = item.findtext("link", "").strip()
        if not link:
            continue
        creator = item.findtext("{http://purl.org/dc/elements/1.1/}creator", "")
        articles.append(AnalystArticle(
            publisher=publisher,
            author=_limpar_html(creator).strip() or publisher,
            title=title.strip(),
            url=link,
            published_at=published_at.astimezone(timezone.utc),
            sentiment=score,
            analyst_view=analyst_view,
        ))
    return articles


def buscar_sentimento_analistas() -> tuple[float, list[AnalystArticle], str]:
    """Aggregate explicit bullish/bearish analyst coverage from public RSS feeds."""
    agora = datetime.now(timezone.utc)
    articles = []
    errors = []
    for publisher, url in ANALYST_FEEDS:
        try:
            response = requests.get(
                url,
                timeout=12,
                headers={"User-Agent": "BTC-Paper-Trader/1.0 (public RSS reader)"},
            )
            response.raise_for_status()
            articles.extend(analisar_feed_analistas(response.text, publisher, agora))
        except (requests.RequestException, ET.ParseError, KeyError, TypeError, ValueError) as exc:
            errors.append(f"{publisher}: {exc}")
            print(f"[AVISO] Não foi possível consultar análises de {publisher}: {exc}")

    if not articles and len(errors) == len(ANALYST_FEEDS):
        raise RuntimeError("Não foi possível consultar os feeds públicos de análises.")

    unique_articles = {}
    for article in sorted(articles, key=lambda item: item.published_at, reverse=True):
        unique_articles.setdefault(article.title.casefold(), article)
    articles = list(unique_articles.values())

    now_utc = agora.astimezone(timezone.utc)
    analyst_articles = [
        article for article in articles
        if article.analyst_view and article.sentiment != 0
    ]
    weights = [
        math.exp(-math.log(2) * max(
            0.0, (now_utc - article.published_at).total_seconds() / 3600
        ) / 8)
        for article in analyst_articles
    ]
    score = (
        sum(
            article.sentiment * weight
            for article, weight in zip(analyst_articles, weights)
        )
        / sum(weights)
        if weights else 0.0
    )
    return (
        score,
        sorted(articles, key=lambda article: article.published_at, reverse=True)[:5],
        "; ".join(errors),
    )


def calcular_previsao(
    history: list[tuple[datetime, float]],
    current_price: float,
    analyst_sentiment: float,
    now: datetime | None = None,
) -> list[ForecastPoint]:
    """Project an indicative 24-hour path using momentum, volatility, and analyst sentiment."""
    if not math.isfinite(current_price) or current_price <= 0:
        raise ValueError("O preço atual precisa ser maior que zero.")
    hourly_prices = {}
    for timestamp, price in history:
        if not math.isfinite(price) or price <= 0:
            continue
        hour = int(timestamp.timestamp() // 3600)
        hourly_prices[hour] = price

    ordered = sorted(hourly_prices.items())[-25:]
    returns = [
        math.log(current / previous)
        for (previous_hour, previous), (current_hour, current)
        in zip(ordered, ordered[1:])
        if current_hour - previous_hour == 1 and current > 0 and previous > 0
    ][-24:]
    if len(returns) < 6:
        raise ValueError("São necessárias ao menos 6 variações horárias para prever.")

    hourly_volatility = stdev(returns) if len(returns) > 1 else 0.0
    daily_volatility = hourly_volatility * math.sqrt(24)
    momentum = fmean(returns[-6:]) * 24
    max_momentum = daily_volatility * 0.5
    momentum = max(-max_momentum, min(max_momentum, momentum))
    sentiment = max(-1.0, min(1.0, analyst_sentiment))
    daily_drift = momentum + sentiment * daily_volatility * 0.2
    daily_drift = max(
        -daily_volatility * 0.75,
        min(daily_volatility * 0.75, daily_drift),
    )

    start = now or datetime.now(timezone.utc)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    points = []
    for hour in range(25):
        fraction = hour / 24
        expected = current_price * math.exp(daily_drift * fraction)
        interval = 1.28 * daily_volatility * math.sqrt(fraction)
        points.append(ForecastPoint(
            timestamp=start + timedelta(hours=hour),
            expected_brl=expected,
            low_brl=current_price * math.exp(daily_drift * fraction - interval),
            high_brl=current_price * math.exp(daily_drift * fraction + interval),
        ))
    return points
