import os, json, time, random, string, asyncio, threading, logging, re
import requests
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)

# ================== LOGGING ==================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("report-bot")

# ================== CONFIG ==================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
FB_UID = os.getenv("FB_UID", "")
FB_COOKIES = os.getenv("FB_COOKIES", "")
CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    "target_id": "",
    "reason": 1,
    "delay": 30,
    "cookie": "",
    "active": False,
    "total_reports": 0,
    "loop": False,
}

REASONS = {
    1: "Spam",
    2: "Nội dung không mong muốn",
    3: "Giả mạo",
    4: "Bạo lực",
    5: "Quấy rối",
    6: "Ngôn từ thù địch",
}

# ================== FLASK (giữ process sống trên Render) ==================
app = Flask(__name__)

@app.route("/")
def _health():
    return "ok", 200

# ================== PROXY POOL ==================
PROXY_LIST = []
PROXY_SOURCES = [
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",
]

def load_proxies():
    global PROXY_LIST
    collected = set()
    for src in PROXY_SOURCES:
        try:
            r = requests.get(src, timeout=15)
            if r.status_code == 200:
                for line in r.text.splitlines():
                    line = line.strip()
                    if line and ":" in line and not line.startswith("#"):
                        collected.add(line)
            logger.info(f"Loaded {len(collected)} proxies from {src}")
        except Exception as e:
            logger.warning(f"Proxy source failed {src}: {e}")
    PROXY_LIST = list(collected)
    logger.info(f"✅ TỔNG PROXY: {len(PROXY_LIST)}")

def get_proxy():
    if not PROXY_LIST:
        return None
    return random.choice(PROXY_LIST)

def proxy_dict(px):
    if not px:
        return None
    return {"http": f"http://{px}", "https": f"http://{px}"}

# ================== COOKIE HELPER ==================
def gen_cookie_from_uid(uid: str) -> str:
    c_user = uid or "10000" + "".join(random.choices(string.digits, k=10))
    xs = "".join(random.choices(string.ascii_letters + string.digits + "%:", k=80))
    datr = "".join(random.choices(string.ascii_letters + string.digits + "_-", k=24))
    sb = "".join(random.choices(string.ascii_letters + string.digits + "_-", k=24))
    fr = "".join(random.choices(string.ascii_letters + string.digits + ".", k=60))
    return f"c_user={c_user}; xs={xs}; datr={datr}; sb={sb}; fr={fr};"

def get_cookie() -> str:
    if FB_COOKIES and len(FB_COOKIES) > 20:
        return FB_COOKIES
    cfg = load_config()
    if cfg.get("cookie"):
        return cfg["cookie"]
    new_cookie = gen_cookie_from_uid(FB_UID)
    cfg["cookie"] = new_cookie
    save_config(cfg)
    logger.warning("⚠️ Thiếu cookie -> đã tự tạo cookie mới (fallback).")
    return new_cookie

# ================== CONFIG IO ==================
def load_config():
    if not os.path.exists(CONFIG_FILE):
        c = DEFAULT_CONFIG.copy()
        c["cookie"] = FB_COOKIES or gen_cookie_from_uid(FB_UID)
        save_config(c)
        return c
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return {**DEFAULT_CONFIG, **json.load(f)}
    except Exception:
        return DEFAULT_CONFIG.copy()

def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"save_config: {e}")

# ================== FB API ==================
FB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 12; SM-G991B) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
    "X-Requested-With": "XMLHttpRequest",
}

def parse_cookie_dict(cookie: str) -> dict:
    d = {}
    for p in cookie.split(";"):
        if "=" in p:
            k, v = p.strip().split("=", 1)
            d[k] = v
    return d

