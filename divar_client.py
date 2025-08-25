from __future__ import annotations
import httpx, re, asyncio
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from dateutil import parser as dateparser

BASE = "https://api.divar.ir"
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://divar.ir",
    "Referer": "https://divar.ir/",
    "User-Agent": "Mozilla/5.0 (compatible; Bot/1.0; +https://example.org)"
}

# نگاشت کتگوری‌های ربات → توکن‌های دیوار
CATEGORY_TOKENS = {
    "car": "cars",
    "real_estate": "real-estate",
    "jobs": "jobs",
    "mobile": "mobile-phones",
    "electronics": "digital-devices",
    "fashion": "apparel",
    "home": "home-kitchen",
    "entertainment": "entertainment",
    "animals": "animals",
}

# چند شهر نمونه + شهرستان‌ها (می‌تونی گسترش بدی)
CITIES = {
    "tehran": {"title": "تهران", "districts": ["شمیرانات", "ری", "اسلام‌شهر", "لواسان", "کرج"]},
    "mashhad": {"title": "مشهد", "districts": ["طرقبه", "شاندیز", "چناران"]},
    "isfahan": {"title": "اصفهان", "districts": ["نجف‌آباد", "خمینی‌شهر", "شاهین‌شهر"]},
    "shiraz": {"title": "شیراز", "districts": ["زرقان", "کازرون", "مرودشت"]},
    "tabriz": {"title": "تبریز", "districts": ["اسکو", "آذرشهر", "شبستر"]},
    "karaj": {"title": "کرج", "districts": ["فردیس", "نظرآباد", "اشتهارد"]},
}

def list_cities():
    return CITIES

def humanize_age(ts: Optional[str]) -> str:
    if not ts:
        return "نامشخص"
    try:
        dt = dateparser.parse(ts)
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = now - dt
        s = int(delta.total_seconds())
        if s < 60: return f"{s} ثانیه پیش"
        m = s // 60
        if m < 60: return f"{m} دقیقه پیش"
        h = m // 60
        if h < 24: return f"{h} ساعت پیش"
        d = h // 24
        return f"{d} روز پیش"
    except Exception:
        return "نامشخص"

PHONE_RX = re.compile(r"(?:09\d{9})")

def extract_phone(text: Optional[str]) -> Optional[str]:
    if not text: return None
    m = PHONE_RX.search(text.replace(" ", "").replace("-", ""))
    return m.group(0) if m else None

async def search(city_slug: str, category_token: str, filters: Dict[str, Any], limit: int = 10) -> List[Dict[str, Any]]:
    """
    جستجوی لیستی. خروجی: id, title, desc, price, city, district, images, url, phone, created_at
    فقط فیلترهای پایدار اعمال می‌شوند (price، و برای خودرو: mileage/year).
    """
    payload = {
        "query": {
            "category": {"value": category_token},
            "search": [],
            "cities": [city_slug],
        },
        "page": 1
    }

    # قیمت (عمومی)
    if "price_min" in filters or "price_max" in filters:
        payload["query"]["search"].append({
            "value": "price",
            "min": filters.get("price_min"),
            "max": filters.get("price_max")
        })

    # خودرو: سال و کارکرد
    if category_token == "cars":
        if "mileage_min" in filters or "mileage_max" in filters:
            payload["query"]["search"].append({
                "value": "mileage",
                "min": filters.get("mileage_min"),
                "max": filters.get("mileage_max")
            })
        if "year_min" in filters or "year_max" in filters:
            payload["query"]["search"].append({
                "value": "production-year",
                "min": filters.get("year_min"),
                "max": filters.get("year_max")
            })

    url = f"{BASE}/v8/web-search/{city_slug}/"
    async with httpx.AsyncClient(timeout=15, headers=HEADERS) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()

    posts = []
    items = (data.get("web_widgets", {}) or {}).get("post_list", []) or []
    for it in items:
        token = it.get("data", {}).get("token")
        title = it.get("data", {}).get("title")
        desc = it.get("data", {}).get("description")
        url = f"https://divar.ir/v/{token}"
        img = it.get("data", {}).get("image")
        images = []
        if isinstance(img, dict) and "url" in img:
            images = [img["url"]]
        elif isinstance(img, list):
            images = [x.get("url") for x in img if isinstance(x, dict) and x.get("url")]

        city = it.get("data", {}).get("city")
        district = it.get("data", {}).get("district")
        created_at = it.get("data", {}).get("post_date") or it.get("data", {}).get("time")

        phone = extract_phone(desc)

        posts.append({
            "id": token,
            "title": title,
            "desc": desc,
            "price": None,
            "city": city,
            "district": district,
            "images": [u for u in images if u],
            "url": url,
            "phone": phone,
            "created_at": created_at
        })
        if len(posts) >= limit:
            break

    return posts

async def fetch_details(token: str) -> Dict[str, Any]:
    """
    تلاش برای گرفتن جزییات (گالری تصاویر و زمان دقیق‌تر).
    """
    urls = [
        f"{BASE}/v8/posts-v2/{token}/",
        f"{BASE}/v8/posts/{token}/",
    ]
    data = None
    async with httpx.AsyncClient(timeout=15, headers=HEADERS) as client:
        for u in urls:
            try:
                r = await client.get(u)
                if r.status_code == 200:
                    data = r.json()
                    break
            except Exception:
                continue
    if not data: 
        return {}

    images: List[str] = []

    def walk(obj: Any):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in ("url", "src") and isinstance(v, str) and v.startswith("http"):
                    if any(v.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp")):
                        images.append(v)
                walk(v)
        elif isinstance(obj, list):
            for x in obj:
                walk(x)

    walk(data)
    images = list(dict.fromkeys(images))
    return {"images": images}
