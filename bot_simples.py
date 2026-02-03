#!/usr/bin/env python3
"""
Bot Telegram + Grok - Versão Simples
Apenas conversação básica com IA
"""
import os
import asyncio
import logging
import aiohttp
import redis
import json
import base64
from datetime import timedelta
from flask import Flask, request
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application, MessageHandler, ContextTypes, filters, CommandHandler
)

"""
═══════════════════════════════════════════════════════════════
⚙️ CONFIGURAÇÃO VIA VARIÁVEIS DE AMBIENTE NO RAILWAY:
═══════════════════════════════════════════════════════════════

Crie estas variáveis no Railway:

TELEGRAM_TOKEN = token do seu bot (@BotFather)
GROK_API_KEY = sua key do Grok (console.x.ai)
ADMIN_IDS = seu ID do Telegram (@userinfobot)
REDIS_URL = (criado automaticamente pelo Railway)

═══════════════════════════════════════════════════════════════
"""

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Variáveis de ambiente (Railway)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
GROK_API_KEY = os.getenv("GROK_API_KEY", "")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
PORT = int(os.getenv("PORT", 8080))

webhook_url = os.getenv("WEBHOOK_BASE_URL", "")
if webhook_url and not webhook_url.startswith("http"):
    webhook_url = f"https://{webhook_url}"
WEBHOOK_BASE_URL = webhook_url or f"https://placeholder.railway.app"
WEBHOOK_PATH = "/telegram"

ADMIN_IDS = set(map(int, os.getenv("ADMIN_IDS", "0").split(",")))

logger.info(f"🚀 Iniciando bot...")
logger.info(f"📍 Webhook: {WEBHOOK_BASE_URL}{WEBHOOK_PATH}")

# Redis
try:
    r = redis.from_url(REDIS_URL, decode_responses=True)
    r.ping()
    logger.info("✅ Redis conectado")
except Exception as e:
    logger.error(f"❌ Redis erro: {e}")
    raise

# Configurações Grok
MODELO = "grok-beta"
GROK_API_URL = "https://api.x.ai/v1/chat/completions"
MAX_MEMORIA = 10

# ================= FUNÇÕES DE MEMÓRIA =================
def memory_key(uid):
    return f"memory:{uid}"

def get_memory(uid):
    try:
        data = r.get(memory_key(uid))
        if data:
            return json.loads(data)
        return []
    except:
        return []

def save_memory(uid, messages):
    try:
        recent = messages[-MAX_MEMORIA:] if len(messages) > MAX_MEMORIA else messages
        r.setex(memory_key(uid), timedelta(days=7), json.dumps(recent, ensure_ascii=False))
    except Exception as e:
        logger.error(f"Erro ao salvar memória: {e}")

def add_to_memory(uid, role, content):
    memory = get_memory(uid)
    memory.append({"role": role, "content": content})
    save_memory(uid, memory)

def clear_memory(uid):
    try:
        r.delete(memory_key(uid))
        logger.info(f"🗑️ Memória limpa: {uid}")
    except Exception as e:
        logger.error(f"Erro ao limpar memória: {e}")

# ================= PROMPT =================
def build_prompt():
    return """Você é Maya, uma assistente amigável e descontraída.
Responda de forma natural e breve (máximo 2-3 frases).
Use emojis de vez em quando.
Seja simpática e prestativa."""

# ================= GROK =================
class Grok:
    async def reply(self, uid, text, image_base64=None):
        mem = get_memory(uid)
        prompt = build_prompt()
        
        if image_base64:
            user_content = []
            if text:
                user_content.append({"type": "text", "text": text})
            user_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}
            })
        else:
            user_content = text
        
        payload = {
            "model": MODELO,
            "messages": [
                {"role": "system", "content": prompt},
                *mem,
                {"role": "user", "content": user_content}
            ],
            "max_tokens": 500,
            "temperature": 0.8
        }
        
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    GROK_API_URL,
                    headers={
                        "Authorization": f"Bearer {GROK_API_KEY}",
                        "Content-Type": "application/json"
                    },
                    json=payload
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.error(f"Grok erro {resp.status}: {error_text}")
                        return "Desculpa, tive um problema. Tenta de novo?"
                    
                    data = await resp.json()
                    if "choices" not in data:
                        return "Ops, algo deu errado..."
                    
                    answer = data["choices"][0]["message"]["content"]
                    
        except Exception as e:
            logger.exception(f"Erro no Grok: {e}")
            return "Desculpa, tive um problema técnico. Tenta de novo?"
        
        # Salva na memória
        memory_text = f"[Foto] {text}" if image_base64 else text
        add_to_memory(uid, "user", memory_text)
        add_to_memory(uid, "assistant", answer)
        
        return answer

