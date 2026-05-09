import os
import json
import asyncio
import logging
import base64
from datetime import datetime, timezone
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import aiohttp

# ─── Configuration ───────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
COINBASE_API_KEY = os.environ.get("COINBASE_API_KEY", "")
COINBASE_API_SECRET = os.environ.get("COINBASE_API_SECRET", "")

STOP_LOSS_PCT = -25.0
TAKE_PROFIT_PCT = 30.0
CHECK_INTERVAL = 300          # Verification positions toutes les 5 min
SCANNER_INTERVAL = 3600       # Scan toutes les heures
NEW_LISTINGS_INTERVAL = 1800  # Nouvelles listings toutes les 30 min

SCANNER_MIN_GAIN_3D = 30.0    # Alerte si +30% en 3 jours
SCANNER_MIN_VOLUME = 1000000  # Volume minimum 1M EUR

POSITIONS_FILE = "positions.json"
SEEN_LISTINGS_FILE = "seen_listings.json"

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ─── Gestion des positions ────────────────────────────────────

def load_positions():
    if os.path.exists(POSITIONS_FILE):
        with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_positions(positions):
    with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(positions, f, indent=2, ensure_ascii=False)

def load_seen_listings():
    if os.path.exists(SEEN_LISTINGS_FILE):
        with open(SEEN_LISTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_seen_listings(seen):
    with open(SEEN_LISTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f)

def add_position(coin, amount_eur, entry_price, date=None):
    positions = load_positions()
    coin = coin.upper()
    key = f"{coin}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    positions[key] = {
        "coin": coin,
        "amount_eur": amount_eur,
        "entry_price": entry_price,
        "quantity": amount_eur / entry_price,
        "date": date or datetime.now().strftime("%Y-%m-%d %H:%M"),
        "alerted": False
    }
    save_positions(positions)
    return key, positions[key]


# ─── Prix en temps reel ───────────────────────────────────────

