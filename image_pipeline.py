"""
image_pipeline.py
=================
خط معالجة صور المنتجات الكامل مستوحى من مشروع nama/kayan:

الخطوات:
1. البحث عن صور المنتج بالباركود عبر DuckDuckGo Images
2. تنزيل الصور المناسبة وفلترة الفاشلة
3. عزل الخلفية (Remove Background) باستخدام rembg
4. إضافة لوجو الشركة (Watermark) بشفافية مناسبة
5. القص والضبط (Crop + Resize) لأبعاد موحدة 1000×1000
6. الضغط والتحسين (Optimize) لأقل حجم ممكن بجودة بصرية عالية
"""

import io
import os
import time
import logging
import requests
from typing import Optional, List, Tuple, Dict
from PIL import Image, ImageOps, ImageDraw, ImageFilter

logger = logging.getLogger("ImagePipeline")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

# أبعاد المنتج الموحدة المطلوبة للمتاجر الإلكترونية (800×800 بكسل)
TARGET_SIZE = (800, 800)

# نسبة ظهور اللوجو من عرض الصورة (0.12 = 12% حجم صغير أنيق وغير مزعج)
LOGO_RATIO = 0.12

# الحجم المستهدف بالكيلوبايت (بين 70 و 150 كيلوبايت)
TARGET_MIN_KB = 70
TARGET_MAX_KB = 150
DEFAULT_WEBP_QUALITY = 88


# ===========================================================================
# 1. البحث عن اسم المنتج والصور بالباركود
# ===========================================================================

BLACKLIST_DOMAINS = [
    "barcode lookup", "17track", "search upc", "free barcode",
    "upcitemdb", "barcodable", "ecomsource", "buycott", "checkcosmetic",
    "track24", "parcelsapp"
]

def resolve_product_title(barcode: str) -> Optional[str]:
    """
    استخراج اسم المنتج الحقيقي من محركات البحث باستخدام رقم الباركود.
    """
    try:
        from ddgs import DDGS
    except ImportError:
        return None

    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(barcode, max_results=8))
            for r in results:
                title = r.get("title", "")
                href = r.get("href", "")

                # تخطي المواقع التافهة أو صفحات تتبع الطرود العامة
                if any(bad in title.lower() or bad in href.lower() for bad in BLACKLIST_DOMAINS):
                    continue

                if title and len(title.strip()) > 4:
                    # تنظيف العنوان من الإضافات التجارية مثل أسماء المتاجر والأسعار
                    import re
                    clean = re.split(r' [–—|] | - |\bPrice\b|\bReviews\b|\bSpecs\b|\bOffers\b', title, flags=re.IGNORECASE)[0].strip()
                    clean = re.sub(r'^[^\w]+|[^\w)]+$', '', clean)
                    if len(clean) > 3:
                        logger.info(f"تم التعرف على المنتج: '{clean}' للباركود: {barcode}")
                        return clean
    except Exception as e:
        logger.warning(f"تعذر استخراج اسم المنتج عبر البحث النصي: {e}")

    return None


def search_images_by_barcode(
    barcode: str,
    extra_keywords: str = "",
    max_results: int = 10
) -> Tuple[List[str], Optional[str]]:
    """
    البحث الذكي عن صور المنتج:
    1. استخراج اسم المنتج الحقيقي من الباركود.
    2. البحث عن صور المنتج بدقة باستخدام اسمه والباركود.
    يُرجع (قائمة بروابط الصور, اسم المنتج المكتشف إن وجد).
    """
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            logger.error("المكتبة ddgs غير مثبتة. قم بتنفيذ: pip install ddgs")
            return [], None

    urls = []
    search_queries = []
    resolved_name = None

    # 1. إذا كان اسم المنتج معروفاً مسبقاً (من شيت الإكسل مثلاً)، فهو الأولوية القصوى والمطلقة
    if extra_keywords and len(extra_keywords.strip()) > 3:
        clean_kw = extra_keywords.strip()
        search_queries.append(clean_kw)
        resolved_name = clean_kw
        logger.info(f"استخدام اسم المنتج الأساسي الموفر: '{clean_kw}'")

    # 2. إذا لم يكن متوفراً اسم، نحاول استخراجه من الباركود
    else:
        product_name = resolve_product_title(barcode)
        if product_name:
            search_queries.append(product_name)
            resolved_name = product_name

    # 3. محاولات إضافية بدمج الباركود واسم المنتج إن وجد
    if resolved_name:
        search_queries.append(f"{resolved_name} {barcode}")
    else:
        search_queries.append(f'"{barcode}"')
        search_queries.append(f"{barcode} product")

    for q in search_queries:
        logger.info(f"البحث عن صور للاستعلام: '{q}'")
        try:
            with DDGS() as ddgs:
                results = list(ddgs.images(q, max_results=max_results))
                for r in results:
                    u = r.get("image", "")
                    if u and u.startswith("http") and u not in urls:
                        urls.append(u)
        except Exception as e:
            logger.warning(f"خطأ في البحث عن الصور لـ '{q}': {e}")

        # إذا وجدنا صوراً كافية للمنتج نكتفي
        if len(urls) >= 4:
            break

    logger.info(f"تم العثور على {len(urls)} رابط صورة للمنتج.")
    return urls, resolved_name


