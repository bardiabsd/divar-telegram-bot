from __future__ import annotations
import os, asyncio, logging
from typing import Dict, Any, List, Optional, Tuple
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from datetime import datetime, timezone

from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove,
    InputMediaPhoto
)
from telegram.ext import (
    Application, ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters
)

from models import make_engine, Base, Session, User, Playlist
from divar_client import list_cities, CATEGORY_TOKENS, search as divar_search, humanize_age, fetch_details

# --- Logging ---
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("app")

# --- Config ---
TOKEN = os.environ.get("TELEGRAM_TOKEN")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL")  # e.g. https://your-app.koyeb.app
PORT = int(os.environ.get("PORT", "8000"))
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL_SECONDS", "90"))

if not TOKEN or not PUBLIC_BASE_URL:
    log.warning("TELEGRAM_TOKEN یا PUBLIC_BASE_URL تنظیم نشده است.")

# --- DB ---
engine = make_engine()
Base.metadata.create_all(engine)

# --- PTB App ---
bot_app: Application = ApplicationBuilder().token(TOKEN or "invalid").build()

# --- Builder state ---
# برای هر کاربر: { step, category, city, district, flow, flow_idx, filters }
builder_state: Dict[int, Dict[str, Any]] = {}

# --- Category flows (مرحله‌ای و دقیق) ---
# هر step یا "range" است (min-max) یا "enum" (انتخاب از بین چند گزینه).
CATEGORY_FLOWS: Dict[str, Dict[str, Any]] = {
    "car": {
        "title": "🚗 خودرو",
        "steps": ["mileage", "year", "price"],
        "labels": {
            "mileage": "کارکرد رو انتخاب کن:",
            "year": "سال تولید رو انتخاب کن:",
            "price": "محدوده قیمت رو انتخاب کن:",
        },
        "options": {
            "mileage": {
                "type": "range",
                "choices": [("۰-۵۰هزار", "0-50000"), ("۵۰-۱۰۰هزار", "50000-100000"), ("۱۰۰-۲۰۰هزار", "100000-200000"),
                            ("۲۰۰-۳۵۰هزار", "200000-350000"), ("۳۵۰-۵۰۰هزار", "350000-500000"), ("بالای ۵۰۰هزار", "500000-10000000")]
            },
            "year": {
                "type": "range",
                "choices": [("۱۳۸۵-۱۳۹۰", "2006-2011"), ("۱۳۹۰-۱۳۹۵", "2011-2016"), ("۱۳۹۵-۱۴۰۰", "2016-2021"),
                            ("۱۴۰۰-۱۴۰۳", "2021-2025")]
            },
            "price": {
                "type": "range",
                "choices": [("تا ۳۰۰م", "0-300000000"), ("۳۰۰-۷۰۰م", "300000000-700000000"),
                            ("۷۰۰م-۱.۵م", "700000000-1500000000"), ("بالای ۱.۵م", "1500000000-100000000000")]
            }
        },
        "map_keys": {  # نگاشت نام‌کلید برای رنج‌ها
            "mileage": ("mileage_min", "mileage_max"),
            "year": ("year_min", "year_max"),
            "price": ("price_min", "price_max"),
        }
    },

    "real_estate": {
        "title": "🏠 املاک",
        "steps": ["deal", "type", "meter", "rooms", "price"],
        "labels": {
            "deal": "نوع معامله رو انتخاب کن:",
            "type": "نوع ملک رو انتخاب کن:",
            "meter": "متراژ رو انتخاب کن:",
            "rooms": "تعداد اتاق:",
            "price": "محدوده قیمت:",
        },
        "options": {
            "deal": {"type": "enum", "choices": [("خرید 🏷️", "buy"), ("رهن/اجاره 🔄", "rent")]},
            "type": {"type": "enum", "choices": [("آپارتمان", "apartment"), ("خانه/ویلایی", "house"), ("زمین/کلنگی", "land")]},
            "meter": {"type": "range", "choices": [("تا ۶۰", "0-60"), ("۶۰-۹۰", "60-90"), ("۹۰-۱۲۰", "90-120"),
                                                  ("۱۲۰-۲۰۰", "120-200"), ("۲۰۰+", "200-10000")]},
            "rooms": {"type": "enum", "choices": [("۱", "1"), ("۲", "2"), ("۳", "3"), ("۴+", "4plus")]},
            # برای اجاره، رنج‌ها نمایشی‌اند؛ در جستجو فعلاً فقط price عمومی اعمال می‌شود.
            "price": {"type": "range", "choices": [("تا ۵۰۰م", "0-500000000"),
                                                   ("۵۰۰م-۲م", "500000000-2000000000"),
                                                   ("۲م-۵م", "2000000000-5000000000"),
                                                   ("بیشتر", "5000000000-100000000000")]}
        },
        "map_keys": {"meter": ("meter_min", "meter_max"), "price": ("price_min", "price_max")}
    },

    "jobs": {
        "title": "💼 استخدام",
        "steps": ["field", "seniority", "work_type", "salary"],
        "labels": {
            "field": "حوزه‌ی کاری رو انتخاب کن:",
            "seniority": "سطح سابقه:",
            "work_type": "نوع همکاری:",
            "salary": "حقوق ماهانه:",
        },
        "options": {
            "field": {"type": "enum", "choices": [("برنامه‌نویسی 👨‍💻", "dev"), ("طراحی 🎨", "design"), ("فروش/مارکتینگ 📣", "sales"),
                                                  ("مالی/اداری 🧾", "admin")]},
            "seniority": {"type": "enum", "choices": [("جونیور", "junior"), ("مید", "mid"), ("سینیور", "senior")]},
            "work_type": {"type": "enum", "choices": [("حضوری 🏢", "onsite"), ("دورکار 🏡", "remote"), ("هیبرید 🔁", "hybrid")]},
            "salary": {"type": "range", "choices": [("تا ۱۰م", "0-10000000"), ("۱۰-۲۰م", "10000000-20000000"),
                                                    ("۲۰-۴۰م", "20000000-40000000"), ("۴۰م+", "40000000-1000000000")]}
        },
        "map_keys": {"salary": ("price_min", "price_max")}  # برای یکپارچگی، salary را در price قرار می‌دهیم
    },

    "mobile": {
        "title": "📱 موبایل",
        "steps": ["brand", "storage", "condition", "price"],
        "labels": {
            "brand": "برند گوشی:",
            "storage": "حجم حافظه:",
            "condition": "وضعیت:",
            "price": "محدوده قیمت:",
        },
        "options": {
            "brand": {"type": "enum", "choices": [("Apple 🍎", "apple"), ("Samsung 🌙", "samsung"), ("Xiaomi ⚡", "xiaomi"),
                                                  ("Huawei", "huawei"), ("Nokia", "nokia"), ("Other", "other")]},
            "storage": {"type": "enum", "choices": [("32GB", "32"), ("64GB", "64"), ("128GB", "128"), ("256GB+", "256plus")]},
            "condition": {"type": "enum", "choices": [("نو ✨", "new"), ("در حد نو ✅", "like_new"), ("کارکرده ♻️", "used")]},
            "price": {"type": "range", "choices": [("تا ۵م", "0-5000000"), ("۵-۱۰م", "5000000-10000000"),
                                                   ("۱۰-۲۰م", "10000000-20000000"), ("۲۰م+", "20000000-1000000000")]}
        },
        "map_keys": {"price": ("price_min", "price_max")}
    },

    "electronics": {
        "title": "🖥️ الکترونیک",
        "steps": ["sub", "condition", "price"],
        "labels": {
            "sub": "نوع دستگاه:",
            "condition": "وضعیت:",
            "price": "محدوده قیمت:",
        },
        "options": {
            "sub": {"type": "enum", "choices": [("لپ‌تاپ", "laptop"), ("PC", "pc"), ("مانیتور", "monitor"), ("کنسول بازی", "console")]},
            "condition": {"type": "enum", "choices": [("نو ✨", "new"), ("در حد نو ✅", "like_new"), ("کارکرده ♻️", "used")]},
            "price": {"type": "range", "choices": [("تا ۱۰م", "0-10000000"), ("۱۰-۳۰م", "10000000-30000000"),
                                                   ("۳۰-۶۰م", "30000000-60000000"), ("۶۰م+", "60000000-1000000000")]}
        },
        "map_keys": {"price": ("price_min", "price_max")}
    },

    "fashion": {
        "title": "👗 مد و پوشاک",
        "steps": ["sub", "size", "condition", "price"],
        "labels": {
            "sub": "دسته پوشاک:",
            "size": "سایز:",
            "condition": "وضعیت:",
            "price": "محدوده قیمت:",
        },
        "options": {
            "sub": {"type": "enum", "choices": [("زنانه", "women"), ("مردانه", "men"), ("بچه‌گانه", "kids"), ("اکسسوری", "acc")]},
            "size": {"type": "enum", "choices": [("S", "S"), ("M", "M"), ("L", "L"), ("XL", "XL")]},
            "condition": {"type": "enum", "choices": [("نو ✨", "new"), ("در حد نو ✅", "like_new"), ("کارکرده ♻️", "used")]},
            "price": {"type": "range", "choices": [("تا ۵۰۰هزار", "0-500000"), ("۵۰۰-۱.۵م", "500000-1500000"),
                                                   ("۱.۵-۳م", "1500000-3000000"), ("۳م+", "3000000-100000000")]}
        },
        "map_keys": {"price": ("price_min", "price_max")}
    },

    "home": {
        "title": "🛋️ خانه و آشپزخانه",
        "steps": ["sub", "condition", "price"],
        "labels": {"sub": "دسته:", "condition": "وضعیت:", "price": "محدوده قیمت:"},
        "options": {
            "sub": {"type": "enum", "choices": [("مبلمان", "furniture"), ("فرش/قالی", "rug"), ("یخچال/لباسشویی", "appliance")]},
            "condition": {"type": "enum", "choices": [("نو ✨", "new"), ("در حد نو ✅", "like_new"), ("کارکرده ♻️", "used")]},
            "price": {"type": "range", "choices": [("تا ۳م", "0-3000000"), ("۳-۸م", "3000000-8000000"),
                                                   ("۸-۱۵م", "8000000-15000000"), ("۱۵م+", "15000000-1000000000")]}
        },
        "map_keys": {"price": ("price_min", "price_max")}
    },

    "entertainment": {
        "title": "🎮 سرگرمی",
        "steps": ["sub", "price"],
        "labels": {"sub": "دسته سرگرمی:", "price": "محدوده قیمت:"},
        "options": {
            "sub": {"type": "enum", "choices": [("بازی ویدئویی", "videogame"), ("کتاب/مجله", "book"), ("ابزار موسیقی", "music")]},
            "price": {"type": "range", "choices": [("تا ۲م", "0-2000000"), ("۲-۵م", "2000000-5000000"),
                                                   ("۵-۱۵م", "5000000-15000000"), ("۱۵م+", "15000000-1000000000")]}
        },
        "map_keys": {"price": ("price_min", "price_max")}
    },

    "animals": {
        "title": "🐶 حیوانات",
        "steps": ["sub", "age", "price"],
        "labels": {"sub": "نوع حیوان:", "age": "سن:", "price": "محدوده قیمت:"},
        "options": {
            "sub": {"type": "enum", "choices": [("سگ", "dog"), ("گربه", "cat"), ("پرنده", "bird"), ("ماهی", "fish")]},
            "age": {"type": "range", "choices": [("تا ۳ ماه", "0-3"), ("۳-۱۲ ماه", "3-12"), ("۱-۳ سال", "12-36"), ("۳ سال+", "36-240")]},
            "price": {"type": "range", "choices": [("تا ۳م", "0-3000000"), ("۳-۱۰م", "3000000-10000000"),
                                                   ("۱۰-۳۰م", "10000000-30000000"), ("۳۰م+", "30000000-1000000000")]}
        },
        "map_keys": {"age": ("age_min", "age_max"), "price": ("price_min", "price_max")}
    },
}

