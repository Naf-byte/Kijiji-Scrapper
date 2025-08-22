import sys, asyncio, csv, random, re, traceback, threading, queue, os, time
from typing import List, Callable, Dict, Any
import streamlit as st
import subprocess
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
try:
    import pandas as pd
except Exception:
    pd = None

# Ensure Playwright browsers (Chromium) are installed once
import subprocess, os, sys, streamlit as st

def ensure_playwright():
    cache_dir = os.path.expanduser("~/.cache/ms-playwright")
    # already have a chromium* folder?
    if os.path.isdir(cache_dir):
        try:
            if any(name.startswith("chromium") for name in os.listdir(cache_dir)):
                return
        except Exception:
            pass

    # Try installing Chromium with the current venv's Python
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True,
            text=True,
            check=True,
        )
        # Optional: surface stdout for debugging
        if proc.stdout:
            st.info("Playwright: " + proc.stdout.splitlines()[-1][:200])
    except subprocess.CalledProcessError as e:
        # Show a concise, safe error + hint about packages.txt
        st.error(
            "❌ Failed to install Playwright Chromium. "
            "On Streamlit Cloud you must provide system packages via packages.txt. "
            "Make sure your repo contains a packages.txt with Chromium deps (see docs)."
        )
        # Show last few lines of stderr (Streamlit may redact full output)
        tail = (e.stderr or "").strip().splitlines()[-10:]
        if tail:
            st.code("\n".join(tail) or "no stderr")
        # Re-raise so the run stops clearly
        raise

ensure_playwright()



# =========================
# Config
# =========================
DEFAULT_URL = "https://www.kijiji.ca/b-cars-trucks/canada/c174l0?for-sale-by=ownr&view=list"
DEFAULT_CSV = "kijiji_cars.csv"

FLUSH_EVERY = 10           # write/refresh every N rows
UI_REFRESH_SECS = 5        # auto-update UI every N seconds while running
LOG_SCROLL_HEIGHT = 320    # px height for scrollable log once >20 lines

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# ---- Windows: Proactor loop (supports subprocesses for Playwright) ----
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# =========================
# Helpers
# =========================
def safe_for_excel(value):
    if isinstance(value, str):
        v = value.strip()
        if v and (v.startswith("-") or v.startswith("+")):
            return " " + v
        return v
    return value

async def human_pause(a=0.8, b=1.8):
    await asyncio.sleep(random.uniform(a, b))

async def with_retries(coro_fn, attempts=3, base_delay=1.5):
    last_exc = None
    for i in range(attempts):
        try:
            return await coro_fn()
        except PlaywrightTimeoutError as e:
            last_exc = e
            await asyncio.sleep(base_delay * (2 ** i) + random.uniform(0.0, 0.8))
    if last_exc:
        raise last_exc

async def create_context(p, *, headless: bool):
    browser = await p.chromium.launch(
        headless=headless,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    )
    context = await browser.new_context(
        user_agent=UA,
        viewport={"width": random.randint(1280, 1600), "height": random.randint(800, 950)},
        locale="en-CA",
        timezone_id="America/Toronto",
        extra_http_headers={"Accept-Language": "en-CA,en;q=0.9"},
    )
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
    )
    # block heavy assets (keep CSS/JS)
    await context.route(
        "**/*",
        lambda route: (
            route.abort() if route.request.resource_type in {"image", "media", "font"} else route.continue_()
        ),
    )
    context.set_default_navigation_timeout(90_000)
    context.set_default_timeout(20_000)
    return browser, context

