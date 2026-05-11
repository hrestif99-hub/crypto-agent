"""
Crypto Signal Agent
- Surveille les cryptos en temps réel via CoinMarketCap
- Scrape Reddit pour les signaux sociaux
- Alerte sur Telegram si volume anormal détecté
- Envoie une newsletter quotidienne par email
"""

import os
import requests
import time
import json
import smtplib
import schedule
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

TELEGRAM_TOKEN          = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID        = int(os.environ.get("TELEGRAM_CHAT_ID", "0"))
ANTHROPIC_API_KEY       = os.environ.get("ANTHROPIC_API_KEY", "")
COINMARKETCAP_API_KEY   = os.environ.get("COINMARKETCAP_API_KEY", "")
EMAIL_SENDER            = os.environ.get("EMAIL_SENDER", "")
EMAIL_PASSWORD          = os.environ.get("EMAIL_PASSWORD", "")
EMAIL_RECEIVER          = os.environ.get("EMAIL_RECEIVER", "")
VOLUME_SPIKE_THRESHOLD  = float(os.environ.get("VOLUME_SPIKE_THRESHOLD", "0.3"))
CHECK_INTERVAL_MINUTES  = int(os.environ.get("CHECK_INTERVAL_MINUTES", "15"))
MARKET_CAP_MINIMUM      = int(os.environ.get("MARKET_CAP_MINIMUM", "50000000"))

# ─── TELEGRAM ────────────────────────────────────────────────────────────────

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            print(f"[Telegram] Message envoyé ✅")
        else:
            print(f"[Telegram] Erreur: {r.text}")
    except Exception as e:
        print(f"[Telegram] Exception: {e}")


# ─── COINMARKETCAP API ───────────────────────────────────────────────────────

def get_crypto_data(limit=500):
    url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
    headers = {
        "X-CMC_PRO_API_KEY": COINMARKETCAP_API_KEY,
        "Accept": "application/json"
    }
    params = {
        "limit": limit,
        "convert": "USD",
        "sort": "volume_24h"
    }
    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        data = r.json()
        cryptos = []
        for c in data.get("data", []):
            quote = c.get("quote", {}).get("USD", {})
            cryptos.append({
                "name": c["name"],
                "symbol": c["symbol"],
                "current_price": quote.get("price", 0),
                "price_change_percentage_24h": quote.get("percent_change_24h", 0),
                "change_7d": quote.get("percent_change_7d", 0),          # NOUVEAU
                "volume_change_7d": quote.get("volume_change_7d", 0),    # NOUVEAU
                "total_volume": quote.get("volume_24h", 0),
                "market_cap": quote.get("market_cap", 0)
            })
        return cryptos
    except Exception as e:
        print(f"[CoinMarketCap] Erreur: {e}")
        return []


def detect_volume_spikes(cryptos):
    spikes = []
    for c in cryptos:
        market_cap = c.get("market_cap", 0)
        volume = c.get("total_volume", 0)
        if market_cap < MARKET_CAP_MINIMUM or market_cap == 0:
            continue
        ratio = volume / market_cap
        if ratio >= VOLUME_SPIKE_THRESHOLD:
            spikes.append({
                "name": c["name"],
                "symbol": c["symbol"],
                "price": c["current_price"],
                "change_24h": c["price_change_percentage_24h"],
                "change_7d": c.get("change_7d", 0),                      # NOUVEAU
                "volume_change_7d": c.get("volume_change_7d", 0),        # NOUVEAU
                "volume": volume,
                "market_cap": market_cap,
                "ratio": ratio
            })
    return sorted(spikes, key=lambda x: x["ratio"], reverse=True)


def get_top_gainers(cryptos, top_n=10):
    filtered = [c for c in cryptos if c.get("market_cap", 0) >= MARKET_CAP_MINIMUM]
    return sorted(filtered, key=lambda x: x.get("price_change_percentage_24h", 0), reverse=True)[:top_n]


# ─── REDDIT SCRAPING ─────────────────────────────────────────────────────────