def build_grid_buttons(pairs: List[Tuple[str, str]], prefix: str) -> InlineKeyboardMarkup:
    rows, row = [], []
    for label, val in pairs:
        row.append(InlineKeyboardButton(label, callback_data=f"{prefix}:{val}"))
        if len(row) == 3:
            rows.append(row); row = []
    if row: rows.append(row)
    return InlineKeyboardMarkup(rows)

def step_keyboard(category: str, step: str) -> InlineKeyboardMarkup:
    cfg = CATEGORY_FLOWS[category]
    opts = cfg["options"][step]
    pairs = [(label, value) for (label, value) in opts["choices"]]
    return build_grid_buttons(pairs, f"flt:{step}")

def step_label(category: str, step: str) -> str:
    return CATEGORY_FLOWS[category]["labels"][step]

def store_filter_value(category: str, filters: Dict[str, Any], step: str, value: str):
    cfg = CATEGORY_FLOWS[category]
    map_keys = cfg.get("map_keys", {})
    if step in map_keys:
        kmin, kmax = map_keys[step]
        if "-" in value:
            lo, hi = value.split("-", 1)
            try:
                filters[kmin] = int(lo)
                filters[kmax] = int(hi)
            except ValueError:
                filters[kmin] = lo
                filters[kmax] = hi
        else:
            filters[kmin] = value
            filters[kmax] = value
    else:
        # enum-like
        filters[step] = value

