"""
bot.py
======
بوت تيليجرام شامل لمعالجة صور المنتجات بالذكاء الاصطناعي.
مستوحى ومطور من مشروع nama/kayan لمعالجة صور الأزياء والمتاجر الإلكترونية.

الميزات الكاملة:
1. عزل خلفيات الصور (Remove Background) بدقة فائقة.
2. البحث عن صور المنتج بالباركود تلقائياً عبر DuckDuckGo Images.
3. رفع شيتات Excel تحتوي بيانات البواركود لمعالجتها دفعة واحدة (Batch).
4. إضافة لوجو الشركة (Watermark) تلقائياً بشفافية مناسبة.
5. قص وتوحيد أبعاد الصور (Crop + Resize إلى 1000×1000).
6. ضغط وتحسين الصور (Optimize) لأقل حجم ممكن.
7. موديلين متخصصين بالذكاء الاصطناعي:
   - الأزياء والملابس والأشخاص (u2net_human_seg)
   - المنتجات العامة والأشياء الصلبة (u2net)
8. أزرار تفاعلية تحت كل صورة للتعديل السريع.
"""

import io
import os
import sys
import json
import time
import logging
import threading
from typing import Dict, Any, Optional

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
from image_pipeline import run_full_pipeline

# مسار ملف اللوجو الافتراضي
LOGO_PATH = os.path.join(os.path.dirname(__file__), "logo.png")

# إعدادات Batch processing من شيتات Excel
BATCH_SETTINGS = {
    "max_per_barcode": 4,    # عدد الصور الأقصى لكل باركود
    "remove_bg": True,       # عزل الخلفية
    "add_logo": True,        # إضافة اللوجو
    "crop_resize": True,     # القص والضبط
    "optimize": True,        # الضغط والتحسين
    "logo_position": "bottom_right",
    "logo_opacity": 0.85,
    "target_size": (1000, 1000),
    "max_size_kb": 250,
}

# حالة المستخدمين أثناء عمليات المعالجة الطويلة (Batch)
user_batch_state: Dict[int, Dict] = {}

# انتظار رفع اللوجو من المستخدمين
waiting_logo: Dict[int, bool] = {}

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
    logo_exists = os.path.exists(LOGO_PATH)
    text = (
        "👋 <b>أهلاً بك في بوت معالجة صور المنتجات الذكي!</b>\n\n"
        "🎯 <b>ما الذي يفعله البوت؟</b>\n"
        "• 🔍 <b>بحث بالباركود:</b> أرسل باركود أي منتج وسيبحث عن صوره تلقائياً\n"
        "• 📊 <b>شيتات Batch:</b> أرسل ملف Excel يحتوي بواركود للمعالجة الجماعية\n"
        "• 🖼️ <b>عزل خلفية:</b> أرسل صورة مباشرة لعزل خلفيتها\n"
        "• 🔖 <b>لوجو تلقائي:</b> يُضاف لوجو شركتك على كل صورة\n"
        "• ✂️ <b>قص وضبط:</b> توحيد أبعاد 1000×1000 تلقائياً\n"
        "• 💾 <b>ضغط وتحسين:</b> أقل حجم بأفضل جودة بصرية\n\n"
        f"🔖 <b>اللوجو:</b> {'✅ محمل وجاهز' if logo_exists else '⚠️ لم يتم رفع لوجو بعد - استخدم /setlogo'} \n"
        f"⚙️ <b>الموديل النشط:</b> {'الأزياء والملابس' if cfg['model'] == 'u2net_human_seg' else 'المنتجات العامة'}\n\n"
        "👇 <i>يمكنك تعديل الإعدادات من الأزرار أدناه:</i>"
    )
    bot.send_message(chat_id, text, reply_markup=make_settings_markup(chat_id))


@bot.message_handler(commands=["settings", "mode", "model"])
def handle_settings(message: types.Message):
    """فتح لوحة الإعدادات"""
    chat_id = message.chat.id
    bot.send_message(
        chat_id,
        "⚙️ <b>لوحة إعدادات معالجة الصور:</b>\n\nاختر نمط المخرجات والموديل المناسب:",
        reply_markup=make_settings_markup(chat_id)
    )