def get_reddit_signals():
    subreddits = [
        "CryptoMoonShots", "altcoin", "SatoshiStreetBets",
        "CryptoCurrency", "defi", "CryptoMarkets",
        "binance", "ethtrader", "BitcoinMarkets", "CryptoTechnology"
    ]
    mentions = {}
    headers = {"User-Agent": "CryptoSignalBot/1.0"}

    for sub in subreddits:
        try:
            url = f"https://www.reddit.com/r/{sub}/new.json?limit=50"
            r = requests.get(url, headers=headers, timeout=10)
            posts = r.json().get("data", {}).get("children", [])

            for post in posts:
                title = post["data"].get("title", "").upper()
                score = post["data"].get("score", 0)
                comments = post["data"].get("num_comments", 0)

                words = title.split()
                for word in words:
                    word = word.strip("$#()[].,!?")
                    if 2 <= len(word) <= 6 and word.isalpha():
                        if word not in mentions:
                            mentions[word] = {"count": 0, "score": 0, "comments": 0}
                        mentions[word]["count"] += 1
                        mentions[word]["score"] += score
                        mentions[word]["comments"] += comments

        except Exception as e:
            print(f"[Reddit] Erreur sur r/{sub}: {e}")

    stopwords = {"THE", "AND", "FOR", "ARE", "BUT", "NOT", "YOU", "ALL",
                 "CAN", "HER", "WAS", "ONE", "OUR", "OUT", "DAY", "GET",
                 "HAS", "HIM", "HIS", "HOW", "ITS", "NEW", "NOW", "OLD",
                 "SEE", "TWO", "WAY", "WHO", "BOY", "DID", "ITS", "LET",
                 "PUT", "SAY", "SHE", "TOO", "USE", "THIS", "WITH", "THAT",
                 "FROM", "THEY", "WILL", "HAVE", "MORE", "BEEN", "WHEN",
                 "JUST", "LIKE", "SOME", "WHAT", "GOOD", "VERY", "KNOW",
                 "PUMP", "DUMP", "MOON", "HODL", "BULL", "BEAR", "NEWS",
                 "HUGE", "MEGA", "EPIC", "BEST", "NEXT", "DONT", "YOUR"}

    filtered = {k: v for k, v in mentions.items()
                if k not in stopwords and v["count"] >= 2}

    return sorted(filtered.items(), key=lambda x: x[1]["score"], reverse=True)[:10]


def get_google_news_mentions(symbol, name):
    """Cherche des news récentes sur une crypto via Google News."""
    try:
        query = f"{name} crypto {symbol}".replace(" ", "+")
        url = f"https://news.google.com/rss/search?q={query}&hl=fr&gl=FR&ceid=FR:fr"
        r = requests.get(url, timeout=10)
        count = r.text.count("<item>")
        return min(count, 10)
    except:
        return 0


def calculate_confidence_score(spike, reddit_signals):
    """Calcule un score de confiance 0-7 pour un signal."""
    score = 0
    details = []

    # Critère 1 — Volume anormal (ratio volume/market cap)
    if spike["ratio"] > 0.5:
        score += 1
        details.append("✅ Volume très anormal (>50% market cap)")
    elif spike["ratio"] > 0.3:
        score += 0.5
        details.append("⚠️ Volume anormal (>30% market cap)")
    else:
        details.append("❌ Volume peu significatif")

    # Critère 2 — Force de la hausse 24h
    if spike["change_24h"] > 50:
        score += 1
        details.append(f"✅ Hausse forte (+{spike['change_24h']:.0f}% en 24h)")
    elif spike["change_24h"] > 20:
        score += 0.5
        details.append(f"⚠️ Hausse modérée (+{spike['change_24h']:.0f}% en 24h)")
    else:
        details.append(f"❌ Hausse faible ({spike['change_24h']:+.0f}% en 24h)")

    # Critère 3 — Mentions Reddit
    symbol = spike["symbol"]
    name_upper = spike["name"].upper()
    reddit_match = [v for k, v in reddit_signals
                    if k == symbol or k == name_upper]
    if reddit_match and reddit_match[0]["count"] >= 5:
        score += 1
        details.append(f"✅ Buzz Reddit ({reddit_match[0]['count']} mentions)")
    elif reddit_match and reddit_match[0]["count"] >= 2:
        score += 0.5
        details.append(f"⚠️ Mentions Reddit ({reddit_match[0]['count']} mentions)")
    else:
        details.append("❌ Pas de buzz Reddit détecté")

    # Critère 4 — News Google
    news_count = get_google_news_mentions(spike["symbol"], spike["name"])
    if news_count >= 5:
        score += 1
        details.append(f"✅ Actualité forte ({news_count} articles récents)")
    elif news_count >= 2:
        score += 0.5
        details.append(f"⚠️ Quelques articles ({news_count} articles)")
    else:
        details.append("❌ Pas de news récente trouvée")

    # Critère 5 — Market cap (fiabilité)
    if spike["market_cap"] > 500_000_000:
        score += 1
        details.append("✅ Market cap solide (>500M$)")
    elif spike["market_cap"] > 100_000_000:
        score += 0.5
        details.append("⚠️ Market cap correct (>100M$)")
    else:
        details.append("❌ Petit market cap — risque élevé")

    # Critère 6 — Tendance haussière sur 7 jours (NOUVEAU)
    change_7d = spike.get("change_7d", 0)
    if change_7d > 30 and spike["change_24h"] > 10:
        score += 1
        details.append(f"✅ Tendance haussière confirmée (+{change_7d:.0f}% sur 7j)")
    elif change_7d > 10:
        score += 0.5
        details.append(f"⚠️ Légère tendance haussière (+{change_7d:.0f}% sur 7j)")
    else:
        details.append(f"❌ Pas de tendance 7j confirmée ({change_7d:+.0f}%)")

    # Critère 7 — Accélération du volume sur 7 jours (NOUVEAU)
    vol_change_7d = spike.get("volume_change_7d", 0)
    if vol_change_7d > 200:
        score += 1
        details.append(f"✅ Volume en explosion (+{vol_change_7d:.0f}% vs 7j)")
    elif vol_change_7d > 100:
        score += 0.5
        details.append(f"⚠️ Volume en hausse (+{vol_change_7d:.0f}% vs 7j)")
    else:
        details.append(f"❌ Volume sans accélération notable ({vol_change_7d:+.0f}% vs 7j)")

    return round(score), details