def download_image(url: str, timeout: int = 12) -> Optional[bytes]:
    """تنزيل صورة من رابط مع فحص أن الملف فعلاً صورة صالحة."""
    try:
        res = requests.get(url, headers=HEADERS, timeout=timeout, stream=True)
        if res.status_code != 200:
            return None

        content_type = res.headers.get("Content-Type", "")
        if "image" not in content_type and "octet-stream" not in content_type:
            return None

        data = res.content
        if len(data) < 2000:  # أقل من 2 كيلوبايت = صورة وهمية
            return None

        # التحقق النهائي أن الصورة قابلة للفتح
        Image.open(io.BytesIO(data)).verify()
        return data
    except Exception as e:
        logger.debug(f"فشل تنزيل {url[:60]}...: {e}")
        return None


def download_best_images(
    urls: List[str],
    max_download: int = 4,
    min_dimension: int = 300
) -> List[bytes]:
    """
    تنزيل أول N صور ناجحة من قائمة الروابط مع فحص أبعادها.
    يتجاهل الصور الصغيرة جداً أو غير الصالحة.
    """
    downloaded = []
    for url in urls:
        if len(downloaded) >= max_download:
            break
        data = download_image(url)
        if data:
            try:
                img = Image.open(io.BytesIO(data))
                w, h = img.size
                if w >= min_dimension and h >= min_dimension:
                    downloaded.append(data)
                    logger.info(f"✓ تم تنزيل صورة {w}x{h} من {url[:60]}...")
                else:
                    logger.debug(f"✗ صورة صغيرة ({w}x{h}) - تجاهل")
            except Exception:
                pass
        time.sleep(0.3)  # تأخير بسيط لتجنب الحجب

    logger.info(f"تم تنزيل {len(downloaded)} صورة بنجاح.")
    return downloaded


# ===========================================================================
# 2. عزل الخلفية
# ===========================================================================

def remove_background(image_bytes: bytes, model_name: str = "u2net") -> Optional[bytes]:
    """
    عزل الخلفية وإرجاع PNG بخلفية بيضاء.
    يستخدم موديل u2net للمنتجات العامة (أفضل من u2net_human_seg للمنتجات الصلبة).
    """
    try:
        from image_processor import process_image_bytes
        result, _ = process_image_bytes(
            image_bytes,
            bg_color=(255, 255, 255),
            model_name=model_name,
            fill_holes=True,
            feather_sigma=0.6
        )
        return result
    except Exception as e:
        logger.error(f"فشل عزل الخلفية: {e}")
        return None


# ===========================================================================
# 3. إضافة اللوجو (Watermark)
# ===========================================================================