@bot.message_handler(commands=["setlogo"])
def handle_setlogo(message: types.Message):
    """تعيين انتظار رفع اللوجو من المستخدم"""
    chat_id = message.chat.id
    waiting_logo[chat_id] = True
    bot.send_message(
        chat_id,
        "🔖 <b>رفع لوجو الشركة:</b>\n\n"
        "أرسل ملف اللوجو الآن (PNG بخلفية شفافة مستحسن للحصول على أفضل نتيجة).\n"
        "سيتم استخدامه تلقائياً على كل الصور المعالجة."
    )


@bot.message_handler(commands=["barcode"])
def handle_barcode_cmd(message: types.Message):
    """البحث عن صورة منتج بالباركود عبر الأمر /barcode"""
    chat_id = message.chat.id
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        bot.send_message(
            chat_id,
            "📦 <b>استخدام أمر الباركود:</b>\n\n"
            "<code>/barcode 6223000511223</code>\n\n"
            "أو ببساطة أرسل الباركود كرسالة نصية مباشرة وسيكتشفه البوت تلقائياً!"
        )
        return
    barcode_text = parts[1].strip()
    process_barcode(chat_id, barcode_text)


@bot.message_handler(commands=["help"])
def handle_help(message: types.Message):
    """رسالة المساعدة والتعليمات"""
    text = (
        "📖 <b>دليل الاستخدام الكامل:</b>\n\n"
        "<b>🔍 البحث بالباركود:</b>\n"
        "• أرسل رقم الباركود كرسالة نصية مباشرة\n"
        "• أو استخدم: <code>/barcode 6223000511223</code>\n\n"
        "<b>📊 المعالجة الجماعية (Batch):</b>\n"
        "• أرسل ملف Excel (.xlsx/.xls/.csv) يحتوي عمود باسم <b>barcode</b> أو <b>sku</b> أو <b>ID</b>\n"
        "• سيقوم البوت بمعالجة كل صف تلقائياً وإرسال الصور\n\n"
        "<b>🖼️ عزل الخلفية المباشر:</b>\n"
        "• أرسل أي صورة مباشرة (عادية أو كملف) لعزل خلفيتها\n\n"
        "<b>🔖 الأوامر المتاحة:</b>\n"
        "• /start - الشاشة الرئيسية\n"
        "• /setlogo - رفع لوجو شركتك\n"
        "• /settings - إعدادات الموديل والمخرجات\n"
        "• /barcode [رقم] - البحث بالباركود\n"
        "• /help - هذه الرسالة"
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


# ===========================================================================
# معالجة الباركود
# ===========================================================================

def process_barcode(chat_id: int, barcode: str, extra_keywords: str = ""):
    """البحث عن صور المنتج بالباركود ومعالجتها كاملاً في خيط منفصل."""
    def _run():
        cfg = get_user_config(chat_id)
        logo_path = LOGO_PATH if os.path.exists(LOGO_PATH) else None
        status_msg = bot.send_message(
            chat_id,
            f"🔍 <b>جاري البحث عن صور الباركود:</b> <code>{barcode}</code>\n"
            "⏳ قد تستغرق العملية دقيقة أو أكثر حسب عدد الصور..."
        )

        def notify(msg: str):
            try:
                bot.edit_message_text(
                    f"🔍 <b>معالجة الباركود:</b> <code>{barcode}</code>\n\n⏳ {msg}",
                    chat_id, status_msg.message_id
                )
            except Exception:
                pass

        result = run_full_pipeline(
            barcode=barcode,
            logo_path=logo_path,
            extra_keywords=extra_keywords,
            remove_bg=True,
            bg_model=cfg.get("model", "u2net"),
            add_logo=(logo_path is not None),
            logo_position=BATCH_SETTINGS["logo_position"],
            logo_opacity=BATCH_SETTINGS["logo_opacity"],
            target_size=BATCH_SETTINGS["target_size"],
            max_size_kb=BATCH_SETTINGS["max_size_kb"],
            max_images=BATCH_SETTINGS["max_per_barcode"],
            status_callback=notify
        )

        try:
            bot.delete_message(chat_id, status_msg.message_id)
        except Exception:
            pass

        if result["status"] == "error":
            bot.send_message(chat_id, f"❌ <b>فشل البحث عن الباركود {barcode}:</b>\n{result['error']}")
            return

        images = result["images"]
        bot.send_message(
            chat_id,
            f"✅ <b>تمت معالجة الباركود:</b> <code>{barcode}</code>\n"
            f"📦 عدد الصور: {len(images)} صورة جاهزة للرفع على المتاجر"
        )

        for idx, img_data in enumerate(images, 1):
            final_bytes = img_data["final_bytes"]
            ext = img_data["final_ext"]
            size_kb = img_data["size_kb"]
            img_token = cache_image(img_data.get("original_bytes", final_bytes))

            doc_file = io.BytesIO(final_bytes)
            doc_file.name = f"{barcode}_{idx}.{ext}"

            try:
                bot.send_document(
                    chat_id,
                    doc_file,
                    caption=(
                        f"📦 <b>باركود:</b> <code>{barcode}</code> | <b>صورة {idx}/{len(images)}</b>\n"
                        f"💾 الحجم: <b>{size_kb} كيلوبايت</b>\n"
                        f"📐 الأبعاد: 1000×1000 بكسل"
                    ),
                    reply_markup=make_action_markup(img_token, cfg.get("model", "u2net"))
                )
            except Exception as e:
                logger.error(f"فشل إرسال صورة {idx}: {e}")

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()


# ===========================================================================
# معالجة شيتات Excel (Batch)
# ===========================================================================

def process_excel_batch(chat_id: int, file_bytes: bytes, filename: str):
    """قراءة ملف Excel أو CSV واستخراج البواركود ومعالجتها بالتسلسل."""
    def _run():
        import openpyxl
        import csv

        barcodes = []

        try:
            if filename.lower().endswith(".csv"):
                text = file_bytes.decode("utf-8-sig", errors="replace")
                reader = csv.DictReader(io.StringIO(text))
                headers = [h.lower().strip() for h in (reader.fieldnames or [])]
                barcode_col = next((h for h in headers if any(k in h for k in ["barcode", "sku", "id", "كود", "باركود"])), None)
                if not barcode_col:
                    barcode_col = headers[0] if headers else None
                if barcode_col:
                    for row in reader:
                        val = str(row.get(barcode_col, "")).strip()
                        if val and val not in ("nan", "", "None"):
                            barcodes.append(val)
            else:
                wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
                ws = wb.active
                headers = [str(ws.cell(1, c).value or "").lower().strip() for c in range(1, ws.max_column + 1)]
                barcode_col_idx = None
                for ci, h in enumerate(headers, 1):
                    if any(k in h for k in ["barcode", "sku", "id", "كود", "باركود"]):
                        barcode_col_idx = ci
                        break
                if not barcode_col_idx:
                    barcode_col_idx = 1
                for r in range(2, ws.max_row + 1):
                    val = ws.cell(r, barcode_col_idx).value
                    if val:
                        val = str(val).strip().split(".")[0]  # إزالة .0 من الأرقام
                        if val and val not in ("None", "", "nan"):
                            barcodes.append(val)
        except Exception as e:
            bot.send_message(chat_id, f"❌ فشل قراءة الملف: {e}")
            return

        if not barcodes:
            bot.send_message(chat_id, "⚠️ لم يتم العثور على بيانات باركود في الملف.\nتأكد من وجود عمود باسم barcode أو sku أو ID.")
            return

        bot.send_message(
            chat_id,
            f"📊 <b>تم قراءة {len(barcodes)} باركود من الملف.</b>\n"
            f"⏳ جاري المعالجة... (سيتم إرسال النتائج باركود باركود)"
        )

        for idx, barcode in enumerate(barcodes, 1):
            bot.send_chat_action(chat_id, "upload_document")
            try:
                process_barcode(chat_id, barcode)
                time.sleep(3)  # فترة انتظار بين كل باركود لتجنب الحجب
            except Exception as e:
                logger.error(f"خطأ في معالجة الباركود {barcode}: {e}")
                bot.send_message(chat_id, f"⚠️ فشل الباركود {barcode}: {e}")

        bot.send_message(chat_id, f"🎉 <b>اكتملت المعالجة الجماعية!</b>\nتمت معالجة {len(barcodes)} باركود بنجاح.")

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()


# ===========================================================================
# معالجة الرسائل النصية (اكتشاف الباركود تلقائياً)
# ===========================================================================

@bot.message_handler(content_types=["text"])
def handle_text(message: types.Message):
    """اكتشاف الباركود تلقائياً من الرسائل النصية."""
    import re
    chat_id = message.chat.id
    text = (message.text or "").strip()

    # اكتشاف الباركود: رقم من 8 إلى 14 خانة
    barcode_match = re.fullmatch(r'[\d]{8,14}', text)
    if barcode_match:
        process_barcode(chat_id, text)
        return

    # رسالة توجيه للأوامر المتاحة
    bot.send_message(
        chat_id,
        "💡 لم أتعرف على هذا الأمر.\n\n"
        "• أرسل <b>رقم الباركود</b> (8-14 رقم) للبحث عن صور المنتج تلقائياً\n"
        "• أرسل <b>صورة</b> لعزل خلفيتها\n"
        "• أرسل <b>ملف Excel</b> يحتوي بواركود للمعالجة الجماعية\n"
        "• استخدم /help لمزيد من المعلومات"
    )


@bot.message_handler(content_types=["photo"])
def handle_photo(message: types.Message):
    """استقبال الصور المضغوطة."""
    chat_id = message.chat.id
    # إذا كان المستخدم في وضع رفع اللوجو
    if waiting_logo.get(chat_id):
        waiting_logo.pop(chat_id, None)
        bot.send_message(chat_id, "⚠️ يرجى إرسال اللوجو كملف (Send as File/Document) وليس صورة مضغوطة للحفاظ على جودة الشفافية.")
        return
    try:
        photo = message.photo[-1]
        file_info = bot.get_file(photo.file_id)
        image_bytes = bot.download_file(file_info.file_path)
        process_and_reply(chat_id, image_bytes, original_filename="photo.png")
    except Exception as e:
        logger.error(f"فشل تنزيل الصورة: {e}")
        bot.send_message(chat_id, f"❌ تعذر تنزيل الصورة: {e}")


@bot.message_handler(content_types=["document"])
def handle_document(message: types.Message):
    """استقبال الصور والملفات المرسلة بدون ضغط."""
    chat_id = message.chat.id
    doc = message.document
    mime = (doc.mime_type or "").lower()
    fname = (doc.file_name or "").lower()

    # 1. رفع لوجو
    if waiting_logo.get(chat_id):
        waiting_logo.pop(chat_id, None)
        if mime.startswith("image/") or fname.endswith((".png", ".jpg", ".jpeg", ".webp")):
            try:
                file_info = bot.get_file(doc.file_id)
                logo_bytes = bot.download_file(file_info.file_path)
                with open(LOGO_PATH, "wb") as f:
                    f.write(logo_bytes)
                bot.send_message(
                    chat_id,
                    "✅ <b>تم حفظ اللوجو بنجاح!</b>\n"
                    "سيُضاف تلقائياً على كل الصور المعالجة من الآن فصاعداً.\n"
                    "يمكنك تغييره في أي وقت عبر /setlogo"
                )
            except Exception as e:
                bot.send_message(chat_id, f"❌ فشل حفظ اللوجو: {e}")
        else:
            bot.send_message(chat_id, "⚠️ الملف ليس صورة. يرجى إرسال ملف PNG أو JPG.")
        return

    # 2. ملف Excel للمعالجة الجماعية
    excel_exts = (".xlsx", ".xls", ".csv")
    if fname.endswith(excel_exts) or mime in ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                               "application/vnd.ms-excel",
                                               "text/csv"):
        try:
            file_info = bot.get_file(doc.file_id)
            file_bytes = bot.download_file(file_info.file_path)
            bot.send_message(
                chat_id,
                f"📊 <b>تم استلام ملف:</b> <code>{doc.file_name}</code>\n"
                "⏳ جاري قراءة البواركود وبدء المعالجة الجماعية..."
            )
            process_excel_batch(chat_id, file_bytes, fname)
        except Exception as e:
            bot.send_message(chat_id, f"❌ فشل تنزيل الملف: {e}")
        return

    # 3. صورة عادية
    valid_img_exts = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff")
    if mime.startswith("image/") or fname.endswith(valid_img_exts):
        try:
            file_info = bot.get_file(doc.file_id)
            image_bytes = bot.download_file(file_info.file_path)
            process_and_reply(chat_id, image_bytes, original_filename=doc.file_name or "document.png")
        except Exception as e:
            bot.send_message(chat_id, f"❌ تعذر تنزيل الملف: {e}")
        return

    bot.reply_to(message, "⚠️ نوع الملف غير مدعوم. يرجى إرسال صورة أو ملف Excel أو CSV.")


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