# --- FastAPI ---
app = FastAPI()

@app.on_event("startup")
async def on_startup():
    # start PTB app to process updates
    await bot_app.initialize()
    await bot_app.start()
    asyncio.create_task(check_new_posts_loop())
    log.info("Started bot app & background checker.")

@app.on_event("shutdown")
async def on_shutdown():
    await bot_app.stop()

@app.get("/")
async def root():
    return {"ok": True, "status": "running", "time": datetime.now(timezone.utc).isoformat()}

@app.get("/set-webhook")
async def set_webhook():
    if not TOKEN or not PUBLIC_BASE_URL:
        raise HTTPException(400, "Missing TELEGRAM_TOKEN or PUBLIC_BASE_URL")
    url = f"{PUBLIC_BASE_URL.rstrip('/')}/webhook/{TOKEN}"
    await bot_app.bot.set_webhook(url)
    return {"ok": True, "webhook": url}

@app.post(f"/webhook/{{token}}")
async def telegram_webhook(token: str, request: Request):
    if token != TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")
    data = await request.json()
    update = Update.de_json(data, bot_app.bot)
    await bot_app.process_update(update)
    return JSONResponse({"ok": True})

# --- UI Texts & Keyboards ---
WELCOME_TEXT = (
    "👋 سلام! به «دیوار‌بات» خوش اومدی.\n\n"
    "من کمکت می‌کنم پلی‌لیست‌های شخصی از آگهی‌های دیوار بسازی و آگهی‌های جدید رو برات بفرستم 📬\n\n"
    "✨ امکانات:\n"
    "• ساخت پلی‌لیست جدید 🎯\n"
    "• دیدن/حذف پلی‌لیست‌ها 📂\n"
    "• دریافت خودکار آگهی‌های جدید 🆕\n\n"
    "یکی از گزینه‌های زیر رو انتخاب کن 👇"
)

