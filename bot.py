"""
🌸 Tinkerbells — Bot Telegram E-commerce Cosmétiques Algérie
=============================================================
Installation : pip install python-telegram-bot openai pymongo
"""

import logging
import json
from bson import ObjectId
from telegram import Update, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from openai import OpenAI
from pymongo import MongoClient
from datetime import datetime

# ─────────────────────────────────────────
# 🔧 CONFIGURATION
# ─────────────────────────────────────────

TELEGRAM_TOKEN   = "8798994407:AAHg8H32FbWegSWVB2j9A7EUOfnLKp3V9rM"        # 👉 Token du bot client
DEEPSEEK_API_KEY = "sk-4b34a821f0164341a641155011e9b05d"         # 👉 Clé DeepSeek
ADMIN_BOT_TOKEN  = "8720072160:AAE7A7v6vOAV3ZbaHdBncuI1rVr6m3pHVL8"         # 👉 Token du bot admin
ADMIN_CHAT_ID    = "5009172498"           # 👉 Ton chat ID Telegram

MONGO_URI = "mongodb+srv://merahlwos_db_user:CytBm67mupWzabhy@cluster0.lpbytcq.mongodb.net/?appName=Cluster0"

# ─────────────────────────────────────────
# 🚀 INITIALISATION
# ─────────────────────────────────────────

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

ai_client    = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
mongo        = MongoClient(MONGO_URI)
db           = mongo["test"]
products_col = db["products"]
orders_col   = db["orders"]

CHAT, GET_PRENOM, GET_NOM, GET_PHONE, GET_WILAYA, GET_COMMUNE, CONFIRM_ORDER = range(7)

# ─────────────────────────────────────────
# 🛍️ CATALOGUE
# ─────────────────────────────────────────

def fetch_catalog() -> list:
    products = list(products_col.find(
        {"$or": [{"stock": {"$gt": 0}}, {"sizes.stock": {"$gt": 0}}]},
        {"name": 1, "brand": 1, "category": 1, "price": 1, "stock": 1, "sizes": 1, "description": 1}
    ))
    for p in products:
        p["_id"] = str(p["_id"])
    logger.info(f"✅ Catalogue : {len(products)} produits")
    return products

def format_catalog(products: list) -> str:
    lines = []
    for p in products:
        stock = p.get("stock", 0) + sum(s.get("stock", 0) for s in p.get("sizes", []))
        if stock <= 0:
            continue
        desc = (p.get("description") or {})
        desc_text = desc.get("fr") or desc.get("en") or desc.get("ar") or ""
        line = f"- NOM: {p['name']} | MARQUE: {p.get('brand','')} | CATÉGORIE: {p.get('category','')} | PRIX: {p.get('price','?')} DA"
        if desc_text:
            line += f" | DESC: {desc_text}"
        lines.append(line)
    return "\n".join(lines) or "Aucun produit disponible."

def find_product(catalog: list, name: str) -> dict | None:
    name_l = name.lower().strip()
    for p in catalog:
        if p.get("name","").lower().strip() == name_l:
            return p
    for p in catalog:
        if name_l in p.get("name","").lower() or p.get("name","").lower() in name_l:
            return p
    words = set(name_l.split())
    best, best_score = None, 0
    for p in catalog:
        score = len(words & set(p.get("name","").lower().split()))
        if score > best_score:
            best_score, best = score, p
    return best if best_score >= 2 else None

# ─────────────────────────────────────────
# 🤖 PROMPT DEEPSEEK — RETOURNE DU JSON
# ─────────────────────────────────────────

