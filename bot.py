"""
cvkcosmetique — Bot Telegram E-commerce Cosmétiques Algérie
Installation : pip install python-telegram-bot openai pymongo
"""

import logging
import json
import re
from bson import ObjectId
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
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
TELEGRAM_TOKEN   = "8798994407:AAHg8H32FbWegSWVB2j9A7EUOfnLKp3V9rM"
DEEPSEEK_API_KEY = "sk-4b34a821f0164341a641155011e9b05d"
ADMIN_BOT_TOKEN  = "8720072160:AAE7A7v6vOAV3ZbaHdBncuI1rVr6m3pHVL8"
ADMIN_CHAT_ID    = "5009172498"
MONGO_URI        = "mongodb+srv://merahlwos_db_user:CytBm67mupWzabhy@cluster0.lpbytcq.mongodb.net/?appName=Cluster0"

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

(
    CHAT,
    ADD_MORE,
    GET_PRENOM,
    GET_NOM,
    GET_PHONE,
    GET_WILAYA,
    GET_COMMUNE,
    VERIFY_INFO,
    CORRECT_FIELD,
    CONFIRM_ORDER,
) = range(10)

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
    logger.info(f"Catalogue : {len(products)} produits")
    return products

def format_catalog(products: list) -> str:
    lines = []
    for p in products:
        stock = p.get("stock", 0) + sum(s.get("stock", 0) for s in p.get("sizes", []))
        if stock <= 0:
            continue
        desc = (p.get("description") or {})
        desc_text = desc.get("fr") or desc.get("en") or desc.get("ar") or ""
        line = f"- NOM: {p['name']} | MARQUE: {p.get('brand','')} | CATEGORIE: {p.get('category','')} | PRIX: {p.get('price','?')} DA"
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

def format_panier(panier: list) -> str:
    if not panier:
        return "Panier vide"
    lines = []
    total = 0
    for item in panier:
        lines.append(f"• {item['nom']} ({item['brand']}) — {item['prix']} DA")
        total += item['prix']
    lines.append(f"\n💰 Total : {total} DA")
    return "\n".join(lines)

# ─────────────────────────────────────────
# 🌍 DÉTECTION DE LANGUE
# ─────────────────────────────────────────

def detect_language(text: str, current_lang: str = "fr") -> str:
    """
    Détecte la langue dominante du message.
    Si le client mélange arabe + autre langue → arabe prioritaire.
    Retourne un code parmi : 'ar', 'fr', 'en', 'darija'
    """
    try:
        result = ai_json(
            system="""Analyse la langue utilisée dans le message et réponds UNIQUEMENT en JSON :
{
  "lang": "ar" | "fr" | "en" | "darija",
  "has_arabic": true | false
}
Règles :
- Si le message contient des mots arabes (alphabet arabe) → has_arabic: true
- Si le message mélange arabe + français ou anglais → lang: "ar" (arabe prioritaire)
- "darija" = dialecte algérien/maghrébin sans alphabet arabe (translittéré en latin)
- Si tu n'es pas sûr → garde la langue actuelle""",
            user=f"Message: {text}\nLangue actuelle: {current_lang}"
        )
        detected = result.get("lang", current_lang)
        has_arabic = result.get("has_arabic", False)
        if has_arabic:
            return "ar"
        return detected if detected in ("ar", "fr", "en", "darija") else current_lang
    except Exception:
        return current_lang

LANG_INSTRUCTION = {
    "fr":     "Réponds en français naturel et chaleureux.",
    "ar":     "أجب باللغة العربية الفصحى بشكل طبيعي وودود.",
    "en":     "Reply in natural, warm English.",
    "darija": "Réponds en darija algérienne (translittéré en latin, ex: 'wach', 'mezian', 'bghit'). Reste naturel et sympa.",
}

def lang_rule(lang: str) -> str:
    return LANG_INSTRUCTION.get(lang, LANG_INSTRUCTION["fr"])