MAIN_KB = ReplyKeyboardMarkup(
    [["➕ ساخت پلی‌لیست جدید", "📂 پلی‌لیست‌های من"], ["ℹ️ راهنما"]],
    resize_keyboard=True
)

CATEGORY_ROWS = [
    [("🚗 خودرو", "car"), ("🏠 املاک", "real_estate"), ("💼 استخدام", "jobs")],
    [("📱 موبایل", "mobile"), ("🖥️ الکترونیک", "electronics"), ("👗 مد و پوشاک", "fashion")],
    [("🛋️ خانه و آشپزخانه", "home"), ("🎮 سرگرمی", "entertainment"), ("🐶 حیوانات", "animals")],
]

def category_keyboard():
    rows = []
    for row in CATEGORY_ROWS:
        rows.append([InlineKeyboardButton(text, callback_data=f"cat:{slug}") for text, slug in row])
    return InlineKeyboardMarkup(rows)

def cities_keyboard():
    rows, temp = [], []
    for slug, info in list_cities().items():
        temp.append(InlineKeyboardButton(f"🏙️ {info['title']}", callback_data=f"city:{slug}"))
        if len(temp) == 3:
            rows.append(temp); temp = []
    if temp: rows.append(temp)
    return InlineKeyboardMarkup(rows)

