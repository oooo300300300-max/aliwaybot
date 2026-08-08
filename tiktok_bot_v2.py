#!/usr/bin/env python3
"""
Telegram Bot — TikTok Video Downloader (No Watermark)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Simple synchronous polling bot using the standard `requests` library.

FIX (v2): curl_cffi uses a compiled native extension that is NOT
compatible with Termux's Android environment (ImportError: dlopen
failed). Replaced curl_cffi with the plain `requests` library, which
works fine on Termux and doesn't need any special native build.

FIX (v1, kept): callback_data used to embed the full TikTok URL, which
exceeds Telegram's 64-byte limit and made sendMessage fail silently.
Now callback_data only carries "video"/"audio" and the URL comes from
user_url_cache.
"""

import os
import re
import json
import time
import logging
import tempfile
import shutil
import requests

# ── Configuration ──────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit(
        "خطأ: لم يتم ضبط متغير البيئة BOT_TOKEN.\n"
        "شغّل البوت هكذا:  BOT_TOKEN='ضع_التوكن_هنا' python tiktok_bot_v2.py"
    )
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

MAX_VIDEO_MB = 50

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

# ── Telegram API helpers ───────────────────────────────────────

def tg_api(method: str, data: dict = None, timeout: int = 30) -> dict:
    """Call Telegram Bot API."""
    url = f"{API_URL}/{method}"
    try:
        r = requests.post(url, data=data or {}, timeout=timeout)
        return r.json()
    except Exception as e:
        logger.error(f"TG API error: {e}")
        return {"ok": False, "description": str(e)}


def send_message(chat_id: int, text: str, parse_mode: str = None,
                 reply_markup: dict = None, reply_to: int = None) -> bool:
    """Send a text message."""
    data = {"chat_id": chat_id, "text": text}
    if parse_mode:
        data["parse_mode"] = parse_mode
    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup)
    if reply_to:
        data["reply_to_message_id"] = reply_to
    result = tg_api("sendMessage", data)
    if not result.get("ok", False):
        logger.error(f"sendMessage failed: {result}")
    return result.get("ok", False)


def send_video(chat_id: int, file_path: str, caption: str = "", reply_to: int = None) -> bool:
    """Send a video file."""
    url = f"{API_URL}/sendVideo"
    try:
        with open(file_path, "rb") as f:
            files = {"video": (os.path.basename(file_path), f)}
            data = {
                "chat_id": chat_id,
                "caption": caption,
                "parse_mode": "HTML",
            }
            if reply_to:
                data["reply_to_message_id"] = reply_to
            r = requests.post(url, data=data, files=files, timeout=120)
            result = r.json()
            if not result.get("ok", False):
                logger.error(f"sendVideo failed: {result}")
            return result.get("ok", False)
    except Exception as e:
        logger.error(f"Send video error: {e}")
        return False


def send_document(chat_id: int, file_path: str, caption: str = "", reply_to: int = None) -> bool:
    """Send a document (for large files)."""
    url = f"{API_URL}/sendDocument"
    try:
        with open(file_path, "rb") as f:
            files = {"document": (os.path.basename(file_path), f)}
            data = {
                "chat_id": chat_id,
                "caption": caption,
                "parse_mode": "HTML",
            }
            if reply_to:
                data["reply_to_message_id"] = reply_to
            r = requests.post(url, data=data, files=files, timeout=120)
            result = r.json()
            if not result.get("ok", False):
                logger.error(f"sendDocument failed: {result}")
            return result.get("ok", False)
    except Exception as e:
        logger.error(f"Send document error: {e}")
        return False


def send_audio(chat_id: int, file_path: str, caption: str = "", reply_to: int = None) -> bool:
    """Send an audio file."""
    url = f"{API_URL}/sendAudio"
    try:
        with open(file_path, "rb") as f:
            files = {"audio": (os.path.basename(file_path), f)}
            data = {
                "chat_id": chat_id,
                "caption": caption,
                "parse_mode": "HTML",
            }
            if reply_to:
                data["reply_to_message_id"] = reply_to
            r = requests.post(url, data=data, files=files, timeout=120)
            result = r.json()
            if not result.get("ok", False):
                logger.error(f"sendAudio failed: {result}")
            return result.get("ok", False)
    except Exception as e:
        logger.error(f"Send audio error: {e}")
        return False


def edit_message(chat_id: int, message_id: int, text: str) -> bool:
    """Edit a message."""
    result = tg_api("editMessageText", {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
    })
    if not result.get("ok", False):
        logger.error(f"editMessageText failed: {result}")
    return result.get("ok", False)


def answer_callback(callback_id: str) -> None:
    """Answer a callback query."""
    tg_api("answerCallbackQuery", {"callback_query_id": callback_id})


def send_chat_action(chat_id: int, action: str = "upload_video") -> None:
    """Send chat action (typing, uploading, etc.)."""
    tg_api("sendChatAction", {"chat_id": chat_id, "action": action})