def report_once(target_id: str, reason: int = 1) -> dict:
    cookie = get_cookie()
    cdict = parse_cookie_dict(cookie)

    if "c_user" not in cdict or "xs" not in cdict:
        cdict["c_user"] = FB_UID or "100000000000000"
        if "xs" not in cdict:
            cdict["xs"] = "".join(random.choices(string.ascii_letters + string.digits + "%:", k=80))

    px = get_proxy()
    proxies = proxy_dict(px)

    payload = {
        "fb_api_req_friendly_name": "CometProfileActionButtonReportMutation",
        "variables": json.dumps({
            "input": {
                "actor_id": cdict.get("c_user", ""),
                "target_id": target_id,
                "reason_id": str(reason),
                "source": "profile",
            },
            "scale": 1,
        }),
        "doc_id": "5834444422223789",
        "__a": "1",
        "__user": cdict.get("c_user", ""),
        "__req": "1",
        "__ccg": "GOOD",
    }

    try:
        r = requests.post(
            "https://www.facebook.com/api/graphql/",
            headers={**FB_HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
            data=payload,
            cookies=cdict,
            proxies=proxies,
            timeout=20,
        )
        ok = r.status_code == 200 and "errors" not in r.text[:500]
        return {"ok": ok, "status": r.status_code, "proxy": px, "body": r.text[:200]}
    except Exception as e:
        return {"ok": False, "status": 0, "proxy": px, "error": str(e)}

# ================== TARGET PARSER ==================
def parse_target(text: str) -> str:
    text = text.strip()
    if "facebook.com" in text:
        m = re.search(r"facebook\.com/(?:profile\.php\?id=|)([0-9A-Za-z._-]+)", text)
        if m:
            return m.group(1).split("?")[0]
    return text.split("?")[0].rstrip("/")

# ================== KEYBOARDS ==================
def kb_main():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ BẮT ĐẦU", callback_data="start_loop"),
         InlineKeyboardButton("⏹ DỪNG", callback_data="stop_loop")],
        [InlineKeyboardButton("🎯 Đổi target", callback_data="set_target"),
         InlineKeyboardButton("📝 Đổi reason", callback_data="set_reason")],
        [InlineKeyboardButton("🍪 Xem cookie", callback_data="show_cookie"),
         InlineKeyboardButton("🔄 Tạo cookie mới", callback_data="new_cookie")],
        [InlineKeyboardButton("🌐 Nạp proxy", callback_data="load_proxy"),
         InlineKeyboardButton("📊 Trạng thái", callback_data="status")],
        [InlineKeyboardButton("🔁 Bật/Tắt loop", callback_data="toggle_loop"),
         InlineKeyboardButton("⏱ Đổi delay", callback_data="set_delay")],
        [InlineKeyboardButton("✍️ Nhập cookie thủ công", callback_data="input_cookie")],
    ])

def kb_reason():
    rows = [[InlineKeyboardButton(f"{num}. {label}", callback_data=f"reason_{num}")] for num, label in REASONS.items()]
    rows.append([InlineKeyboardButton("« Quay lại", callback_data="back")])
    return InlineKeyboardMarkup(rows)

def format_status(cfg):
    return (
        f"📊 <b>TRẠNG THÁI BOT</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🎯 Target: <code>{cfg.get('target_id') or 'Chưa đặt'}</code>\n"
        f"📝 Reason: {REASONS.get(cfg.get('reason'), cfg.get('reason'))}\n"
        f"⏱️ Delay: {cfg.get('delay')}s\n"
        f"🍪 Cookie: {'✅ Có' if cfg.get('cookie') else '❌ Thiếu (sẽ tự tạo)'}\n"
        f"🌐 Proxy: {len(PROXY_LIST)} con\n"
        f"🔁 Loop: {'✅ BẬT' if cfg.get('loop') else '❌ TẮT'}\n"
        f"⚙️ Hoạt động: {'🟢 ĐANG CHẠY' if cfg.get('active') else '🔴 DỪNG'}\n"
        f"📈 Tổng report: <b>{cfg.get('total_reports', 0)}</b>\n"
        f"🔑 UID acc clone: <code>{FB_UID or 'N/A'}</code>"
    )

# ================== LOOP WORKER ==================
STOP_FLAG = threading.Event()

def loop_worker(app_tg: Application):
    while not STOP_FLAG.is_set():
        cfg = load_config()
        if not cfg.get("active"):
            time.sleep(2)
            continue
        target = cfg.get("target_id")
        if not target:
            time.sleep(5)
            continue

        res = report_once(target, int(cfg.get("reason", 1)))
        if res["ok"]:
            cfg["total_reports"] = cfg.get("total_reports", 0) + 1
            save_config(cfg)
            msg = (
                f"✅ Report OK\n🎯 <code>{target}</code>\n"
                f"🌐 Proxy: <code>{res.get('proxy') or 'direct'}</code>\n"
                f"📈 Tổng: {cfg['total_reports']}"
            )
        else:
            msg = (
                f"❌ Report FAIL\n🎯 <code>{target}</code>\n"
                f"🌐 Proxy: <code>{res.get('proxy') or 'direct'}</code>\n"
                f"⚠️ {res.get('error') or 'status ' + str(res.get('status'))}"
            )
            if "auth" in (res.get("error") or "").lower() or res.get("status") == 400:
                cfg["cookie"] = gen_cookie_from_uid(FB_UID)
                save_config(cfg)
                msg += "\n🍪 Đã tự tạo cookie mới."

        try:
            asyncio.run_coroutine_threadsafe(
                app_tg.bot.send_message(ADMIN_ID, msg, parse_mode="HTML"),
                app_tg.loop,
            )
        except Exception as e:
            logger.error(f"tele notify: {e}")

        for _ in range(int(cfg.get("delay", 30))):
            if STOP_FLAG.is_set() or not load_config().get("active"):
                break
            time.sleep(1)

        if not load_config().get("loop"):
            c = load_config()
            c["active"] = False
            save_config(c)

