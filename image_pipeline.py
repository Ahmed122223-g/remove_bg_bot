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

# أبعاد المنتج الموحدة المطلوبة للمتاجر الإلكترونية
TARGET_SIZE = (1000, 1000)

# نسبة ظهور اللوجو من حجم الصورة (0.18 = 18%)
LOGO_RATIO = 0.18

# جودة الضغط (1-95)
JPEG_QUALITY = 82


# ===========================================================================
# 1. البحث عن الصور بالباركود
# ===========================================================================

def search_images_by_barcode(
    barcode: str,
    extra_keywords: str = "",
    max_results: int = 8
) -> List[str]:
    """
    البحث عن صور المنتج باستخدام الباركود عبر DuckDuckGo Images.
    يُرجع قائمة بروابط الصور المجدية.
    """
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            logger.error("المكتبة ddgs غير مثبتة. قم بتنفيذ: pip install ddgs")
            return []

    query = f"{barcode} product"
    if extra_keywords:
        query += f" {extra_keywords}"

    logger.info(f"البحث عن صور: '{query}'")
    urls = []
    try:
        with DDGS() as ddgs:
            results = ddgs.images(
                query,
                max_results=max_results,
                size="Medium",
                type_image="photo"
            )
            for r in results:
                url = r.get("image", "")
                if url and url.startswith("http"):
                    urls.append(url)
    except Exception as e:
        logger.warning(f"خطأ في البحث عبر DuckDuckGo: {e}. محاولة بدون فلاتر...")
        try:
            with DDGS() as ddgs:
                results = ddgs.images(query, max_results=max_results)
                for r in results:
                    url = r.get("image", "")
                    if url and url.startswith("http"):
                        urls.append(url)
        except Exception as e2:
            logger.error(f"فشل البحث تماماً: {e2}")

    logger.info(f"تم العثور على {len(urls)} رابط صورة.")
    return urls


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
    max_size_kb: int = 250,
    quality: int = JPEG_QUALITY
) -> Tuple[bytes, str]:
    """
    ضغط وتحسين الصورة لأصغر حجم ممكن بجودة بصرية مقبولة للمتاجر الإلكترونية.

    الاستراتيجية:
    - يجرب حفظ كـ JPEG أولاً (أفضل ضغطاً للمنتجات ذات الخلفية البيضاء الصلبة)
    - إذا كانت الصورة بخلفية شفافة يحفظ كـ PNG مع ضغط كامل (compress_level=9)
    - يخفض الجودة تدريجياً حتى يصل للحجم المطلوب

    المخرجات: (بايتات الصورة المضغوطة، الامتداد 'jpg' أو 'png')
    """
    img = Image.open(io.BytesIO(image_bytes))
    has_transparency = img.mode in ("RGBA", "LA") or (
        img.mode == "P" and "transparency" in img.info
    )

    if has_transparency:
        # تصدير PNG مضغوط
        out = io.BytesIO()
        img.convert("RGBA").save(out, format="PNG", optimize=True, compress_level=9)
        out.seek(0)
        return out.read(), "png"

    # تحويل إلى RGB وحفظ JPEG
    rgb = img.convert("RGB")
    q = quality
    while q >= 55:
        out = io.BytesIO()
        rgb.save(out, format="JPEG", quality=q, optimize=True, progressive=True, subsampling=0)
        size_kb = out.tell() / 1024
        if size_kb <= max_size_kb or q == 55:
            out.seek(0)
            logger.info(f"الصورة محسوّنة: {size_kb:.1f} كيلوبايت (جودة {q}%)")
            return out.read(), "jpg"
        q -= 5

    out.seek(0)
    return out.read(), "jpg"


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
    max_size_kb: int = 250,
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

    # 1. البحث عن الصور
    urls = search_images_by_barcode(barcode, extra_keywords, max_results=12)
    if not urls:
        return {"status": "error", "barcode": barcode, "images": [], "error": "لم يتم العثور على صور لهذا الباركود"}

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
        _notify(f"💾 ضغط وتحسين (صورة {idx})...")
        final_bytes, final_ext = optimize_image(current, max_size_kb=max_size_kb)
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
        "images": results,
        "error": None
    }