# ─────────────────────────────────────────
# 🤖 HELPERS IA
# ─────────────────────────────────────────

def ai_json(system: str, user: str) -> dict:
    try:
        resp = ai_client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            max_tokens=400,
        )
        return json.loads(resp.choices[0].message.content)
    except Exception as e:
        logger.error(f"ai_json error: {e}")
        return {}

def ai_text(system: str, user: str, lang: str = "fr") -> str:
    """Retourne le texte du champ 'message' dans la bonne langue."""
    full_system = f"{system}\n\nREGLE LANGUE : {lang_rule(lang)}\nRéponds UNIQUEMENT en JSON avec le champ 'message'."
    try:
        resp = ai_client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "system", "content": full_system}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            max_tokens=500,
        )
        data = json.loads(resp.choices[0].message.content)
        return data.get("message", "")
    except Exception as e:
        logger.error(f"ai_text error: {e}")
        return ""

BASE_PERSONA = (
    "Tu es Mehdi 🌸, conseiller beauté de cvkcosmetique, une marque de cosmétiques algérienne. "
    "Tu es chaleureux, naturel, enthousiaste comme un vrai vendeur humain. Tu utilises des emojis avec naturel. "
    "Tu ne te répètes jamais, tu ne salues pas à chaque message. Tu adaptes ton ton au contexte."
)

# ─────────────────────────────────────────
# 🤖 PROMPT PRINCIPAL CHAT
# ─────────────────────────────────────────

def build_system_prompt(products: list, lang: str) -> str:
    return f"""Tu es Mehdi 🌸, conseiller beauté de cvkcosmetique, une marque de cosmétiques algérienne.

RÈGLE LANGUE ABSOLUE :
- Tu détectes la langue de CHAQUE message du client et tu réponds TOUJOURS dans cette même langue
- Si le client mélange arabe + français/anglais → tu réponds en arabe (priorité arabe)
- Français → français | Anglais → anglais | Arabe → arabe | Darija → darija
- Langue actuelle détectée du client : {lang}
- {lang_rule(lang)}
- Tu ne changes JAMAIS de langue de ta propre initiative

Ta personnalité :
- Tu es comme un vrai vendeur humain : naturel, chaleureux, enthousiaste 💕✨
- Tu utilises des emojis avec naturel 🌸💄✨🥰💅
- Tu complimentes le client sincèrement
- Tu ne dis jamais "bonjour" ou "bonsoir" à chaque message — seulement au tout premier échange
- Tu enchaînes naturellement sans formules répétitives

REGLE ABSOLUE : Tu réponds UNIQUEMENT en JSON valide :
{{
  "message": "ton message",
  "action": "CHAT" | "COMMANDER" | "DEMANDER_CONFIRMATION",
  "produit_nom": "nom exact ou null",
  "produit_prix": nombre ou null
}}

LOGIQUE DES ACTIONS :
"CHAT" → conseiller, poser des questions, présenter des produits
"DEMANDER_CONFIRMATION" → client semble intéressé mais hésitant
"COMMANDER" → client veut clairement acheter ("je le veux", "oui", "ok", "bghitha", "wah", "أريده", "I want it")
  → Si le client dit OUI après une confirmation → COMMANDER obligatoire

REGLES :
- Ne propose QUE des produits du catalogue
- NE demande JAMAIS infos personnelles (nom, tel, adresse) — le système s'en charge
- NE fais JAMAIS de récapitulatif de commande dans ce state

🌸 Catalogue :
{format_catalog(products)}
"""