# ── TikTok download helpers ────────────────────────────────────

TIKTOK_URL_RE = re.compile(
    r"https?://(?:www\.|vm\.|vt\.|m\.|t\.)?tiktok\.com/[\w\-/?=&@#.]+",
    re.IGNORECASE,
)
TIKWM_BASE = "https://www.tikwm.com"


def extract_tiktok_url(text: str) -> str:
    match = TIKTOK_URL_RE.search(text)
    return match.group(0) if match else None


def fetch_tikwm(url: str) -> dict:
    """Fetch TikTok video data from tikwm.com API."""
    candidates = [url]

    vid_match = re.search(r'/video/(\d+)', url)
    if vid_match:
        vid_id = vid_match.group(1)
        candidates.append(f"https://www.tiktok.com/@user/video/{vid_id}")
        candidates.append(f"https://m.tiktok.com/v/{vid_id}.html")

    for attempt_url in candidates:
        try:
            r = requests.post(
                "https://www.tikwm.com/api/",
                data={"url": attempt_url, "hd": 1},
                headers={
                    **BROWSER_HEADERS,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": "https://www.tikwm.com",
                    "Referer": "https://www.tikwm.com/",
                },
                timeout=30,
            )
            if r.status_code == 200:
                data = r.json()
                if data.get("code") == 0:
                    return data.get("data", {})
                else:
                    logger.warning(f"tikwm returned code={data.get('code')} msg={data.get('msg')}")
        except Exception as e:
            logger.warning(f"tikwm failed for {attempt_url}: {e}")
            continue
    return {}


def download_file(url: str, out_dir: str, suffix: str = ".mp4") -> str:
    """Download a file from URL."""
    if url.startswith("/"):
        url = TIKWM_BASE + url
    try:
        r = requests.get(url, headers=BROWSER_HEADERS, timeout=120, stream=True)
        if r.status_code != 200:
            logger.error(f"Download HTTP {r.status_code} for {url}")
            return None
        out_path = os.path.join(out_dir, f"download{suffix}")
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
        return out_path
    except Exception as e:
        logger.error(f"Download error: {e}")
        return None


def get_video_url(info: dict) -> str:
    """Get the best video URL (no watermark preferred)."""
    return info.get("hdplay") or info.get("play") or info.get("wmplay", "")


def get_audio_url(info: dict) -> str:
    """Get the audio URL."""
    return info.get("music", "")


# ── Arabic strings ─────────────────────────────────────────────

WELCOME_TEXT = (
    "🎬 أهلاً بك في بوت تحميل فيديوهات تيك توك بدون علامة مائية!\n\n"
    "📥 *طريقة الاستخدام:*\n"
    "1️⃣ أرسل رابط فيديو تيك توك\n"
    "2️⃣ اختر: فيديو MP4 أو صوت MP3\n"
    "3️⃣ البوت سيرسل لك الملف\n\n"
    "✏️ أرسل رابط تيك توك الآن!"
)

HELP_TEXT = (
    "📋 *المساعدة*\n\n"
    "• أرسل رابط فيديو تيك توك\n"
    "• اختر: فيديو MP4 أو صوت MP3\n"
    "• سيتم التحميل وإرسال الملف\n\n"
    "⚠️ تأكد أن الرابط صحيح والفيديو متاح."
)

NOT_TIKTOK = (
    "❓ هذا ليس رابط تيك توك.\n"
    "أرسل رابط فيديو تيك توك وسأساعدك!\n\n"
    "استخدم /help للمزيد."
)

LINK_RECEIVED = "🔗 تم استلام الرابط، اختر ما تريد:"
BTN_VIDEO = "فيديو MP4 🎬"
BTN_AUDIO = "صوت MP3 🎵"
DOWNLOADING_VIDEO = "⬇️ جاري تحميل الفيديو بدون علامة مائية... انتظر قليلاً..."
DOWNLOADING_AUDIO = "⬇️ جاري تحميل الصوت MP3... انتظر قليلاً..."
VIDEO_SENT = "✅ تم إرسال الفيديو بنجاح!"
AUDIO_SENT = "✅ تم إرسال الصوت بنجاح!"
ERROR_TEXT = "❌ حدث خطأ. تأكد من صحة الرابط أو جرّب رابطاً آخر."
PROCESSING = "⏳ جاري معالجة طلبك..."


# ── State ──────────────────────────────────────────────────────

user_url_cache: dict = {}       # chat_id -> tiktok_url
processing_users: set = set()   # set of user_ids currently processing


def get_buttons() -> dict:
    """Create inline keyboard with video and audio buttons."""
    return {
        "inline_keyboard": [
            [
                {"text": BTN_VIDEO, "callback_data": "video"},
                {"text": BTN_AUDIO, "callback_data": "audio"},
            ]
        ]
    }


# ── Message handlers ───────────────────────────────────────────

