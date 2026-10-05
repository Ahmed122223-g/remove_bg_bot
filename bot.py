"""
bot.py
======
بوت تيليجرام لعزل خلفيات الصور والمنتجات بالذكاء الاصطناعي.
مبني ومطور بناءً على تجربة مشروع nama/kayan لعزل صور الأزياء والمنتجات التجارية.

المميزات:
1. عزل خلفيات الصور بدقة فائقة مع تنعيم الحواف (Feathering) وحماية الفراغات (Fill Holes).
2. موديلين متخصصين:
   - موديل الأزياء والموديلز والملابس (u2net_human_seg)
   - موديل المنتجات العامة والأشياء (u2net)
3. خيارات مخرجات متعددة:
   - خلفية بيضاء نقية (مناسبة لمنصات نون، جوميا، وأمازون)
   - خلفية شفافة PNG (ملف عالي الدقة بدون ضغط)
   - إرسال كلاهما معاً لتوفير الوقت
4. أزرار تفاعلية تحت كل صورة لتغيير لون الخلفية فوراً (أبيض، أسود، شفاف، أو إعادة العزل بموديل مختلف).
5. دعم استقبال الصور العادية أو الصور كملفات (Documents) بدون ضغط.
"""

import io
import os
import sys
import json
import time
import logging
from typing import Dict, Any

# ضبط مخرجات الطرفية لدعم UTF-8 والرموز التعبيرية على ويندوز
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import telebot
from telebot import types

import config
from image_processor import process_image_bytes

# إعداد السجلات (Logging)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("RemoveBgBot")

# تهيئة البوت
bot = telebot.TeleBot(config.BOT_TOKEN, parse_mode="HTML")

# ذاكرة مؤقتة لتخزين إعدادات المستخدمين
user_settings: Dict[int, Dict[str, Any]] = {}

# ذاكرة مؤقتة للصور الأخيرة لتسهيل التعديل السريع عبر الأزرار التفاعلية
# {image_token: {"bytes": b"...", "created_at": timestamp}}
recent_images: Dict[str, Dict[str, Any]] = {}
MAX_CACHE_ITEMS = 50