async def get_prices(coins):
    coin_ids_map = {
        "BTC": "bitcoin", "ETH": "ethereum", "BNB": "binancecoin",
        "SOL": "solana", "ADA": "cardano", "XRP": "ripple",
        "DOGE": "dogecoin", "AVAX": "avalanche-2", "DOT": "polkadot",
        "MATIC": "matic-network", "LINK": "chainlink", "LTC": "litecoin",
    "JTO": "jito-governance-token",
    "BILL": "billions-network",
    }
    ids = [coin_ids_map.get(c.upper(), c.lower()) for c in coins]
    ids_str = ",".join(set(ids))
    url = (
        f"https://api.coingecko.com/api/v3/simple/price"
        f"?ids={ids_str}&vs_currencies=eur&include_24hr_change=true"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                data = await r.json()
                result = {}
                for coin in coins:
                    coin_id = coin_ids_map.get(coin.upper(), coin.lower())
                    if coin_id in data:
                        result[coin.upper()] = {
                            "price": data[coin_id]["eur"],
                            "change_24h": data[coin_id].get("eur_24h_change", 0)
                        }
                return result
    except Exception as e:
        logger.error(f"Erreur prix: {e}")
        return {}


# ─── Scanner top movers 3 jours ──────────────────────────────

async def scan_top_movers():
    url = (
        "https://api.coingecko.com/api/v3/coins/markets"
        "?vs_currency=eur&order=market_cap_desc"
        "&per_page=250&page=1&sparkline=false"
        "&price_change_percentage=24h,7d"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                coins = await r.json()

        gainers = []
        for coin in coins:
            change_7d = coin.get("price_change_percentage_7d_in_currency") or 0
            change_24h = coin.get("price_change_percentage_24h") or 0
            volume = coin.get("total_volume") or 0
            market_cap = coin.get("market_cap") or 0

            if volume < SCANNER_MIN_VOLUME:
                continue
            if market_cap > 5_000_000_000:
                continue

            # On utilise 7j comme proxy pour 3j (CoinGecko gratuit n'a pas 3j)
            # On divise par ~2.3 pour estimer 3j
            change_3d_approx = change_7d / 2.3

            if change_3d_approx >= SCANNER_MIN_GAIN_3D:
                gainers.append({
                    "id": coin["id"],
                    "name": coin["name"],
                    "symbol": coin["symbol"].upper(),
                    "price": coin["current_price"],
                    "change_24h": change_24h,
                    "change_7d": change_7d,
                    "change_3d_approx": change_3d_approx,
                    "volume": volume,
                    "market_cap": market_cap,
                })

        return sorted(gainers, key=lambda x: x["change_3d_approx"], reverse=True)[:8]

    except Exception as e:
        logger.error(f"Erreur scanner: {e}")
        return []


# ─── Nouvelles listings CoinGecko ────────────────────────────

async def get_new_listings():
    url = "https://api.coingecko.com/api/v3/coins/list/new"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status != 200:
                    return []
                coins = await r.json()

        seen = load_seen_listings()
        new_ones = []

        for coin in coins:
            coin_id = coin.get("id")
            if coin_id and coin_id not in seen:
                new_ones.append(coin)
                seen.append(coin_id)

        # Garder seulement les 500 derniers vus
        save_seen_listings(seen[-500:])
        return new_ones[:5]

    except Exception as e:
        logger.error(f"Erreur nouvelles listings: {e}")
        return []


# ─── Details d'une crypto (DeFiLlama + CoinGecko) ────────────

async def get_coin_details(coin_id):
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}?localization=false&tickers=false&community_data=true&developer_data=true"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status != 200:
                    return None
                return await r.json()
    except Exception as e:
        logger.error(f"Erreur details coin: {e}")
        return None


def score_project(details):
    if not details:
        return 0, []

    score = 0
    signaux = []
    alertes = []

    # Site web
    if details.get("links", {}).get("homepage", [None])[0]:
        score += 1
        signaux.append("Site web present")
    else:
        alertes.append("Pas de site web")

    # Whitepaper
    if details.get("links", {}).get("whitepaper"):
        score += 1
        signaux.append("Whitepaper present")
    else:
        alertes.append("Pas de whitepaper")

    # GitHub actif
    dev_data = details.get("developer_data", {})
    if dev_data.get("commit_count_4_weeks", 0) > 0:
        score += 2
        signaux.append(f"GitHub actif ({dev_data['commit_count_4_weeks']} commits/mois)")
    else:
        alertes.append("GitHub inactif ou absent")

    # Communaute Twitter
    community = details.get("community_data", {})
    twitter = community.get("twitter_followers", 0) or 0
    if twitter > 10000:
        score += 1
        signaux.append(f"Twitter : {twitter:,} followers")
    elif twitter > 1000:
        score += 0.5
        signaux.append(f"Twitter : {twitter:,} followers")
    else:
        alertes.append(f"Peu de followers Twitter ({twitter})")

    # Market cap raisonnable
    market_cap = details.get("market_data", {}).get("market_cap", {}).get("eur", 0) or 0
    if market_cap > 100000:
        score += 1
        signaux.append(f"Market cap : {market_cap:,.0f} EUR")
    else:
        alertes.append("Market cap tres faible (risque rug pull)")

    # Description
    desc = details.get("description", {}).get("en", "")
    if desc and len(desc) > 100:
        score += 1
        signaux.append("Projet decrit")
    else:
        alertes.append("Pas de description du projet")

    return round(score), signaux, alertes


# ─── Analyse screenshot via Claude ───────────────────────────

async def analyze_screenshot(image_bytes):
    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    prompt = """Analyse ce screenshot de trade crypto.
Reponds UNIQUEMENT en JSON :
{
  "coin": "symbole en majuscules (ex: BTC)",
  "amount_eur": montant investi en euros (nombre),
  "entry_price": prix achat en euros (nombre),
  "date": "YYYY-MM-DD ou null"
}
Si montant en USD, multiplie par 0.92. UNIQUEMENT le JSON, rien d autre."""

    headers = {
        "Content-Type": "application/json",
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01"
    }
    body = {
        "model": "claude-opus-4-6",
        "max_tokens": 300,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
                {"type": "text", "text": prompt}
            ]
        }]
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers, json=body,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as r:
                data = await r.json()
                text = data["content"][0]["text"].strip()
                if "```" in text:
                    text = text.split("```")[1]
                    if text.startswith("json"):
                        text = text[4:]
                return json.loads(text.strip())
    except Exception as e:
        logger.error(f"Erreur screenshot: {e}")
        return None


# ─── Verification des positions ───────────────────────────────