# ─── CLAUDE ANALYSE ──────────────────────────────────────────────────────────

def analyze_with_claude(spikes, gainers, reddit_signals):
    spikes_text = json.dumps(spikes[:5], indent=2, ensure_ascii=False)
    gainers_text = json.dumps([
        {
            "name": g["name"],
            "symbol": g["symbol"],
            "change_24h": float(g.get("price_change_percentage_24h", 0)),
            "price": float(g.get("current_price", 0))
        } for g in gainers
    ], indent=2, ensure_ascii=False)
    reddit_text = json.dumps([
        {"ticker": k, "mentions": v["count"], "score_total": v["score"]}
        for k, v in reddit_signals
    ], indent=2, ensure_ascii=False)

    prompt = f"""Tu es un analyste crypto expérimenté. Voici les données du marché en ce moment :

**ANOMALIES DE VOLUME :**
{spikes_text}

**TOP GAINERS 24H :**
{gainers_text}

**SIGNAUX REDDIT :**
{reddit_text}

Génère une analyse concise pour un investisseur individuel. Structure :

🚨 ALERTES SIGNAL FORT (2-3 cryptos max qui méritent attention immédiate)
📈 MOUVEMENTS NOTABLES (tendances générales)
🔍 SIGNAUX SOCIAUX (ce qui buzz sur Reddit)
⚠️ AVERTISSEMENT (rappel informatif, pas conseil financier)

Sois direct, utilise des emojis, explique POURQUOI ces signaux sont intéressants. Maximum 400 mots."""

    try:
        headers = {
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
        body = {
            "model": "claude-sonnet-4-6",
            "max_tokens": 1000,
            "messages": [{"role": "user", "content": prompt}]
        }
        r = requests.post("https://api.anthropic.com/v1/messages",
                          headers=headers, json=body, timeout=30)
        data = r.json()
        return data["content"][0]["text"]
    except Exception as e:
        print(f"[Claude] Erreur: {e}")
        return "Erreur lors de l'analyse Claude."


# ─── EMAIL ───────────────────────────────────────────────────────────────────

def send_email(subject, body_html):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECEIVER

    part = MIMEText(body_html, "html")
    msg.attach(part)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        print(f"[Email] Newsletter envoyée ✅")
    except Exception as e:
        print(f"[Email] Erreur: {e}")


def build_newsletter_html(analysis, spikes, gainers):
    now = datetime.now().strftime("%d/%m/%Y à %H:%M")

    gainers_rows = ""
    for g in gainers[:5]:
        change = float(g.get("price_change_percentage_24h", 0))
        color = "#00c853" if change > 0 else "#d50000"
        gainers_rows += f"""
        <tr>
            <td style='padding:8px'>{g['name']} ({g['symbol'].upper()})</td>
            <td style='padding:8px'>${float(g.get('current_price', 0)):.4f}</td>
            <td style='padding:8px; color:{color}'>{change:+.2f}%</td>
        </tr>"""

    analysis_html = analysis.replace("\n", "<br>")

    return f"""
    <html><body style='font-family:Arial,sans-serif; max-width:600px; margin:auto; background:#0d1117; color:#e6edf3'>
        <div style='background:#161b22; padding:30px; border-radius:12px; margin:20px'>
            <h1 style='color:#58a6ff; border-bottom:2px solid #30363d; padding-bottom:15px'>
                🔮 Crypto Signal Daily — {now}
            </h1>
            <div style='background:#0d1117; padding:20px; border-radius:8px; margin:20px 0'>
                <h2 style='color:#f0883e'>📊 Analyse du jour</h2>
                <p>{analysis_html}</p>
            </div>
            <div style='background:#0d1117; padding:20px; border-radius:8px; margin:20px 0'>
                <h2 style='color:#3fb950'>🚀 Top Gainers 24h</h2>
                <table style='width:100%; border-collapse:collapse'>
                    <tr style='background:#161b22; color:#8b949e'>
                        <th style='padding:8px; text-align:left'>Crypto</th>
                        <th style='padding:8px; text-align:left'>Prix</th>
                        <th style='padding:8px; text-align:left'>24h</th>
                    </tr>
                    {gainers_rows}
                </table>
            </div>
            <div style='background:#161b22; padding:15px; border-radius:8px; margin-top:20px;
                        border-left:4px solid #d29922; font-size:12px; color:#8b949e'>
                ⚠️ Ce rapport est fourni à titre informatif uniquement.
                Il ne constitue pas un conseil en investissement.
                Investir en crypto comporte des risques importants de perte en capital.
            </div>
        </div>
    </body></html>
    """


# ─── JOBS PLANIFIÉS ──────────────────────────────────────────────────────────

previous_volumes = {}

def check_for_spikes():
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Vérification des spikes...")

    cryptos = get_crypto_data(limit=500)
    if not cryptos:
        return

    spikes = detect_volume_spikes(cryptos)
    reddit_signals = get_reddit_signals()

    for spike in spikes[:10]:
        symbol = spike["symbol"]
        current_vol = spike["volume"]

        prev_vol = previous_volumes.get(symbol, current_vol)
        vol_increase = ((current_vol - prev_vol) / prev_vol * 100) if prev_vol > 0 else 0

        previous_volumes[symbol] = current_vol

        if spike["ratio"] > 0.3:
            confidence, details = calculate_confidence_score(spike, reddit_signals)

            # Ignorer si score insuffisant
            if confidence < 4:
                print(f"[Spike] Score {confidence}/7 insuffisant pour {symbol} — ignoré")
                continue

            if confidence >= 6:
                signal_emoji = "🔥"
                signal_label = "SIGNAL TRÈS FORT"
            elif confidence >= 4:
                signal_emoji = "⚡"
                signal_label = "SIGNAL FORT"
            else:
                signal_emoji = "👀"
                signal_label = "SIGNAL MODÉRÉ"

            details_text = "\n".join(details)

            message = f"""{signal_emoji} *{signal_label} — {spike['name']} ({symbol})*
Score confiance: {confidence}/7

💰 Prix: ${spike['price']:.4f}
📈 Variation 24h: {spike['change_24h']:+.2f}%
📅 Variation 7j: {spike.get('change_7d', 0):+.2f}%
📊 Volume/Market Cap: {spike['ratio']:.2%}
🔥 Hausse volume: {vol_increase:+.1f}% vs dernier check

{details_text}

⚠️ Informatif uniquement — pas un conseil financier."""

            send_telegram(message)
            print(f"[Spike] Alerte envoyée pour {symbol} — Score: {confidence}/7")


def send_daily_newsletter():
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Génération newsletter quotidienne...")

    cryptos = get_crypto_data(limit=500)
    if not cryptos:
        return

    spikes = detect_volume_spikes(cryptos)
    gainers = get_top_gainers(cryptos)
    reddit_signals = get_reddit_signals()

    analysis = analyze_with_claude(spikes, gainers, reddit_signals)

    html = build_newsletter_html(analysis, spikes, gainers)
    send_email(
        subject=f"🔮 Crypto Signal Daily — {datetime.now().strftime('%d/%m/%Y')}",
        body_html=html
    )

    send_telegram(f"📰 *Newsletter quotidienne envoyée !*\n\n{analysis[:500]}...")
    print("[Newsletter] Terminé ✅")


# ─── MAIN ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("🚀 Crypto Signal Agent démarré !")
    send_telegram("🤖 *Crypto Signal Agent démarré !*\nJe surveille le marché 24/7 et t'alerterai dès qu'un signal fort est détecté.")

    schedule.every().day.at("08:00").do(send_daily_newsletter)
    schedule.every(CHECK_INTERVAL_MINUTES).minutes.do(check_for_spikes)

    check_for_spikes()

    print(f"⏰ Surveillance active — check toutes les {CHECK_INTERVAL_MINUTES} min")
    print("📧 Newsletter quotidienne programmée à 08:00")

    while True:
        schedule.run_pending()
        time.sleep(30)