async def fetch_listing(context, href, referer_url: str, log: Callable[[str], None]) -> Dict[str, Any]:
    data = {
        'Duration Posted': '-', 'Listing Link': href, 'Name': '-', 'Price': '-',
        'Location': '-', 'Seller Name': '-', 'Phone': '-',
        'Seats': '-', 'Kilometres': '-', 'Body Style': '-', 'Doors': '-',
        'Transmission': '-', 'Model': '-', 'Extra Info': '-', 'Fuel': '-'
    }
    page = await context.new_page()
    try:
        await human_pause(0.3, 1.0)

        async def go():
            return await page.goto(href, timeout=60_000, wait_until='domcontentloaded', referer=referer_url)
        await with_retries(go, attempts=3, base_delay=1.2)

        # Duration
        tag = await page.query_selector('[data-testid="listing-date"]')
        if tag:
            raw = await tag.text_content()
            if raw:
                data['Duration Posted'] = " ".join(raw.split())

        # Basic fields
        name = await page.query_selector('h1')
        if name:
            data['Name'] = (await name.inner_text()).strip()

        price = await page.query_selector('p[data-testid="vip-price"]')
        if price:
            data['Price'] = safe_for_excel((await price.inner_text()).strip())

        loc = await page.query_selector('[data-testid="seller-profile"] [data-testid*="location"], .bEMmoW .iCgpsX button')
        if loc:
            data['Location'] = (await loc.inner_text()).strip()

        seller = await page.query_selector('h3 a, [data-testid="seller-profile"] h3 a')
        if seller:
            data['Seller Name'] = (await seller.inner_text()).strip()

        # Phone: 3–6s pause then reveal
        try:
            await asyncio.sleep(random.uniform(3, 6))
            reveal_btn = await page.query_selector('button:has-text("Reveal")') \
                        or await page.query_selector("button:has(p[aria-label='Reveal phone number'])")
            if reveal_btn:
                try: await reveal_btn.scroll_into_view_if_needed()
                except: pass
                await reveal_btn.click()
                await page.wait_for_selector('a[href^="tel:"]', timeout=5000)

            phone_a = await page.query_selector('a[href^="tel:"]')
            if phone_a:
                data['Phone'] = safe_for_excel((await phone_a.inner_text()).strip())
            else:
                phone_p = await page.query_selector('p:has-text("+1-"), p:has-text("+1 ")')
                if phone_p:
                    data['Phone'] = safe_for_excel((await phone_p.inner_text()).strip())
        except:
            pass

        # Vehicle details (light)
        detail_divs = await page.query_selector_all(
            'div.sc-eb45309b-0.iNzWBi, [data-testid="attributes"] div, [data-testid="attribute-row"]'
        )
        for div in detail_divs:
            try:
                label_tag = await div.query_selector('p.sc-82669b63-0.cqjWkX, p')
                value_tags = await div.query_selector_all('p.sc-991ea11d-0.fgtvkm, p')
                label = (await label_tag.inner_text()).strip() if label_tag else ''
                values = [ (await v.inner_text()).strip() for v in value_tags ][1:] if value_tags else []
                if label == 'Seats':
                    data['Seats'] = safe_for_excel(', '.join(values))
                elif label == 'Kilometres':
                    data['Kilometres'] = safe_for_excel(', '.join(values))
                elif label == 'Body Style':
                    if values: data['Body Style'] = safe_for_excel(values[0])
                    if len(values) > 1: data['Doors'] = safe_for_excel(values[1])
                elif label == 'Transmission':
                    data['Transmission'] = safe_for_excel(', '.join(values))
                elif label == 'Model':
                    if values: data['Model'] = safe_for_excel(values[0])
                    if len(values) > 1: data['Extra Info'] = safe_for_excel(', '.join(values[1:]))
                elif label == 'Fuel':
                    data['Fuel'] = safe_for_excel(', '.join(values))
            except:
                continue

    except PlaywrightTimeoutError:
        log(f"Timeout while loading {href}")
    except Exception as e:
        log(f"Error scraping {href}: {e}")
    finally:
        await page.close()
    return data