async def check_positions(app):
    positions = load_positions()
    if not positions:
        return

    coins = list(set(p["coin"] for p in positions.values()))
    prices = await get_prices(coins)
    if not prices:
        return

    modified = False
    for key, pos in positions.items():
        coin = pos["coin"]
        if coin not in prices:
            continue

        current_price = prices[coin]["price"]
        entry_price = pos["entry_price"]
        pct_change = ((current_price - entry_price) / entry_price) * 100
        current_value = pos["quantity"] * current_price
        pnl_eur = current_value - pos["amount_eur"]

        if pct_change <= STOP_LOSS_PCT and not pos.get("stop_loss_alerted"):
            msg = (
                f"STOP LOSS ATTEINT !\n\n"
                f"Crypto : {coin}\n"
                f"Date entree : {pos['date']}\n"
                f"Investi : {pos['amount_eur']:.2f} EUR\n"
                f"Prix entree : {entry_price:,.2f} EUR\n"
                f"Prix actuel : {current_price:,.2f} EUR\n"
                f"Performance : {pct_change:.1f}%\n"
                f"P&L : {pnl_eur:.2f} EUR\n\n"
                f"RECOMMANDATION : VENDRE MAINTENANT"
            )
            await app.bot.send_message(chat_id=CHAT_ID, text=msg)
            positions[key]["stop_loss_alerted"] = True
            modified = True

        elif pct_change >= TAKE_PROFIT_PCT and not pos.get("take_profit_alerted"):
            msg = (
                f"TAKE PROFIT ATTEINT !\n\n"
                f"Crypto : {coin}\n"
                f"Date entree : {pos['date']}\n"
                f"Investi : {pos['amount_eur']:.2f} EUR\n"
                f"Prix entree : {entry_price:,.2f} EUR\n"
                f"Prix actuel : {current_price:,.2f} EUR\n"
                f"Performance : +{pct_change:.1f}%\n"
                f"P&L : +{pnl_eur:.2f} EUR\n\n"
                f"RECOMMANDATION : PRENDRE LES GAINS"
            )
            await app.bot.send_message(chat_id=CHAT_ID, text=msg)
            positions[key]["take_profit_alerted"] = True
            modified = True

    if modified:
        save_positions(positions)


# ─── Commandes Telegram ───────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = (
        "Bot de Trading Crypto\n\n"
        "Commandes :\n\n"
        "/buy BTC 500 65000 — Ajouter une position\n"
        "/recap — Resume complet avec P&L\n"
        "/scanner — Scanner les cryptos qui bougent\n"
        "/nouveautes — Nouvelles cryptos listees\n"
        "/prix BTC ETH SOL — Prix actuels\n"
        "/delete ID — Supprimer une position\n\n"
        "Ou envoie un screenshot de ton trade !\n\n"
        f"Stop loss : {STOP_LOSS_PCT}%\n"
        f"Take profit : +{TAKE_PROFIT_PCT}%\n"
        f"Scanner : alerte si +{SCANNER_MIN_GAIN_3D}% en 3 jours"
    )
    await update.message.reply_text(msg)

async def cmd_buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if len(args) < 3:
        await update.message.reply_text("Usage : /buy BTC 500 65000\n(crypto montant_eur prix_entree)")
        return
    try:
        coin = args[0].upper()
        amount = float(args[1])
        price = float(args[2])
        date = args[3] if len(args) > 3 else None
        key, pos = add_position(coin, amount, price, date)
        await update.message.reply_text(
            f"Position ajoutee !\n\n"
            f"Crypto : {coin}\n"
            f"Montant : {amount:.2f} EUR\n"
            f"Prix entree : {price:,.2f} EUR\n"
            f"Quantite : {pos['quantity']:.8f}\n"
            f"Date : {pos['date']}\n"
            f"ID : {key}\n\n"
            f"Surveillance active !"
        )
    except ValueError:
        await update.message.reply_text("Erreur : montant et prix doivent etre des nombres.")

