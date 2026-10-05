"""
config.py
=========
إعدادات وثوابت بوت تيليجرام لعزل الخلفية.
يتم تحميل الإعدادات تلقائياً من ملف متغيرات البيئة (.env).
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# تحميل ملف .env من نفس مسار المجلد
ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=ENV_PATH)

# التوكن الخاص بالبوت
BOT_TOKEN = os.getenv("BOT_TOKEN", "8631248092:AAFkTZu7eDgiBADhVc5LSheDAv9-gzWVrIA").strip()

# الموديل الافتراضي (الأزياء والملابس والأشخاص كما في مشروع nama)
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "u2net_human_seg").strip()

# نمط المخرجات الافتراضي:
# 'both' = إرسال صورة بخلفية بيضاء نقية + ملف PNG شفاف مفرغ
# 'white' = خلفية بيضاء فقط
# 'transparent' = خلفية شفافة فقط
DEFAULT_OUTPUT_MODE = os.getenv("DEFAULT_OUTPUT_MODE", "both").strip()

# تفعيل ملء الفراغات الداخلية افتراضياً لحماية الملابس
DEFAULT_FILL_HOLES = os.getenv("DEFAULT_FILL_HOLES", "True").strip().lower() in ("true", "1", "yes")

# مسار حفظ إعدادات المستخدمين
SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "user_settings.json")