def build_system_prompt(products: list) -> str:
    return f"""Tu es Mina 🌸, conseillère beauté de Tinkerbells, une marque de cosmétiques algérienne.

Ta personnalité :
- Tu es ultra girly, douce, chaleureuse et pétillante 💕✨
- Tu parles comme une vraie copine algérienne qui adore la beauté
- Tu utilises des emojis avec naturel 🌸💄✨🥰💅
- Tu complimentes toujours le client sincèrement ("Oh ce choix est trop bien pour toi !")
- Tu détectes automatiquement la langue du client et tu réponds TOUJOURS dans la même langue
- Si le client écrit en arabe classique → tu réponds en arabe classique
- Si le client écrit en français → tu réponds en français
- Si le client écrit en anglais → tu réponds en anglais
- Si le client écrit en darija et que tu n'es pas sûre de comprendre, réponds en français :
  "Désolée ma belle, je comprends mieux le français, l'anglais ou l'arabe classique 😊 Tu préfères quelle langue ? 🌸"
- EXCEPTION : les questions du formulaire (prénom, nom, téléphone, wilaya, commune) sont TOUJOURS en français, peu importe la langue du client
- Tu es enthousiaste et positive dans CHAQUE message

RÈGLE ABSOLUE : Tu réponds UNIQUEMENT en JSON valide, rien d'autre. Format strict :
{{
  "message": "ton message au client",
  "action": "CHAT" | "COMMANDER" | "DEMANDER_CONFIRMATION",
  "produit_nom": "nom exact du produit si action=COMMANDER ou DEMANDER_CONFIRMATION, sinon null",
  "produit_prix": prix en nombre si action=COMMANDER ou DEMANDER_CONFIRMATION, sinon null
}}

═══ LOGIQUE DES ACTIONS ═══

"CHAT" → pour conseiller, poser des questions, présenter des produits.
  - Pour les soins cheveux : pose 1-2 questions avant de recommander (cheveux secs/gras/abîmés ? problème principal ?)
  - Pour la peau : demande le type de peau si pas mentionné
  - Mentionne TOUJOURS la marque ET le nom exact du produit

"DEMANDER_CONFIRMATION" → uniquement quand tu as recommandé un produit et que le client semble intéressé mais n'a pas encore dit qu'il veut l'acheter.
  - Ton message doit demander clairement s'il veut commander.

"COMMANDER" → quand le client exprime clairement l'intention d'acheter.
  EXEMPLES qui déclenchent COMMANDER :
  - "je le veux", "je la veux", "je veux ça", "je veux commander"
  - "oui" / "ok" / "go" / "parfait" EN RÉPONSE à ta question de confirmation
  - "j'achète", "je prends", "commande"
  - En darija : "wah", "ah ewa", "hia", "bghitha"
  ⚠️ Si le client dit OUI après que tu aies posé une question de confirmation → c'est COMMANDER, PAS DEMANDER_CONFIRMATION

═══ RÈGLES ABSOLUES ═══
- Ne propose QUE des produits du catalogue
- NE demande JAMAIS nom, prénom, téléphone, adresse — le système s'en charge automatiquement
- NE fais JAMAIS de récapitulatif de commande

🌸 Catalogue :
{format_catalog(products)}
"""