async def scrape_kijiji(url: str, max_pages: int, csv_name: str,
                        log: Callable[[str], None],
                        stop_event: threading.Event,
                        out_q: queue.Queue):
    """
    Runs inside a background thread (asyncio in that thread).
    Buffers rows and flushes CSV every FLUSH_EVERY rows (and at end).
    Sends events to UI via out_q: {'type': 'log'|'flush'|'done'|'error', ...}
    """
    total_rows = 0
    header: List[str] = []
    buffer: List[Dict[str, Any]] = []

    def flush_to_csv(final=False):
        nonlocal header, buffer, total_rows
        if not buffer:
            return
        write_header = (not os.path.exists(csv_name)) or (total_rows == 0)
        mode = "w" if write_header else "a"
        with open(csv_name, mode, newline="", encoding="utf-8") as f:
            if not header:
                header = list(buffer[0].keys())
            w = csv.DictWriter(f, fieldnames=header)
            if write_header:
                w.writeheader()
            w.writerows(buffer)
        total_rows += len(buffer)
        buffer.clear()
        out_q.put({"type": "flush", "total": total_rows, "final": final})

    try:
        async with async_playwright() as p:
            browser, context = await create_context(p, headless=True)

            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded")
            await page.wait_for_selector(
                "[data-testid='srp-search-list'] section, .vAthl .vAthl div section", timeout=30_000
            )

            current_page_url = url
            page_count = 1

            while current_page_url and page_count <= max_pages and not stop_event.is_set():
                out_q.put({"type": "log", "msg": f"Scraping Page {page_count}: {current_page_url}"})
                await with_retries(
                    lambda: page.goto(current_page_url, wait_until="domcontentloaded", timeout=45_000), attempts=2
                )
                await page.wait_for_selector(
                    "[data-testid='srp-search-list'] section, .vAthl .vAthl div section", timeout=30_000
                )

                first_containers = await page.query_selector_all(".vAthl .vAthl div section")
                other_containers = await page.query_selector_all("[data-testid='srp-search-list'] section")
                all_containers = first_containers + other_containers
                out_q.put({"type": "log", "msg": f"Found {len(all_containers)} listings on this page"})

                hrefs: List[str] = []
                for container in all_containers:
                    if stop_event.is_set(): break
                    try:
                        duration_tag = await container.query_selector('[data-testid="listing-date"]')
                        duration_text = (await duration_tag.text_content()).strip() if duration_tag else "-"
                        link_tag = await container.query_selector('a[data-testid="listing-link"]')
                        href = await link_tag.get_attribute("href") if link_tag else None
                        if href and href.startswith("/"):
                            href = "https://www.kijiji.ca" + href
                        if href and any(u in duration_text for u in ["hrs", "hr", "mins", "min", "seconds", "sec"]):
                            hrefs.append(href)
                    except:
                        continue

                for idx, href in enumerate(hrefs, 1):
                    if stop_event.is_set(): break
                    out_q.put({"type": "log", "msg": f"  • Listing {idx}/{len(hrefs)}"})
                    row = await fetch_listing(context, href, url, lambda m: out_q.put({"type":"log","msg":m}))
                    buffer.append(row)

                    if len(buffer) >= FLUSH_EVERY:
                        flush_to_csv(final=False)

                    await human_pause(1.0, 2.0)

                if page_count >= max_pages or stop_event.is_set():
                    break

                # Pagination
                try:
                    next_a = await page.query_selector('li[data-testid="pagination-next-link"] a') \
                             or await page.query_selector('nav[aria-label="Search Pagination"] a:has-text("Next")')
                    if next_a:
                        next_href = await next_a.get_attribute("href")
                        if next_href:
                            if next_href.startswith("/"):
                                next_href = "https://www.kijiji.ca" + next_href
                            m = re.search(r"/page-(\d+)/", next_href)
                            if m and int(m.group(1)) > max_pages:
                                current_page_url = None
                            else:
                                current_page_url = next_href
                                page_count += 1
                        else:
                            current_page_url = None
                    else:
                        current_page_url = None
                except:
                    current_page_url = None

            # final flush
            flush_to_csv(final=True)
            await context.close()
            await browser.close()

        out_q.put({"type": "done", "total": total_rows})

    except Exception:
        out_q.put({"type": "error", "trace": traceback.format_exc()})

