import os
import base64
import telebot
from openai import OpenAI
from dotenv import load_dotenv

# Загружаем переменные из файла .env
load_dotenv()

# --- 1. Инициализация ---
# Берем ключи ТОЛЬКО из файла .env
BOT_TOKEN = os.getenv("BOT_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Проверка, что ключи на месте (чтобы бот не падал с непонятной ошибкой)
if not BOT_TOKEN:
    print("❌ ОШИБКА: BOT_TOKEN не найден в файле .env!")
    exit(1)
if not DEEPSEEK_API_KEY:
    print("❌ ОШИБКА: DEEPSEEK_API_KEY не найден в файле .env!")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)

ds_client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
# Если Groq ключ пустой, бот не упадет, а просто пропустит проверку картинок
groq_client = OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1") if GROQ_API_KEY else None

ADMIN_ID = 8489564800
ANONYMOUS_BOT_ID = 1087968824
CHANNEL_BOT_ID = 136817688

# --- 2. Промпты ---
TEXT_PROMPT = """
Ты — строгий модератор Telegram-чата. Проанализируй текст, ссылки и профиль.
Текст: "{text}"
Ссылки: "{links}"
Био: "{bio}"

Отвечай "DELETE", если есть:
1. Реклама/спам/казино/крипта.
2. Порно/18+/интим ("шлюхи", "скучно", "ищу компанию").
3. Скрытые ссылки.
4. Оскорбления.
5. Услуги с оплатой ("поглажу", "массаж" за деньги).
6. Плохие слова в био (мат, наркотики).

ВАЖНО ПРО БИО:
Если в био просто ссылка на личный канал, блог или творчество (@my_channel, "Тут про котиков") — это НОРМАЛЬНО, не удаляй.
Если в био реклама, казино, порно или "заработок" — это НАРУШЕНИЕ.

Если всё чисто — "OK". Только одно слово.
"""

VISION_PROMPT = """
Ты — строгий модератор. Посмотри на аватарку пользователя.
Отвечай "DELETE", если видишь:
1. Полуобнажённое тело (бельё, купальник, откровенные позы).
2. Текст с рекламой/интимом.
Если обычное фото — "OK". Только одно слово.
"""

CHAT_IMAGE_PROMPT = """
Ты — строгий модератор. Посмотри на это фото из чата.
Отвечай "DELETE", если видишь:
1. Рекламу (казино, крипта, ссылки, QR-коды).
2. Порно / 18+.
3. Спам-мемы с призывами.
Если обычное фото — "OK". Только одно слово.
"""

# --- 3. Вспомогательные функции ---
def extract_links_and_entities(message):
    links = []
    if message.entities:
        for entity in message.entities:
            if entity.type == 'text_link' and entity.url:
                links.append(entity.url)
            elif entity.type == 'url':
                links.append(message.text[entity.offset:entity.offset + entity.length])
            elif entity.type == 'mention':
                links.append(message.text[entity.offset:entity.offset + entity.length])
    if message.reply_markup and hasattr(message.reply_markup, 'inline_keyboard'):
        for row in message.reply_markup.inline_keyboard:
            for button in row:
                if button.url: links.append(button.url)
                if button.text: links.append(f"Кнопка: {button.text}")
    return " | ".join(links) if links else "нет"

def check_text(text, links, bio):
    if not text and not links: return False, ""
    try:
        response = ds_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "Отвечай только DELETE или OK."},
                {"role": "user", "content": TEXT_PROMPT.format(text=text, links=links, bio=bio)}
            ],
            temperature=0.0, max_tokens=10
        )
        verdict = response.choices[0].message.content.strip().upper()
        print(f"[DeepSeek] {text[:30]!r} -> {verdict}")
        return ("DELETE" in verdict), "Текст/Ссылки/Био" if "DELETE" in verdict else ""
    except Exception as e:
        print(f"❌ [Error DeepSeek] {e}")
        return False, ""

def check_image_base64(base64_image, prompt, user_id, type_name):
    if not groq_client: return False, ""
    try:
        response = groq_client.chat.completions.create(
            model="qwen/qwen3.8-27b",
            messages=[{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
            ]}],
            temperature=0.0, max_tokens=10
        )
        verdict = response.choices[0].message.content.strip().upper()
        print(f"[Groq {type_name}] User {user_id} -> {verdict}")
        return ("DELETE" in verdict), f"{type_name} (Groq)" if "DELETE" in verdict else ""
    except Exception as e:
        print(f"❌ [Error {type_name}] {e}")
        return False, ""