def districts_keyboard(city_slug: str):
    dist = list_cities().get(city_slug, {}).get("districts", [])
    rows, temp = [], []
    for d in dist:
        temp.append(InlineKeyboardButton(d, callback_data=f"dist:{d}"))
        if len(temp) == 3:
            rows.append(temp); temp = []
    if temp: rows.append(temp)
    rows.append([InlineKeyboardButton("⏭️ رد کردن (کل شهر)", callback_data="dist:__ALL__")])
    return InlineKeyboardMarkup(rows)

def playlist_summary(p: Playlist) -> str:
    f = p.filters or {}
    bits = []
    # خلاصهٔ هوشمند برای چند کتگوری
    if p.category == "car":
        if f.get("mileage_min") or f.get("mileage_max"):
            bits.append(f"کارکرد: {f.get('mileage_min','-')} - {f.get('mileage_max','-')}")
        if f.get("year_min") or f.get("year_max"):
            bits.append(f"سال: {f.get('year_min','-')} - {f.get('year_max','-')}")
    if p.category == "real_estate":
        if f.get("meter_min") or f.get("meter_max"):
            bits.append(f"متراژ: {f.get('meter_min','-')}-{f.get('meter_max','-')}")
        if f.get("rooms"):
            bits.append(f"اتاق: {f['rooms']}")
        if f.get("deal"):
            bits.append("معامله: " + ("خرید" if f["deal"]=="buy" else "رهن/اجاره"))
        if f.get("type"):
            bits.append(f"نوع: {f['type']}")
    if f.get("price_min") or f.get("price_max"):
        bits.append(f"قیمت: {f.get('price_min','-')} - {f.get('price_max','-')}")
    loc = p.city if not p.district or p.district=='__ALL__' else f"{p.city} / {p.district}"
    return f"🏷 {p.title}\n📂 دسته: {p.category}\n📍 موقعیت: {loc}\n🎯 فیلترها: " + ("، ".join(bits) if bits else "—")

# --- Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    with Session(engine) as db:
        user = db.get(User, u.id)
        if not user:
            user = User(id=u.id, first_name=u.first_name, last_name=u.last_name, username=u.username)
            db.add(user); db.commit()
    await update.effective_message.reply_text(WELCOME_TEXT, reply_markup=MAIN_KB)

