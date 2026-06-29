import io
import os
import sys
import logging
import asyncio
import warnings
import requests
from datetime import datetime

# Отключаем предупреждение о CallbackQueryHandler
from telegram.warnings import PTBUserWarning
warnings.filterwarnings(action="ignore", message=r".*CallbackQueryHandler", category=PTBUserWarning)

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Проверка импорта PIL
try:
    from PIL import Image, ImageDraw, ImageFont
    logger.info(f"✅ Pillow версия: {Image.__version__}")
except ImportError as e:
    logger.error(f"❌ Pillow не установлен: {e}")
    print("Выполните: python -m pip install Pillow")
    sys.exit(1)

from telegram import Update, InputFile, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, 
    ContextTypes, ConversationHandler, CallbackQueryHandler
)
from telegram.error import Conflict, NetworkError

# ========== КОНФИГУРАЦИЯ ==========
TOKEN = "8558634839:AAGcfBT6bFlE7e7M8rBpdY6WbZTduyxmOv8"
TEMPLATE = "template.png"
WAIT_PHONE, WAIT_CODE = 0, 1

# === КООРДИНАТЫ ===
CODE_POSITIONS = {
    1: (140, 677),
    2: (209, 677),
    3: (273, 677),
    4: (340, 677),
    5: (406, 676),
    6: (472, 676),
}
PHONE_POS = (217, 520)

# === НАСТРОЙКИ ===
CODE_FONT_SIZE = 27
PHONE_FONT_SIZE = 23
CODE_COLOR = "#1B4027"
PHONE_COLOR = "#0F1015"

# Хранилище для активных диалогов
active_conversations = {}


def mask_phone(phone: str) -> str:
    digits = ''.join(filter(str.isdigit, phone))
    if len(digits) >= 11:
        if digits[0] == '8':
            digits = '7' + digits[1:]
        if len(digits) == 10:
            digits = '7' + digits
        return f"+{digits[0]} ({digits[1:4]}) ***-**-{digits[-2:]}"
    return "+7 (***) ***-**-**"