# =========================
# Streamlit UI (polished)
# =========================
st.set_page_config(page_title="Kijiji Cars Scraper", page_icon="🚗", layout="wide")

# --- Global CSS (subtle, professional) ---
st.markdown("""
<style>
/* Hide Streamlit chrome we don't need */
header[data-testid="stHeader"] { background: transparent; }
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}

/* Typography & base colors */
:root {
  --brand: #373373;  /* deep indigo (Kijiji-ish accent) */
  --soft: #F5F6FA;
  --ok: #12B886;
  --warn: #F08C00;
  --muted: #6B7280;
}
body, .stMarkdown, .stText, .stCode { color: #1F2937; }

/* Card-like containers */
.block-container { padding-top: 1.6rem; }
div[data-testid="stVerticalBlock"] > div:has(> .element-container) {
  border-radius: 10px;
}

/* Top bar */
.topbar {
  background: white;
  border: 1px solid #EEF0F4;
  border-radius: 12px;
  padding: 14px 18px;
  margin-bottom: 12px;
}
.brand {
  font-weight: 700; color: var(--brand); font-size: 1.15rem;
  letter-spacing: .2px;
}
.subtle { color: var(--muted); }

/* Status chip */
.status {
  display:inline-block; padding: 4px 10px; border-radius: 999px;
  font-size: 0.82rem; border: 1px solid #E5E7EB;
}
.status.running { background: #ECFDF5; color: var(--ok); border-color: #C7F9E9;}
.status.idle    { background: #F3F4F6; color: #6B7280;}

/* KPI tiles */
.kpi {
  background: white; border: 1px solid #EEF0F4; border-radius: 12px;
  padding: 12px 14px; height: 92px;
}
.kpi .label { color: var(--muted); font-size: .82rem; }
.kpi .value { font-weight: 700; font-size: 1.4rem; margin-top: 6px; }

/* CTA buttons */
.stButton > button {
  border-radius: 10px; padding: 0.55rem 1rem; font-weight: 600;
}
.stButton > button[kind="primary"] {
  background: var(--brand);
}
.stButton > button:hover { filter: brightness(0.98); }

/* Scrollable log area */
textarea[aria-label="Log"] {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
  background: #E5E7EB;
  color: #E5E7EB;
  border-radius: 10px;
  border: 1px solid #111827;
}

/* Sticky footer */
.footer {
  margin-top: 10px; padding: 8px 12px; border-radius: 10px;
  background: var(--soft); color: #4B5563; font-size: .9rem;
  border: 1px solid #EEF0F4;
}
</style>
""", unsafe_allow_html=True)

# --- Top bar with brand + status + KPIs ---
with st.container():
    st.markdown('<div class="topbar">', unsafe_allow_html=True)
    top_cols = st.columns([3, 2, 2, 2, 2])
    with top_cols[0]:
        st.markdown("**<span class='brand'>Kijiji Cars Scraper</span>**<br>"
                    "<span class='subtle'>Fast, reliable, CSV-ready</span>",
                    unsafe_allow_html=True)
    # placeholders for status + KPIs (filled below)
    status_box = top_cols[1].empty()
    kpi_pages = top_cols[2].container()
    kpi_rows  = top_cols[3].container()
    kpi_rate  = top_cols[4].container()
    st.markdown("</div>", unsafe_allow_html=True)

# --- Sidebar (controls) ---
with st.sidebar:
    st.markdown("### 🔍 Search")
    url = st.text_input("Search URL", value=DEFAULT_URL, help="Paste a Kijiji results URL.")
    max_pages = st.number_input("Max pages", min_value=1, max_value=200, value=45, step=1)
    csv_name = st.text_input("CSV file name", value=DEFAULT_CSV)

    st.markdown("---")
    c1, c2 = st.columns(2)
    btn_start = c1.button("Start", type="primary", use_container_width=True)
    btn_stop  = c2.button("Stop", use_container_width=True)

    st.markdown("---")
    st.caption("While running, the UI auto-updates every "
               f"**{UI_REFRESH_SECS}s**. You can download partial CSV any time.")

