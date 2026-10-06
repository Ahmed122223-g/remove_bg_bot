"""
run.py
======
مشغل البوت الذكي والتحقق التلقائي من البيئة والمكتبات.
"""

import sys
import os
import subprocess
import shutil

# ضبط المخرجات لتدعم UTF-8 على ويندوز
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(PROJECT_DIR, ".env")
ENV_EXAMPLE = os.path.join(PROJECT_DIR, ".env.example")
REQ_FILE = os.path.join(PROJECT_DIR, "requirements.txt")


def print_banner():
    print("=" * 65)
    print("   🤖 بوت تيليجرام لعزل خلفيات الصور بالذكاء الاصطناعي")
    print("=" * 65)
    print(f"[*] إصدار بايثون: {sys.version.split()[0]}")
    print(f"[*] مسار العمل: {PROJECT_DIR}\n")


def check_and_create_env():
    """التحقق من وجود ملف .env وإنشاؤه إن لم يكن موجوداً"""
    if not os.path.exists(ENV_FILE):
        if os.path.exists(ENV_EXAMPLE):
            shutil.copyfile(ENV_EXAMPLE, ENV_FILE)
            print("[✓] تم إنشاء ملف متغيرات البيئة (.env) تلقائياً من .env.example")
        else:
            print("[!] تنبيه: ملف .env غير موجود وسيتم استخدام الإعدادات الافتراضية.")
    else:
        print("[✓] تم التحقق من وجود ملف متغيرات البيئة (.env).")


def check_and_install_dependencies():
    """فحص المكاتب وتثبيتها تلقائياً في حال نقصان أي منها"""
    print("[*] جاري فحص بيئة بايثون والمكاتب المطلوبة...")

    required_modules = [
        ("telebot", "pyTelegramBotAPI"),
        ("rembg", "rembg"),
        ("PIL", "Pillow"),
        ("numpy", "numpy"),
        ("scipy", "scipy"),
        ("dotenv", "python-dotenv"),
        ("onnxruntime", "onnxruntime"),
        ("ddgs", "ddgs"),
        ("openpyxl", "openpyxl"),
        ("requests", "requests")
    ]

    missing = []
    for mod_name, pkg_name in required_modules:
        try:
            __import__(mod_name)
        except ImportError:
            missing.append(pkg_name)

    if missing:
        print(f"[!] تم العثور على مكاتب ناقصة: {', '.join(missing)}")
        print("[*] جاري تثبيت الحزم المطلوبة من requirements.txt الآن...")
        print("-" * 50)
        try:
            cmd = [sys.executable, "-m", "pip", "install", "-r", REQ_FILE]
            res = subprocess.run(cmd, cwd=PROJECT_DIR)
            if res.returncode != 0:
                print(f"\n[X] فشل تثبيت المكاتب (رمز الخطأ: {res.returncode}).")
                print("يرجى التأكد من اتصال الإنترنت ثم تنفيذ الأمر يدوياً:")
                print(f"    {sys.executable} -m pip install -r requirements.txt")
                sys.exit(1)
            print("[✓] تم تثبيت المكاتب بنجاح!")
        except Exception as e:
            print(f"[X] خطأ أثناء تشغيل pip: {e}")
            sys.exit(1)
    else:
        print("[✓] كافة المكاتب المطلوبة متوفرة وجاهزة للعمل بنجاح!")


def start_bot():
    """تشغيل السكربت الأساسي للبوت"""
    print("\n" + "=" * 65)
    print("🚀 جاري بدء تشغيل البوت...")
    print("💡 يمكنك إيقاف البوت في أي وقت بالضغط على Ctrl+C")
    print("=" * 65 + "\n")

    import bot
    bot.main()


if __name__ == "__main__":
    print_banner()
    check_and_create_env()
    check_and_install_dependencies()
    try:
        start_bot()
    except KeyboardInterrupt:
        print("\n\n[!] تم إيقاف البوت بواسطة المستخدم.")
    except Exception as e:
        print(f"\n[X] حدث خطأ غير متوقع: {e}")
        input("\nاضغط Enter للخروج...")