def get_roboto_font(size: int, bold: bool = True):
    """Загружает шрифт с fallback-ами"""
    # Сначала пробуем локальные шрифты (быстрее)
    local_fonts = [
        "C:/Windows/Fonts/Roboto-Bold.ttf",
        "C:/Windows/Fonts/Roboto-Regular.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    
    for font_path in local_fonts:
        try:
            font = ImageFont.truetype(font_path, size)
            logger.info(f"✅ Локальный шрифт: {font_path}")
            return font
        except:
            continue
    
    # Если локальных нет — пробуем скачать из интернета
    if bold:
        font_url = 'https://github.com/googlefonts/roboto/blob/main/src/hinted/Roboto-Bold.ttf?raw=true'
    else:
        font_url = 'https://github.com/googlefonts/roboto/blob/main/src/hinted/Roboto-Regular.ttf?raw=true'
    
    try:
        response = requests.get(font_url, timeout=10, allow_redirects=True)
        font = ImageFont.truetype(io.BytesIO(response.content), size)
        logger.info(f"✅ Roboto шрифт загружен из сети")
        return font
    except Exception as e:
        logger.warning(f"⚠️ Не удалось загрузить Roboto: {e}")
    
    return ImageFont.load_default()


def create_screenshot(phone: str, code: str) -> bytes:
    """Создаёт скриншот"""
    img = Image.open(TEMPLATE).convert("RGB")
    draw = ImageDraw.Draw(img)
    
    font_code = get_roboto_font(CODE_FONT_SIZE, bold=True)
    font_phone = get_roboto_font(PHONE_FONT_SIZE, bold=True)
    
    code_digits = ''.join(filter(str.isdigit, code))[:6]
    while len(code_digits) < 6:
        code_digits += " "
    
    for i, digit in enumerate(code_digits, start=1):
        if digit != " ":
            x, y = CODE_POSITIONS[i]
            draw.text((x, y), digit, fill=CODE_COLOR, font=font_code)
    
    masked = mask_phone(phone)
    draw.text(PHONE_POS, masked, fill=PHONE_COLOR, font=font_phone)
    
    out = io.BytesIO()
    img.save(out, format="PNG")
    out.seek(0)
    return out.getvalue()


# ========== ГЛАВНОЕ МЕНЮ ==========

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, message=None):
    keyboard = [
        [InlineKeyboardButton("🆕 Создать скриншот", callback_data="new_screenshot")],
        [InlineKeyboardButton("❓ Помощь", callback_data="help")],
        [InlineKeyboardButton("ℹ️ О боте", callback_data="about")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = (
        "🏛️ **Главное меню**\n\n"
        "Я создаю скриншоты страницы восстановления пароля Госуслуг.\n"
        "Просто нажми на кнопку ниже и введи данные."
    )
    
    try:
        if message:
            await message.edit_text(text, parse_mode="Markdown", reply_markup=reply_markup)
        else:
            await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Ошибка в main_menu: {e}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info(f"Пользователь {user_id} запустил бота")
    await main_menu(update, context)


async def g_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


# ========== ДИАЛОГ СОЗДАНИЯ СКРИНШОТА ==========

async def new_screenshot_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    context.user_data.clear()
    active_conversations[user_id] = datetime.now()
    
    await update.callback_query.message.reply_text(
        "📱 **Шаг 1/2**\n\n"
        "Введи номер телефона:\n"
        "Пример: `+7 966 123-45-67`\n\n"
        "Или отправь /cancel для отмены",
        parse_mode="Markdown"
    )
    return WAIT_PHONE


async def generate_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    context.user_data.clear()
    active_conversations[user_id] = datetime.now()
    
    await update.message.reply_text(
        "📱 **Шаг 1/2**\n\n"
        "Введи номер телефона:\n"
        "Пример: `+7 966 123-45-67`\n\n"
        "Или отправь /cancel для отмены",
        parse_mode="Markdown"
    )
    return WAIT_PHONE


async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.text:
        await update.message.reply_text("❌ Пожалуйста, отправь номер текстом.")
        return WAIT_PHONE
    
    phone = update.message.text.strip()
    digits_count = sum(1 for c in phone if c.isdigit())
    
    if digits_count < 5:
        await update.message.reply_text(
            "❌ Слишком короткий номер. Попробуй ещё раз:"
        )
        return WAIT_PHONE
    
    context.user_data['phone'] = phone
    
    await update.message.reply_text(
        "🔢 **Шаг 2/2**\n\n"
        "Введи 6 цифр кода:\n"
        "Пример: `434322`\n\n"
        "Или отправь /cancel для отмены",
        parse_mode="Markdown"
    )
    return WAIT_CODE


async def get_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not update.message.text:
        await update.message.reply_text("❌ Отправь код цифрами.")
        return WAIT_CODE
    
    code_raw = update.message.text.strip()
    code_clean = ''.join(filter(str.isdigit, code_raw))
    
    if len(code_clean) != 6:
        await update.message.reply_text(
            f"❌ Нужно ровно 6 цифр. У тебя {len(code_clean)}.\nПопробуй ещё раз:"
        )
        return WAIT_CODE
    
    phone = context.user_data.get('phone', '+7 966 123-45-67')
    
    # Отправляем сообщение о начале генерации
    status_msg = await update.message.reply_text("🎨 Генерация скриншота... ⏳")
    
    try:
        img_bytes = create_screenshot(phone, code_clean)
        
        keyboard = [
            [InlineKeyboardButton("🔄 Ещё один скриншот", callback_data="another")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="help")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await status_msg.delete()
        
        await update.message.reply_photo(
            photo=InputFile(io.BytesIO(img_bytes), filename="gosuslugi.png"),
            caption=f"✅ **Готово!**\n\n"
                    f"📞 Номер: `{mask_phone(phone)}`\n"
                    f"🔢 Код: `{code_clean}`",
            parse_mode="Markdown",
            reply_markup=reply_markup
        )
        
        active_conversations.pop(user_id, None)
        
    except FileNotFoundError:
        await status_msg.edit_text(f"❌ Ошибка: файл `{TEMPLATE}` не найден!")
    except Exception as e:
        logger.error(f"Ошибка генерации: {e}")
        await status_msg.edit_text("❌ Ошибка при генерации. Попробуй ещё раз.")
    
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    active_conversations.pop(user_id, None)
    context.user_data.clear()
    
    await update.message.reply_text(
        "❌ Отменено.\nНажми /start для возврата в меню"
    )
    return ConversationHandler.END


# ========== ОБРАБОТЧИК КНОПОК ==========

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    
    try:
        await query.answer()
    except:
        pass
    
    if query.data == "new_screenshot":
        await new_screenshot_start(update, context)
        return WAIT_PHONE
    
    elif query.data == "another":
        await query.message.reply_text(
            "📱 **Шаг 1/2**\n\n"
            "Введи номер телефона:\n"
            "Пример: `+7 966 123-45-67`",
            parse_mode="Markdown"
        )
        return WAIT_PHONE
    
    elif query.data == "help":
        help_text = (
            "❓ **Помощь**\n\n"
            "1. Нажми 'Создать скриншот'\n"
            "2. Введи номер телефона\n"
            "3. Введи 6 цифр кода\n"
            "4. Получи готовый скриншот!\n\n"
            "📌 **Команды:**\n"
            "/start или /g - Главное меню\n"
            "/cancel - Отменить\n\n"
            "⚡ Бот обрабатывает много запросов без задержек"
        )
        await query.message.edit_text(help_text, parse_mode="Markdown")
        await asyncio.sleep(3)
        await main_menu(update, context, query.message)
    
    elif query.data == "about":
        about_text = (
            "ℹ️ **О боте**\n\n"
            "Версия: 2.2 (быстрая)\n"
            "Шрифт: Roboto Bold\n\n"
            "⚡ Без ограничений по скорости\n"
            "🛡️ С защитой от ошибок"
        )
        await query.message.edit_text(about_text, parse_mode="Markdown")
        await asyncio.sleep(3)
        await main_menu(update, context, query.message)


# ========== ГЛОБАЛЬНЫЙ ОБРАБОТЧИК ОШИБОК ==========

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Ошибка: {context.error}")
    
    if isinstance(context.error, Conflict):
        logger.warning("Конфликт: бот уже запущен в другом месте")
    elif isinstance(context.error, NetworkError):
        logger.warning("Ошибка сети, но бот продолжает работу")
    else:
        try:
            if update and hasattr(update, 'effective_chat'):
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="❌ Ошибка. Попробуй ещё раз."
                )
        except:
            pass


async def set_commands(app: Application):
    commands = [
        BotCommand("start", "🏠 Главное меню"),
        BotCommand("g", "🚀 Быстрый старт"),
        BotCommand("generate", "✨ Создать скриншот"),
        BotCommand("cancel", "❌ Отменить"),
    ]
    await app.bot.set_my_commands(commands)
    logger.info("✅ Меню команд установлено")


def main():
    if not os.path.exists(TEMPLATE):
        logger.error(f"❌ Файл {TEMPLATE} не найден!")
        print(f"❌ Файл {TEMPLATE} не найден!")
        return
    
    logger.info(f"✅ Шаблон: {TEMPLATE}")
    print(f"✅ Бот запущен! Без ограничений по скорости")
    
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("generate", generate_start),
            CommandHandler("g", generate_start),
            CallbackQueryHandler(button_callback, pattern="new_screenshot"),
            CallbackQueryHandler(button_callback, pattern="another"),
        ],
        states={
            WAIT_PHONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone),
            ],
            WAIT_CODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_code),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    
    # Приложение без таймаутов
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("g", g_command))
    app.add_handler(CommandHandler("generate", generate_start))
    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_error_handler(error_handler)
    
    app.post_init = set_commands
    
    print("🚀 Бот запущен! Без ограничений по скорости")
    print("📱 Отправьте /start в Telegram")
    
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()