def check_avatar(user_id):
    try:
        photos = bot.get_user_profile_photos(user_id, limit=1)
        if not photos.photos: return False, ""
        file_id = photos.photos[0][-1].file_id
        downloaded_file = bot.download_file(bot.get_file(file_id).file_path)
        base64_image = base64.b64encode(downloaded_file).decode('utf-8')
        return check_image_base64(base64_image, VISION_PROMPT, user_id, "Avatar")
    except: return False, ""

def check_chat_image(message):
    try:
        file_id = message.photo[-1].file_id
        downloaded_file = bot.download_file(bot.get_file(file_id).file_path)
        base64_image = base64.b64encode(downloaded_file).decode('utf-8')
        return check_image_base64(base64_image, CHAT_IMAGE_PROMPT, message.from_user.id, "Chat Photo")
    except: return False, ""

def notify_admin(chat_title, chat_id, user_id, username, text, reason, message_link):
    report = (
        f"🚨 **Удалено**\n**Чат:** {chat_title}\n**Юзер:** @{username or user_id}\n"
        f"**Причина:** {reason}\n**Текст:** {text[:100]}...\n🔗 [Ссылка]({message_link})"
    )
    try: bot.send_message(ADMIN_ID, report, parse_mode='Markdown', disable_web_page_preview=True)
    except Exception as e: print(f"❌ [Error Admin] {e}")

# --- 4. Обработчики ---
@bot.message_handler(content_types=['text'])
def moderate_message(message):
    if message.text and message.text.startswith('/'): return
    user_id = message.from_user.id
    sender_chat_id = message.sender_chat.id if message.sender_chat else None

    if user_id in [ANONYMOUS_BOT_ID, CHANNEL_BOT_ID] or sender_chat_id is not None: return
    try:
        if bot.get_chat_member(message.chat.id, user_id).status in ['administrator', 'creator']: return
    except: pass

    links = extract_links_and_entities(message)
    text = message.text or ""
    bio = ""
    try: bio = bot.get_chat(user_id).bio or ""
    except: pass

    chat_title = message.chat.title or "Личка"
    message_link = f"https://t.me/{message.chat.username}/{message.message_id}" if message.chat.username else f"https://t.me/c/{str(message.chat.id).replace('-100', '')}/{message.message_id}"

    is_bad, reason = check_text(text, links, bio)
    if is_bad:
        try:
            bot.delete_message(message.chat.id, message.message_id)
            notify_admin(chat_title, message.chat.id, user_id, message.from_user.username, text, reason, message_link)
        except Exception as e: print(f"❌ [Error] {e}")
        return

    is_bad_av, reason_av = check_avatar(user_id)
    if is_bad_av:
        try:
            bot.delete_message(message.chat.id, message.message_id)
            notify_admin(chat_title, message.chat.id, user_id, message.from_user.username, text, reason_av, message_link)
        except Exception as e: print(f"❌ [Error] {e}")

@bot.message_handler(content_types=['photo'])
def moderate_photo(message):
    if message.caption and message.caption.startswith('/'): return
    user_id = message.from_user.id
    sender_chat_id = message.sender_chat.id if message.sender_chat else None

    if user_id in [ANONYMOUS_BOT_ID, CHANNEL_BOT_ID] or sender_chat_id is not None: return
    try:
        if bot.get_chat_member(message.chat.id, user_id).status in ['administrator', 'creator']: return
    except: pass

    chat_title = message.chat.title or "Личка"
    message_link = f"https://t.me/{message.chat.username}/{message.message_id}" if message.chat.username else f"https://t.me/c/{str(message.chat.id).replace('-100', '')}/{message.message_id}"

    is_bad, reason = check_chat_image(message)
    if is_bad:
        try:
            bot.delete_message(message.chat.id, message.message_id)
            text_for_report = message.caption or "[Фото без подписи]"
            notify_admin(chat_title, message.chat.id, user_id, message.from_user.username, text_for_report, reason, message_link)
        except Exception as e: print(f"❌ [Error] {e}")

if __name__ == "__main__":
    print("🚀 Бот-модератор запущен...")
    bot.infinity_polling()