# ─────────────────────────────────────────
# 📩 DÉMARRAGE
# ─────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    catalog = fetch_catalog()
    context.user_data["catalog"] = catalog
    context.user_data["history"] = []
    context.user_data["panier"]  = []
    context.user_data["lang"]    = "fr"  # défaut

    msg = ai_text(
        system=BASE_PERSONA,
        user="[SYSTEM] Premier message. Le client ouvre le bot pour la première fois. Génère UN message de bienvenue chaleureux et naturel, présente-toi comme Mehdi de cvkcosmetique et propose ton aide.",
        lang="fr"
    )
    await update.message.reply_text(msg or "🌸 Bienvenue chez cvkcosmetique !", reply_markup=ReplyKeyboardRemove())
    return CHAT

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = context.user_data.get("lang", "fr")
    context.user_data.clear()
    msg = ai_text(
        system=BASE_PERSONA,
        user="[SYSTEM] Conversation réinitialisée. Génère un court message et dis d'envoyer /start.",
        lang=lang
    )
    await update.message.reply_text(msg or "🔄 Réinitialisé ! Envoie /start.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# ─────────────────────────────────────────
# 💬 CONVERSATION PRINCIPALE
# ─────────────────────────────────────────

def parse_ai_response(raw: str) -> dict:
    clean = raw.strip()
    if clean.startswith("```"):
        clean = clean.split("```")[1]
        if clean.startswith("json"):
            clean = clean[4:]
    clean = clean.strip()
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        message_match = re.search(r'"message"\s*:\s*"(.*?)"(?=\s*,\s*"action")', clean, re.DOTALL)
        action_match  = re.search(r'"action"\s*:\s*"(\w+)"', clean)
        nom_match     = re.search(r'"produit_nom"\s*:\s*"(.*?)"', clean)
        prix_match    = re.search(r'"produit_prix"\s*:\s*([0-9.]+)', clean)
        return {
            "message":      message_match.group(1) if message_match else "",
            "action":       action_match.group(1)  if action_match  else "CHAT",
            "produit_nom":  nom_match.group(1)     if nom_match     else None,
            "produit_prix": float(prix_match.group(1)) if prix_match else None,
        }

async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    # Détection et mise à jour de la langue
    current_lang = context.user_data.get("lang", "fr")
    lang = detect_language(user_text, current_lang)
    context.user_data["lang"] = lang

    catalog = context.user_data.get("catalog", [])
    history = context.user_data.get("history", [])
    history.append({"role": "user", "content": user_text})

    try:
        response = ai_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": build_system_prompt(catalog, lang)},
                *history[-20:]
            ],
            response_format={"type": "json_object"}
        )
        raw  = response.choices[0].message.content
        data = parse_ai_response(raw)

        message   = data.get("message", "")
        action    = data.get("action", "CHAT")
        prod_nom  = data.get("produit_nom")
        prod_prix = data.get("produit_prix")

        if action == "DEMANDER_CONFIRMATION" and context.user_data.get("produit_en_attente") and prod_nom:
            action = "COMMANDER"

        history.append({"role": "assistant", "content": raw})
        context.user_data["history"] = history

        try:
            await update.message.reply_text(message, parse_mode="Markdown")
        except Exception:
            await update.message.reply_text(message)

        if action == "COMMANDER" and prod_nom:
            produit = find_product(catalog, prod_nom)
            if produit:
                item = {
                    "id":    produit["_id"],
                    "nom":   produit["name"],
                    "brand": produit.get("brand", ""),
                    "prix":  produit.get("price", prod_prix or 0),
                }
            else:
                item = {"id": None, "nom": prod_nom, "brand": "", "prix": prod_prix or 0}

            panier = context.user_data.get("panier", [])
            panier.append(item)
            context.user_data["panier"] = panier
            context.user_data["produit_en_attente"] = None
            logger.info(f"Panier : {[p['nom'] for p in panier]}")

            panier_txt = format_panier(panier)
            add_msg = ai_text(
                system=BASE_PERSONA,
                user=f"[SYSTEM] Produit ajouté au panier. Panier:\n{panier_txt}\nConfirme l'ajout avec enthousiasme, affiche le panier, et demande naturellement si le client veut ajouter autre chose ou finaliser.",
                lang=lang
            )
            keyboard = [["✅ Je finalise"], ["🛍️ J'ajoute autre chose"]]
            try:
                await update.message.reply_text(
                    add_msg or panier_txt,
                    parse_mode="Markdown",
                    reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
                )
            except Exception:
                await update.message.reply_text(
                    add_msg or panier_txt,
                    reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
                )
            return ADD_MORE

        elif action == "DEMANDER_CONFIRMATION" and prod_nom:
            produit = find_product(catalog, prod_nom)
            if produit:
                context.user_data["produit_en_attente"] = {
                    "id":    produit["_id"],
                    "nom":   produit["name"],
                    "brand": produit.get("brand", ""),
                    "prix":  produit.get("price", prod_prix or 0),
                }

        return CHAT

    except Exception as e:
        logger.error(f"Erreur chat : {e}")
        err_msg = ai_text(
            system=BASE_PERSONA,
            user="[SYSTEM] Erreur technique. Court message d'excuse, demande de réessayer.",
            lang=lang
        )
        await update.message.reply_text(err_msg or "⚠️ Une erreur s'est produite, réessaie.")
        return CHAT