async def handle_main_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.effective_message.text or ""
    if "ساخت پلی‌لیست" in text:
        builder_state[update.effective_user.id] = {"step":"category"}
        await update.effective_message.reply_text("خب، اول دسته‌بندی رو انتخاب کن 👇", reply_markup=ReplyKeyboardRemove())
        await update.effective_message.reply_text("دسته‌بندی‌ها:", reply_markup=category_keyboard())
    elif "پلی‌لیست‌های من" in text:
        with Session(engine) as db:
            pls = db.query(Playlist).filter(Playlist.user_id==update.effective_user.id).all()
        if not pls:
            await update.effective_message.reply_text("هنوز پلی‌لیستی نساختی. با دکمه «➕ ساخت پلی‌لیست جدید» شروع کن 🙌", reply_markup=MAIN_KB)
            return
        rows=[]
        for p in pls:
            rows.append([InlineKeyboardButton(f"📋 {p.title}", callback_data=f"pl:show:{p.id}")])
        kb = InlineKeyboardMarkup(rows + [[InlineKeyboardButton("➕ ساخت پلی‌لیست جدید", callback_data="pl:new")]])
        await update.effective_message.reply_text("📂 پلی‌لیست‌های شما:", reply_markup=kb)
    elif "راهنما" in text:
        await update.effective_message.reply_text("ℹ️ با «➕ ساخت پلی‌لیست جدید» شروع کن. هر پلی‌لیست فیلترهای خودش رو داره و آگهی‌های جدیدش به‌صورت خودکار میاد.", reply_markup=MAIN_KB)

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    uid = update.effective_user.id
    st = builder_state.get(uid)

    # مدیریت پلی‌لیست‌ها
    if data.startswith("pl:new"):
        builder_state[uid] = {"step":"category"}
        await q.message.edit_text("خب، اول دسته‌بندی رو انتخاب کن 👇")
        await q.message.edit_reply_markup(category_keyboard())
        return

    if data.startswith("pl:show:"):
        pid = int(data.split(":")[-1])
        with Session(engine) as db:
            p = db.get(Playlist, pid)
        if not p:
            await q.message.edit_text("این پلی‌لیست پیدا نشد.")
            return
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑️ حذف", callback_data=f"pl:del:{p.id}")],
        ])
        await q.message.edit_text(playlist_summary(p))
        await q.message.edit_reply_markup(kb)
        return

    if data.startswith("pl:del:"):
        pid = int(data.split(":")[-1])
        with Session(engine) as db:
            p = db.get(Playlist, pid)
            if p and p.user_id == uid:
                db.delete(p); db.commit()
                await q.message.edit_text("✅ پلی‌لیست حذف شد.")
            else:
                await q.message.edit_text("نتونستم حذف کنم.")
        return

    # جریان ساخت پلی‌لیست
    if data.startswith("cat:"):
        slug = data.split(":")[1]
        if slug not in CATEGORY_TOKENS:
            await q.message.edit_text("این دسته پشتیبانی نمی‌شود.")
            return
        # آماده‌سازی فلو
        flow = CATEGORY_FLOWS[slug]["steps"]
        builder_state[uid] = {
            "step": "city",
            "category": slug,
            "filters": {},
            "flow": flow,
            "flow_idx": 0
        }
        await q.message.edit_text("👌 دسته انتخاب شد. حالا شهر رو انتخاب کن:")
        await q.message.edit_reply_markup(cities_keyboard())
        return

    if data.startswith("city:"):
        if not st: return
        city_slug = data.split(":")[1]
        st["city"] = city_slug
        st["step"] = "district"
        await q.message.edit_text("خیلی خب! یکی از شهرستان‌ها/مناطق رو انتخاب کن یا «⏭️ رد کردن» رو بزن:")
        await q.message.edit_reply_markup(districts_keyboard(city_slug))
        return

    if data.startswith("dist:"):
        if not st: return
        dist = data.split(":")[1]
        st["district"] = None if dist == "__ALL__" else dist
        # شروع مرحله‌های فیلتر
        st["step"] = "filters"
        cat = st["category"]
        step = st["flow"][st["flow_idx"]]
        await q.message.edit_text(step_label(cat, step))
        await q.message.edit_reply_markup(step_keyboard(cat, step))
        return

    if data.startswith("flt:"):
        if not st: return
        _, step, value = data.split(":", 2)
        cat = st["category"]
        store_filter_value(cat, st["filters"], step, value)

        # مرحله بعد
        st["flow_idx"] += 1
        if st["flow_idx"] < len(st["flow"]):
            next_step = st["flow"][st["flow_idx"]]
            await q.message.edit_text(step_label(cat, next_step))
            await q.message.edit_reply_markup(step_keyboard(cat, next_step))
            return

        # اتمام؛ ذخیره پلی‌لیست
        title = f"{CATEGORY_FLOWS[cat]['title']} | {st['city']}"
        with Session(engine) as db:
            p = Playlist(
                user_id=uid,
                title=title,
                category=st["category"],
                city=st["city"],
                district=st.get("district"),
                filters=st["filters"],
                last_seen_ids=[]
            )
            db.add(p); db.commit()
            pid = p.id
        del builder_state[uid]
        await q.message.edit_text("✅ پلی‌لیست با موفقیت ثبت شد! الان نتایج مطابق فیلترها رو می‌فرستم...")
        await send_initial_results(uid, pid, context)
        await context.bot.send_message(chat_id=uid, text="هر زمان آماده‌ای ادامه بده 👇", reply_markup=MAIN_KB)
        return