# ─────────────────────────────────────────
# 📩 COMMANDES
# ─────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["catalog"] = fetch_catalog()
    context.user_data["history"] = []
    await update.message.reply_text(
        "🌸 Bienvenue chez Tinkerbells !\n\nJe suis Mina, votre conseillère beauté 💄\nComment puis-je vous aider ?",
        reply_markup=ReplyKeyboardRemove()
    )
    return CHAT

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("🔄 Conversation réinitialisée ! Envoyez /start.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# ─────────────────────────────────────────
# 💬 CONVERSATION — DEEPSEEK DÉCIDE TOUT
# ─────────────────────────────────────────

async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    catalog = context.user_data.get("catalog", [])
    history = context.user_data.get("history", [])
    history.append({"role": "user", "content": user_text})

    try:
        response = ai_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": build_system_prompt(catalog)},
                *history[-20:]
            ],
            response_format={"type": "json_object"}  # force le JSON
        )
        raw = response.choices[0].message.content

        # Nettoyage robuste du JSON — retire les backticks et espaces parasites
        clean = raw.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        clean = clean.strip()

        try:
            data = json.loads(clean)
        except json.JSONDecodeError:
            # Tentative de réparation : extrait les champs à la main
            import re
            message_match = re.search(r'"message"\s*:\s*"(.*?)"(?=\s*,\s*"action")', clean, re.DOTALL)
            action_match  = re.search(r'"action"\s*:\s*"(\w+)"', clean)
            nom_match     = re.search(r'"produit_nom"\s*:\s*"(.*?)"', clean)
            prix_match    = re.search(r'"produit_prix"\s*:\s*([0-9.]+)', clean)
            data = {
                "message":     message_match.group(1) if message_match else "Je suis là pour t'aider 🌸",
                "action":      action_match.group(1)  if action_match  else "CHAT",
                "produit_nom": nom_match.group(1)     if nom_match     else None,
                "produit_prix":float(prix_match.group(1)) if prix_match else None,
            }

        message  = data.get("message", "")
        action   = data.get("action", "CHAT")
        prod_nom = data.get("produit_nom")
        prod_prix = data.get("produit_prix")

        # Si un produit était déjà en attente de confirmation et que DeepSeek
        # retourne encore DEMANDER_CONFIRMATION → on force COMMANDER
        if action == "DEMANDER_CONFIRMATION" and context.user_data.get("produit_id") and prod_nom:
            action = "COMMANDER"

        history.append({"role": "assistant", "content": raw})
        context.user_data["history"] = history

        # Envoie le message de Sarah
        try:
            await update.message.reply_text(message, parse_mode="Markdown")
        except Exception:
            await update.message.reply_text(message)

        if action == "COMMANDER" and prod_nom:
            # Cherche le produit et passe directement au formulaire
            produit = find_product(catalog, prod_nom)
            if produit:
                context.user_data["produit_id"]    = produit["_id"]
                context.user_data["produit_nom"]   = produit["name"]
                context.user_data["produit_prix"]  = produit.get("price", prod_prix or 0)
                context.user_data["produit_brand"] = produit.get("brand", "")
                logger.info(f"✅ Commande : {produit['name']}")
            else:
                context.user_data["produit_id"]    = None
                context.user_data["produit_nom"]   = prod_nom
                context.user_data["produit_prix"]  = prod_prix or 0
                context.user_data["produit_brand"] = ""

            await update.message.reply_text("Parfait ! 📝 Ton prénom ? 👤")
            return GET_PRENOM

        elif action == "DEMANDER_CONFIRMATION" and prod_nom:
            # Sarah a posé la question, on mémorise le produit en attendant
            produit = find_product(catalog, prod_nom)
            if produit:
                context.user_data["produit_id"]    = produit["_id"]
                context.user_data["produit_nom"]   = produit["name"]
                context.user_data["produit_prix"]  = produit.get("price", prod_prix or 0)
                context.user_data["produit_brand"] = produit.get("brand", "")

        return CHAT

    except Exception as e:
        logger.error(f"Erreur : {e}")
        await update.message.reply_text("⚠️ Une erreur s'est produite, réessaie.")
        return CHAT

# ─────────────────────────────────────────
# 📦 FORMULAIRE DE COMMANDE
# ─────────────────────────────────────────

async def get_prenom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["prenom"] = update.message.text.strip()
    await update.message.reply_text("Ton nom ? 👤")
    return GET_NOM

async def get_nom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["nom"] = update.message.text.strip()
    await update.message.reply_text("Ton numéro de téléphone ? 📱")
    return GET_PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["phone"] = update.message.text.strip()
    await update.message.reply_text("Ta wilaya ? 🗺️")
    return GET_WILAYA

