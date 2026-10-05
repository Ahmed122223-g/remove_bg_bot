"""
api/webhook.py
==============
معالج الويب هوك (Serverless Webhook Handler) لنشر البوت على منصة فيرسيل (Vercel).
مبني بنفس نمط وهيكلية البوت الموجود في E:/projects/EDU/admagh/bot_admagh.

المسارات:
- POST /api/webhook : استقبال التحديثات من تيليجرام
- GET  /            : صفحة حالة البوت والتحكم في الويب هوك (تفعيله، فحصه، أو حذفه)
"""

import os
import sys
import json
import time
import uuid
import urllib.request
import urllib.parse
from http.server import BaseHTTPRequestHandler

# إضافة المسار الرئيسي للمشروع لتمكين استيراد image_processor و config
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# في بيئة فيرسيل يتم توجيه نماذج الذكاء الاصطناعي إلى /tmp
os.environ["U2NET_HOME"] = "/tmp/.u2net"

# قراءة المتغيرات
BOT_TOKEN = os.environ.get("BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN", "8631248092:AAFkTZu7eDgiBADhVc5LSheDAv9-gzWVrIA").strip()
DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL", "u2net_human_seg").strip()
DEFAULT_MODE = os.environ.get("DEFAULT_OUTPUT_MODE", "both").strip()

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
TELEGRAM_FILE_API = f"https://api.telegram.org/file/bot{BOT_TOKEN}"

# استيراد محرك عزل الصور
try:
    from image_processor import process_image_bytes
except Exception as e:
    print(f"Warning: Failed to import image_processor directly: {e}")
    process_image_bytes = None

# ذاكرة مؤقتة للمستخدمين والصور في الجلسة السحابية الحالية
user_configs = {}
cached_images = {}


# ==================== دوال التواصل مع تيليجرام ====================

def send_telegram_request(method: str, data: dict):
    """إرسال طلب JSON عادي إلى تيليجرام"""
    url = f"{TELEGRAM_API}/{method}"
    headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode("utf-8"),
        headers=headers,
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            return json.loads(res.read().decode("utf-8"))
    except Exception as e:
        print(f"Error in send_telegram_request ({method}): {e}")
        return None