# --- ارسال نتایج ---
async def send_initial_results(user_id: int, playlist_id: int, context: ContextTypes.DEFAULT_TYPE):
    with Session(engine) as db:
        p = db.get(Playlist, playlist_id)
        if not p: 
            return
    token = CATEGORY_TOKENS.get(p.category, p.category)
    filters = p.filters or {}
    posts = await divar_search(p.city, token, filters, limit=12)
    if not posts:
        await context.bot.send_message(chat_id=user_id, text="چیزی پیدا نشد 🤷‍♂️")
        return
    sent_ids: List[str] = []
    for post in posts:
        await send_post(user_id, post, context)
        if post.get("id"):
            sent_ids.append(post["id"])
    # ذخیره MRU
    with Session(engine) as db:
        p = db.get(Playlist, playlist_id)
        if p:
            p.last_seen_ids = list(dict.fromkeys(sent_ids))[:40]
            db.commit()

async def send_post(chat_id: int, post: Dict[str, Any], context: ContextTypes.DEFAULT_TYPE):
    title = post.get("title") or "بدون عنوان"
    desc = post.get("desc") or ""
    city = post.get("city") or ""
    dist = post.get("district") or ""
    when = humanize_age(post.get("created_at"))
    url = post.get("url")
    phone = post.get("phone")
    price = post.get("price")

    caption = f"📝 {title}\n"
    if city or dist:
        loc = f"{city} - {dist}" if dist else city
        caption += f"📍 {loc}\n"
    caption += f"⏰ {when}\n"
    if price:
        caption += f"💰 {price}\n"
    if desc:
        caption += f"\n{desc}\n"
    if phone:
        caption += f"\n📞 {phone}\n"

    btns = []
    if url:
        btns.append([InlineKeyboardButton("🔗 مشاهده در دیوار", url=url)])
    reply_markup = InlineKeyboardMarkup(btns) if btns else None

    images = [u for u in (post.get("images") or []) if u]

    # تلاش برای گالری کامل
    if url and (len(images) < 2) and post.get("id"):
        try:
            det = await fetch_details(post["id"])
            gal = det.get("images") or []
            if gal:
                images = gal
        except Exception:
            pass

    if images:
        media = [InputMediaPhoto(media=images[0], caption=caption)]
        for u in images[1:9]:
            media.append(InputMediaPhoto(media=u))
        try:
            await context.bot.send_media_group(chat_id=chat_id, media=media)
            if reply_markup:
                await context.bot.send_message(chat_id=chat_id, text="...", reply_markup=reply_markup)
        except Exception:
            await context.bot.send_message(chat_id=chat_id, text=caption, reply_markup=reply_markup)
    else:
        await context.bot.send_message(chat_id=chat_id, text=caption, reply_markup=reply_markup)

# --- چکِ پس‌زمینه برای آگهی‌های جدید ---
async def check_new_posts_loop():
    while True:
        try:
            await check_once()
        except Exception as e:
            log.exception("checker error: %s", e)
        await asyncio.sleep(CHECK_INTERVAL)

async def check_once():
    with Session(engine) as db:
        playlists = db.query(Playlist).all()
    for p in playlists:
        token = CATEGORY_TOKENS.get(p.category, p.category)
        filters = p.filters or {}
        try:
            posts = await divar_search(p.city, token, filters, limit=8)
        except Exception:
            continue
        seen = set(p.last_seen_ids or [])
        new_posts = [x for x in posts if x.get("id") and x["id"] not in seen]
        if not new_posts:
            continue
        for post in new_posts:
            await send_post(p.user_id, post, bot_app)
        # به‌روزرسانی MRU
        ids = [x.get("id") for x in new_posts if x.get("id")]
        with Session(engine) as db:
            obj = db.get(Playlist, p.id)
            if obj:
                mru = list(dict.fromkeys(ids + (obj.last_seen_ids or [])))[:40]
                obj.last_seen_ids = mru
                db.commit()

# --- Wire handlers ---
bot_app.add_handler(CommandHandler("start", start))
bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_main_buttons))
bot_app.add_handler(CallbackQueryHandler(on_callback))