async def cmd_recap(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    positions = load_positions()
    if not positions:
        await update.message.reply_text("Aucune position enregistree.")
        return

    await update.message.reply_text("Calcul en cours...")
    coins = list(set(p["coin"] for p in positions.values()))
    prices = await get_prices(coins)

    msg = "RECAP DE TES POSITIONS\n"
    msg += f"{datetime.now().strftime('%d/%m/%Y %H:%M')}\n"
    msg += "─────────────────\n\n"

    total_investi = 0
    total_actuel = 0
    nb_gain = 0
    nb_perte = 0

    for key, pos in positions.items():
        coin = pos["coin"]
        info = prices.get(coin, {})
        current = info.get("price", 0)
        change_24h = info.get("change_24h", 0)
        pct = ((current - pos["entry_price"]) / pos["entry_price"] * 100) if current else 0
        val = pos["quantity"] * current if current else 0
        pnl = val - pos["amount_eur"]

        statut = "EN GAIN" if pct >= 0 else "EN PERTE"
        if pct >= 0:
            nb_gain += 1
        else:
            nb_perte += 1

        msg += (
            f"{coin} — {statut}\n"
            f"Entree : {pos['entry_price']:,.2f} EUR\n"
            f"Actuel : {current:,.2f} EUR\n"
            f"Variation 24h : {change_24h:+.1f}%\n"
            f"Perf totale : {pct:+.1f}%\n"
            f"Investi : {pos['amount_eur']:.2f} EUR\n"
            f"Valeur : {val:.2f} EUR\n"
            f"P&L : {pnl:+.2f} EUR\n"
            f"ID : {key}\n\n"
        )
        total_investi += pos["amount_eur"]
        total_actuel += val

    total_pnl = total_actuel - total_investi
    total_pct = (total_pnl / total_investi * 100) if total_investi else 0

    msg += "─────────────────\n"
    msg += f"BILAN GLOBAL\n"
    msg += f"Investi : {total_investi:.2f} EUR\n"
    msg += f"Valeur actuelle : {total_actuel:.2f} EUR\n"
    msg += f"P&L total : {total_pnl:+.2f} EUR ({total_pct:+.1f}%)\n"
    msg += f"Positions en gain : {nb_gain}\n"
    msg += f"Positions en perte : {nb_perte}"

    await update.message.reply_text(msg)

async def cmd_scanner(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Scan en cours...")
    gainers = await scan_top_movers()

    if not gainers:
        await update.message.reply_text("Aucune opportunite detectee pour le moment.")
        return

    msg = f"SCANNER — TOP MOVERS\n"
    msg += f"{datetime.now().strftime('%d/%m/%Y %H:%M')}\n"
    msg += f"Critere : +{SCANNER_MIN_GAIN_3D}% sur 3 jours\n\n"

    for c in gainers:
        details = await get_coin_details(c["id"])
        score, signaux, alertes = score_project(details)
        niveau = "SOLIDE" if score >= 5 else "MOYEN" if score >= 3 else "RISQUE"
        barre = "|" * score + "." * (7 - score)

        msg += (
            f"{c['symbol']} — {c['name']}\n"
            f"Prix : {c['price']:.4f} EUR\n"
            f"Est. 3j : +{c['change_3d_approx']:.1f}%\n"
            f"24h : {c['change_24h']:+.1f}%\n"
            f"7j : {c['change_7d']:+.1f}%\n"
            f"Volume : {c['volume']:,.0f} EUR\n"
            f"Score : {score}/7 [{barre}] {niveau}\n"
        )
        if alertes:
            msg += "Alertes : " + " | ".join(alertes[:2]) + "\n"
        msg += "\n"
        await asyncio.sleep(0.5)

    msg += "ATTENTION : Ces donnees sont informatives.\nLes cryptos peuvent perdre autant qu elles gagnent."
    await update.message.reply_text(msg)

async def cmd_nouveautes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Recherche des nouvelles listings...")
    new_coins = await get_new_listings()

    if not new_coins:
        await update.message.reply_text("Aucune nouvelle crypto detectee depuis la derniere verification.")
        return

    for coin in new_coins:
        coin_id = coin.get("id")
        name = coin.get("name", "Inconnu")
        symbol = coin.get("symbol", "?").upper()

        details = await get_coin_details(coin_id)
        score, signaux, alertes = score_project(details)

        niveau = "SOLIDE" if score >= 5 else "MOYEN" if score >= 3 else "RISQUE"

        msg = (
            f"NOUVELLE CRYPTO DETECTEE\n\n"
            f"Nom : {name}\n"
            f"Symbole : {symbol}\n"
            f"Score solidite : {score}/7 — {niveau}\n\n"
        )

        if signaux:
            msg += "Points positifs :\n"
            for s in signaux:
                msg += f"+ {s}\n"
            msg += "\n"

        if alertes:
            msg += "Signaux d alerte :\n"
            for a in alertes:
                msg += f"! {a}\n"
            msg += "\n"

        if details:
            desc = details.get("description", {}).get("en", "")
            if desc:
                msg += f"Description : {desc[:200]}...\n\n"

        msg += "RAPPEL : Nouvelles cryptos = risque tres eleve."
        await update.message.reply_text(msg)
        await asyncio.sleep(1)

async def cmd_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage : /delete ID_POSITION")
        return
    key = ctx.args[0]
    positions = load_positions()
    if key in positions:
        del positions[key]
        save_positions(positions)
        await update.message.reply_text(f"Position {key} supprimee.")
    else:
        await update.message.reply_text(f"Position {key} introuvable.")

async def cmd_prix(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    coins = [c.upper() for c in ctx.args] if ctx.args else ["BTC", "ETH", "SOL"]
    prices = await get_prices(coins)
    if not prices:
        await update.message.reply_text("Impossible de recuperer les prix.")
        return
    msg = "Prix actuels\n\n"
    for coin, info in prices.items():
        msg += f"{coin} : {info['price']:,.2f} EUR ({info['change_24h']:+.1f}% 24h)\n"
    await update.message.reply_text(msg)

async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Screenshot recu ! Analyse en cours...")
    photo = update.message.photo[-1]
    file = await ctx.bot.get_file(photo.file_id)
    async with aiohttp.ClientSession() as session:
        async with session.get(file.file_path) as r:
            image_bytes = await r.read()

    result = await analyze_screenshot(image_bytes)

    if not result or not result.get("coin") or not result.get("amount_eur") or not result.get("entry_price"):
        await update.message.reply_text(
            "Impossible de lire toutes les infos.\n"
            "Utilise la commande manuelle :\n/buy BTC 500 65000"
        )
        return

    coin = result["coin"]
    amount = float(result["amount_eur"])
    price = float(result["entry_price"])
    date = result.get("date")

    key, pos = add_position(coin, amount, price, date)
    await update.message.reply_text(
        f"Position ajoutee automatiquement !\n\n"
        f"Crypto : {coin}\n"
        f"Montant : {amount:.2f} EUR\n"
        f"Prix entree : {price:,.2f} EUR\n"
        f"Quantite : {pos['quantity']:.8f}\n"
        f"Date : {pos['date']}\n"
        f"ID : {key}\n\n"
        f"Surveillance active !"
    )


# ─── Boucles automatiques ─────────────────────────────────────

async def positions_loop(app):
    while True:
        try:
            await check_positions(app)
        except Exception as e:
            logger.error(f"Erreur positions loop: {e}")
        await asyncio.sleep(CHECK_INTERVAL)

async def scanner_loop(app):
    await asyncio.sleep(30)
    while True:
        try:
            gainers = await scan_top_movers()
            if gainers:
                msg = f"RADAR AUTO — {len(gainers)} opportunite(s) detectee(s)\n\n"
                for c in gainers[:3]:
                    msg += f"{c['symbol']} : est. +{c['change_3d_approx']:.1f}% en 3j\n"
                msg += "\nTape /scanner pour les details."
                await app.bot.send_message(chat_id=CHAT_ID, text=msg)
        except Exception as e:
            logger.error(f"Erreur scanner loop: {e}")
        await asyncio.sleep(SCANNER_INTERVAL)

async def new_listings_loop(app):
    await asyncio.sleep(60)
    while True:
        try:
            new_coins = await get_new_listings()
            for coin in new_coins:
                coin_id = coin.get("id")
                name = coin.get("name", "Inconnu")
                symbol = coin.get("symbol", "?").upper()
                details = await get_coin_details(coin_id)
                score, signaux, alertes = score_project(details)
                niveau = "SOLIDE" if score >= 5 else "MOYEN" if score >= 3 else "RISQUE"

                msg = (
                    f"NOUVELLE CRYPTO DETECTEE\n\n"
                    f"Nom : {name} ({symbol})\n"
                    f"Score : {score}/7 — {niveau}\n"
                )
                if alertes:
                    msg += "\nAlertes :\n"
                    for a in alertes[:3]:
                        msg += f"! {a}\n"
                msg += "\nTape /nouveautes pour les details."
                await app.bot.send_message(chat_id=CHAT_ID, text=msg)
                await asyncio.sleep(2)
        except Exception as e:
            logger.error(f"Erreur listings loop: {e}")
        await asyncio.sleep(NEW_LISTINGS_INTERVAL)


# ─── Main ─────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("buy", cmd_buy))
    app.add_handler(CommandHandler("recap", cmd_recap))
    app.add_handler(CommandHandler("positions", cmd_recap))
    app.add_handler(CommandHandler("scanner", cmd_scanner))
    app.add_handler(CommandHandler("nouveautes", cmd_nouveautes))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(CommandHandler("prix", cmd_prix))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    async def on_startup(app):
        asyncio.create_task(positions_loop(app))
        asyncio.create_task(scanner_loop(app))
        asyncio.create_task(new_listings_loop(app))

    app.post_init = on_startup

    logger.info("Bot demarre — positions + scanner + nouvelles listings + screenshot actifs")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
