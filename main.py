import os
import telebot
from openai import OpenAI
from dotenv import load_dotenv

# Загружаем переменные из файла .env (для локального запуска)
load_dotenv()

# --- 1. Инициализация ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

if not BOT_TOKEN:
    print("❌ ОШИБКА: BOT_TOKEN не найден в файле .env или переменных окружения!")
    exit(1)
if not DEEPSEEK_API_KEY:
    print("❌ ОШИБКА: DEEPSEEK_API_KEY не найден в файле .env или переменных окружения!")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)
ds_client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

ADMIN_ID = 8489564800
ANONYMOUS_BOT_ID = 1087968824
CHANNEL_BOT_ID = 136817688

# --- 2. Промпт для текста и ссылок ---
TEXT_PROMPT = """
Ты — безжалостный модератор Telegram-чата. Твоя цель — найти рекламу, спам, скрытые ссылки и попрошайничество.
Твоя задача НЕ проверять био и НЕ проверять картинки. Смотри ТОЛЬКО на текст и ссылки.

ДАННЫЕ ДЛЯ ПРОВЕРКИ:
1. Текст сообщения: "{text}"
2. Все ссылки (включая скрытые под текстом и кнопки): "{links}"

ПРАВИЛА (Отвечай "DELETE", если есть ХОТЯ БЫ ОДНО совпадение):
- РЕКЛАМА: Крипта, казино, ставки, продажа услуг, боты, мини-игры.
- СКРЫТЫЕ ССЫЛКИ: Если в поле "Ссылки" есть любой URL (t.me, http://, https://), который ведет на сторонний ресурс (не на личный канал человека). Даже если текст безобидный.
- СПАМ: Бессмысленный набор символов, флуд, призывы перейти по ссылке.
- ПОПРОШАЙНИЧЕСТВО И ДЕНЬГИ: Просьбы одолжить денег, "переведи на карту", "скинь на пиво", "помоги материально", предложения "заработка", "помощи с выводом средств", "инвестиций".

ИСКЛЮЧЕНИЕ (Отвечай "OK"):
- Маты и нецензурная лексика РАЗРЕШЕНЫ.
- Обычное общение людей без ссылок, рекламы и просьб о деньгах.

ВАЖНО: Если сомневаешься — удаляй (DELETE). Отвечай ТОЛЬКО одним словом: "DELETE" или "OK".
"""


# --- 3. Вспомогательные функции ---

def extract_links_and_entities(message):
    """Вытаскивает все ссылки, скрытые ссылки и кнопки."""
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
                if button.url:
                    links.append(button.url)
                if button.text:
                    links.append(f"Кнопка: {button.text}")
    return " | ".join(links) if links else "нет"


def check_text(text, links):
    """Проверка текста и ссылок через DeepSeek."""
    if not text and not links: return False, ""
    try:
        response = ds_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system",
                 "content": "Ты — строгий робот-модератор. Отвечай только DELETE или OK. Маты разрешены, но попрошайничество и просьбы о деньгах запрещены."},
                {"role": "user", "content": TEXT_PROMPT.format(text=text, links=links)}
            ],
            temperature=0.1,
            max_tokens=20
        )
        verdict = response.choices[0].message.content.strip().upper()
        print(f"[DeepSeek] Текст: {text[:20]!r} | Ссылки: {links[:30]} -> {verdict}")
        return ("DELETE" in verdict), "Реклама/Спам/Деньги/Ссылки" if "DELETE" in verdict else ""
    except Exception as e:
        print(f"❌ [Error DeepSeek] {e}")
        return False, ""


def notify_admin(chat_title, chat_id, user_id, username, text, reason, message_link):
    """Отправка отчета админу в ЛС."""
    report = (
        f"🚨 **Удалено**\n**Чат:** {chat_title}\n**Юзер:** @{username or user_id}\n"
        f"**Причина:** {reason}\n**Текст:** {text[:100]}...\n🔗 [Ссылка]({message_link})"
    )
    try:
        bot.send_message(ADMIN_ID, report, parse_mode='Markdown', disable_web_page_preview=True)
    except Exception as e:
        print(f"❌ [Error Admin] {e}")


# --- 4. Обработчик сообщений ---

@bot.message_handler(content_types=['text'])
def moderate_message(message):
    if message.text and message.text.startswith('/'): return
    user_id = message.from_user.id
    sender_chat_id = message.sender_chat.id if message.sender_chat else None

    # Защита админов, каналов и анонимных админов
    if user_id in [ANONYMOUS_BOT_ID, CHANNEL_BOT_ID] or sender_chat_id is not None: return
    try:
        if bot.get_chat_member(message.chat.id, user_id).status in ['administrator', 'creator']: return
    except:
        pass

    links = extract_links_and_entities(message)
    text = message.text or ""

    chat_title = message.chat.title or "Личка"
    message_link = f"https://t.me/{message.chat.username}/{message.message_id}" if message.chat.username else f"https://t.me/c/{str(message.chat.id).replace('-100', '')}/{message.message_id}"

    # Проверка только текста и ссылок
    is_bad, reason = check_text(text, links)
    if is_bad:
        try:
            bot.delete_message(message.chat.id, message.message_id)
            notify_admin(chat_title, message.chat.id, user_id, message.from_user.username, text, reason, message_link)
        except Exception as e:
            print(f"❌ [Error] {e}")


if __name__ == "__main__":
    print("🚀 Бот-модератор (реклама, спам, деньги) запущен...")
    bot.infinity_polling()