# ================== BOT HANDLERS ==================
WAITING = {"kind": None}

def _is_admin(uid: int) -> bool:
    return uid == ADMIN_ID

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Bạn không phải ADMIN.")
        return
    await update.message.reply_text(
        "🤖 <b>FB AUTO REPORT BOT</b>\nQuản lý qua nút bên dưới hoặc lệnh:\n"
        "<code>/settarget &lt;id&gt;</code>\n<code>/setreason &lt;1-6&gt;</code>\n"
        "<code>/setdelay &lt;giây&gt;</code>\n<code>/setcookie &lt;cookie&gt;</code>",
        reply_markup=kb_main(),
        parse_mode="HTML",
    )

async def cmd_settarget(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return
    if not ctx.args:
        await update.message.reply_text("Cú pháp: /settarget <id>")
        return
    cfg = load_config()
    cfg["target_id"] = parse_target(" ".join(ctx.args))
    save_config(cfg)
    await update.message.reply_text(f"🎯 Target: <code>{cfg['target_id']}</code>", parse_mode="HTML")

async def cmd_setreason(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return
    if not ctx.args or not ctx.args[0].isdigit() or int(ctx.args[0]) not in REASONS:
        await update.message.reply_text("Cú pháp: /setreason <1-6>")
        return
    cfg = load_config()
    cfg["reason"] = int(ctx.args[0])
    save_config(cfg)
    await update.message.reply_text(f"📝 Reason: {REASONS[int(ctx.args[0])]}")

async def cmd_setdelay(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return
    if not ctx.args or not ctx.args[0].isdigit():
        await update.message.reply_text("Cú pháp: /setdelay <giây>")
        return
    cfg = load_config()
    cfg["delay"] = int(ctx.args[0])
    save_config(cfg)
    await update.message.reply_text(f"⏱️ Delay: {ctx.args[0]}s")

async def cmd_setcookie(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return
    cookie = " ".join(ctx.args).strip()
    if not cookie:
        await update.message.reply_text("Cú pháp: /setcookie <cookie>")
        return
    cfg = load_config()
    cfg["cookie"] = cookie
    save_config(cfg)
    await update.message.reply_text("🍪 Đã cập nhật cookie.")

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return
    await update.message.reply_text(format_status(load_config()), parse_mode="HTML")

# ---- Handler text đầu vào (fix lỗi NameError) ----
async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update.effective_user.id):
        return
    kind = WAITING.get("kind")
    if not kind:
        return
    text = update.message.text.strip()
    cfg = load_config()

    if kind == "target":
        cfg["target_id"] = parse_target(text)
        save_config(cfg)
        WAITING["kind"] = None
        await update.message.reply_text(
            f"✅ Đã đặt target:\n<code>{cfg['target_id']}</code>",
            reply_markup=kb_main(),
            parse_mode="HTML",
        )

    elif kind == "delay":
        if not text.isdigit():
            await update.message.reply_text("❌ Phải là số. Nhập lại:")
            return
        cfg["delay"] = int(text)
        save_config(cfg)
        WAITING["kind"] = None
        await update.message.reply_text(
            f"✅ Delay: {text}s", reply_markup=kb_main(), parse_mode="HTML"
        )

    elif kind == "cookie":
        cfg["cookie"] = text
        save_config(cfg)
        WAITING["kind"] = None
        await update.message.reply_text("✅ Đã cập nhật cookie.", reply_markup=kb_main())

async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not _is_admin(q.from_user.id):
        await q.edit_message_text("⛔ Không phải ADMIN.")
        return

    data = q.data
    cfg = load_config()

    if data == "start_loop":
        if not cfg.get("target_id"):
            await q.edit_message_text(
                "⚠️"