def handle_message(msg: dict) -> None:
    """Handle a regular message."""
    chat_id = msg["chat"]["id"]
    text = msg.get("text", "")
    message_id = msg.get("message_id", 0)

    if text == "/start":
        send_message(chat_id, WELCOME_TEXT, parse_mode="Markdown", reply_to=message_id)
        return

    if text == "/help":
        send_message(chat_id, HELP_TEXT, parse_mode="Markdown", reply_to=message_id)
        return

    url = extract_tiktok_url(text)
    if not url:
        send_message(chat_id, NOT_TIKTOK, reply_to=message_id)
        return

    user_url_cache[chat_id] = url
    send_message(
        chat_id, LINK_RECEIVED,
        reply_markup=get_buttons(),
        reply_to=message_id,
    )


def handle_callback(cb: dict) -> None:
    """Handle a callback query (button press)."""
    cb_id = cb["id"]
    action = cb.get("data", "")
    chat = cb.get("message", {}).get("chat", {})
    chat_id = chat.get("id")
    message_id = cb.get("message", {}).get("message_id", 0)
    from_user = cb.get("from", {})
    user_id = from_user.get("id", 0)

    answer_callback(cb_id)

    if user_id in processing_users:
        edit_message(chat_id, message_id, PROCESSING)
        return

    url = user_url_cache.get(chat_id, "")
    if not url:
        edit_message(chat_id, message_id, ERROR_TEXT)
        return

    processing_users.add(user_id)
    tmp_dir = None
    file_path = None

    try:
        is_video = (action == "video")
        status_text = DOWNLOADING_VIDEO if is_video else DOWNLOADING_AUDIO
        edit_message(chat_id, message_id, status_text)
        send_chat_action(chat_id, "upload_video" if is_video else "upload_document")

        info = fetch_tikwm(url)
        if not info:
            edit_message(chat_id, message_id, ERROR_TEXT)
            return

        if is_video:
            video_url = get_video_url(info)
            if not video_url:
                edit_message(chat_id, message_id, ERROR_TEXT)
                return

            tmp_dir = tempfile.mkdtemp(prefix="tiktok_")
            file_path = download_file(video_url, tmp_dir, ".mp4")
            if not file_path:
                edit_message(chat_id, message_id, ERROR_TEXT)
                return

            title = info.get("title", "") or "فيديو تيك توك"
            author = info.get("author", {}).get("nickname", "مجهول")
            duration = info.get("duration", 0)
            caption = f"📹 {title[:80]}\n👤 {author}\n⏱️ {duration} ثانية"

            file_size = os.path.getsize(file_path)
            if file_size <= MAX_VIDEO_MB * 1024 * 1024:
                send_video(chat_id, file_path, caption, reply_to=message_id)
            else:
                send_document(chat_id, file_path, caption, reply_to=message_id)

            edit_message(chat_id, message_id, VIDEO_SENT)

        else:
            audio_url = get_audio_url(info)
            if not audio_url:
                edit_message(chat_id, message_id, ERROR_TEXT)
                return

            tmp_dir = tempfile.mkdtemp(prefix="tiktok_")
            file_path = download_file(audio_url, tmp_dir, ".mp3")
            if not file_path:
                edit_message(chat_id, message_id, ERROR_TEXT)
                return

            title = info.get("title", "") or "صوت تيك توك"
            author = info.get("author", {}).get("nickname", "مجهول")
            caption = f"🎵 {title[:60]}\n👤 {author}"

            send_audio(chat_id, file_path, caption, reply_to=message_id)
            edit_message(chat_id, message_id, AUDIO_SENT)

    except Exception as e:
        logger.exception(f"Error for user {user_id}: {e}")
        edit_message(chat_id, message_id, ERROR_TEXT)
    finally:
        processing_users.discard(user_id)
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ── Polling loop ───────────────────────────────────────────────

def poll_updates(offset: int):
    """Get updates from Telegram API."""
    result = tg_api("getUpdates", {
        "offset": offset,
        "timeout": 10,
        "allowed_updates": json.dumps(["message", "callback_query"]),
    }, timeout=20)

    if not result.get("ok"):
        return [], offset

    updates = result.get("result", [])
    for u in updates:
        offset = max(offset, u.get("update_id", 0) + 1)

    return updates, offset


def main() -> None:
    """Main polling loop."""
    logger.info("Starting TikTok Downloader Bot (simple polling)...")

    me = tg_api("getMe")
    if me.get("ok"):
        logger.info(f"Bot: @{me['result']['username']}")
    else:
        logger.error(f"Bot token invalid! {me}")
        return

    tg_api("deleteWebhook", {"drop_pending_updates": True})

    offset = 0
    logger.info("Bot is running! Polling for updates...")

    while True:
        try:
            updates, offset = poll_updates(offset)

            for update in updates:
                if "message" in update:
                    handle_message(update["message"])
                elif "callback_query" in update:
                    handle_callback(update["callback_query"])

        except Exception as e:
            logger.exception(f"Polling error: {e}")
            time.sleep(3)


if __name__ == "__main__":
    main()