# --- Session state ---
if "running" not in st.session_state: st.session_state["running"] = False
if "log_lines" not in st.session_state: st.session_state["log_lines"] = []
if "events_q" not in st.session_state: st.session_state["events_q"] = queue.Queue()
if "stop_event" not in st.session_state: st.session_state["stop_event"] = threading.Event()
if "thread" not in st.session_state: st.session_state["thread"] = None
if "total_rows" not in st.session_state: st.session_state["total_rows"] = 0
if "pages_done" not in st.session_state: st.session_state["pages_done"] = 0  # we’ll derive loosely from logs

# --- Tabs ---
tabs = st.tabs(["Overview", "Logs", "Settings"])

# Overview tab
with tabs[0]:
    # KPI tiles
    k1, k2, k3 = st.columns(3)
    with k1:
        st.markdown("<div class='kpi'><div class='label'>Pages (approx)</div>"
                    f"<div class='value'>{st.session_state['pages_done']}</div></div>", unsafe_allow_html=True)
    with k2:
        st.markdown("<div class='kpi'><div class='label'>Rows flushed</div>"
                    f"<div class='value'>{st.session_state['total_rows']}</div></div>", unsafe_allow_html=True)
    with k3:
        # Fake success rate as a simple function of rows (you can wire a real metric if you want)
        rate = "OK" if st.session_state["total_rows"] > 0 else "-"
        st.markdown("<div class='kpi'><div class='label'>Status</div>"
                    f"<div class='value'>{rate}</div></div>", unsafe_allow_html=True)

    # Download placeholder
    dl_placeholder = st.empty()
    st.markdown("")

    # Progress
    progress = st.progress(0, text="Idle")

    # Recent preview (lazy: show file exists)
    if os.path.exists(csv_name) and st.session_state["total_rows"] > 0:
        st.caption("Preview (first 50 rows):")
        try:
            import pandas as pd
            df_prev = pd.read_csv(csv_name, nrows=50)
            st.dataframe(df_prev, use_container_width=True, height=410)
        except Exception:
            st.info("CSV written. Preview unavailable due to encoding or read error.")

# Logs tab
with tabs[1]:
    st.caption("Live run log")
    log_box = st.empty()  # we render as code or textarea depending on length

# Settings tab
with tabs[2]:
    st.caption("Tuning")
    st.write(f"- **Flush every:** {FLUSH_EVERY} rows")
    st.write(f"- **UI refresh:** every {UI_REFRESH_SECS}s")
    st.write("- **Assets blocked:** images, fonts, media")
    st.write("- **Headless:** True")
    st.write("- **Masked webdriver:** Yes")
    st.write("- **Timeouts:** nav=90s, default=20s")

# --- Event handling / Rendering ---
def render_status_and_kpis():
    # status chip
    state_cls = "running" if st.session_state["running"] else "idle"
    state_txt = "Running" if st.session_state["running"] else "Idle"
    status_box.markdown(
        f"<span class='status {state_cls}'>{state_txt}</span>",
        unsafe_allow_html=True
    )

def render_download():
    if os.path.exists(csv_name) and st.session_state["total_rows"] > 0:
        with open(csv_name, "rb") as f:
            tabs[0].download_button(
                label=f"⬇️ Download CSV (rows: {st.session_state['total_rows']})",
                data=f.read(),
                file_name=csv_name,
                mime="text/csv",
                key="dl_btn",  # stable key; only one button per rerun
                help=f"Updates every {FLUSH_EVERY} rows while scraping.",
            )