def load_settings():
    """تحميل إعدادات المستخدمين من الملف"""
    global user_settings
    if os.path.exists(config.SETTINGS_FILE):
        try:
            with open(config.SETTINGS_FILE, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
                user_settings = {int(k): v for k, v in raw_data.items()}
            logger.info(f"تم تحميل إعدادات {len(user_settings)} مستخدم.")
        except Exception as e:
            logger.error(f"خطأ أثناء تحميل الإعدادات: {e}")


def save_settings():
    """حفظ إعدادات المستخدمين إلى الملف"""
    try:
        with open(config.SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump({str(k): v for k, v in user_settings.items()}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"خطأ أثناء حفظ الإعدادات: {e}")


def get_user_config(chat_id: int) -> Dict[str, Any]:
    """جلب إعدادات المستخدم مع تعيين القيم الافتراضية إذا كانت جديدة"""
    if chat_id not in user_settings:
        user_settings[chat_id] = {
            "mode": config.DEFAULT_OUTPUT_MODE,       # 'both', 'white', 'transparent'
            "model": config.DEFAULT_MODEL,           # 'u2net_human_seg', 'u2net'
            "fill_holes": config.DEFAULT_FILL_HOLES, # True, False
        }
        save_settings()
    return user_settings[chat_id]


def cache_image(image_bytes: bytes) -> str:
    """حفظ بايتات الصورة في الذاكرة المؤقتة وإرجاع معرف فريد"""
    import uuid
    # تنظيف الذاكرة إذا امتلأت
    if len(recent_images) > MAX_CACHE_ITEMS:
        oldest_key = min(recent_images.keys(), key=lambda k: recent_images[k]["created_at"])
        del recent_images[oldest_key]

    token = str(uuid.uuid4())[:8]
    recent_images[token] = {
        "bytes": image_bytes,
        "created_at": time.time()
    }
    return token


def make_settings_markup(chat_id: int) -> types.InlineKeyboardMarkup:
    """إنشاء لوحة التحكم التفاعلية للإعدادات"""
    cfg = get_user_config(chat_id)
    kb = types.InlineKeyboardMarkup(row_width=2)

    # أزرار نوع الإخراج
    mode_both = "✅ كلاهما (أبيض + شفاف)" if cfg["mode"] == "both" else "كلاهما (أبيض + شفاف)"
    mode_white = "✅ أبيض فقط" if cfg["mode"] == "white" else "أبيض فقط"
    mode_trans = "✅ شفاف فقط" if cfg["mode"] == "transparent" else "شفاف فقط"

    kb.add(
        types.InlineKeyboardButton(mode_both, callback_data="set_mode_both")
    )
    kb.add(
        types.InlineKeyboardButton(mode_white, callback_data="set_mode_white"),
        types.InlineKeyboardButton(mode_trans, callback_data="set_mode_transparent")
    )

    # أزرار نوع الموديل
    model_fashion = "✅ أزياء وملابس وأشخاص" if cfg["model"] == "u2net_human_seg" else "أزياء وملابس وأشخاص"
    model_prod = "✅ منتجات عامة وأشياء" if cfg["model"] == "u2net" else "منتجات عامة وأشياء"

    kb.add(
        types.InlineKeyboardButton(model_fashion, callback_data="set_model_human"),
        types.InlineKeyboardButton(model_prod, callback_data="set_model_prod")
    )

    # زر حماية الثقوب (Fill Holes)
    holes_text = "✅ حماية تفاصيل الملابس (مفعل)" if cfg.get("fill_holes", True) else "❌ حماية تفاصيل الملابس (معطل)"
    kb.add(types.InlineKeyboardButton(holes_text, callback_data="toggle_fill_holes"))

    return kb


def make_action_markup(img_token: str, current_model: str) -> types.InlineKeyboardMarkup:
    """أزرار تحكم سريعة تحت الصورة المعالجة"""
    kb = types.InlineKeyboardMarkup(row_width=3)
    kb.add(
        types.InlineKeyboardButton("⚪ أبيض", callback_data=f"act_{img_token}_white"),
        types.InlineKeyboardButton("⚫ أسود", callback_data=f"act_{img_token}_black"),
        types.InlineKeyboardButton("🏁 شفاف PNG", callback_data=f"act_{img_token}_trans")
    )
    # خيار التبديل للموديل الآخر
    other_model = "u2net" if current_model == "u2net_human_seg" else "u2net_human_seg"
    other_label = "🔄 تجربة موديل المنتجات" if current_model == "u2net_human_seg" else "🔄 تجربة موديل الأزياء"
    kb.add(types.InlineKeyboardButton(other_label, callback_data=f"act_{img_token}_mod_{other_model}"))
    return kb


@bot.message_handler(commands=["start"])
def handle_start(message: types.Message):
    """الترحيب بالمستخدم وشرح طريقة العمل"""
    chat_id = message.chat.id
    cfg = get_user_config(chat_id)
    text = (
        "👋 <b>أهلاً بك في بوت عزل خلفيات الصور الذكي!</b>\n\n"
        "✨ <b>كيف يعمل البوت؟</b>\n"
        "فقط أرسل أي صورة هنا (سواء صورة عادية أو كملف Document للحفاظ على الدقة العالية)، "
        "وسيقوم البوت بعزل الخلفية تلقائياً بدقة بالغة وبتقنية الذكاء الاصطناعي المستخدمة لمتاجر نون وجوميا.\n\n"
        "⚙️ <b>إعداداتك الحالية:</b>\n"
        f"• نمط المخرجات: <b>{'كلاهما (أبيض + شفاف)' if cfg['mode'] == 'both' else ('أبيض' if cfg['mode'] == 'white' else 'شفاف')}</b>\n"
        f"• الموديل النشط: <b>{'الأزياء والملابس (u2net_human_seg)' if cfg['model'] == 'u2net_human_seg' else 'المنتجات العامة (u2net)'}</b>\n\n"
        "👇 <i>يمكنك تعديل الإعدادات من الأزرار أدناه أو إرسال صورة للبدء فوراً:</i>"
    )
    bot.send_message(chat_id, text, reply_markup=make_settings_markup(chat_id))


@bot.message_handler(commands=["settings", "mode", "model"])
def handle_settings(message: types.Message):
    """فتح لوحة الإعدادات"""
    chat_id = message.chat.id
    cfg = get_user_config(chat_id)
    text = (
        "⚙️ <b>لوحة إعدادات عزل الخلفية:</b>\n\n"
        "اختر نمط المخرجات المفضل والموديل المناسب لنوع صورك:"
    )
    bot.send_message(chat_id, text, reply_markup=make_settings_markup(chat_id))


@bot.message_handler(commands=["help"])
def handle_help(message: types.Message):
    """رسالة المساعدة والتعليمات"""
    text = (
        "📖 <b>دليل الاستخدام السريع:</b>\n\n"
        "1. <b>إرسال الصور:</b> أرسل أي صورة مباشرة كصورة عادية أو أرسلها كملف (Send as File / Document) للحصول على أقصى دقة ممكنة.\n"
        "2. <b>موديل الأزياء والملابس:</b> مخصص للموديلز، الملابس، الحجاب، البورتريهات لتجنب أي ثقوب أو تشويه في الأقمشة.\n"
        "3. <b>موديل المنتجات العامة:</b> مخصص للأحذية، الشنط، الإكسسوارات، الأجهزة، والأدوات.\n"
        "4. <b>الأزرار التفاعلية:</b> تحت كل نتيجة تظهر أزرار لتحويل الخلفية إلى أبيض، أسود، أو شفاف بضغطة زر دون إعادة الإرسال.\n\n"
        "لضبط إعداداتك في أي وقت، استخدم الأمر /settings"
    )
    bot.send_message(message.chat.id, text)


@bot.callback_query_handler(func=lambda call: call.data.startswith("set_") or call.data == "toggle_fill_holes")
def handle_settings_callback(call: types.CallbackQuery):
    """معالجة النقر على أزرار تغيير الإعدادات"""
    chat_id = call.message.chat.id
    cfg = get_user_config(chat_id)

    if call.data == "set_mode_both":
        cfg["mode"] = "both"
    elif call.data == "set_mode_white":
        cfg["mode"] = "white"
    elif call.data == "set_mode_transparent":
        cfg["mode"] = "transparent"
    elif call.data == "set_model_human":
        cfg["model"] = "u2net_human_seg"
    elif call.data == "set_model_prod":
        cfg["model"] = "u2net"
    elif call.data == "toggle_fill_holes":
        cfg["fill_holes"] = not cfg.get("fill_holes", True)

    save_settings()
    try:
        bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=make_settings_markup(chat_id))
        bot.answer_callback_query(call.id, "✅ تم تحديث الإعدادات")
    except Exception:
        pass


@bot.callback_query_handler(func=lambda call: call.data.startswith("act_"))
def handle_action_callback(call: types.CallbackQuery):
    """معالجة الأزرار التفاعلية أسفل الصور المعالجة"""
    parts = call.data.split("_")
    # act_{img_token}_white
    # act_{img_token}_black
    # act_{img_token}_trans
    # act_{img_token}_mod_{model_name}
    if len(parts) < 3:
        bot.answer_callback_query(call.id, "طلب غير صالح")
        return

    img_token = parts[1]
    action_type = parts[2]

    item = recent_images.get(img_token)
    if not item:
        bot.answer_callback_query(call.id, "⚠️ انتهت صلاحية الصورة المؤقتة. يرجى إرسال الصورة من جديد.", show_alert=True)
        return

    chat_id = call.message.chat.id
    cfg = get_user_config(chat_id)
    raw_bytes = item["bytes"]

    bot.answer_callback_query(call.id, "⏳ جاري المعالجة...")
    bot.send_chat_action(chat_id, "upload_document")

    try:
        if action_type == "white":
            res_bytes, _ = process_image_bytes(
                raw_bytes,
                bg_color=(255, 255, 255),
                model_name=cfg["model"],
                fill_holes=cfg.get("fill_holes", True)
            )
            bot.send_photo(
                chat_id,
                res_bytes,
                caption="⚪ <b>النتيجة بخلفية بيضاء نقية</b>",
                reply_markup=make_action_markup(img_token, cfg["model"])
            )
        elif action_type == "black":
            res_bytes, _ = process_image_bytes(
                raw_bytes,
                bg_color=(0, 0, 0),
                model_name=cfg["model"],
                fill_holes=cfg.get("fill_holes", True)
            )
            bot.send_photo(
                chat_id,
                res_bytes,
                caption="⚫ <b>النتيجة بخلفية سوداء</b>",
                reply_markup=make_action_markup(img_token, cfg["model"])
            )
        elif action_type == "trans":
            res_bytes, _ = process_image_bytes(
                raw_bytes,
                bg_color=None,
                model_name=cfg["model"],
                fill_holes=cfg.get("fill_holes", True)
            )
            doc_file = io.BytesIO(res_bytes)
            doc_file.name = f"removed_bg_{img_token}.png"
            bot.send_document(
                chat_id,
                doc_file,
                caption="🏁 <b>ملف PNG مفرغ بخلفية شفافة (دقة كاملة)</b>",
                reply_markup=make_action_markup(img_token, cfg["model"])
            )
        elif action_type == "mod" and len(parts) >= 4:
            new_model = parts[3]
            model_title = "الأزياء والملابس" if new_model == "u2net_human_seg" else "المنتجات العامة"
            res_white, _ = process_image_bytes(
                raw_bytes,
                bg_color=(255, 255, 255),
                model_name=new_model,
                fill_holes=cfg.get("fill_holes", True)
            )
            bot.send_photo(
                chat_id,
                res_white,
                caption=f"🔄 <b>تمت إعادة العزل بموديل {model_title}</b>",
                reply_markup=make_action_markup(img_token, new_model)
            )
    except Exception as e:
        logger.error(f"خطأ أثناء معالجة زر الإجراء: {e}")
        bot.send_message(chat_id, f"❌ حدث خطأ أثناء المعالجة: {e}")


def process_and_reply(chat_id: int, image_bytes: bytes, original_filename: str = "image.png"):
    """المعالجة الرئيسية للصورة وإرسال الرد للمستخدم"""
    cfg = get_user_config(chat_id)
    status_msg = bot.send_message(chat_id, "⏳ <b>جاري العزل بالذكاء الاصطناعي...</b>")
    bot.send_chat_action(chat_id, "upload_photo")

    try:
        img_token = cache_image(image_bytes)
        mode = cfg["mode"]
        model = cfg["model"]
        fill_holes = cfg.get("fill_holes", True)

        start_time = time.time()

        if mode == "both":
            # 1. صورة بخلفية بيضاء
            white_bytes, _ = process_image_bytes(image_bytes, bg_color=(255, 255, 255), model_name=model, fill_holes=fill_holes)
            # 2. ملف شفاف عالي الجودة
            trans_bytes, _ = process_image_bytes(image_bytes, bg_color=None, model_name=model, fill_holes=fill_holes)

            duration = round(time.time() - start_time, 2)

            # إرسال الصورة البيضاء للمعاينة والتجارة
            bot.send_photo(
                chat_id,
                white_bytes,
                caption=f"⚪ <b>خلفية بيضاء للمتاجر</b> (⏱️ {duration} ثانية)",
                reply_markup=make_action_markup(img_token, model)
            )

            # إرسال ملف PNG الشفاف كملف document للحفاظ على أعلى جودة
            doc_file = io.BytesIO(trans_bytes)
            base_name = os.path.splitext(original_filename)[0]
            doc_file.name = f"{base_name}_transparent.png"
            bot.send_document(
                chat_id,
                doc_file,
                caption="🏁 <b>ملف PNG شفاف ومفرغ بجودة كاملة</b>"
            )

        elif mode == "white":
            white_bytes, _ = process_image_bytes(image_bytes, bg_color=(255, 255, 255), model_name=model, fill_holes=fill_holes)
            duration = round(time.time() - start_time, 2)
            bot.send_photo(
                chat_id,
                white_bytes,
                caption=f"⚪ <b>تم عزل الخلفية باللون الأبيض</b> (⏱️ {duration} ثانية)",
                reply_markup=make_action_markup(img_token, model)
            )

        elif mode == "transparent":
            trans_bytes, _ = process_image_bytes(image_bytes, bg_color=None, model_name=model, fill_holes=fill_holes)
            duration = round(time.time() - start_time, 2)
            doc_file = io.BytesIO(trans_bytes)
            base_name = os.path.splitext(original_filename)[0]
            doc_file.name = f"{base_name}_transparent.png"
            bot.send_document(
                chat_id,
                doc_file,
                caption=f"🏁 <b>ملف PNG مفرغ وشفاف</b> (⏱️ {duration} ثانية)",
                reply_markup=make_action_markup(img_token, model)
            )

        # حذف رسالة "جاري المعالجة" بعد الانتهاء
        try:
            bot.delete_message(chat_id, status_msg.message_id)
        except Exception:
            pass

    except Exception as e:
        logger.error(f"خطأ أثناء معالجة الصورة: {e}", exc_info=True)
        try:
            bot.edit_message_text(f"❌ <b>حدث خطأ أثناء معالجة الصورة:</b>\n<code>{e}</code>", chat_id, status_msg.message_id)
        except Exception:
            bot.send_message(chat_id, f"❌ حدث خطأ: {e}")


@bot.message_handler(content_types=["photo"])
def handle_photo(message: types.Message):
    """استقبال الصور المضغوطة العادية من تيليجرام"""
    chat_id = message.chat.id
    try:
        # جلب أعلى دقة للصورة المرسلة
        photo = message.photo[-1]
        file_info = bot.get_file(photo.file_id)
        image_bytes = bot.download_file(file_info.file_path)
        process_and_reply(chat_id, image_bytes, original_filename="photo.png")
    except Exception as e:
        logger.error(f"فشل تنزيل الصورة: {e}")
        bot.send_message(chat_id, f"❌ تعذر تنزيل الصورة: {e}")


@bot.message_handler(content_types=["document"])
def handle_document(message: types.Message):
    """استقبال الصور المرسلة كملف للحفاظ على الجودة الكاملة"""
    chat_id = message.chat.id
    doc = message.document

    # التحقق من أن الملف صورة
    mime = (doc.mime_type or "").lower()
    fname = (doc.file_name or "").lower()
    valid_exts = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff")

    if not (mime.startswith("image/") or fname.endswith(valid_exts)):
        bot.reply_to(message, "⚠️ الملف المرسل ليس صورة. يرجى إرسال ملف بصيغة (JPG, PNG, WEBP, BMP).")
        return

    try:
        file_info = bot.get_file(doc.file_id)
        image_bytes = bot.download_file(file_info.file_path)
        process_and_reply(chat_id, image_bytes, original_filename=doc.file_name or "document.png")
    except Exception as e:
        logger.error(f"فشل تنزيل المستند: {e}")
        bot.send_message(chat_id, f"❌ تعذر تنزيل الملف: {e}")


def main():
    load_settings()
    logger.info("=" * 55)
    logger.info("🚀 جاري بدء تشغيل بوت عزل الخلفية بالذكاء الاصطناعي...")
    logger.info(f"🔑 البوت مسجل بالمعرف: {config.BOT_TOKEN[:10]}...")
    logger.info("=" * 55)

    # التحقق من الاتصال
    try:
        me = bot.get_me()
        logger.info(f"✅ تم الاتصال بنجاح! اسم البوت: @{me.username} ({me.first_name})")
    except Exception as e:
        logger.error(f"❌ تعذر الاتصال بـ Telegram API: {e}")
        sys.exit(1)

    # تشغيل الاستقبال الدائم (Infinity Polling) مع إعادة المحاولة التلقائية عند انقطاع الاتصال
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=60)
        except Exception as e:
            logger.error(f"⚠️ حدث انقطاع في الاتصال: {e}. جاري إعادة المحاولة خلال 5 ثوان...")
            time.sleep(5)


if __name__ == "__main__":
    main()