async def get_wilaya(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["wilaya"] = update.message.text.strip()
    await update.message.reply_text("Ta commune ? 🏘️")
    return GET_COMMUNE

async def get_commune(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["commune"] = update.message.text.strip()
    d = context.user_data
    recap = (
        f"📋 Récapitulatif de ta commande :\n\n"
        f"🌸 Produit : {d.get('produit_nom')}\n"
        f"🏷️ Marque : {d.get('produit_brand')}\n"
        f"💰 Prix : {d.get('produit_prix')} DA\n\n"
        f"👤 Prénom : {d.get('prenom')}\n"
        f"👤 Nom : {d.get('nom')}\n"
        f"📱 Téléphone : {d.get('phone')}\n"
        f"🗺️ Wilaya : {d.get('wilaya')}\n"
        f"🏘️ Commune : {d.get('commune')}\n\n"
        f"Tape CONFIRMER pour valider ou ANNULER pour annuler."
    )
    await update.message.reply_text(recap)
    return CONFIRM_ORDER

async def confirm_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # DeepSeek analyse si le client confirme ou annule
    user_text = update.message.text
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        check = ai_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": 'Réponds uniquement en JSON: {"confirmed": true} si le message confirme une commande, {"confirmed": false} sinon.'},
                {"role": "user", "content": user_text}
            ],
            response_format={"type": "json_object"}
        )
        result = json.loads(check.choices[0].message.content)
        confirmed = result.get("confirmed", False)
    except Exception:
        confirmed = False

    d = context.user_data
    if confirmed:
        prix        = d.get("produit_prix", 0)
        nom_produit = d.get("produit_nom", "Produit")
        produit_id  = d.get("produit_id")
        brand       = d.get("produit_brand", "")

        try:
            order_doc = {
                "customerInfo": {
                    "firstName": d.get("prenom"),
                    "lastName":  d.get("nom"),
                    "phone":     d.get("phone"),
                    "wilaya":    d.get("wilaya"),
                    "commune":   d.get("commune"),
                },
                "items": [{"product": ObjectId(produit_id) if produit_id else None,
                           "name": nom_produit, "quantity": 1, "price": prix}],
                "total": prix, "deliveryFee": 0,
                "deliveryType": "home", "deliverySpeed": "express",
                "status": "en attente", "source": "telegram",
                "createdAt": datetime.utcnow(), "updatedAt": datetime.utcnow(),
            }
            result = orders_col.insert_one(order_doc)
            if produit_id:
                products_col.update_one({"_id": ObjectId(produit_id)}, {"$inc": {"stock": -1, "purchaseCount": 1}})
            logger.info(f"✅ Commande sauvegardée : {result.inserted_id}")
        except Exception as e:
            logger.error(f"Erreur MongoDB : {e}")

        try:
            from telegram import Bot
            admin_bot = Bot(token=ADMIN_BOT_TOKEN)
            now = datetime.now().strftime("%d/%m/%Y %H:%M")
            await admin_bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=(f"🛍️ *NOUVELLE COMMANDE TINKERBELLS*\n📅 {now}\n\n"
                      f"🌸 *Produit :* {nom_produit}\n🏷️ *Marque :* {brand}\n💰 *Prix :* {prix} DA\n\n"
                      f"👤 *Prénom :* {d.get('prenom')}\n👤 *Nom :* {d.get('nom')}\n"
                      f"📱 *Téléphone :* {d.get('phone')}\n🗺️ *Wilaya :* {d.get('wilaya')}\n"
                      f"🏘️ *Commune :* {d.get('commune')}"),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Erreur admin : {e}")

        await update.message.reply_text(
            "🎉 Commande confirmée ! Merci pour ta confiance 🌸\n"
            "Notre équipe te contactera très bientôt pour la livraison.\n\n"
            "Tinkerbells — La beauté à votre portée ✨"
        )
    else:
        await update.message.reply_text("❌ Commande annulée. Tu peux continuer à magasiner 🌸")

    # Repart en mode chat
    catalog = fetch_catalog()
    context.user_data.clear()
    context.user_data["catalog"] = catalog
    context.user_data["history"] = []
    return CHAT

# ─────────────────────────────────────────
# ▶️  LANCEMENT
# ─────────────────────────────────────────

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        allow_reentry=True,
        states={
            CHAT:          [MessageHandler(filters.TEXT & ~filters.COMMAND, chat)],
            GET_PRENOM:    [MessageHandler(filters.TEXT & ~filters.COMMAND, get_prenom)],
            GET_NOM:       [MessageHandler(filters.TEXT & ~filters.COMMAND, get_nom)],
            GET_PHONE:     [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
            GET_WILAYA:    [MessageHandler(filters.TEXT & ~filters.COMMAND, get_wilaya)],
            GET_COMMUNE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, get_commune)],
            CONFIRM_ORDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_order)],
        },
        fallbacks=[CommandHandler("reset", reset)],
    )
    app.add_handler(conv)
    logger.info("✅ Bot Tinkerbells démarré")
    app.run_polling()

if __name__ == "__main__":
    main()