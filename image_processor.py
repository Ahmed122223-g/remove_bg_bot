"""
image_processor.py
==================
وحدة معالجة وعزل الخلفية للصور باستخدام الذكاء الاصطناعي (rembg + u2net).
مستوحاة ومطورة بناءً على نموذج nama/kayan لعزل صور المنتجات والأزياء بدقة فائقة:
1. إمكانية الاختيار بين موديل الملابس والأشخاص (u2net_human_seg) وموديل المنتجات العامة (u2net).
2. معالجة الثقوب الداخلية (Binary Fill Holes) لمنع تفريغ أجزاء من الملابس أو المنتجات بالخطأ.
3. تنعيم حواف العزل (Feathering) بفلتر غاوسي لمنع التسنن والتشوهات.
4. دعم الخلفية الشفافة (PNG)، الخلفية البيضاء النقية (RGB)، أو أي لون مخصص.
"""

import io
import os
import logging
from typing import Optional, Tuple

# في بيئة فيرسيل (Vercel Serverless) يجب توجيه مجلد النماذج إلى /tmp
if os.environ.get("VERCEL"):
    os.environ["U2NET_HOME"] = "/tmp/.u2net"

from PIL import Image, ImageOps
import numpy as np
import scipy.ndimage as ndimage
from rembg import new_session, remove as rembg_remove

logger = logging.getLogger("ImageProcessor")

# تخزين الجلسات (Sessions) لمنع إعادة تحميل أوزان الموديل في كل صورة
_sessions = {}

def get_session(model_name: str = "u2net_human_seg"):
    """
    استرجاع جلسة الموديل المحددة أو إنشاؤها إذا لم تكن موجودة.
    الموديلات المدعومة الشائعة:
    - 'u2net_human_seg': ممتاز للأشخاص، الأزياء، الطرح، الموديلز
    - 'u2net': ممتاز للمنتجات العامة، الأحذية، الحقائب، الإلكترونيات
    - 'u2netp': موديل فائق الخفة والسرعة مناسب جداً للاستضافة السحابية وفيرسيل (4.7 ميجابايت فقط)
    """
    global _sessions
    if model_name not in _sessions:
        logger.info(f"تحميل موديل الذكاء الاصطناعي: {model_name}...")
        _sessions[model_name] = new_session(model_name)
        logger.info(f"تم تحميل الموديل {model_name} بنجاح.")
    return _sessions[model_name]


def process_image_bytes(
    image_bytes: bytes,
    bg_color: Optional[Tuple[int, int, int]] = (255, 255, 255),
    model_name: str = "u2net_human_seg",
    fill_holes: bool = True,
    feather_sigma: float = 0.8
) -> Tuple[bytes, str]:
    """
    عزل خلفية الصورة من مصفوفة بايتات وإعادتها بالخلفية المطلوبة.

    المعاملات:
    - image_bytes: بايتات الصورة الأصلية
    - bg_color: لون الخلفية (R, G, B) مثل (255, 255, 255) للأبيض، أو None للحصول على خلفية شفافة PNG
    - model_name: 'u2net_human_seg' أو 'u2net'
    - fill_holes: تفعيل ملء الفراغات الداخلية لتجنب ثقوب الملابس
    - feather_sigma: درجة تنعيم الحواف (افتراضياً 0.8)

    المخرجات:
    - (result_bytes, format_ext) -> (البايتات الناتجة، صيغة الملف مثل 'png')
    """
    # 1. فتح الصورة وتصحيح الاتجاه إن وُجد EXIF orientation
    orig_img = Image.open(io.BytesIO(image_bytes))
    orig_img = ImageOps.exif_transpose(orig_img)
    orig_rgb = orig_img.convert("RGB")
    a_orig = np.array(orig_rgb)

    # 2. تشغيل عزل الخلفية عبر rembg
    session = get_session(model_name)
    rembg_out_bytes = rembg_remove(image_bytes, session=session)
    rembg_img = Image.open(io.BytesIO(rembg_out_bytes))
    rembg_img = ImageOps.exif_transpose(rembg_img)

    # التأكد من تطابق الأبعاد
    if rembg_img.size != orig_rgb.size:
        rembg_img = rembg_img.resize(orig_rgb.size, Image.Resampling.LANCZOS)

    alpha = np.array(rembg_img)[:, :, 3]

    # 3. قناع العزل (Mask)
    mask = (alpha > 20)

    # 4. ملء الفراغات الداخلية (Binary Fill Holes) لحماية تفاصيل المنتج والملابس
    if fill_holes:
        mask = ndimage.binary_fill_holes(mask)

    # 5. تنعيم الحواف (Feathering)
    if feather_sigma > 0:
        feathered = ndimage.gaussian_filter(mask.astype(float), sigma=feather_sigma)
        feathered[feathered > 0.95] = 1.0
        feathered[feathered < 0.05] = 0.0
    else:
        feathered = mask.astype(float)

    out_buf = io.BytesIO()

    # 6. إذا كان المطلوب خلفية شفافة (Transparent PNG)
    if bg_color is None:
        alpha_channel = (feathered * 255).astype(np.uint8)
        rgba_arr = np.dstack((a_orig, alpha_channel))
        final_img = Image.fromarray(rgba_arr, mode="RGBA")
        final_img.save(out_buf, format="PNG", optimize=True)
        format_name = "png"
    else:
        # إذا كان المطلوب خلفية ملونة (أبيض ناصع لمواقع التسوق مثل نون وجوميا)
        weight = feathered[:, :, np.newaxis]
        bg_arr = np.array(bg_color, dtype=float)
        final_arr = (a_orig.astype(float) * weight + bg_arr * (1.0 - weight)).astype(np.uint8)
        final_img = Image.fromarray(final_arr, mode="RGB")
        final_img.save(out_buf, format="PNG", optimize=True)
        format_name = "png"

    out_buf.seek(0)
    return out_buf.read(), format_name