async def add_more(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    lang = context.user_data.get("lang", "fr")
    lang = detect_language(user_text, lang)
    context.user_data["lang"] = lang

    result = ai_json(
        system=f'Réponds uniquement en JSON: {{"add_more": true}} si la personne veut ajouter autre chose à son panier, {{"add_more": false}} si elle veut finaliser/passer commande. Le message peut être en français, arabe, anglais ou darija.',
        user=user_text
    )
    add_more_flag = result.get("add_more", False)

    if add_more_flag:
        msg = ai_text(
            system=BASE_PERSONA,
            user="[SYSTEM] Le client veut ajouter un autre produit. Réponds de façon courte et naturelle pour l'inviter à continuer ses achats.",
            lang=lang
        )
        await update.message.reply_text(msg or "Bien sûr ! Qu'est-ce que tu veux ajouter ? 🌸", reply_markup=ReplyKeyboardRemove())
        return CHAT
    else:
        msg = ai_text(
            system=BASE_PERSONA,
            user="[SYSTEM] Le client finalise. Commence le formulaire de livraison de façon naturelle (comme un vrai vendeur). Demande son prénom SANS dire bonjour ni faire de longue intro. N'utilise JAMAIS des formules comme 'Pour plus de conseils' ou 'Pour mieux vous aider' — va directement à la question.",
            lang=lang
        )
        await update.message.reply_text(msg or "Parfait ! Ton prénom ?", reply_markup=ReplyKeyboardRemove())
        return GET_PRENOM

# ─────────────────────────────────────────
# 📦 FORMULAIRE — fluide comme un vrai vendeur
# ─────────────────────────────────────────

async def get_prenom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = context.user_data.get("lang", "fr")
    context.user_data["prenom"] = update.message.text.strip()
    msg = ai_text(
        system=BASE_PERSONA,
        user=f"[SYSTEM] Prénom reçu : {context.user_data['prenom']}. Enchaîne naturellement et demande le nom de famille. Pas de 'bonjour', pas de répétition, juste fluide. Dis simplement quelque chose comme 'Maintenant ton nom de famille ?' — JAMAIS de formules comme 'Pour plus de conseils' ou 'Pour mieux vous aider'.",
        lang=lang
    )
    await update.message.reply_text(msg or "Et ton nom ?")
    return GET_NOM

async def get_nom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = context.user_data.get("lang", "fr")
    context.user_data["nom"] = update.message.text.strip()
    msg = ai_text(
        system=BASE_PERSONA,
        user=f"[SYSTEM] Nom reçu : {context.user_data['nom']}. Enchaîne et demande le numéro de téléphone. Court et naturel. Dis quelque chose comme 'Et ton numéro de téléphone ?' — JAMAIS de formules comme 'Pour plus de conseils' ou 'Pour mieux vous aider'.",
        lang=lang
    )
    await update.message.reply_text(msg or "Ton numéro de téléphone ? 📱")
    return GET_PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = context.user_data.get("lang", "fr")
    context.user_data["phone"] = update.message.text.strip()
    msg = ai_text(
        system=BASE_PERSONA,
        user=f"[SYSTEM] Téléphone reçu : {context.user_data['phone']}. Demande la wilaya. Court et naturel. Dis quelque chose comme 'Ta wilaya ?' ou 'On passe à ta wilaya !' — JAMAIS de formules comme 'Pour plus de conseils' ou 'Pour mieux vous aider'.",
        lang=lang
    )
    await update.message.reply_text(msg or "Ta wilaya ? 🗺️")
    return GET_WILAYA

async def get_wilaya(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = context.user_data.get("lang", "fr")
    context.user_data["wilaya"] = update.message.text.strip()
    msg = ai_text(
        system=BASE_PERSONA,
        user=f"[SYSTEM] Wilaya reçue : {context.user_data['wilaya']}. Demande la commune. Court et naturel. Dis quelque chose comme 'Et ta commune ?' ou 'J'ai besoin de ta commune aussi !' — JAMAIS de formules comme 'Pour plus de conseils' ou 'Pour mieux vous aider'.",
        lang=lang
    )
    await update.message.reply_text(msg or "Ta commune ? 🏘️")
    return GET_COMMUNE

async def get_commune(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = context.user_data.get("lang", "fr")
    context.user_data["commune"] = update.message.text.strip()

    # Passe à la vérification des infos
    return await show_info_recap(update, context, first_time=True)

# ─────────────────────────────────────────
# ✅ VÉRIFICATION DES INFOS
# ─────────────────────────────────────────

async def show_info_recap(update: Update, context: ContextTypes.DEFAULT_TYPE, first_time: bool = True):
    """Affiche le récap et demande si tout est correct."""
    lang  = context.user_data.get("lang", "fr")
    d     = context.user_data
    panier = d.get("panier", [])
    total  = sum(item["prix"] for item in panier)
    panier_txt = format_panier(panier)

    info_raw = (
        f"Prénom: {d.get('prenom', '—')}\n"
        f"Nom: {d.get('nom', '—')}\n"
        f"Téléphone: {d.get('phone', '—')}\n"
        f"Wilaya: {d.get('wilaya', '—')}\n"
        f"Commune: {d.get('commune', '—')}\n"
        f"Panier: {panier_txt}\n"
        f"Total: {total} DA"
    )

    if first_time:
        prompt = f"[SYSTEM] Le client vient de remplir tout le formulaire. Voici ses infos :\n{info_raw}\nAffiche-lui un récapitulatif clair et naturel de ses infos ET sa commande, puis demande-lui si tout est correct ou s'il veut corriger quelque chose. Sois naturel comme un vrai vendeur, pas robotique."
    else:
        prompt = f"[SYSTEM] Le client a corrigé une information. Nouvelles infos :\n{info_raw}\nAffiche le récapitulatif mis à jour et redemande si tout est maintenant correct."

    msg = ai_text(system=BASE_PERSONA, user=prompt, lang=lang)
    try:
        await update.message.reply_text(msg or info_raw, parse_mode="Markdown")
    except Exception:
        await update.message.reply_text(msg or info_raw)
    return VERIFY_INFO

async def verify_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Le client répond si ses infos sont correctes ou non."""
    user_text = update.message.text
    lang = context.user_data.get("lang", "fr")
    lang = detect_language(user_text, lang)
    context.user_data["lang"] = lang
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    d = context.user_data
    info_raw = (
        f"Prénom: {d.get('prenom', '—')}, Nom: {d.get('nom', '—')}, "
        f"Téléphone: {d.get('phone', '—')}, Wilaya: {d.get('wilaya', '—')}, Commune: {d.get('commune', '—')}"
    )

    result = ai_json(
        system=f"""Analyse la réponse du client concernant ses informations de livraison.
Réponds UNIQUEMENT en JSON :
{{
  "is_correct": true | false,
  "field_to_correct": "prenom" | "nom" | "phone" | "wilaya" | "commune" | null,
  "new_value": "nouvelle valeur si le client l'a donnée directement, sinon null"
}}
- is_correct: true si le client confirme que tout est bon
- field_to_correct: le champ qu'il veut changer (si mentionné)
- new_value: si le client donne directement la correction dans son message
Infos actuelles : {info_raw}""",
        user=user_text
    )

    is_correct     = result.get("is_correct", False)
    field_to_fix   = result.get("field_to_correct")
    new_value      = result.get("new_value")

    if is_correct:
        # Tout est bon → passe à la confirmation finale
        panier    = d.get("panier", [])
        total     = sum(item["prix"] for item in panier)
        panier_txt = format_panier(panier)
        msg = ai_text(
            system=BASE_PERSONA,
            user=f"[SYSTEM] Les infos sont correctes. Demande maintenant au client de confirmer définitivement sa commande (total: {total} DA, panier: {panier_txt}). Sois enthousiaste et naturel.",
            lang=lang
        )
        try:
            await update.message.reply_text(msg or "Super ! Tu confirmes ta commande ?", parse_mode="Markdown")
        except Exception:
            await update.message.reply_text(msg or "Super ! Tu confirmes ta commande ?")
        return CONFIRM_ORDER

    elif field_to_fix and new_value:
        # Le client a donné directement la correction
        d[field_to_fix] = new_value.strip()
        context.user_data.update(d)
        return await show_info_recap(update, context, first_time=False)

    elif field_to_fix:
        # Le client veut corriger un champ mais n'a pas donné la valeur
        context.user_data["field_to_fix"] = field_to_fix
        field_labels = {
            "prenom": "prénom", "nom": "nom de famille",
            "phone": "numéro de téléphone", "wilaya": "wilaya", "commune": "commune"
        }
        label = field_labels.get(field_to_fix, field_to_fix)
        msg = ai_text(
            system=BASE_PERSONA,
            user=f"[SYSTEM] Le client veut corriger son {label}. Demande-lui la bonne valeur de façon naturelle et directe.",
            lang=lang
        )
        await update.message.reply_text(msg or f"Donne-moi ton {label} correct :")
        return CORRECT_FIELD

    else:
        # Réponse ambiguë → reformule
        msg = ai_text(
            system=BASE_PERSONA,
            user=f"[SYSTEM] Le client a répondu de façon ambiguë. Infos actuelles : {info_raw}. Redemande-lui simplement si tout est correct ou s'il veut changer quelque chose, de façon naturelle.",
            lang=lang
        )
        await update.message.reply_text(msg or "Est-ce que tout est correct ?")
        return VERIFY_INFO

async def correct_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Le client fournit la valeur corrigée d'un champ."""
    user_text  = update.message.text.strip()
    lang       = context.user_data.get("lang", "fr")
    field      = context.user_data.pop("field_to_fix", None)

    if field:
        context.user_data[field] = user_text

    return await show_info_recap(update, context, first_time=False)

# ─────────────────────────────────────────
# ✅ CONFIRMATION FINALE
# ─────────────────────────────────────────

async def confirm_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    lang = context.user_data.get("lang", "fr")
    lang = detect_language(user_text, lang)
    context.user_data["lang"] = lang
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    d      = context.user_data
    panier = d.get("panier", [])

    result    = ai_json(
        system='Réponds UNIQUEMENT en JSON: {"confirmed": true} si le message confirme/valide la commande, {"confirmed": false} si le client annule ou dit non. Le message peut être en n\'importe quelle langue.',
        user=user_text
    )
    confirmed = result.get("confirmed", False)

    if confirmed and panier:
        total = sum(item["prix"] for item in panier)
        items_doc = [
            {
                "product":  ObjectId(item["id"]) if item.get("id") else None,
                "name":     item["nom"],
                "quantity": 1,
                "price":    item["prix"],
            }
            for item in panier
        ]

        try:
            order_doc = {
                "customerInfo": {
                    "firstName": d.get("prenom"),
                    "lastName":  d.get("nom"),
                    "phone":     d.get("phone"),
                    "wilaya":    d.get("wilaya"),
                    "commune":   d.get("commune"),
                },
                "items":         items_doc,
                "total":         total,
                "deliveryFee":   0,
                "deliveryType":  "home",
                "deliverySpeed": "express",
                "status":        "en attente",
                "source":        "telegram",
                "createdAt":     datetime.utcnow(),
                "updatedAt":     datetime.utcnow(),
            }
            ins = orders_col.insert_one(order_doc)
            logger.info(f"Commande sauvegardée : {ins.inserted_id}")
        except Exception as e:
            logger.error(f"Erreur MongoDB : {e}")

        try:
            from telegram import Bot
            admin_bot = Bot(token=ADMIN_BOT_TOKEN)
            now       = datetime.now().strftime("%d/%m/%Y %H:%M")
            items_txt = "\n".join([f"  • {i['nom']} — {i['prix']} DA" for i in panier])
            await admin_bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=(
                    f"🛍️ *NOUVELLE COMMANDE CVKCOSMETIQUE*\n📅 {now}\n\n"
                    f"🛒 *Produits :*\n{items_txt}\n"
                    f"💰 *Total : {total} DA*\n\n"
                    f"👤 *Prénom :* {d.get('prenom')}\n"
                    f"👤 *Nom :* {d.get('nom')}\n"
                    f"📱 *Téléphone :* {d.get('phone')}\n"
                    f"🗺️ *Wilaya :* {d.get('wilaya')}\n"
                    f"🏘️ *Commune :* {d.get('commune')}"
                ),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Erreur admin : {e}")

        msg = ai_text(
            system=BASE_PERSONA,
            user=f"[SYSTEM] Commande validée et enregistrée ! Total {total} DA. Remercie le client chaleureusement et dis que l'équipe cvkcosmetique le contactera bientôt pour la livraison.",
            lang=lang
        )
        try:
            await update.message.reply_text(msg or "🎉 Commande confirmée ! Merci 🌸", parse_mode="Markdown")
        except Exception:
            await update.message.reply_text(msg or "🎉 Commande confirmée ! Merci 🌸")

    else:
        msg = ai_text(
            system=BASE_PERSONA,
            user="[SYSTEM] Le client a annulé la commande. Message compréhensif, invite-le à continuer à explorer cvkcosmetique.",
            lang=lang
        )
        await update.message.reply_text(msg or "❌ Commande annulée. N'hésite pas à continuer 🌸")

    # Réinitialisation
    catalog = fetch_catalog()
    context.user_data.clear()
    context.user_data["catalog"] = catalog
    context.user_data["history"] = []
    context.user_data["panier"]  = []
    context.user_data["lang"]    = lang
    return CHAT

# ─────────────────────────────────────────
# ▶️  LANCEMENT
# ─────────────────────────────────────────

def main():
    app  = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        allow_reentry=True,
        states={
            CHAT:          [MessageHandler(filters.TEXT & ~filters.COMMAND, chat)],
            ADD_MORE:      [MessageHandler(filters.TEXT & ~filters.COMMAND, add_more)],
            GET_PRENOM:    [MessageHandler(filters.TEXT & ~filters.COMMAND, get_prenom)],
            GET_NOM:       [MessageHandler(filters.TEXT & ~filters.COMMAND, get_nom)],
            GET_PHONE:     [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
            GET_WILAYA:    [MessageHandler(filters.TEXT & ~filters.COMMAND, get_wilaya)],
            GET_COMMUNE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, get_commune)],
            VERIFY_INFO:   [MessageHandler(filters.TEXT & ~filters.COMMAND, verify_info)],
            CORRECT_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, correct_field)],
            CONFIRM_ORDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_order)],
        },
        fallbacks=[CommandHandler("reset", reset)],
    )
    app.add_handler(conv)
    logger.info("Bot cvkcosmetique démarré")
    app.run_polling()

if __name__ == "__main__":
    main()