def send_telegram_file(method: str, chat_id: int, file_field: str, file_bytes: bytes, filename: str, extra_data: dict = None):
    """إرسال ملفات بصيغة multipart/form-data بدون الحاجة لمكتبات خارجية"""
    boundary = f"----FormBoundary{uuid.uuid4().hex}"
    body = bytearray()

    fields = extra_data.copy() if extra_data else {}
    fields["chat_id"] = str(chat_id)

    # إضافة الحقول النصية
    for key, val in fields.items():
        if val is None:
            continue
        if isinstance(val, (dict, list)):
            val = json.dumps(val)
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
        body.extend(f"{val}\r\n".encode("utf-8"))

    # إضافة ملف الصورة
    content_type = "image/png" if filename.lower().endswith(".png") else "application/octet-stream"
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode("utf-8"))
    body.extend(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
    body.extend(file_bytes)
    body.extend(b"\r\n")

    body.extend(f"--{boundary}--\r\n".encode("utf-8"))

    url = f"{TELEGRAM_API}/{method}"
    req = urllib.request.Request(
        url,
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.loads(res.read().decode("utf-8"))
    except Exception as e:
        print(f"Error in send_telegram_file ({method}): {e}")
        return None


def send_message(chat_id: int, text: str, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return send_telegram_request("sendMessage", payload)


def send_chat_action(chat_id: int, action: str = "upload_photo"):
    return send_telegram_request("sendChatAction", {"chat_id": chat_id, "action": action})


def delete_message(chat_id: int, message_id: int):
    return send_telegram_request("deleteMessage", {"chat_id": chat_id, "message_id": message_id})


def answer_callback_query(callback_query_id: str, text: str = None, show_alert: bool = False):
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    if show_alert:
        payload["show_alert"] = show_alert
    return send_telegram_request("answerCallbackQuery", payload)


def download_telegram_file(file_id: str) -> bytes:
    """جلب رابط الملف وتنزيل بايتات الصورة"""
    res = send_telegram_request("getFile", {"file_id": file_id})
    if not res or not res.get("ok"):
        raise RuntimeError("تعذر الحصول على معلومات الملف من تيليجرام")

    file_path = res["result"]["file_path"]
    download_url = f"{TELEGRAM_FILE_API}/{file_path}"
    with urllib.request.urlopen(download_url, timeout=20) as response:
        return response.read()


# ==================== لوحات المفاتيح والأزرار ====================

def get_settings_keyboard(cfg: dict):
    mode = cfg.get("mode", DEFAULT_MODE)
    model = cfg.get("model", DEFAULT_MODEL)

    mode_both = "✅ كلاهما (أبيض + شفاف)" if mode == "both" else "كلاهما (أبيض + شفاف)"
    mode_white = "✅ أبيض فقط" if mode == "white" else "أبيض فقط"
    mode_trans = "✅ شفاف فقط" if mode == "transparent" else "شفاف فقط"

    model_fashion = "✅ أزياء وملابس" if model == "u2net_human_seg" else "أزياء وملابس"
    model_prod = "✅ منتجات عامة" if model == "u2net" else "منتجات عامة"

    return {
        "inline_keyboard": [
            [{"text": mode_both, "callback_data": "cfg_mode_both"}],
            [
                {"text": mode_white, "callback_data": "cfg_mode_white"},
                {"text": mode_trans, "callback_data": "cfg_mode_transparent"}
            ],
            [
                {"text": model_fashion, "callback_data": "cfg_mod_u2net_human_seg"},
                {"text": model_prod, "callback_data": "cfg_mod_u2net"}
            ]
        ]
    }


def get_action_keyboard(token: str, current_model: str):
    other_model = "u2net" if current_model == "u2net_human_seg" else "u2net_human_seg"
    other_name = "🔄 تبديل لموديل المنتجات" if current_model == "u2net_human_seg" else "🔄 تبديل لموديل الأزياء"
    return {
        "inline_keyboard": [
            [
                {"text": "⚪ أبيض", "callback_data": f"act_{token}_white"},
                {"text": "⚫ أسود", "callback_data": f"act_{token}_black"},
                {"text": "🏁 شفاف PNG", "callback_data": f"act_{token}_trans"}
            ],
            [
                {"text": other_name, "callback_data": f"act_{token}_mod_{other_model}"}
            ]
        ]
    }


def get_user_cfg(chat_id: int):
    if chat_id not in user_configs:
        user_configs[chat_id] = {
            "mode": DEFAULT_MODE,
            "model": DEFAULT_MODEL,
            "fill_holes": True
        }
    return user_configs[chat_id]


# ==================== معالجة الصور والأوامر ====================

def process_and_send(chat_id: int, image_bytes: bytes, filename: str = "image.png"):
    cfg = get_user_cfg(chat_id)
    send_chat_action(chat_id, "upload_photo")

    status_res = send_message(chat_id, "⏳ <b>جاري العزل بالذكاء الاصطناعي...</b>")
    status_msg_id = status_res.get("result", {}).get("message_id") if status_res else None

    try:
        token = str(uuid.uuid4())[:8]
        cached_images[token] = {"bytes": image_bytes, "time": time.time()}

        # تنظيف الذاكرة القديمة
        if len(cached_images) > 30:
            oldest = min(cached_images.keys(), key=lambda k: cached_images[k]["time"])
            del cached_images[oldest]

        mode = cfg.get("mode", DEFAULT_MODE)
        model = cfg.get("model", DEFAULT_MODEL)

        t0 = time.time()

        if mode == "both":
            white_bytes, _ = process_image_bytes(image_bytes, bg_color=(255, 255, 255), model_name=model)
            trans_bytes, _ = process_image_bytes(image_bytes, bg_color=None, model_name=model)
            elapsed = round(time.time() - t0, 2)

            send_telegram_file(
                "sendPhoto", chat_id, "photo", white_bytes, "white.png",
                extra_data={
                    "caption": f"⚪ <b>خلفية بيضاء للمتاجر</b> (⏱️ {elapsed} ثانية)",
                    "parse_mode": "HTML",
                    "reply_markup": get_action_keyboard(token, model)
                }
            )
            send_telegram_file(
                "sendDocument", chat_id, "document", trans_bytes, f"{token}_transparent.png",
                extra_data={
                    "caption": "🏁 <b>ملف PNG مفرغ وشفاف بجودة كاملة</b>",
                    "parse_mode": "HTML"
                }
            )

        elif mode == "white":
            white_bytes, _ = process_image_bytes(image_bytes, bg_color=(255, 255, 255), model_name=model)
            elapsed = round(time.time() - t0, 2)
            send_telegram_file(
                "sendPhoto", chat_id, "photo", white_bytes, "white.png",
                extra_data={
                    "caption": f"⚪ <b>تم عزل الخلفية باللون الأبيض</b> (⏱️ {elapsed} ثانية)",
                    "parse_mode": "HTML",
                    "reply_markup": get_action_keyboard(token, model)
                }
            )

        elif mode == "transparent":
            trans_bytes, _ = process_image_bytes(image_bytes, bg_color=None, model_name=model)
            elapsed = round(time.time() - t0, 2)
            send_telegram_file(
                "sendDocument", chat_id, "document", trans_bytes, f"{token}_transparent.png",
                extra_data={
                    "caption": f"🏁 <b>ملف PNG مفرغ وشفاف</b> (⏱️ {elapsed} ثانية)",
                    "parse_mode": "HTML",
                    "reply_markup": get_action_keyboard(token, model)
                }
            )

        if status_msg_id:
            delete_message(chat_id, status_msg_id)

    except Exception as e:
        print(f"Error processing image: {e}")
        send_message(chat_id, f"❌ حدث خطأ أثناء المعالجة: {e}")


def handle_update(update: dict):
    """معالجة التحديث الوارد من ويب هوك تيليجرام"""
    # 1. معالجة نقرات الأزرار (Callback Queries)
    if "callback_query" in update:
        cq = update["callback_query"]
        cq_id = cq["id"]
        chat_id = cq["message"]["chat"]["id"]
        data = cq.get("data", "")
        cfg = get_user_cfg(chat_id)

        if data.startswith("cfg_mode_"):
            cfg["mode"] = data.replace("cfg_mode_", "")
            answer_callback_query(cq_id, "✅ تم تحديث نمط المخرجات")
            send_telegram_request("editMessageReplyMarkup", {
                "chat_id": chat_id,
                "message_id": cq["message"]["message_id"],
                "reply_markup": get_settings_keyboard(cfg)
            })
            return

        elif data.startswith("cfg_mod_"):
            cfg["model"] = data.replace("cfg_mod_", "")
            answer_callback_query(cq_id, "✅ تم تغيير موديل الذكاء الاصطناعي")
            send_telegram_request("editMessageReplyMarkup", {
                "chat_id": chat_id,
                "message_id": cq["message"]["message_id"],
                "reply_markup": get_settings_keyboard(cfg)
            })
            return

        elif data.startswith("act_"):
            parts = data.split("_")
            if len(parts) >= 3:
                token = parts[1]
                action = parts[2]
                cached = cached_images.get(token)
                if not cached:
                    answer_callback_query(cq_id, "⚠️ انتهت صلاحية الصورة المؤقتة. يرجى إعادة إرسالها.", show_alert=True)
                    return

                answer_callback_query(cq_id, "⏳ جاري المعالجة...")
                raw_bytes = cached["bytes"]
                current_model = cfg.get("model", DEFAULT_MODEL)

                if action == "white":
                    res_bytes, _ = process_image_bytes(raw_bytes, bg_color=(255, 255, 255), model_name=current_model)
                    send_telegram_file("sendPhoto", chat_id, "photo", res_bytes, "white.png", {
                        "caption": "⚪ <b>النتيجة بخلفية بيضاء</b>",
                        "parse_mode": "HTML",
                        "reply_markup": get_action_keyboard(token, current_model)
                    })
                elif action == "black":
                    res_bytes, _ = process_image_bytes(raw_bytes, bg_color=(0, 0, 0), model_name=current_model)
                    send_telegram_file("sendPhoto", chat_id, "photo", res_bytes, "black.png", {
                        "caption": "⚫ <b>النتيجة بخلفية سوداء</b>",
                        "parse_mode": "HTML",
                        "reply_markup": get_action_keyboard(token, current_model)
                    })
                elif action == "trans":
                    res_bytes, _ = process_image_bytes(raw_bytes, bg_color=None, model_name=current_model)
                    send_telegram_file("sendDocument", chat_id, "document", res_bytes, f"{token}_trans.png", {
                        "caption": "🏁 <b>ملف PNG شفاف مفرغ</b>",
                        "parse_mode": "HTML",
                        "reply_markup": get_action_keyboard(token, current_model)
                    })
                elif action == "mod" and len(parts) >= 4:
                    new_model = parts[3]
                    m_title = "الأزياء والملابس" if new_model == "u2net_human_seg" else "المنتجات العامة"
                    res_bytes, _ = process_image_bytes(raw_bytes, bg_color=(255, 255, 255), model_name=new_model)
                    send_telegram_file("sendPhoto", chat_id, "photo", res_bytes, "white.png", {
                        "caption": f"🔄 <b>تمت إعادة العزل بموديل {m_title}</b>",
                        "parse_mode": "HTML",
                        "reply_markup": get_action_keyboard(token, new_model)
                    })
            return

    # 2. معالجة الرسائل النصية والصور
    if "message" not in update:
        return

    msg = update["message"]
    chat_id = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()

    # أمر /start
    if text == "/start":
        cfg = get_user_cfg(chat_id)
        welcome = (
            "👋 <b>أهلاً بك في بوت عزل خلفيات الصور الذكي على Vercel!</b>\n\n"
            "✨ <b>كيف يعمل البوت؟</b>\n"
            "فقط أرسل أي صورة هنا (صورة عادية أو كملف Document للحفاظ على الدقة)، "
            "وسيقوم البوت بعزل الخلفية تلقائياً بدقة بالغة بالذكاء الاصطناعي.\n\n"
            "👇 <i>يمكنك تعديل إعداداتك من الأزرار أدناه:</i>"
        )
        send_message(chat_id, welcome, reply_markup=get_settings_keyboard(cfg))
        return

    # أمر /settings أو /mode أو /model
    if text in ("/settings", "/mode", "/model"):
        cfg = get_user_cfg(chat_id)
        send_message(chat_id, "⚙️ <b>لوحة إعدادات عزل الخلفية:</b>", reply_markup=get_settings_keyboard(cfg))
        return

    # أمر /help
    if text == "/help":
        help_text = (
            "📖 <b>دليل الاستخدام:</b>\n\n"
            "1. أرسل صورتك وسيقوم البوت بمعالجتها فوراً.\n"
            "2. يمكنك إرسال الصورة كملف (Send as Document) للحفاظ على جودة الكاميرا الكاملة.\n"
            "3. للتحكم بنمط المخرجات والموديل أرسل /settings."
        )
        send_message(chat_id, help_text)
        return

    # استقبال صورة عادية (Photo)
    if "photo" in msg:
        photos = msg["photo"]
        best_photo = photos[-1]
        try:
            img_bytes = download_telegram_file(best_photo["file_id"])
            process_and_send(chat_id, img_bytes, "photo.png")
        except Exception as e:
            send_message(chat_id, f"❌ فشل تنزيل الصورة: {e}")
        return

    # استقبال صورة كملف (Document)
    if "document" in msg:
        doc = msg["document"]
        mime = (doc.get("mime_type") or "").lower()
        fname = (doc.get("file_name") or "").lower()
        if mime.startswith("image/") or fname.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp")):
            try:
                img_bytes = download_telegram_file(doc["file_id"])
                process_and_send(chat_id, img_bytes, doc.get("file_name") or "image.png")
            except Exception as e:
                send_message(chat_id, f"❌ فشل تنزيل الملف: {e}")
        else:
            send_message(chat_id, "⚠️ الملف المرسل ليس صورة. يرجى إرسال ملف صورة بصيغة (JPG, PNG, WEBP).")
        return


# ==================== كلاس سيرفر فيرسيل (Vercel Handler) ====================

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        """استقبال تحديثات تيليجرام من الويب هوك"""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            update = json.loads(body.decode("utf-8"))
            handle_update(update)
        except Exception as e:
            print(f"Error handling webhook POST: {e}")

        # الرد دائماً بـ 200 OK لمنع تيليجرام من تكرار الإرسال
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True}).encode("utf-8"))

    def do_GET(self):
        """صفحة إدارة الويب هوك وفحص الحالة عبر المتصفح"""
        parsed_url = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed_url.query)

        host = self.headers.get("Host", "localhost")
        webhook_url = f"https://{host}/api/webhook"

        action_result = None

        # إجراء: تفعيل الويب هوك
        if "set_webhook" in query or "setup" in query:
            res = send_telegram_request("setWebhook", {"url": webhook_url})
            action_result = f"تفعيل الويب هوك: {json.dumps(res, ensure_ascii=False)}"

        # إجراء: حذف الويب هوك (للعودة للتشغيل المحلي)
        elif "delete_webhook" in query:
            res = send_telegram_request("deleteWebhook", {})
            action_result = f"حذف الويب هوك: {json.dumps(res, ensure_ascii=False)}"

        # جلب معلومات الويب هوك الحالية
        info = send_telegram_request("getWebhookInfo", {}) or {}
        bot_info = send_telegram_request("getMe", {}) or {}

        html = f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <title>لوحة تحكم بوت عزل الخلفية - Vercel</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 40px; margin: 0; }}
        .card {{ background: #1e293b; border-radius: 12px; padding: 24px; max-width: 650px; margin: auto; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }}
        h1 {{ margin-top: 0; color: #38bdf8; }}
        .btn {{ display: inline-block; padding: 10px 18px; margin: 8px 4px; border-radius: 8px; text-decoration: none; font-weight: bold; cursor: pointer; }}
        .btn-primary {{ background: #0284c7; color: white; }}
        .btn-danger {{ background: #e11d48; color: white; }}
        .btn-info {{ background: #475569; color: white; }}
        pre {{ background: #0b1120; padding: 12px; border-radius: 8px; overflow-x: auto; font-size: 13px; color: #a5f3fc; }}
        .alert {{ background: #064e3b; border: 1px solid #10b981; padding: 12px; border-radius: 8px; margin-bottom: 16px; color: #a7f3d0; }}
    </style>
</head>
<body>
    <div class="card">
        <h1>🤖 بوت عزل خلفيات الصور على Vercel</h1>
        <p>البوت: <b>@{bot_info.get('result', {}).get('username', 'N/A')}</b> ({bot_info.get('result', {}).get('first_name', '')})</p>
        
        {f'<div class="alert">{action_result}</div>' if action_result else ''}

        <h3>🔗 خيارات إدارة الويب هوك:</h3>
        <p>رابط الويب هوك الخاص بك: <code>{webhook_url}</code></p>
        <a class="btn btn-primary" href="?set_webhook=1">⚡ تفعيل الويب هوك (Set Webhook)</a>
        <a class="btn btn-danger" href="?delete_webhook=1">🛑 حذف الويب هوك (Delete Webhook)</a>
        <a class="btn btn-info" href="?">🔄 تحديث الحالة</a>

        <h3>📊 حالة الويب هوك لدى تيليجرام:</h3>
        <pre>{json.dumps(info, indent=2, ensure_ascii=False)}</pre>
    </div>
</body>
</html>"""

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))