def drain_events_and_render_logs():
    q = st.session_state["events_q"]
    updated = False
    # drain
    try:
        while True:
            evt = q.get_nowait()
            et = evt.get("type")
            if et == "log":
                msg = evt["msg"]
                st.session_state["log_lines"].append(msg)
                # naive page count increment when we see page starts
                if msg.startswith("Scraping Page "):
                    try:
                        n = int(msg.split("Scraping Page ")[1].split(":")[0].strip())
                        st.session_state["pages_done"] = max(st.session_state["pages_done"], n)
                    except:
                        pass
            elif et == "flush":
                st.session_state["total_rows"] = evt["total"]
                updated = True
            elif et == "done":
                st.session_state["running"] = False
                st.session_state["total_rows"] = evt["total"]
                updated = True
            elif et == "error":
                st.session_state["log_lines"].append("ERROR:\n" + evt["trace"])
                st.session_state["running"] = False
                updated = True
    except queue.Empty:
        pass

    # cap log memory & display
    st.session_state["log_lines"] = st.session_state["log_lines"][-400:]
    logs = "\n".join(st.session_state["log_lines"])
    if len(st.session_state["log_lines"]) <= 20:
        log_box.code(logs or "—")
    else:
        log_box.text_area(
            "Log",
            logs,
            height=LOG_SCROLL_HEIGHT,
            label_visibility="collapsed",
            disabled=True,  # readonly
        )
    # refresh download on flush/done
    # if updated:
    #     render_download()

# --- Start/Stop buttons ---
if btn_start and not st.session_state["running"]:
    # reset for fresh run
    st.session_state["log_lines"] = []
    st.session_state["total_rows"] = 0
    st.session_state["pages_done"] = 0
    st.session_state["events_q"] = queue.Queue()
    st.session_state["stop_event"] = threading.Event()
    if os.path.exists(csv_name):
        try: os.remove(csv_name)
        except: pass
    st.session_state["running"] = True

    def thread_target(url_, pages_, csv_, stop_evt, out_q):
        try:
            asyncio.run(
                scrape_kijiji(
                    url_, pages_, csv_,
                    lambda m: out_q.put({"type":"log","msg":m}),
                    stop_evt, out_q
                )
            )
        except Exception:
            out_q.put({"type": "error", "trace": traceback.format_exc()})

    t = threading.Thread(
        target=thread_target,
        args=(url, max_pages, csv_name, st.session_state["stop_event"], st.session_state["events_q"]),
        daemon=True,
    )
    st.session_state["thread"] = t
    t.start()

if btn_stop and st.session_state["running"]:
    st.session_state["stop_event"].set()

# --- Draw UI bits that change ---
render_status_and_kpis()
drain_events_and_render_logs()
render_download()

# --- Progress + footer + auto-rerun ---
with tabs[0]:
    if st.session_state["running"]:
        progress.progress(50, text=f"Scraping… rows flushed: {st.session_state['total_rows']} (auto-updates every {UI_REFRESH_SECS}s)")
    else:
        if st.session_state["total_rows"] > 0:
            progress.progress(100, text=f"Finished / Stopped — rows: {st.session_state['total_rows']}")
        else:
            progress.progress(0, text="Idle")

st.markdown(
    "<div class='footer'>Tip: keep the app open while scraping. You can download partial CSV any time — the scraper keeps running in the background.</div>",
    unsafe_allow_html=True,
)

# gentle auto-rerun while running so UI keeps updating
if st.session_state["running"]:
    time.sleep(UI_REFRESH_SECS)
    try: st.rerun()
    except Exception: st.experimental_rerun()

st.markdown(
    """
    <style>
    .footer {
        margin-top: 25px;
        padding: 10px;
        text-align: center;
        font-size: 0.9rem;
        color: #6B7280; /* soft gray */
    }
    </style>
    <div class="footer">
        Developed by <b>Nafay Ur Rehman</b>
    </div>
    """,
    unsafe_allow_html=True
)