def add_logo_watermark(
    product_bytes: bytes,
    logo_path: str,
    position: str = "bottom_right",
    opacity: float = 0.85,
    ratio: float = LOGO_RATIO
) -> bytes:
    """
    إضافة لوجو الشركة على صورة المنتج.

    المعاملات:
    - product_bytes: بايتات صورة المنتج بخلفية بيضاء
    - logo_path: مسار ملف اللوجو (PNG مفضلاً لدعم الشفافية)
    - position: موضع اللوجو: bottom_right | bottom_left | top_right | top_left | center
    - opacity: شفافية اللوجو (0.0 = شفاف تماماً، 1.0 = معتم كامل)
    - ratio: نسبة حجم اللوجو من عرض الصورة

    المخرجات: بايتات الصورة النهائية بعد إضافة اللوجو
    """
    base_img = Image.open(io.BytesIO(product_bytes)).convert("RGBA")
    bw, bh = base_img.size

    if not os.path.exists(logo_path):
        logger.warning(f"ملف اللوجو غير موجود: {logo_path}")
        # إرجاع الصورة بدون لوجو
        out = io.BytesIO()
        base_img.convert("RGB").save(out, format="PNG", optimize=True)
        out.seek(0)
        return out.read()

    logo = Image.open(logo_path).convert("RGBA")

    # حساب حجم اللوجو المناسب
    logo_w = int(bw * ratio)
    logo_h = int(logo.height * logo_w / logo.width)
    logo = logo.resize((logo_w, logo_h), Image.Resampling.LANCZOS)

    # ضبط الشفافية على قناة Alpha للوجو
    if opacity < 1.0:
        r, g, b, a = logo.split()
        a = a.point(lambda x: int(x * opacity))
        logo = Image.merge("RGBA", (r, g, b, a))

    # تحديد موضع اللوجو
    margin = int(bw * 0.025)
    if position == "bottom_right":
        pos = (bw - logo_w - margin, bh - logo_h - margin)
    elif position == "bottom_left":
        pos = (margin, bh - logo_h - margin)
    elif position == "top_right":
        pos = (bw - logo_w - margin, margin)
    elif position == "top_left":
        pos = (margin, margin)
    else:  # center
        pos = ((bw - logo_w) // 2, (bh - logo_h) // 2)

    # لصق اللوجو على الصورة
    base_img.paste(logo, pos, mask=logo.split()[3])

    out = io.BytesIO()
    base_img.convert("RGB").save(out, format="PNG", optimize=True)
    out.seek(0)
    return out.read()


# ===========================================================================
# 4. القص والضبط وتوحيد الأبعاد
# ===========================================================================

def crop_and_resize(
    image_bytes: bytes,
    target_size: Tuple[int, int] = TARGET_SIZE,
    padding_ratio: float = 0.07,
    bg_color: Tuple[int, int, int] = (255, 255, 255)
) -> bytes:
    """
    قص الحواف الزائدة من الصورة وتوحيد الأبعاد مع إضافة حدود متساوية.

    الخطوات:
    1. قص الحواف البيضاء الزائدة حول المنتج تلقائياً (Auto Crop)
    2. إضافة حواف فارغة (Padding) بنسبة محددة للحصول على شكل مريح
    3. تحجيم الصورة إلى الأبعاد الموحدة المطلوبة (1000×1000)
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    # 1. Auto Crop: قص الخلفية البيضاء الزائدة
    # تحويل إلى صورة ثنائية لتحديد المنتج
    gray = img.convert("L")
    import numpy as np
    arr = np.array(gray)
    # تحديد البكسلات غير البيضاء (المنتج)
    mask = arr < 248
    rows = np.any(mask, axis=1)
    cols = np.any(mask, axis=0)
    if rows.any() and cols.any():
        rmin, rmax = np.where(rows)[0][[0, -1]]
        cmin, cmax = np.where(cols)[0][[0, -1]]
        img = img.crop((cmin, rmin, cmax + 1, rmax + 1))

    # 2. إضافة Padding متساوٍ حول المنتج
    w, h = img.size
    pad = int(max(w, h) * padding_ratio)
    canvas_size = max(w, h) + 2 * pad
    canvas = Image.new("RGB", (canvas_size, canvas_size), bg_color)
    paste_x = (canvas_size - w) // 2
    paste_y = (canvas_size - h) // 2
    canvas.paste(img, (paste_x, paste_y))

    # 3. تحجيم إلى الأبعاد الموحدة
    final = canvas.resize(target_size, Image.Resampling.LANCZOS)

    out = io.BytesIO()
    final.save(out, format="PNG", optimize=True)
    out.seek(0)
    return out.read()


# ===========================================================================
# 5. الضغط والتحسين
# ===========================================================================

def optimize_image(
    image_bytes: bytes,
    target_format: str = "webp",
    min_size_kb: int = TARGET_MIN_KB,
    max_size_kb: int = TARGET_MAX_KB,
    initial_quality: int = DEFAULT_WEBP_QUALITY
) -> Tuple[bytes, str]:
    """
    ضغط وتحسين الصورة لتناسب المتاجر الإلكترونية بدقة عالية وحجم مثالي:
    - الصيغة الأساسية: WebP (أو JPEG إذا طلبت)
    - الحجم المستهدف: بين 70 و 150 كيلوبايت كحد أقصى للصورة
    - الجودة البصرية: عالية جداً بدون تشويش أو ضبابية

    المخرجات: (بايتات الصورة المضغوطة، الامتداد 'webp' أو 'jpg')
    """
    img = Image.open(io.BytesIO(image_bytes))

    # إذا كانت الصورة شفافة ونريد الاحتفاظ بالشفافية
    has_transparency = img.mode in ("RGBA", "LA") or (
        img.mode == "P" and "transparency" in img.info
    )

    fmt = target_format.upper()
    if fmt == "WEBP":
        ext = "webp"
    else:
        ext = "jpg"
        fmt = "JPEG"

    # خلفية بيضاء نقية موحدة Pure White #FFFFFF
    if img.mode != "RGB":
        white_bg = Image.new("RGB", img.size, (255, 255, 255))
        if has_transparency:
            white_bg.paste(img.convert("RGBA"), mask=img.convert("RGBA").split()[3])
        else:
            white_bg.paste(img)
        rgb_img = white_bg
    else:
        rgb_img = img

    # التدرج في ضبط الجودة للوصول لحجم بين 70KB و 150KB
    best_bytes = None
    best_size_kb = 0

    # نبدأ بجودة عالية ونخفض تدريجياً إذا تجاوزت 150KB
    q = initial_quality
    while q >= 45:
        out = io.BytesIO()
        if fmt == "WEBP":
            rgb_img.save(out, format="WEBP", quality=q, method=6)
        else:
            rgb_img.save(out, format="JPEG", quality=q, optimize=True, progressive=True, subsampling=0)

        size_kb = out.tell() / 1024
        best_bytes = out.getvalue()
        best_size_kb = size_kb

        # إذا أصبح الحجم ضمن النطاق المقبول (أقل من أو يساوي 150KB)
        if size_kb <= max_size_kb:
            logger.info(f"الصورة محسوّنة {fmt}: {size_kb:.1f} كيلوبايت (جودة {q}%)")
            return best_bytes, ext

        q -= 4

    return best_bytes, ext


# ===========================================================================
# الدالة الرئيسية - تشغيل خط المعالجة الكامل
# ===========================================================================

def run_full_pipeline(
    barcode: str,
    logo_path: Optional[str] = None,
    extra_keywords: str = "",
    remove_bg: bool = True,
    bg_model: str = "u2net",
    add_logo: bool = True,
    logo_position: str = "bottom_right",
    logo_opacity: float = 0.85,
    target_size: Tuple[int, int] = TARGET_SIZE,
    target_format: str = "webp",
    min_size_kb: int = TARGET_MIN_KB,
    max_size_kb: int = TARGET_MAX_KB,
    max_images: int = 4,
    status_callback=None
) -> Dict:
    """
    تشغيل خط معالجة الصور الكامل لمنتج واحد بالباركود.

    المخرجات:
    {
        "status": "success" | "error",
        "barcode": str,
        "images": [
            {
                "original_bytes": bytes,
                "final_bytes": bytes,
                "final_ext": "jpg" | "png",
                "size_kb": float,
                "url": str
            },
            ...
        ],
        "error": str  # عند الخطأ فقط
    }
    """
    def _notify(msg: str):
        logger.info(msg)
        if status_callback:
            status_callback(msg)

    _notify(f"🔍 البحث عن صور الباركود: {barcode}...")

    # 1. البحث عن الصور واسم المنتج
    urls, product_name = search_images_by_barcode(barcode, extra_keywords, max_results=12)
    if product_name:
        _notify(f"🏷️ تم التعرف على المنتج: <b>{product_name}</b>")

    if not urls:
        return {"status": "error", "barcode": barcode, "product_name": product_name, "images": [], "error": "لم يتم العثور على صور لهذا الباركود"}

    # 2. تنزيل أفضل الصور
    _notify(f"⬇️ جاري تنزيل الصور ({len(urls)} رابط متاح)...")
    raw_images = download_best_images(urls, max_download=max_images)
    if not raw_images:
        return {"status": "error", "barcode": barcode, "images": [], "error": "فشل تنزيل أي صورة صالحة"}

    results = []
    for idx, raw_bytes in enumerate(raw_images, 1):
        url = urls[idx - 1] if idx - 1 < len(urls) else ""
        _notify(f"🖼️ معالجة الصورة {idx}/{len(raw_images)}...")

        current = raw_bytes

        # 3. عزل الخلفية
        if remove_bg:
            _notify(f"🤖 عزل الخلفية (صورة {idx})...")
            removed = remove_background(current, model_name=bg_model)
            if removed:
                current = removed

        # 4. القص والضبط
        _notify(f"✂️ قص وضبط الأبعاد (صورة {idx})...")
        current = crop_and_resize(current, target_size=target_size)

        # 5. إضافة اللوجو
        if add_logo and logo_path:
            _notify(f"🔖 إضافة اللوجو (صورة {idx})...")
            current = add_logo_watermark(
                current, logo_path,
                position=logo_position,
                opacity=logo_opacity
            )

        # 6. الضغط والتحسين
        _notify(f"💾 ضغط وتحسين إلى WebP (صورة {idx})...")
        final_bytes, final_ext = optimize_image(
            current,
            target_format=target_format,
            min_size_kb=min_size_kb,
            max_size_kb=max_size_kb
        )
        size_kb = len(final_bytes) / 1024

        results.append({
            "original_bytes": raw_bytes,
            "final_bytes": final_bytes,
            "final_ext": final_ext,
            "size_kb": round(size_kb, 1),
            "url": url
        })

    _notify(f"✅ اكتملت المعالجة: {len(results)} صورة جاهزة!")
    return {
        "status": "success",
        "barcode": barcode,
        "product_name": product_name,
        "images": results,
        "error": None
    }