grok = Grok()

# ================= DOWNLOAD DE FOTO =================
async def download_photo_base64(bot, file_id):
    try:
        file = await bot.get_file(file_id)
        file_bytes = await file.download_as_bytearray()
        return base64.b64encode(file_bytes).decode('utf-8')
    except Exception as e:
        logger.error(f"Erro ao baixar foto: {e}")
        return None

# ================= HANDLERS =================
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Oi! 👋\n"
        "Sou a Maya, sua assistente virtual!\n"
        "Pode me perguntar o que quiser 😊"
    )

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    
    try:
        has_photo = bool(update.message.photo)
        text = update.message.text or ""
        
        # Se tem foto
        if has_photo:
            photo_file_id = update.message.photo[-1].file_id
            caption = update.message.caption or ""
            
            image_base64 = await download_photo_base64(context.bot, photo_file_id)
            if image_base64:
                try:
                    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
                except:
                    pass
                
                reply = await grok.reply(uid, caption, image_base64=image_base64)
                await update.message.reply_text(reply)
                return
            else:
                await update.message.reply_text("Não consegui ver a foto. Tenta de novo?")
                return
        
        # Mensagem normal
        try:
            await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
            await asyncio.sleep(2)
        except:
            pass
        
        reply = await grok.reply(uid, text)
        await update.message.reply_text(reply)
        
    except Exception as e:
        logger.error(f"Erro message: {e}")
        await update.message.reply_text("Ops, algo deu errado. Tenta de novo?")

# ================= COMANDOS ADMIN =================
async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    
    try:
        total_keys = len(r.keys("memory:*"))
        await update.message.reply_text(f"📊 Usuários com conversas: {total_keys}")
    except:
        await update.message.reply_text("Erro ao buscar stats")

async def clearmemory_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    
    if not context.args:
        await update.message.reply_text("Uso: /clearmemory <user_id>")
        return
    
    clear_memory(int(context.args[0]))
    await update.message.reply_text("✅ Memória limpa")

# ================= SETUP =================
def setup_application():
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler("stats", stats_cmd))
    application.add_handler(CommandHandler("clearmemory", clearmemory_cmd))
    application.add_handler(MessageHandler(
        (filters.TEXT | filters.PHOTO) & ~filters.COMMAND,
        message_handler
    ))
    
    logger.info("✅ Handlers registrados")
    return application

# ================= FLASK =================
app = Flask(__name__)
application = setup_application()

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

def start_loop():
    loop.run_forever()

import threading
threading.Thread(target=start_loop, daemon=True).start()

@app.route("/", methods=["GET"])
def health():
    return "ok", 200

@app.route("/set-webhook", methods=["GET"])
def set_webhook_route():
    asyncio.run_coroutine_threadsafe(setup_webhook(), loop)
    return "Webhook configurado", 200

@app.route(WEBHOOK_PATH, methods=["POST"])
def telegram_webhook():
    try:
        data = request.json
        if not data:
            return "ok", 200
        update = Update.de_json(data, application.bot)
        asyncio.run_coroutine_threadsafe(application.process_update(update), loop)
        return "ok", 200
    except Exception as e:
        logger.exception(f"Erro webhook: {e}")
        return "error", 500

async def setup_webhook():
    try:
        await application.bot.delete_webhook(drop_pending_updates=True)
        webhook_url = f"{WEBHOOK_BASE_URL}{WEBHOOK_PATH}"
        await application.bot.set_webhook(webhook_url)
        logger.info(f"✅ Webhook: {webhook_url}")
    except Exception as e:
        logger.error(f"Erro webhook: {e}")

if __name__ == "__main__":
    # Validação de variáveis (só valida quando o app roda, não no build)
    if not TELEGRAM_TOKEN:
        logger.error("❌ Configure a variável TELEGRAM_TOKEN no Railway")
        exit(1)
    if not GROK_API_KEY:
        logger.error("❌ Configure a variável GROK_API_KEY no Railway")
        exit(1)
    
    asyncio.run_coroutine_threadsafe(application.initialize(), loop)
    asyncio.run_coroutine_threadsafe(application.start(), loop)
    logger.info(f"🌐 Flask porta {PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
