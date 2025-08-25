# Divar Telegram Bot (Webhook, Koyeb-ready)

- FastAPI + python-telegram-bot v21 (وبهوک)
- کیبوردهای ایموجی‌دار ۳×۳
- فلوهای مرحله‌ای کامل برای دسته‌ها (خودرو، املاک، استخدام، موبایل، الکترونیک، مد و پوشاک، خانه و آشپزخانه، سرگرمی، حیوانات)
- ذخیره پلی‌لیست در SQLite/PostgreSQL
- ارسال یک‌باره نتایج اولیه، سپس فقط آگهی‌های جدید
- آلبوم عکس‌ها + لینک «مشاهده در دیوار» + زمان نسبی + تلاش برای استخراج شماره از متن

## Env
- `TELEGRAM_TOKEN` (لازمی)
- `PUBLIC_BASE_URL` مثل `https://your-app.koyeb.app` (لازمی)
- `PORT` (توسط Koyeb ست می‌شود)
- `DATABASE_URL` (اختیاری؛ پیش‌فرض SQLite)
- `CHECK_INTERVAL_SECONDS` (اختیاری، پیش‌فرض 90)

## اجرای لوکال
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export TELEGRAM_TOKEN=123:ABC
export PUBLIC_BASE_URL=http://localhost:8000
uvicorn app:app --reload
# یک بار وبهوک:
curl http://localhost:8000/set-webhook 
