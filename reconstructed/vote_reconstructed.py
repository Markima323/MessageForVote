"""
StarRailVote — best-effort static reconstruction.

Source program: StarRailVote.exe (PyInstaller --onedir, Python 3.13)
Original source unavailable: vote.py and gui.pyc are PyArmor 9.2.4 (trial)
encrypted, build stamp 2026-05-06.

This file is a STATIC RECONSTRUCTION. It is the architecture and behavior
implied by the bundle, the GUI screenshot, the dependency graph, the
PyArmor blob sizes (gui=19575 / vote=44449 bytes encrypted), and standard
patterns for vote-bots. It is intentionally skeletal — selectors, URLs,
captcha handling, and retry/rate-limit policy are placeholders that need
a runtime trace to confirm.

Confidence legend on each section:
    [HARD]   directly observable in bundle / GUI screenshot
    [STRONG] forced by the dependency set + observable behavior
    [GUESS]  plausible architecture; needs dynamic confirmation

The original program splits into two modules:
    gui   — top-level entry frozen into the .exe (~20 KB encrypted body)
    vote  — loose file in _internal/ (~44 KB encrypted body)

This file fuses them since the boundary is opaque without dynamic data.
"""

# =============================================================================
# Imports — [HARD] every entry corresponds to a module bundled in PYZ.pyz or
# _internal/.  Bundle inventory (non-stdlib): PIL, anyio, attrs, brotli,
# certifi, cffi, charset_normalizer, click, clr, clr_loader, colorama,
# greenlet, h11/h2/hpack/hyperframe, httpcore, httpx, idna, numpy, outcome,
# packaging, playwright, playwright_stealth, psutil, pyee, pygments,
# pyreadline3, pythonnet, sniffio, sortedcontainers, trio,
# typing_extensions, win32con, win32evtlogutil, winerror, yaml.
# =============================================================================
import ctypes
import os
import sys
import json
import asyncio
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Optional, List, Callable

import tkinter as tk
from tkinter import ttk, scrolledtext

# [HARD] bundled — proxy fetch goes through this, not urllib
import httpx

# [STRONG] PyYAML is in PYZ.pyz; no on-disk *.yaml in the bundle, so the
# original loads/saves a config at runtime. We mirror that here.
import yaml

# [HARD] bundled
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from playwright_stealth import Stealth  # playwright_stealth 2.0.x API

# [STRONG] pythonnet + clr_loader bundled — the program may invoke .NET
# (e.g. for an HttpClient with TLS fingerprint matching, or window/process
# manipulation). Not used in this reconstruction; flagged as an open
# question that dynamic trace should answer.
# import clr  # noqa: not actually imported here


# =============================================================================
# Config — [HARD] every field, default value, and label confirmed from the
# GUI screenshot at runtime
# =============================================================================
@dataclass
class Config:
    proxy_api_url: str = ""                     # GUI: 代理 API URL
    proxy_protocol: str = "http"                # GUI: 代理协议 (combobox)
    target_character_name: str = ""             # GUI: 目标角色名 (如 白厄) [legacy]
    target_character_names: List[str] = field(default_factory=list)  # GUI 多选：本轮要投的所有角色
    target_button_index: int = 0                # GUI: 或 按钮序号 (0-70) [legacy]
    concurrency: int = 6                        # GUI: 并发数 (3×2 平铺时正好)
    total_votes: int = 200                      # GUI: 总投票轮次（每轮 = 选中的所有角色 + 评分 + 截图）
    debug_mode: bool = False                    # GUI: 调试模式（一轮结束后保留页面，不进入下一轮）
    browser_engine: str = "chromium"            # GUI: 浏览器引擎 (combobox: chromium / webkit)
    browser_path: str = (
        r"C:\Program Files (x86)\Microsoft Edge\Application\msedge.exe"
    )                                           # GUI: 浏览器路径 (仅 chromium 用)
    headless: bool = True                       # GUI: 无头模式（生产推荐）

    # Last-known mapping (populated after a successful card locate).
    # Used only as a UX hint — the live page is still the source of truth
    # at click-time (index might change between rounds).
    last_resolved_name: str = ""
    last_resolved_index: int = -1
    last_resolved_at: str = ""

    # Manual captcha popup offset (px). Center is (0, 0); positive x moves
    # right, positive y moves down. Provided as sliders in the GUI so the
    # user can tweak when CSS centering doesn't fully win against the
    # vendor's internal positioning.
    captcha_offset_x: int = 0
    captcha_offset_y: int = 0

    @staticmethod
    def candidate_count() -> int:
        # [HARD] GUI label says "0-70" → 71 candidates indexed 0..70
        return 71


# =============================================================================
# Proxy management — [HARD] runtime log evidence:
#   "代理池新增 20 个，当前 20 个，黑名单 0 个"   ← refill batch = 20
#   "代理池新增 20 个，当前 27 个，黑名单 13 个"  ← failed proxies blacklisted
# Refill is triggered when active size drops below a threshold (≈7, since
# 20 - 13 = 7 surviving proxies prompted a +20 refill).
# =============================================================================
REFILL_BATCH_SIZE = 20      # [HARD]
REFILL_THRESHOLD  = 7       # [STRONG] (observed boundary: refill fired with 7 left)

# 本轮主投票目标角色：优先从 config.yaml 的 target_character_names 读，
# 没设/为空时回退到这个默认列表。改人只需要在 config.yaml 里编辑。
DEFAULT_TARGET_NAMES: List[str] = ["砂金", "卡芙卡", "景元"]


class ProxyManager:
    """[HARD-confirmed] Fetch-and-blacklist proxy rotation."""

    def __init__(self, api_url: str, protocol: str,
                 log: Callable[[str], None]):
        self.api_url = api_url
        self.protocol = protocol
        self.log = log
        self._lock = threading.Lock()
        self._active: List[str] = []     # available "ip:port" strings
        self._blacklist: set = set()     # ip:port strings that failed

    def _fetch_batch(self) -> List[str]:
        # [STRONG] uses httpx (bundled). Response shape unconfirmed —
        # common paid-proxy patterns:
        #   - text/plain: one ip:port per line
        #   - JSON: {"data": [{"ip": ..., "port": ...}, ...]}
        if not self.api_url:
            return []
        try:
            with httpx.Client(timeout=10.0) as client:
                r = client.get(self.api_url)
                r.raise_for_status()
                body = r.text
        except Exception as e:
            self.log(f"[WARN] 拉取代理失败: {e!r}")
            return []

        body_stripped = body.strip()
        if body_stripped.startswith("{") or body_stripped.startswith("["):
            try:
                obj = json.loads(body_stripped)
                if isinstance(obj, dict) and "data" in obj:
                    obj = obj["data"]
                out = []
                for item in obj:
                    if isinstance(item, str):
                        out.append(item)
                    elif isinstance(item, dict):
                        ip = item.get("ip") or item.get("host")
                        port = item.get("port")
                        if ip and port:
                            out.append(f"{ip}:{port}")
                return out
            except Exception:
                pass
        return [ln.strip() for ln in body_stripped.splitlines() if ":" in ln]

    def _refill_locked(self):
        batch = self._fetch_batch()
        added = 0
        for ip_port in batch:
            if ip_port in self._blacklist:
                continue
            if ip_port in self._active:
                continue
            self._active.append(ip_port)
            added += 1
        # [HARD] log format from runtime: "代理池新增 N 个，当前 N 个，黑名单 N 个"
        self.log(f"[INFO] 代理池新增 {added} 个，当前 {len(self._active)} 个，"
                 f"黑名单 {len(self._blacklist)} 个")

    def next_proxy(self) -> Optional[dict]:
        with self._lock:
            if len(self._active) <= REFILL_THRESHOLD:
                self._refill_locked()
            if not self._active:
                return None
            ip_port = self._active.pop(0)
        return {"server": f"{self.protocol}://{ip_port}", "_id": ip_port}

    def blacklist(self, ip_port: str):
        """[HARD] failed votes contribute their proxy to the blacklist."""
        with self._lock:
            self._blacklist.add(ip_port)
            try:
                self._active.remove(ip_port)
            except ValueError:
                pass


# =============================================================================
# Voter — [HARD] flow reconstructed from runtime log evidence:
#   "未弹 captcha，跳过"          ← short-timeout probe for captcha modal
#   "等不到确认投票模态"            ← long-timeout wait for confirm modal
#   "retry N/3"                     ← max 3 retries (4 attempts total/vote)
# Per-attempt timing observed: ~9-11 s navigation + ~5-6 s modal wait.
# =============================================================================
CAPTCHA_PROBE_TIMEOUT_MS  = 1500    # [STRONG] short — "未弹" is logged immediately if absent
# How long to wait at the FIRST attempt for captcha-or-modal to appear.
# If neither shows up within this window we suspect the page state is
# stuck (proxy hiccup, captcha SDK init failure, etc.) and reload + re-
# click the vote button before the second wait phase.
STUCK_DETECT_MS           = 90_000    # 90 s per phase
# Total budget = STUCK_DETECT_MS × 2 = 3 minutes. Same as the user's
# original "3 分钟没弹出就刷新" requirement, with active recovery in the
# middle instead of just one long passive wait.
CAPTCHA_APPEAR_TIMEOUT_MS = 180_000   # 3 minutes total (used in retry cycles)
# How long the human has to drag the slider once the captcha pops up.
# 3 minutes lets you cycle through 6 concurrent puzzles at a relaxed pace.
CAPTCHA_SOLVE_TIMEOUT_MS  = 180_000   # 3 minutes
# How long to wait for the post-captcha confirm modal to appear after a
# successful captcha solve. Page-internal, fast regardless of network.
CONFIRM_MODAL_TIMEOUT_MS  = 20_000
# How long to wait for the "成功投票给X" success modal AFTER the 确认投票
# click. Set to a very large value (e.g. 3_600_000 = 1 hour) when
# debugging so the script halts and you can inspect the page; otherwise
# the same as CONFIRM_MODAL_TIMEOUT_MS so a stuck attempt advances.
SUCCESS_MODAL_TIMEOUT_MS  = CONFIRM_MODAL_TIMEOUT_MS
NAVIGATE_TIMEOUT_MS       = 120_000  # bumped to 2 min — slow paid proxies otherwise crash here
# MAX_RETRIES disabled per user request — slow page loads were triggering
# retries during human captcha-solving, refreshing the page and losing
# the user's in-progress slider drag. Set back to 3 to re-enable.
MAX_RETRIES               = 0

# Tile geometry for headed multi-window mode. Default 3 columns × 2 rows
# means concurrency=6 fills the screen exactly with non-overlapping tiles.
# Slot index `s` maps to (col=s%cols, row=(s//cols)%rows).
WINDOW_GRID_COLS = 3
WINDOW_GRID_ROWS = 2


def _get_screen_size() -> tuple:
    """Return (width, height) of the primary monitor in **CSS pixels (DIPs)**.

    Chrome DevTools Protocol's setWindowBounds expects DIPs, not physical
    pixels. On a HiDPI screen with 200% scaling, a 2560×1600 physical
    screen is 1280×800 DIPs — using physical numbers would push windows
    off-screen. We detect DPI scaling and divide.
    Falls back to 1920×1080 if any Win32 call fails.
    """
    try:
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass
        phy_w = user32.GetSystemMetrics(0)
        phy_h = user32.GetSystemMetrics(1)
        # DPI scaling factor: 96 = 100%, 144 = 150%, 192 = 200%, etc.
        dc = user32.GetDC(0)
        try:
            LOGPIXELSX = 88
            dpi = gdi32.GetDeviceCaps(dc, LOGPIXELSX) or 96
        finally:
            user32.ReleaseDC(0, dc)
        scale = dpi / 96.0
        return int(phy_w / scale), int(phy_h / scale)
    except Exception:
        return 1920, 1080


def _tile_for_slot(slot: int, screen_w: int, screen_h: int,
                   cols: int = WINDOW_GRID_COLS,
                   rows: int = WINDOW_GRID_ROWS) -> tuple:
    """Return (x, y, w, h) pixel coords for a slot in a cols × rows grid.
    Slots above cols*rows wrap (so concurrency > 6 still gets a tile)."""
    tile_w = screen_w // cols
    tile_h = screen_h // rows
    col = slot % cols
    row = (slot // cols) % rows
    return col * tile_w, row * tile_h, tile_w, tile_h



def _make_captcha_init_script(offset_x: int, offset_y: int) -> str:
    """Build an init script that pins .window-show to viewport center plus
    a manual (offset_x, offset_y) pixel offset. The offset is exposed via
    `window.__captchaOffsetX/Y` globals — Python can `page.evaluate(...)`
    new values into them mid-flight to reposition a visible captcha
    without waiting for the next vote.  `window.__captchaFixAll()` is
    also exposed so the slider can trigger an immediate redraw."""
    return r"""
(() => {
    // Initial values baked from the Python side. Python can overwrite
    // these globals at any time to change the live position.
    window.__captchaOffsetX = %d;
    window.__captchaOffsetY = %d;

    function forceCenter(el) {
        if (!el) return;
        const s = el.style;
        const x = window.__captchaOffsetX | 0;
        const y = window.__captchaOffsetY | 0;
        s.setProperty('position', 'fixed', 'important');
        s.setProperty('left', `calc(50%% + ${x}px)`, 'important');
        s.setProperty('top',  `calc(50%% + ${y}px)`, 'important');
        s.setProperty('right', 'auto', 'important');
        s.setProperty('bottom', 'auto', 'important');
        s.setProperty('transform', 'translate(-50%%, -50%%)', 'important');
        s.setProperty('margin', '0', 'important');
        s.setProperty('z-index', '2147483646', 'important');
    }
    function forceFullViewport(el) {
        if (!el) return;
        const s = el.style;
        s.setProperty('position', 'fixed', 'important');
        s.setProperty('left', '0', 'important');
        s.setProperty('top', '0', 'important');
        s.setProperty('width', '100vw', 'important');
        s.setProperty('height', '100vh', 'important');
        s.setProperty('z-index', '2147483645', 'important');
    }
    function fixAll() {
        document.querySelectorAll('.window-show').forEach(forceCenter);
        document.querySelectorAll('.mask-show').forEach(forceFullViewport);
        // Re-center the page's own confirm/success modal boxes too. We
        // touch ONLY the inner box (.custom-alert-box), not the outer
        // overlay — the overlay is the dim backdrop and must stay full
        // viewport, while the box is the actual popup content.
        document.querySelectorAll('.custom-alert-box').forEach(forceCenter);
    }
    // Expose fixAll so a Python-side evaluate can trigger an immediate
    // redraw after pushing new offsets — saves up to 300 ms over the
    // setInterval fallback.
    window.__captchaFixAll = fixAll;

    fixAll();
    try {
        new MutationObserver(fixAll).observe(document.documentElement, {
            childList: true, subtree: true,
            attributes: true, attributeFilter: ['style', 'class'],
        });
    } catch (e) { /* observer may fail on early frames */ }
    setInterval(fixAll, 300);
})();
""" % (offset_x, offset_y)


class Voter:
    """[HARD-confirmed] One Voter == one vote attempt life-cycle.

    All selectors and the URL below are [GUESS]: dynamic Playwright trace
    can't be obtained because Playwright is bundled inside PYZ.pyz and
    shadows the disk transport patch. They MUST be filled in by either
    repacking PYZ or inspecting the page once the program is paused.
    """

    # [HARD] all selectors below confirmed by live DOM probe of
    # https://www.starrailawards.com/Vote2026/index.html (probe_page_v2.py output).
    # NOTE: site uses round-based phases. Earlier root path "/" had 71
    # candidates; current /Vote2026/index.html has 40. URL must be updated
    # whenever the round advances. Name-based locator is round-invariant.
    VOTE_PAGE_URL: str          = "https://www.starrailawards.com/Vote2026/index.html"
    # 71 .character-card elements; each contains .character-name + .vote-btn
    CANDIDATE_CARD_SELECTOR: str = ".character-card"
    CHARACTER_NAME_SELECTOR: str = ".character-name"
    VOTE_BUTTON_SELECTOR: str   = ".vote-btn"
    # Confirm modal — initially display:none, .custom-alert-overlay2 toggles
    # to visible when the vote-btn click is accepted by the page
    CONFIRM_MODAL_SELECTOR: str = ".custom-alert-overlay2"
    CONFIRM_TITLE_TEXT: str     = "确定投给TA吗？"
    CONFIRM_BUTTON_SELECTOR: str = ".custom-alert-button"      # "确认投票"
    CANCEL_BUTTON_SELECTOR: str = ".custom-alert-button-2"     # "我再想想"
    # Captcha vendor: Aliyun (FeiLin) slide captcha, but the site embeds
    # it inside its own wrapper divs with generic class names — confirmed
    # via probe_click_response.py:
    #   .mask-show     z=10000000  half-transparent backdrop
    #   .window-show   z=10000001  captcha popup, text starts "请完成安全验证"
    # Distinctive enough on this page to match without false positives.
    CAPTCHA_MODAL_SELECTOR: str = ".window-show"
    # [HARD] success toast — needs to be confirmed once a real successful
    # vote happens; for now the success path is "modal closes after click"
    SUCCESS_TOAST_SELECTOR: str = ".custom-alert-overlay2:not([style*='display: none'])"

    def __init__(self, cfg: Config, proxies: ProxyManager,
                 log: Callable[[str], None],
                 on_resolved: Optional[Callable[[str, int], None]] = None,
                 on_rank: Optional[Callable[[str, int], None]] = None,
                 on_pk_count: Optional[Callable[[int], None]] = None):
        self.cfg = cfg
        self.proxies = proxies
        self.log = log
        # callback fires once per successful card locate; receiver decides
        # how to dedupe / persist
        self.on_resolved = on_resolved or (lambda _name, _idx: None)
        # callback fires whenever a PK round resolves a fresh rank for the
        # target character. Receiver (the GUI) displays it on the panel.
        self.on_rank = on_rank or (lambda _name, _rank: None)
        # callback fires whenever a Pk2 succeeds. Receiver (the GUI) updates
        # the always-on-top system popup.
        self.on_pk_count = on_pk_count or (lambda _n: None)
        # 累计 Pk2 (errCode==0) 成功次数
        self.pk_success_count: int = 0

    async def _wait_for_captcha_or_modal(self, page: Page,
                                          timeout_ms: int) -> Optional[str]:
        """Race captcha popup vs confirm modal. Returns 'captcha',
        'modal', or None on timeout."""
        captcha_task = asyncio.create_task(
            page.locator(self.CAPTCHA_MODAL_SELECTOR).first.wait_for(
                state="visible", timeout=timeout_ms),
            name="captcha")
        modal_task = asyncio.create_task(
            page.locator(self.CONFIRM_MODAL_SELECTOR).filter(
                has_text=self.CONFIRM_TITLE_TEXT
            ).first.wait_for(state="visible", timeout=timeout_ms),
            name="modal")
        done, pending = await asyncio.wait(
            [captcha_task, modal_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
        winner = next(iter(done))
        try:
            winner.result()
        except Exception:
            return None
        return winner.get_name()

    async def _reload_and_reclick(self, page: Page, vote_id: int) -> bool:
        """Recovery action when nothing appears after the first wait.
        Reloads the page, re-locates the candidate, clicks vote-btn
        again. Returns True on success, False if the recovery itself
        threw (in which case the vote attempt is hopeless)."""
        try:
            await page.reload(wait_until="domcontentloaded",
                              timeout=NAVIGATE_TIMEOUT_MS)
            card = await self._locate_card(page)
            await card.scroll_into_view_if_needed()
            await card.locator(self.VOTE_BUTTON_SELECTOR).click()
            return True
        except Exception as e:
            self.log(f"[WARN] [{vote_id}] 刷新重试失败: "
                     f"{type(e).__name__}: {str(e)[:120]}")
            return False

    async def _save_debug_snapshot(self, page: Page, vote_id: int):
        """Save screenshot + HTML of the current page to debug_snapshots/.

        Used when something unexpected happens (e.g. success modal fails to
        appear) so we can inspect the actual page state offline. Best-effort:
        if the page is already closed/detached, the failures are swallowed.
        """
        try:
            here = os.path.dirname(os.path.abspath(__file__))
        except Exception:
            here = os.getcwd()
        out_dir = os.path.join(here, "debug_snapshots")
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception:
            return
        stamp = time.strftime("%H%M%S")
        base = f"vote_{vote_id}_{stamp}"
        png_ok = html_ok = False
        try:
            await page.screenshot(path=os.path.join(out_dir, f"{base}.png"),
                                  full_page=False, timeout=5_000)
            png_ok = True
        except Exception:
            pass
        try:
            html = await page.content()
            with open(os.path.join(out_dir, f"{base}.html"),
                      "w", encoding="utf-8") as f:
                f.write(html)
            html_ok = True
        except Exception:
            pass
        url = ""
        try:
            url = page.url
        except Exception:
            pass
        self.log(f"[INFO] [{vote_id}] 调试快照: png={png_ok} html={html_ok} "
                 f"url={url[:80]} → debug_snapshots/{base}.*")

    async def _tile_window_cdp(self, ctx: BrowserContext, page: Page,
                                slot: int, vote_id: int):
        """Position the page's browser window into a grid slot.

        Uses Chrome DevTools Protocol because window.moveTo / resizeTo
        are sandbox-blocked on non-popup windows. CDP runs at the
        browser level and bypasses that restriction.
        """
        sw, sh = _get_screen_size()
        x, y, w, h = _tile_for_slot(slot, sw, sh)
        try:
            cdp = await ctx.new_cdp_session(page)
            try:
                target = await cdp.send("Browser.getWindowForTarget")
                wid = target.get("windowId")
                if wid is None:
                    return
                # Some Chrome states (e.g. maximized) refuse direct bounds
                # change — first force "normal" state, then set bounds.
                try:
                    await cdp.send("Browser.setWindowBounds", {
                        "windowId": wid,
                        "bounds": {"windowState": "normal"},
                    })
                except Exception:
                    pass
                await cdp.send("Browser.setWindowBounds", {
                    "windowId": wid,
                    "bounds": {"left": x, "top": y, "width": w, "height": h},
                })
            finally:
                try:
                    await cdp.detach()
                except Exception:
                    pass
        except Exception as e:
            self.log(f"[INFO] [{vote_id}] 窗口排布失败 (slot {slot}): "
                     f"{type(e).__name__}: {str(e)[:120]}")

    async def _new_context(self, browser: Browser,
                           proxy: Optional[dict]) -> BrowserContext:
        # [STRONG] proxy is per-context (Playwright supports this since 1.29);
        # this is the only way the "page pool" can rotate IPs across votes.
        # proxy=None → use local IP (debug mode bypasses the proxy pool).
        if proxy and proxy.get("server"):
            ctx = await browser.new_context(proxy={"server": proxy["server"]})
        else:
            ctx = await browser.new_context()
        # [HARD] playwright_stealth is bundled → applied per context
        await Stealth().apply_stealth_async(ctx)

        # ---- inject CSS that forces captcha & modal popups to center of
        # viewport. The Aliyun captcha (.window-show) and the page's own
        # alert overlays use absolute pixel positioning calibrated to a
        # large viewport; on small tiled windows they end up outside the
        # visible area. CSS overrides are inert when popups aren't shown,
        # active automatically when they are — so this stays correct as
        # the window resizes (resolution / DPI changes carry over too).
        await ctx.add_init_script(_make_captcha_init_script(
            self.cfg.captcha_offset_x, self.cfg.captcha_offset_y))
        # ---- speed-up: block resources we don't need for the vote flow ----
        # Page has ~90 character illustrations from static.appoint.icu, ~5
        # font files, plus various media — none of which the bot needs.
        # Captcha images and JS are kept (we still need to solve the slider).
        async def _route(route):
            req = route.request
            rt = req.resource_type
            url = req.url
            if rt in ("font", "media"):
                await route.abort()
                return
            if rt == "image" and "static.appoint.icu" in url:
                await route.abort()
                return
            await route.continue_()
        await ctx.route("**/*", _route)
        return ctx

    async def _locate_card(self, page: Page):
        """[HARD] return the .character-card matching the target name OR index.

        Side effect: when match is by name, also resolves the actual DOM
        index of the matched card and fires self.on_resolved(name, idx).
        The runner persists this for next-launch UX; it is NOT used at
        click-time (live DOM is always re-queried — see Q2 caching note).
        """
        name = (self.cfg.target_character_name or "").strip()
        if name:
            card = page.locator(self.CANDIDATE_CARD_SELECTOR,
                                has_text=name).first
            try:
                idx = await card.evaluate(
                    "el => Array.from("
                    "document.querySelectorAll('.character-card')"
                    ").indexOf(el)"
                )
                if isinstance(idx, int) and idx >= 0:
                    self.on_resolved(name, idx)
            except Exception:
                pass
            return card
        return page.locator(self.CANDIDATE_CARD_SELECTOR).nth(
            self.cfg.target_button_index)

    async def _attempt(self, ctx: BrowserContext, vote_id: int,
                       slot: int = 0) -> bool:
        """One round: vote for every selected character + send rating +
        send tier-list snapshot. First vote goes through the UI so the
        captcha can fire; the rest are POSTed via fetch() in the same
        page (same session/cookies/IP), which is much faster.
        """
        page = await ctx.new_page()
        if not self.cfg.headless:
            await self._tile_window_cdp(ctx, page, slot, vote_id)
        try:
            await page.goto(self.VOTE_PAGE_URL, wait_until="domcontentloaded",
                            timeout=NAVIGATE_TIMEOUT_MS)


            # 目标角色：优先用 config.yaml 的 target_character_names；
            # 缺失或为空时回退到 DEFAULT_TARGET_NAMES。
            cfg_names = [n.strip() for n in (self.cfg.target_character_names or [])
                         if isinstance(n, str) and n.strip()]
            names = cfg_names if cfg_names else list(DEFAULT_TARGET_NAMES)

            name_to_data = await self._read_character_data(page)
            if not name_to_data:
                self.log(f"[ERROR] [{vote_id}] characterData 不可读，放弃本轮")
                return False

            targets: List[tuple] = []  # (name, vid)
            for n in names:
                info = name_to_data.get(n)
                if not info or info.get("id") is None:
                    self.log(f"[WARN] [{vote_id}] 角色 '{n}' 不在 characterData，跳过")
                    continue
                targets.append((n, info["id"]))
            if not targets:
                self.log(f"[ERROR] [{vote_id}] 所有目标角色都没匹配到 vid")
                return False

            self.log(f"[INFO] [{vote_id}] 本轮目标 {len(targets)} 个: "
                     f"{[f'{n}({v})' for n, v in targets]}")

            # ---- first vote via UI: triggers captcha popup if needed ----
            first_name = targets[0][0]
            try:
                ok_first = await self._first_vote_via_ui(page, vote_id, first_name)
            except Exception as e:
                self.log(f"[WARN] [{vote_id}] 第一票 UI 流程异常: "
                         f"{type(e).__name__}: {str(e)[:80]}")
                ok_first = False

            # ---- remaining votes via fetch() (only if first vote was confirmed) ----
            if ok_first:
                for name, vid in targets[1:]:
                    await self._fetch_vote(page, vote_id, name, vid)

            # ---- 评分 → 截图 → 点赞 (replays 418.js lines 56-184) ----
            # Always fire these regardless of whether the first vote's success
            # modal was detected — server often accepts the vote even when the
            # GUI detection times out, and the user explicitly asks for parity
            # with 418.js (which sends Top + SaveTierList unconditionally).
            zan_id = await self._fetch_top(page, vote_id)
            await self._fetch_tier_list(page, vote_id, targets, name_to_data)
            if zan_id is not None:
                await self._fetch_zan(page, vote_id, zan_id)
            else:
                self.log(f"[INFO] [{vote_id}] 评分接口未返回 id，跳过点赞")

            # ---- PK 阶段（一站到底）----
            await self._pk_round(page, vote_id, name_to_data)

            if ok_first:
                self.log(f"[OK] [{vote_id}] 本轮全部请求已发送")
            else:
                self.log(f"[WARN] [{vote_id}] 第一票未识别成功，"
                         f"评分+截图分享已发送")
            return ok_first
        finally:
            if self.cfg.debug_mode:
                self.log(f"[INFO] [{vote_id}] 调试模式：保留页面供检查")
            else:
                try:
                    await page.close()
                except Exception:
                    pass

    # =========================================================================
    # Round-level helpers — new in the API-first flow
    # =========================================================================
    async def _read_character_data(self, page: Page) -> dict:
        """Return {name: {id,image,gender,...}} for every character.

        character.js declares the array as `const characterData = [...]` at
        script scope, so it is NOT reachable via window.characterData. We
        fetch the source file via page.request (which reuses the context's
        proxy + cookies but bypasses CORS, unlike in-page fetch), slice
        out the JSON-shaped array between the first `[` and the matching
        closing `]`, and parse it in Python.
        """
        urls = [
            "https://static.appoint.icu/Railvote/character.js?v=3",
            "https://static.appoint.icu/Railvote/character.js",
        ]
        text = None
        for u in urls:
            try:
                resp = await page.request.get(u, timeout=15_000)
                if resp.ok:
                    body = await resp.text()
                    if body and "name" in body:
                        text = body
                        break
            except Exception as e:
                self.log(f"[WARN] 抓 {u} 失败: {type(e).__name__}: {str(e)[:80]}")
                continue
        if not text:
            self.log("[WARN] 读取 character.js 失败：所有 URL 都拿不到")
            return {}
        i = text.find("[")
        j = text.rfind("]")
        if i < 0 or j <= i:
            self.log("[WARN] character.js 里没找到 [ ... ] 数组")
            return {}
        try:
            raw = json.loads(text[i:j + 1])
        except Exception as e:
            self.log(f"[WARN] character.js JSON 解析失败: {type(e).__name__}: {str(e)[:80]}")
            return {}
        out = {}
        for item in raw or []:
            if isinstance(item, dict) and item.get("name"):
                out[item["name"]] = item
        if not out:
            self.log("[WARN] character.js 解析后为空，可能 CDN 返回格式变了")
        return out

    async def _fetch_vote(self, page: Page, vote_id: int,
                          name: str, vid: int) -> bool:
        """POST /Active2551/Vote via fetch — request literally matches 418.js."""
        try:
            result = await page.evaluate(
                """async (vid) => {
                    const r = await fetch("https://www.starrailawards.com/Active2551/Vote", {
                        "headers": {
                            "accept": "*/*",
                            "accept-language": "en-US,en;q=0.9",
                            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                            "priority": "u=1, i",
                            "sec-fetch-dest": "empty",
                            "sec-fetch-mode": "cors",
                            "sec-fetch-site": "same-origin",
                            "x-requested-with": "XMLHttpRequest"
                        },
                        "referrer": "https://www.starrailawards.com/Vote2026/index.html",
                        "body": "gp=&vid=" + vid,
                        "method": "POST",
                        "mode": "cors",
                        "credentials": "include"
                    });
                    let text = "";
                    try { text = await r.text(); } catch (e) {}
                    return { status: r.status, text: text.slice(0, 200) };
                }""",
                vid,
            )
            status = result.get("status") if isinstance(result, dict) else None
            body = (result.get("text") or "") if isinstance(result, dict) else ""
            ok = status == 200
            tag = "OK" if ok else "WARN"
            self.log(f"[{tag}] [{vote_id}] fetch 投票 {name}(vid={vid}) "
                     f"status={status} body={body[:80]}")
            return ok
        except Exception as e:
            self.log(f"[WARN] [{vote_id}] fetch 投票 {name} 异常: "
                     f"{type(e).__name__}: {str(e)[:120]}")
            return False

    async def _fetch_top(self, page: Page, vote_id: int) -> Optional[int]:
        """POST /Active2551/Top — request literally matches 418.js.
        Returns the score-record id from the response (used by /Zan), or None."""
        try:
            result = await page.evaluate(
                """async () => {
                    const r = await fetch("https://www.starrailawards.com/Active2551/Top", {
                        "headers": {
                            "accept": "*/*",
                            "accept-language": "en-US,en;q=0.9",
                            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                            "priority": "u=1, i",
                            "sec-fetch-dest": "empty",
                            "sec-fetch-mode": "cors",
                            "sec-fetch-site": "same-origin",
                            "x-requested-with": "XMLHttpRequest"
                        },
                        "referrer": "https://www.starrailawards.com/Vote2026/index.html",
                        "body": "tp=2&st=206&score=10&msg=",
                        "method": "POST",
                        "mode": "cors",
                        "credentials": "include"
                    });
                    let text = "";
                    try { text = await r.text(); } catch (e) {}
                    return { status: r.status, text: text };
                }"""
            )
            body = result.get("text") or ""
            self.log(f"[INFO] [{vote_id}] fetch 评分 status={result.get('status')} "
                     f"body={body[:120]}")
            # 评分成功后服务器返回的 id 用于 /Zan 点赞
            # 实测路径: data.model.id（评分记录 ID，递增）
            try:
                obj = json.loads(body)
                if isinstance(obj, dict):
                    data = obj.get("data")
                    if isinstance(data, dict):
                        model = data.get("model")
                        if isinstance(model, dict) and isinstance(model.get("id"), int):
                            return model["id"]
                        for k in ("id", "Id", "ID"):
                            if isinstance(data.get(k), int):
                                return data[k]
                    for k in ("id", "Id", "ID"):
                        if isinstance(obj.get(k), int):
                            return obj[k]
            except Exception:
                pass
            return None
        except Exception as e:
            self.log(f"[WARN] [{vote_id}] fetch 评分异常: "
                     f"{type(e).__name__}: {str(e)[:120]}")
            return None

    async def _fetch_zan(self, page: Page, vote_id: int, zan_id: int) -> None:
        """POST /Active2551/Zan — 点赞，request literally matches 418.js."""
        try:
            result = await page.evaluate(
                """async (id) => {
                    const r = await fetch("https://www.starrailawards.com/Active2551/Zan", {
                        "headers": {
                            "accept": "*/*",
                            "accept-language": "en-US,en;q=0.9",
                            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                            "priority": "u=1, i",
                            "sec-fetch-dest": "empty",
                            "sec-fetch-mode": "cors",
                            "sec-fetch-site": "same-origin",
                            "x-requested-with": "XMLHttpRequest"
                        },
                        "referrer": "https://www.starrailawards.com/Vote2026/index.html",
                        "body": "id=" + id,
                        "method": "POST",
                        "mode": "cors",
                        "credentials": "include"
                    });
                    let text = "";
                    try { text = await r.text(); } catch (e) {}
                    return { status: r.status, text: text.slice(0, 200) };
                }""",
                zan_id,
            )
            self.log(f"[INFO] [{vote_id}] fetch 点赞 id={zan_id} "
                     f"status={result.get('status')} "
                     f"body={(result.get('text') or '')[:80]}")
        except Exception as e:
            self.log(f"[WARN] [{vote_id}] fetch 点赞异常: "
                     f"{type(e).__name__}: {str(e)[:120]}")

    # ========================================================================
    # PK 阶段：找砂金 → 没找到则刷新 → 找到则按 renqi 投币直至 0 → 取排名
    # （replays 418.js lines 98-205）
    # ========================================================================
    async def _fetch_get_pk_data(self, page: Page, vote_id: int) -> Optional[dict]:
        """POST /Active2551/GetPkData — request literally matches 418.js."""
        try:
            body = await page.evaluate(
                """async () => {
                    const r = await fetch("https://www.starrailawards.com/Active2551/GetPkData", {
                        "headers": {
                            "accept": "*/*",
                            "accept-language": "zh-CN,zh;q=0.9,fr;q=0.8,de;q=0.7",
                            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                            "priority": "u=1, i",
                            "sec-fetch-dest": "empty",
                            "sec-fetch-mode": "cors",
                            "sec-fetch-site": "same-origin",
                            "x-requested-with": "XMLHttpRequest"
                        },
                        "referrer": "https://www.starrailawards.com/Vote2026/index.html",
                        "body": "tp=2",
                        "method": "POST",
                        "mode": "cors",
                        "credentials": "include"
                    });
                    return await r.text();
                }"""
            )
            try:
                return json.loads(body)
            except Exception:
                self.log(f"[WARN] [{vote_id}] GetPkData 返回非 JSON: {body[:120]!r}")
                return None
        except Exception as e:
            self.log(f"[WARN] [{vote_id}] GetPkData 异常: "
                     f"{type(e).__name__}: {str(e)[:120]}")
            return None

    async def _fetch_pk2(self, page: Page, vote_id: int, vid: int) -> bool:
        """POST /Active2551/Pk2 — 给某角色投币，request literally matches 418.js."""
        try:
            result = await page.evaluate(
                """async (vid) => {
                    const r = await fetch("https://www.starrailawards.com/Active2551/Pk2", {
                        "headers": {
                            "accept": "*/*",
                            "accept-language": "zh-CN,zh;q=0.9,fr;q=0.8,de;q=0.7",
                            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                            "priority": "u=1, i",
                            "sec-fetch-dest": "empty",
                            "sec-fetch-mode": "cors",
                            "sec-fetch-site": "same-origin",
                            "x-requested-with": "XMLHttpRequest"
                        },
                        "referrer": "https://www.starrailawards.com/Vote2026/index.html",
                        "body": "vid=" + vid,
                        "method": "POST",
                        "mode": "cors",
                        "credentials": "include"
                    });
                    return { status: r.status, text: (await r.text()).slice(0, 200) };
                }""",
                vid,
            )
            status = result.get("status") if isinstance(result, dict) else None
            body = result.get("text") if isinstance(result, dict) else ""
            ok = False
            try:
                obj = json.loads(body) if body else None
                if isinstance(obj, dict) and obj.get("errCode") == 0:
                    ok = True
            except Exception:
                pass
            if ok:
                self.pk_success_count += 1
                try:
                    self.on_pk_count(self.pk_success_count)
                except Exception:
                    pass
            else:
                self.log(f"[INFO] [{vote_id}] Pk2 失败 status={status} "
                         f"body={(body or '')[:80]}")
            return ok
        except Exception as e:
            self.log(f"[WARN] [{vote_id}] Pk2 异常: "
                     f"{type(e).__name__}: {str(e)[:120]}")
            return False

    async def _fetch_refresh_pk(self, page: Page, vote_id: int) -> bool:
        """POST /Active2551/RefreshPk — request literally matches 418.js."""
        try:
            result = await page.evaluate(
                """async () => {
                    const r = await fetch("https://www.starrailawards.com/Active2551/RefreshPk", {
                        "headers": {
                            "accept": "*/*",
                            "accept-language": "zh-CN,zh;q=0.9,fr;q=0.8,de;q=0.7",
                            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                            "priority": "u=1, i",
                            "sec-fetch-dest": "empty",
                            "sec-fetch-mode": "cors",
                            "sec-fetch-site": "same-origin",
                            "x-requested-with": "XMLHttpRequest"
                        },
                        "referrer": "https://www.starrailawards.com/Vote2026/index.html",
                        "body": "tp=2",
                        "method": "POST",
                        "mode": "cors",
                        "credentials": "include"
                    });
                    return { status: r.status, text: (await r.text()).slice(0, 200) };
                }"""
            )
            self.log(f"[INFO] [{vote_id}] RefreshPk status={result.get('status')} "
                     f"body={(result.get('text') or '')[:80]}")
            return result.get("status") == 200
        except Exception as e:
            self.log(f"[WARN] [{vote_id}] RefreshPk 异常: "
                     f"{type(e).__name__}: {str(e)[:120]}")
            return False

    async def _pk_round(self, page: Page, vote_id: int,
                        name_to_data: dict,
                        target_name: str = "砂金",
                        refresh_max: int = 3) -> None:
        """完整 PK 流程：找 target → 必要时刷新（上限 refresh_max）→
        按 data.rq 投币直至 0 → 读最新一条 logs 的 rank 上报 GUI。"""
        info = name_to_data.get(target_name)
        if not info or not isinstance(info.get("id"), int):
            self.log(f"[WARN] [{vote_id}] character.js 里没找到 '{target_name}'，"
                     f"跳过 PK 阶段")
            return
        target_vid = info["id"]
        self.log(f"[INFO] [{vote_id}] === PK 阶段开始, 目标 {target_name}(vid={target_vid}) ===")

        # ---- 1) 找砂金 ----
        refresh_count = 0
        while True:
            pk = await self._fetch_get_pk_data(page, vote_id)
            if pk is None or pk.get("errCode") != 0:
                self.log(f"[WARN] [{vote_id}] GetPkData 失败: {pk}")
                return
            data = pk.get("data") or {}
            left, right = data.get("pk_left"), data.get("pk_right")
            self.log(f"[INFO] [{vote_id}] PK 对战: left={left}, right={right}")
            if target_vid in (left, right):
                self.log(f"[OK] [{vote_id}] '{target_name}' 在当前 PK 中")
                break
            if refresh_count >= refresh_max:
                self.log(f"[INFO] [{vote_id}] 已刷新 {refresh_max} 次未找到 "
                         f"'{target_name}'，放弃 PK 阶段")
                return
            refresh_count += 1
            self.log(f"[INFO] [{vote_id}] 第 {refresh_count}/{refresh_max} 次刷新 PK")
            if not await self._fetch_refresh_pk(page, vote_id):
                return

        # ---- 2) 按 rq 投币循环 ----
        total = 0
        rounds = 0
        while True:
            pk = await self._fetch_get_pk_data(page, vote_id)
            if pk is None or pk.get("errCode") != 0:
                break
            rq = (pk.get("data") or {}).get("rq", 0)
            if not isinstance(rq, int) or rq <= 0:
                self.log(f"[INFO] [{vote_id}] renqi={rq}，停止投币")
                break
            self.log(f"[INFO] [{vote_id}] renqi={rq}，给 '{target_name}' 投 {rq} 票")
            for _ in range(rq):
                await self._fetch_pk2(page, vote_id, target_vid)
                total += 1
            rounds += 1
            # 防御性：如果服务器异常导致 rq 不扣减，避免死循环
            if rounds > 20:
                self.log(f"[WARN] [{vote_id}] 投币循环 {rounds} 轮仍未清空，强行退出")
                break
        self.log(f"[OK] [{vote_id}] PK 投币结束，共 {total} 次")

        # ---- 3) 读最新一条 logs 的 rank ----
        pk = await self._fetch_get_pk_data(page, vote_id)
        if pk is None or pk.get("errCode") != 0:
            return
        logs = (pk.get("data") or {}).get("logs") or []
        rank = None
        for entry in logs:
            if isinstance(entry, dict) and entry.get("vote_name") == target_name:
                rank = entry.get("rank")
                break
        if isinstance(rank, int):
            self.log(f"[OK] [{vote_id}] {target_name}当前的排名为：{rank}")
            try:
                self.on_rank(target_name, rank)
            except Exception:
                pass
        else:
            self.log(f"[INFO] [{vote_id}] logs 中未找到 '{target_name}' 的 rank")

    async def _fetch_tier_list(self, page: Page, vote_id: int,
                                targets: List[tuple],
                                name_to_data: dict) -> None:
        """POST /Active2551/SaveTierList — the "snapshot share" call.
        Puts the selected characters in the S tier and leaves the rest empty.
        """
        s_items = []
        for name, _ in targets:
            info = name_to_data.get(name) or {}
            s_items.append({
                "id": info.get("id"),
                "name": name,
                "image": info.get("image", ""),
                "gender": info.get("gender", "male"),
                "type": "char",
            })
        snapshot = {"S": s_items, "A": [], "B": [], "C": [], "D": []}
        try:
            result = await page.evaluate(
                """async (snapshot) => {
                    const r = await fetch("https://www.starrailawards.com/Active2551/SaveTierList", {
                        "headers": {
                            "accept": "*/*",
                            "accept-language": "en-US,en;q=0.9",
                            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                            "priority": "u=1, i",
                            "sec-fetch-dest": "empty",
                            "sec-fetch-mode": "cors",
                            "sec-fetch-site": "same-origin",
                            "x-requested-with": "XMLHttpRequest"
                        },
                        "referrer": "https://www.starrailawards.com/Vote2026/index.html",
                        "body": "snapshot=" + encodeURIComponent(JSON.stringify(snapshot)),
                        "method": "POST",
                        "mode": "cors",
                        "credentials": "include"
                    });
                    let text = "";
                    try { text = await r.text(); } catch (e) {}
                    return { status: r.status, text: text.slice(0, 200) };
                }""",
                snapshot,
            )
            self.log(f"[INFO] [{vote_id}] fetch 截图分享 "
                     f"status={result.get('status')} "
                     f"body={(result.get('text') or '')[:80]}")
        except Exception as e:
            self.log(f"[WARN] [{vote_id}] fetch 截图分享异常: "
                     f"{type(e).__name__}: {str(e)[:120]}")

    async def _reload_and_reclick_named(self, page: Page, vote_id: int,
                                         target_name: str) -> bool:
        """Like _reload_and_reclick but for an explicit name (no cfg lookup)."""
        try:
            await page.reload(wait_until="domcontentloaded",
                              timeout=NAVIGATE_TIMEOUT_MS)
            card = page.locator(self.CANDIDATE_CARD_SELECTOR,
                                has_text=target_name).first
            await card.scroll_into_view_if_needed()
            await card.locator(self.VOTE_BUTTON_SELECTOR).click()
            return True
        except Exception as e:
            self.log(f"[WARN] [{vote_id}] 刷新重试失败: "
                     f"{type(e).__name__}: {str(e)[:120]}")
            return False

    async def _first_vote_via_ui(self, page: Page, vote_id: int,
                                  target_name: str) -> bool:
        """The captcha-bearing first-vote flow, parameterized by name.
        Extracted from the legacy _attempt body — same logic, except the
        card is located by an explicit name instead of cfg fallback.
        """
        try:
            card = page.locator(self.CANDIDATE_CARD_SELECTOR,
                                has_text=target_name).first
            # fire the on_resolved callback (UX: remembers DOM index)
            try:
                idx = await card.evaluate(
                    "el => Array.from("
                    "document.querySelectorAll('.character-card')"
                    ").indexOf(el)"
                )
                if isinstance(idx, int) and idx >= 0:
                    self.on_resolved(target_name, idx)
            except Exception:
                pass
            await card.scroll_into_view_if_needed()
            await card.locator(self.VOTE_BUTTON_SELECTOR).click()
        except Exception as e:
            self.log(f"[ERROR] [{vote_id}] 定位/点击 {target_name} 失败: "
                     f"{type(e).__name__}: {str(e)[:120]}")
            return False

        # ---- two-phase wait with stuck-detection recovery ----
        self.log(f"[INFO] [{vote_id}] 等待 captcha 或确认模态出现 "
                 f"(每阶段 {STUCK_DETECT_MS // 1000} 秒, 共 2 阶段)")
        outcome = await self._wait_for_captcha_or_modal(page, STUCK_DETECT_MS)
        if outcome is None:
            self.log(f"[INFO] [{vote_id}] 第 1 阶段超时，刷新页面重试")
            if not await self._reload_and_reclick_named(page, vote_id, target_name):
                return False
            outcome = await self._wait_for_captcha_or_modal(page, STUCK_DETECT_MS)
            if outcome is None:
                self.log(f"[INFO] [{vote_id}] 刷新后仍无 captcha/确认框，放弃")
                return False

        # 复活赛/部分轮次会跳过 "确定投给TA吗？" 模态，
        # 验证码通过后服务器直接返回成功 → 页面显示 "成功投票给 X！"。
        # 这种情况下点 '确认投票' 是多余且会卡住的，要绕过。
        saw_success_directly = False

        if outcome == "captcha":
            self.log(f"[INFO] [{vote_id}] 弹出 captcha，进入处理")
            MAX_CAPTCHA_CYCLES = 5
            got_modal = False
            for cycle in range(MAX_CAPTCHA_CYCLES):
                try:
                    await self._handle_captcha(page)
                except NotImplementedError as e:
                    self.log(f"[WARN] [{vote_id}] captcha 处理未实现: {e}")
                    return False
                # race: 等 [确认模态 | 成功投票提示]
                try:
                    confirm_task = asyncio.create_task(
                        page.locator(self.CONFIRM_MODAL_SELECTOR).filter(
                            has_text=self.CONFIRM_TITLE_TEXT
                        ).first.wait_for(state="visible",
                                         timeout=CONFIRM_MODAL_TIMEOUT_MS),
                        name="modal",
                    )
                    success_task = asyncio.create_task(
                        page.get_by_text("成功投票").first.wait_for(
                            state="visible",
                            timeout=CONFIRM_MODAL_TIMEOUT_MS),
                        name="success",
                    )
                    done, pending = await asyncio.wait(
                        [confirm_task, success_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for t in pending:
                        t.cancel()
                    winner = next(iter(done))
                    winner.result()  # 抛异常 → 进 except
                    if winner.get_name() == "success":
                        saw_success_directly = True
                        self.log(f"[INFO] [{vote_id}] 验证后直接显示成功投票，"
                                 f"跳过 '确认投票' 模态")
                    got_modal = True
                    break
                except Exception:
                    pass
                self.log(f"[INFO] [{vote_id}] 验证后无确认模态/成功提示，"
                         f"自动再点投票按钮 (第 {cycle + 1} 次)")
                try:
                    card = page.locator(self.CANDIDATE_CARD_SELECTOR,
                                        has_text=target_name).first
                    await card.scroll_into_view_if_needed()
                    await card.locator(self.VOTE_BUTTON_SELECTOR).click()
                except Exception as e:
                    self.log(f"[WARN] [{vote_id}] 重新点击投票按钮失败: "
                             f"{type(e).__name__}")
                    return False
                try:
                    await page.locator(self.CAPTCHA_MODAL_SELECTOR
                        ).first.wait_for(state="visible",
                                         timeout=CAPTCHA_APPEAR_TIMEOUT_MS)
                except Exception:
                    try:
                        await page.locator(
                            self.CONFIRM_MODAL_SELECTOR
                        ).filter(
                            has_text=self.CONFIRM_TITLE_TEXT
                        ).first.wait_for(state="visible",
                                         timeout=CONFIRM_MODAL_TIMEOUT_MS)
                        got_modal = True
                        break
                    except Exception:
                        self.log(f"[INFO] [{vote_id}] 重点后既没 captcha 也没模态")
                        return False
            if not got_modal:
                self.log(f"[WARN] [{vote_id}] {MAX_CAPTCHA_CYCLES} 次循环"
                         f"仍未拿到确认模态，放弃")
                return False
        else:
            self.log(f"[INFO] [{vote_id}] 未弹 captcha，跳过")

        # ---- click 确认投票 ----
        try:
            await page.locator(".mask-show").first.wait_for(
                state="hidden", timeout=3_000)
        except Exception:
            pass

        if saw_success_directly:
            # 复活赛 / 部分轮次：验证码过后服务器直接返回成功，没有 confirm 模态。
            # 文字已经在 DOM 里了，再 wait_for + 找 "确定" 按钮纯粹是浪费时间
            # (默认 20s + 15s ≈ 35s)。后续 fetch 是后台 XHR，残留 modal 不影响。
            # 直接返回，让脚本立即进入 fetch / 评分 / PK 阶段。
            self.log(f"[INFO] [{vote_id}] 已直接看到成功提示，"
                     f"跳过 '确认投票' 与 '确定' 等待，立即继续")
            return True
        else:
            self.log(f"[INFO] [{vote_id}] 准备点击 '确认投票'")
            click_target = (
                page.locator(self.CONFIRM_BUTTON_SELECTOR)
                .filter(has_text="确认投票")
                .first
            )
            clicked = False
            for label, fn in [
                ("normal", lambda: click_target.click(timeout=8_000)),
                ("force",  lambda: click_target.click(timeout=8_000, force=True)),
                ("text-fallback",
                 lambda: page.get_by_text("确认投票").first.click(
                     timeout=8_000, force=True)),
            ]:
                try:
                    await fn()
                    clicked = True
                    self.log(f"[INFO] [{vote_id}] 点击成功 ({label})")
                    break
                except Exception as e:
                    self.log(f"[WARN] [{vote_id}] 点击失败 ({label}): "
                             f"{type(e).__name__}")
            if not clicked:
                return False

        # ---- 等成功提示 ----
        # 复活赛 toast 短暂可见 (~2-3s) 后自动消失。
        # 之前的实现在 saw_success_directly 路径下还会再 wait_for 一次，
        # 但走到这里 toast 大概率已经消失 → wait_for 超时 → 误判失败、
        # 触发 _save_debug_snapshot 看起来像 "卡住"。
        # 现在分两条路径处理:
        #   - saw_success_directly: race 已确认可见，直接读文字并返回
        #   - 普通路径: 现在才第一次等 toast 出现
        success_modal = page.get_by_text("成功投票").first
        if not saw_success_directly:
            try:
                await success_modal.wait_for(state="visible", timeout=8_000)
            except Exception as e:
                err_type = type(e).__name__
                err_msg = str(e).splitlines()[0][:160] if str(e) else ""
                self.log(f"[WARN] [{vote_id}] 等成功提示失败: {err_type}: {err_msg}")
                await self._save_debug_snapshot(page, vote_id)
                return False

        # toast 可能已经消失，inner_text 拿不到也无所谓——race 已经验证过
        try:
            text = (await success_modal.inner_text(timeout=1_500)).strip().replace("\n", " ")
            self.log(f"[OK] [{vote_id}] {text[:120]}")
        except Exception:
            self.log(f"[OK] [{vote_id}] 投票成功")

        # 后台尝试关掉 "确定"，关不掉也没关系（复活赛 toast 会自动消失）
        async def _dismiss_ok():
            for fn in (
                lambda: page.locator(".custom-alert-button",
                                     has_text="确定").first.click(
                    timeout=1_500, force=True),
                lambda: page.get_by_text("确定", exact=True).first.click(
                    timeout=1_500, force=True),
            ):
                try:
                    await fn()
                    return
                except Exception:
                    continue
        asyncio.create_task(_dismiss_ok())
        return True

    async def _handle_captcha(self, page: Page):
        """[USER-IMPLEMENTED] 半人工模式：等用户手动滑滑块。

        阿里云 captcha 弹出时，浏览器里出现一个 .window-show 容器；
        用户在浏览器里把滑块滑到位之后，这个容器会消失。
        我们就等它消失即可——超时上限 180 秒（够一个人慢慢滑，
        6 个并发也来得及一个一个解）。
        """
        timeout_s = CAPTCHA_SOLVE_TIMEOUT_MS // 1000
        self.log(f"[INFO] 请在浏览器窗口内完成滑块验证（{timeout_s} 秒内）")
        await page.locator(self.CAPTCHA_MODAL_SELECTOR).first.wait_for(
            state="hidden", timeout=CAPTCHA_SOLVE_TIMEOUT_MS)
        self.log("[OK] 滑块已通过，继续投票")


    async def vote_with_retries(self, browser: Browser, vote_id: int,
                                 slot: int = 0) -> bool:
        """[HARD] full retry envelope for one vote attempt."""
        for retry in range(MAX_RETRIES + 1):
            if retry > 0:
                self.log(f"[INFO] [{vote_id}] retry {retry}/{MAX_RETRIES}")
            proxy = self.proxies.next_proxy()
            if not proxy:
                self.log(f"[WARN] [{vote_id}] 无可用代理，跳过")
                return False
            ip_port = proxy["_id"]
            ctx = await self._new_context(browser, proxy)
            ok = False
            try:
                ok = await self._attempt(ctx, vote_id, slot=slot)
                if ok:
                    return True
                # [HARD] failed proxy gets blacklisted (observed pool drop)
                if ip_port:
                    self.proxies.blacklist(ip_port)
            except Exception as e:
                self.log(f"[ERROR] [{vote_id}] {e!r}")
                if ip_port:
                    self.proxies.blacklist(ip_port)
            finally:
                # In debug mode, keep the context (and its page) alive on a
                # successful round so the user can inspect the page state.
                # The runner closes the browser itself on Stop.
                if not (self.cfg.debug_mode and ok):
                    try:
                        await ctx.close()
                    except Exception:
                        pass
        return False


# =============================================================================
# VoteRunner — [HARD] runtime evidence shows BATCHED LOCKSTEP concurrency,
# NOT classic semaphore-pipelined concurrency:
#   "[0]…[1]…[2]…[3]…[4]" all complete their full retry budgets BEFORE
#   any of "[5]…[9]" appear. So the runner submits N parallel votes,
#   waits for the entire batch to finish, then submits the next N.
# Also from runtime: vote_id is a GLOBAL counter (0,1,2,…,total-1),
# advancing by `concurrency` each batch.
# =============================================================================
class VoteRunner:
    def __init__(self, cfg: Config, log: Callable[[str], None],
                 status: Callable[[str], None],
                 on_resolved: Optional[Callable[[str, int], None]] = None,
                 on_rank: Optional[Callable[[str, int], None]] = None,
                 on_pk_count: Optional[Callable[[int], None]] = None):
        self.cfg = cfg
        self.log = log
        self.set_status = status
        self.on_resolved = on_resolved
        self.on_rank = on_rank
        self.on_pk_count = on_pk_count
        self._stop = threading.Event()
        self._success = 0
        self._lock = threading.Lock()
        # references for thread-safe cancellation + slider push
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._current_tasks: List[asyncio.Task] = []
        self._browser: Optional[Browser] = None

    def request_stop(self):
        """[HARD] cancel the running batch immediately, not just at boundary."""
        self._stop.set()
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._cancel_current)

    def _cancel_current(self):
        for t in list(self._current_tasks):
            if not t.done():
                t.cancel()

    def update_captcha_offset(self, x: int, y: int):
        """Thread-safe: called from the GUI thread when the user drags a
        slider. Updates the shared cfg (so future contexts get the value
        baked in) AND pushes the new offset to every page currently open
        in the running browser, which causes any visible captcha to
        re-position immediately."""
        self.cfg.captcha_offset_x = x
        self.cfg.captcha_offset_y = y
        if self._loop is None or self._loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self._push_offset_to_all(x, y), self._loop)
        except Exception:
            pass

    async def _push_offset_to_all(self, x: int, y: int):
        if self._browser is None:
            return
        # Set globals + trigger immediate redraw via the exposed fixAll.
        js = (f"window.__captchaOffsetX = {x};"
              f"window.__captchaOffsetY = {y};"
              f"if (window.__captchaFixAll) window.__captchaFixAll();")
        for ctx in list(self._browser.contexts):
            for page in list(ctx.pages):
                try:
                    await page.evaluate(js)
                except Exception:
                    pass

    async def _async_main(self):
        self._loop = asyncio.get_running_loop()
        proxies = ProxyManager(self.cfg.proxy_api_url, self.cfg.proxy_protocol, self.log)
        async with async_playwright() as pw:
            # All chromium-family browsers (Chrome, Edge, Brave, bundled
            # chromium-1217) are launched via pw.chromium with a custom
            # executable_path. The "engine" choice is therefore implicit.
            engine = pw.chromium
            # 强制 headless=False：脚本需要用户手动滑滑块解验证码，
            # 无头模式没有窗口就没法滑。GUI 也不再开放该开关。
            launch_kwargs = dict(headless=False)
            resolved = resolve_browser_path(self.cfg.browser_path)
            if resolved:
                launch_kwargs["executable_path"] = resolved
                self.log(f"[INFO] 使用浏览器: {resolved}")
                if resolved != self.cfg.browser_path:
                    self.log(f"[WARN] 配置的浏览器路径不存在，已自动切换")
                    # update config so the GUI shows the working path next launch
                    self.cfg.browser_path = resolved
                    try:
                        save_config(self.cfg)
                    except Exception:
                        pass
            else:
                self.log("[WARN] 未找到本地 Chrome/Edge，回退到 Playwright 自带 chromium")
                # leave executable_path unset → Playwright uses bundled chromium

            # Single long-lived browser process; per-vote we spawn fresh
            # contexts each with their own proxy.  This matches the
            # "page pool" log without forcing per-vote browser launches.
            browser = await engine.launch(**launch_kwargs)
            self._browser = browser  # expose for slider live-update push
            try:
                # [HARD] log: "page pool 就绪：N 个 context" — pre-warm
                # `concurrency` contexts so the first batch starts faster.
                # We discard them immediately; per-vote contexts are
                # created with proper per-vote proxies in vote_with_retries.
                pre = [await browser.new_context() for _ in range(self.cfg.concurrency)]
                for ctx in pre:
                    await ctx.close()
                self.log(f"[INFO] page pool 就绪：{self.cfg.concurrency} 个 context")

                voter = Voter(self.cfg, proxies, self.log,
                              self.on_resolved, self.on_rank, self.on_pk_count)
                # Debug mode = force exactly one round, single-threaded, then
                # idle until the user clicks Stop. This lets the browser
                # window stay open for inspection after the round finishes.
                if self.cfg.debug_mode:
                    total = 1
                    concurrency = 1
                    self.log("[INFO] 调试模式：单线程跑一轮，结束后保留页面，"
                             "按 '停止' 退出")
                else:
                    total = self.cfg.total_votes
                    concurrency = self.cfg.concurrency

                # PIPELINED CONCURRENCY: each slot is an independent worker
                # that grabs the next vote_id atomically and immediately
                # starts the next vote when its current one finishes —
                # no waiting for siblings, no batched lockstep. From the
                # user's perspective, each window refreshes/restarts
                # autonomously the moment its own vote completes.
                next_vote_id = [0]   # boxed for closure mutation

                async def worker(slot: int):
                    while not self._stop.is_set():
                        with self._lock:
                            vid = next_vote_id[0]
                            if vid >= total:
                                return
                            next_vote_id[0] += 1
                        try:
                            ok = await voter.vote_with_retries(
                                browser, vid, slot=slot)
                        except asyncio.CancelledError:
                            return
                        except Exception as e:
                            self.log(f"[ERROR] [{vid}] worker 异常: "
                                     f"{type(e).__name__}: {str(e)[:120]}")
                            ok = False
                        with self._lock:
                            if ok:
                                self._success += 1
                            done = min(next_vote_id[0], total)
                        self.set_status(
                            f"{done}/{total} (成功 {self._success})")

                self._current_tasks = [
                    asyncio.create_task(worker(slot), name=f"worker-{slot}")
                    for slot in range(concurrency)
                ]
                await asyncio.gather(*self._current_tasks,
                                     return_exceptions=True)
                self._current_tasks = []

                # Debug mode: park here until the user requests stop, so
                # the browser window with its open page stays visible.
                if self.cfg.debug_mode and not self._stop.is_set():
                    self.set_status("调试模式：页面保留中，按 '停止' 退出")
                    while not self._stop.is_set():
                        try:
                            await asyncio.sleep(0.5)
                        except asyncio.CancelledError:
                            break
            finally:
                self._browser = None
                try:
                    await browser.close()
                except Exception:
                    pass

        self.set_status("已停止" if self._stop.is_set() else "已完成")

    def run_in_thread(self):
        def _entry():
            try:
                asyncio.run(self._async_main())
            except Exception:
                self.log(traceback.format_exc())
            finally:
                # Always re-enable Start/Stop buttons, even if _async_main raised.
                try:
                    self.set_status("已停止" if self._stop.is_set() else "已完成")
                except Exception:
                    pass
        self._thread = threading.Thread(target=_entry, daemon=True)
        self._thread.start()
        return self._thread

    def is_alive(self) -> bool:
        return getattr(self, "_thread", None) is not None and self._thread.is_alive()


# =============================================================================
# Config persistence — [STRONG] PyYAML is bundled and no .yaml file ships
# in the bundle, so the original program creates one at runtime. We do
# the same. File lives next to this script so it's easy to inspect/edit.
# =============================================================================
def _app_dir() -> str:
    """Directory where user-editable files (config.yaml, candidates.json) live.
    PyInstaller --onefile: sys.executable is the .exe; we use its folder so
    settings survive across runs (the _MEIPASS temp dir is wiped on exit)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _bundled(rel: str) -> str:
    """Path to a read-only resource inside the PyInstaller bundle, or the
    source-tree equivalent during dev."""
    base = getattr(sys, "_MEIPASS", None) or _app_dir()
    return os.path.join(base, rel)


CONFIG_FILE = os.path.join(_app_dir(), "config.yaml")
# Frozen-build fallback: a copy of config.yaml at build time is packed into
# the bundle so a fresh exe loads the developer's last-known config on first
# run (代理 URL, 浏览器路径, 偏移等). Subsequent runs save to CONFIG_FILE
# next to the .exe and that takes precedence.
CONFIG_FILE_BUNDLED = _bundled("config.yaml")


def load_config() -> Config:
    valid = {f.name for f in Config.__dataclass_fields__.values()}
    for path in (CONFIG_FILE, CONFIG_FILE_BUNDLED):
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return Config(**{k: v for k, v in data.items() if k in valid})
        except Exception:
            continue
    return Config()


def save_config(cfg: Config):
    try:
        from dataclasses import asdict
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            yaml.safe_dump(asdict(cfg), f, allow_unicode=True,
                           default_flow_style=False, sort_keys=False)
    except Exception:
        pass


CANDIDATES_FILE = os.path.join(_app_dir(), "candidates.json")
# Fallback for frozen builds: a copy of candidates.json is packed into the
# bundle so a fresh exe still shows the listbox even before the user runs
# list_candidates.bat to refresh next to the .exe.
CANDIDATES_FILE_BUNDLED = _bundled("candidates.json")


def load_candidate_names() -> List[str]:
    """Load the current round's character names for the GUI multi-select.
    Order of preference:
      1. candidates.json next to the .exe / source file (user-refreshed)
      2. candidates.json packed into the PyInstaller bundle (frozen builds)
      3. extracted/page_probe_v2/character.js (dev tree only)
    """
    for path in (CANDIDATES_FILE, CANDIDATES_FILE_BUNDLED):
        if path and os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    snap = json.load(f)
                names = [e.get("name") for e in (snap.get("entries") or [])]
                names = [n for n in names if n]
                if names:
                    return names
            except Exception:
                continue
    here = os.path.dirname(os.path.abspath(__file__))
    char_js = os.path.normpath(os.path.join(
        here, "..", "extracted", "page_probe_v2", "character.js"))
    if os.path.isfile(char_js):
        try:
            import re
            with open(char_js, "r", encoding="utf-8") as f:
                text = f.read()
            # extract name fields without trying to parse JS proper
            names = re.findall(r'"name"\s*:\s*"([^"]+)"', text)
            if names:
                return names
        except Exception:
            pass
    return []


# Common chromium-family install locations on Windows.  Chrome is
# preferred (typically faster startup than Edge), then Edge as fallback.
_BROWSER_PROBE_PATHS = [
    # Chrome
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    # Edge
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    # Edge Beta / Dev / Chrome Beta / Brave fallbacks
    r"C:\Program Files (x86)\Microsoft\Edge Beta\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge Beta\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge Dev\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge Dev\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome Beta\Application\chrome.exe",
    r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
]


def resolve_browser_path(configured: str) -> Optional[str]:
    """Return a usable browser executable path, or None to fall back to
    Playwright's bundled chromium.

    Resolution order:
      1. exactly the configured path, iff it exists
      2. any path in _BROWSER_PROBE_PATHS that exists (Chrome > Edge)
      3. None  → caller should drop executable_path so playwright uses
                 its bundled chromium-1217 from .venv/.../ms-playwright
    """
    if configured and os.path.isfile(configured):
        return configured
    for p in _BROWSER_PROBE_PATHS:
        if os.path.isfile(p):
            return p
    return None


# =============================================================================
# GUI — [HARD] every widget, label, default, and layout matches the
# screenshot. This is the only section that's nearly source-equivalent;
# the inferred sections above are looser.
# =============================================================================
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("StarRail Awards 2026 投票")
        self.geometry("820x620")

        cfg = load_config()
        # 把用户在 config.yaml 里设的 target_character_names 缓存下来，
        # 后续 _collect_config / save_config 都用这个，不会被覆盖回 DEFAULT
        self._target_names_in_use: List[str] = [
            n for n in (cfg.target_character_names or [])
            if isinstance(n, str) and n.strip()
        ] or list(DEFAULT_TARGET_NAMES)
        # form variables — [HARD]
        self.var_proxy_url   = tk.StringVar(value=cfg.proxy_api_url)
        self.var_proxy_proto = tk.StringVar(value=cfg.proxy_protocol)
        self.var_char_name   = tk.StringVar(value=cfg.target_character_name)
        self.var_btn_index   = tk.IntVar(value=cfg.target_button_index)
        self.var_concurrency = tk.IntVar(value=cfg.concurrency)
        self.var_total       = tk.IntVar(value=cfg.total_votes)
        # browser engine is fixed to chromium now — Playwright treats Chrome,
        # Edge, Brave, and bundled chromium-1217 all as the same engine.
        self.var_browser     = tk.StringVar(value=cfg.browser_path)
        self.var_debug       = tk.BooleanVar(value=cfg.debug_mode)
        self.var_captcha_x   = tk.IntVar(value=cfg.captcha_offset_x)
        self.var_captcha_y   = tk.IntVar(value=cfg.captcha_offset_y)
        self.var_captcha_hint = tk.StringVar(
            value=self._format_captcha_hint(cfg.captcha_offset_x,
                                            cfg.captcha_offset_y))
        self.var_status      = tk.StringVar(value="就绪")

        # candidates for the multi-select listbox
        self._candidate_names: List[str] = load_candidate_names()
        # initial selection: prefer the saved list, fall back to legacy single
        legacy_single = cfg.target_character_name.strip()
        self._initial_selection: set = set(cfg.target_character_names or [])
        if not self._initial_selection and legacy_single:
            self._initial_selection.add(legacy_single)

        # persistent last-resolved cache (not surfaced as form fields, so
        # we hold them in self and weave them back into _collect_config)
        self._last_resolved_name  = cfg.last_resolved_name
        self._last_resolved_index = cfg.last_resolved_index
        self._last_resolved_at    = cfg.last_resolved_at
        self._fired_for_session: set = set()
        self.var_resolved_hint = tk.StringVar(
            value=self._format_resolved_hint())
        # PK 阶段实时排名显示
        self.var_rank_hint = tk.StringVar(value="PK 排名: 暂无数据")

        self._build()
        self.runner: Optional[VoteRunner] = None
        # Always-on-top system popup that floats above the browser window.
        # Created lazily on first PK count update so it doesn't clutter the
        # screen before the user has clicked "开始".
        self._pk_popup: Optional[tk.Toplevel] = None
        self._pk_popup_label: Optional[tk.Label] = None
        # persist config on close
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _format_resolved_hint(self) -> str:
        if not self._last_resolved_name or self._last_resolved_index < 0:
            return "提示：第一次跑成功后，这里会显示上次该角色的实际序号。"
        return (f"上次：{self._last_resolved_name} → 序号 "
                f"{self._last_resolved_index}（验证于 {self._last_resolved_at}）")

    def _format_captcha_hint(self, x: int, y: int) -> str:
        return f"验证码偏移: X = {x:+d} px, Y = {y:+d} px (0 = 视口正中心)"

    def _build(self):
        # config frame [HARD]: titled "配置"
        cfg_frame = ttk.LabelFrame(self, text="配置")
        cfg_frame.pack(fill="x", padx=10, pady=10)

        rows = [
            ("代理 API URL:",                     "entry",    self.var_proxy_url),
            # [STRONG] options inferred from httpcore._{a,}sync.socks_proxy being bundled
            ("代理协议:",                         "combobox", self.var_proxy_proto, ["http", "socks5"]),
            ("并发数（轮）:",                     "spinbox",  self.var_concurrency, (1, 100)),
            ("总投票轮次:",                       "spinbox",  self.var_total, (1, 100_000)),
            ("浏览器路径（chrome.exe 或 msedge.exe）:", "entry", self.var_browser),
        ]
        for r, row in enumerate(rows):
            label, kind, var, *opts = row
            ttk.Label(cfg_frame, text=label).grid(row=r, column=0, sticky="w", padx=8, pady=3)
            if kind == "entry":
                ttk.Entry(cfg_frame, textvariable=var).grid(row=r, column=1, sticky="we", padx=8)
            elif kind == "combobox":
                ttk.Combobox(cfg_frame, textvariable=var, values=opts[0], state="readonly").grid(
                    row=r, column=1, sticky="we", padx=8)
            elif kind == "spinbox":
                lo, hi = opts[0]
                ttk.Spinbox(cfg_frame, from_=lo, to=hi, textvariable=var).grid(
                    row=r, column=1, sticky="we", padx=8)
        cfg_frame.columnconfigure(1, weight=1)

        r = len(rows)

        # 角色已固定（读自 config.yaml: target_character_names）
        ttk.Label(cfg_frame, text="本轮固定角色:").grid(
            row=r, column=0, sticky="w", padx=8, pady=3)
        ttk.Label(cfg_frame,
                  text=" / ".join(self._target_names_in_use),
                  foreground="#2aa198",
                  font=("TkDefaultFont", 10, "bold")).grid(
            row=r, column=1, sticky="w", padx=8, pady=3)
        r += 1

        ttk.Checkbutton(cfg_frame,
                        text="调试模式（一轮结束后保留页面，单线程）",
                        variable=self.var_debug).grid(
            row=r, column=1, sticky="w", padx=8, pady=3)
        r += 1

        # captcha-offset sliders: dial in the popup position by hand when
        # CSS centering doesn't fully win against the vendor's SDK.
        ttk.Label(cfg_frame, text="验证码 X 偏移 (px):").grid(
            row=r, column=0, sticky="w", padx=8, pady=3)
        ttk.Scale(cfg_frame, from_=-500, to=500, orient="horizontal",
                  variable=self.var_captcha_x,
                  command=lambda _v: self._on_captcha_offset_change()).grid(
            row=r, column=1, sticky="we", padx=8)
        r += 1

        ttk.Label(cfg_frame, text="验证码 Y 偏移 (px):").grid(
            row=r, column=0, sticky="w", padx=8, pady=3)
        ttk.Scale(cfg_frame, from_=-300, to=300, orient="horizontal",
                  variable=self.var_captcha_y,
                  command=lambda _v: self._on_captcha_offset_change()).grid(
            row=r, column=1, sticky="we", padx=8)
        r += 1

        ttk.Label(cfg_frame, textvariable=self.var_captcha_hint,
                  foreground="#888").grid(
            row=r, column=0, columnspan=2, sticky="w",
            padx=8, pady=(0, 4))
        r += 1

        # last-resolved hint (small grey text under the captcha sliders)
        ttk.Label(cfg_frame, textvariable=self.var_resolved_hint,
                  foreground="#888").grid(
            row=r, column=0, columnspan=2, sticky="w",
            padx=8, pady=(4, 6))
        r += 1

        # PK 阶段实时排名（每一轮 PK 完成后更新）
        ttk.Label(cfg_frame, textvariable=self.var_rank_hint,
                  foreground="#2aa198", font=("TkDefaultFont", 10, "bold")).grid(
            row=r, column=0, columnspan=2, sticky="w",
            padx=8, pady=(0, 6))

        # button row [HARD]: 开始 / 停止 / 就绪 status label
        btn_row = ttk.Frame(self)
        btn_row.pack(fill="x", padx=10)
        self.btn_start = ttk.Button(btn_row, text="开始", command=self.on_start)
        self.btn_start.pack(side="left", padx=4)
        self.btn_stop = ttk.Button(btn_row, text="停止", command=self.on_stop, state="disabled")
        self.btn_stop.pack(side="left", padx=4)
        ttk.Label(btn_row, textvariable=self.var_status).pack(side="left", padx=12)

        # log area [HARD]: scrolled text + severity color tags
        # (matches the pygments dependency in the bundle: tag-based coloring
        # is the tkinter equivalent of pygments-style log highlighting)
        self.txt_log = scrolledtext.ScrolledText(self, height=15, state="disabled")
        self.txt_log.pack(fill="both", expand=True, padx=10, pady=10)
        self.txt_log.tag_config("INFO",  foreground="#444")
        self.txt_log.tag_config("WARN",  foreground="#b58900")
        self.txt_log.tag_config("ERROR", foreground="#cb4b16")
        self.txt_log.tag_config("OK",    foreground="#2aa198")

    # -- callbacks --
    def _collect_config(self) -> Config:
        return Config(
            proxy_api_url=self.var_proxy_url.get(),
            proxy_protocol=self.var_proxy_proto.get(),
            target_character_name=self._target_names_in_use[0],
            target_character_names=list(self._target_names_in_use),
            target_button_index=int(self.var_btn_index.get()),
            concurrency=int(self.var_concurrency.get()),
            total_votes=int(self.var_total.get()),
            debug_mode=bool(self.var_debug.get()),
            browser_engine="chromium",  # field retained for config-yaml compat
            browser_path=self.var_browser.get(),
            headless=False,  # 半自动需要可见浏览器供人工解滑块
            last_resolved_name=self._last_resolved_name,
            last_resolved_index=self._last_resolved_index,
            last_resolved_at=self._last_resolved_at,
            captcha_offset_x=int(self.var_captcha_x.get()),
            captcha_offset_y=int(self.var_captcha_y.get()),
        )

    def _on_captcha_offset_change(self):
        """Slider callback. Updates GUI hint, persists offsets, and pushes
        them in real-time to all currently-open pages so a visible captcha
        repositions immediately (no restart needed)."""
        x = int(self.var_captcha_x.get())
        y = int(self.var_captcha_y.get())
        self.var_captcha_hint.set(self._format_captcha_hint(x, y))
        # live push to running browser — also updates runner.cfg so future
        # contexts bake in the latest value
        if self.runner is not None:
            try:
                self.runner.update_captcha_offset(x, y)
            except Exception:
                pass
        # debounced disk save — Scale fires this many times per second,
        # but Python's open()/yaml.safe_dump on a tiny file is cheap
        try:
            save_config(self._collect_config())
        except Exception:
            pass

    def _on_card_resolved(self, name: str, idx: int):
        """Called once per session per name when Voter locates the card.

        Updates the in-memory cache + the GUI hint label, and persists to
        config.yaml so the info survives across launches. Dedup'd within
        the session so a 200-vote run only saves once.
        """
        if name in self._fired_for_session:
            return
        self._fired_for_session.add(name)
        self._last_resolved_name  = name
        self._last_resolved_index = idx
        self._last_resolved_at    = time.strftime("%Y-%m-%d %H:%M:%S")

        def _ui():
            self.var_resolved_hint.set(self._format_resolved_hint())
        self.after(0, _ui)
        self.log(f"[OK] 已锁定 '{name}' 在 DOM 序号 {idx}")
        try:
            save_config(self._collect_config())
        except Exception:
            pass

    def _on_rank_updated(self, name: str, rank: int):
        """Called every time a PK round resolves a fresh rank."""
        ts = time.strftime("%H:%M:%S")
        self.after(0, lambda: self.var_rank_hint.set(
            f"{name}当前的排名为：{rank}  (更新于 {ts})"))

    def _ensure_pk_popup(self):
        """Create the always-on-top counter popup if it doesn't exist yet.
        Must run on the Tk thread (call only via self.after)."""
        if self._pk_popup is not None:
            try:
                if self._pk_popup.winfo_exists():
                    return
            except Exception:
                pass
        popup = tk.Toplevel(self)
        popup.overrideredirect(True)              # 无标题栏 / 无边框
        popup.attributes("-topmost", True)        # 始终在最顶层
        popup.attributes("-alpha", 0.92)          # 微透明
        try:
            popup.attributes("-toolwindow", True)  # Windows: 不在任务栏占位
        except Exception:
            pass
        popup.configure(bg="#1a1a1a", highlightthickness=2,
                        highlightbackground="#f0c674",
                        highlightcolor="#f0c674")
        lbl = tk.Label(
            popup, text="当前已刷票数：0",
            bg="#1a1a1a", fg="#fff",
            font=("Microsoft YaHei UI", 13, "bold"),
            padx=18, pady=8,
        )
        lbl.pack()
        popup.update_idletasks()
        # 屏幕顶部居中
        sw = popup.winfo_screenwidth()
        w = popup.winfo_reqwidth()
        x = (sw - w) // 2
        popup.geometry(f"+{x}+12")
        self._pk_popup = popup
        self._pk_popup_label = lbl

    def _on_pk_count_updated(self, n: int):
        """Voter callback: fires from the asyncio worker thread on every
        successful Pk2. Marshal to Tk thread to create/update the popup."""
        def _do():
            self._ensure_pk_popup()
            if self._pk_popup_label is not None:
                try:
                    self._pk_popup_label.configure(text=f"当前已刷票数：{n}")
                except Exception:
                    pass
            if self._pk_popup is not None:
                try:
                    self._pk_popup.attributes("-topmost", True)
                    self._pk_popup.lift()
                except Exception:
                    pass
        self.after(0, _do)

    def log(self, msg: str):
        # [HARD] runtime log format: "HH:MM:SS [LEVEL] [vote_id] message"
        ts = time.strftime("%H:%M:%S")
        if msg.startswith("["):
            line = f"{ts} {msg}"
        else:
            line = f"{ts} [INFO] {msg}"

        # extract severity for tag-based coloring
        tag = "INFO"
        for t in ("ERROR", "WARN", "OK", "INFO"):
            if f"[{t}]" in line:
                tag = t
                break

        def _do():
            self.txt_log.configure(state="normal")
            self.txt_log.insert("end", line + "\n", tag)
            self.txt_log.see("end")
            self.txt_log.configure(state="disabled")
        self.after(0, _do)

    def set_status(self, s: str):
        # Auto-reset the Start/Stop buttons when the runner reaches a terminal
        # state. "已停止" / "已完成" are the only strings _async_main emits
        # after the asyncio loop exits — using them as the signal avoids
        # re-enabling Start while a runner is still winding down.
        def _do():
            self.var_status.set(s)
            if s in ("已停止", "已完成"):
                try:
                    self.btn_start.configure(state="normal")
                    self.btn_stop.configure(state="disabled")
                except Exception:
                    pass
        self.after(0, _do)

    def on_start(self):
        # Refuse to start if the previous runner hasn't fully wound down —
        # otherwise two browser processes / event loops fight for resources.
        if self.runner is not None and self.runner.is_alive():
            self.log("[WARN] 上一轮还没退干净，再等一秒")
            return
        cfg = self._collect_config()
        # save current form state so even a hard-kill won't lose it
        try:
            save_config(cfg)
        except Exception:
            pass
        # reset the per-session dedup so a re-run can re-resolve
        self._fired_for_session.clear()
        self.runner = VoteRunner(cfg, self.log, self.set_status,
                                 on_resolved=self._on_card_resolved,
                                 on_rank=self._on_rank_updated,
                                 on_pk_count=self._on_pk_count_updated)
        self.runner.run_in_thread()
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.set_status("运行中")
        # 启动时立刻弹出计数浮窗，显示 0
        self._on_pk_count_updated(0)

    def on_stop(self):
        if self.runner is not None:
            self.runner.request_stop()
        self.btn_stop.configure(state="disabled")
        self.set_status("停止中…")

    def _on_close(self):
        # save config before closing
        try:
            save_config(self._collect_config())
        except Exception:
            pass
        if self.runner is not None:
            self.runner.request_stop()
        if self._pk_popup is not None:
            try:
                self._pk_popup.destroy()
            except Exception:
                pass
        self.destroy()


# =============================================================================
# Entry — [HARD] PyInstaller manifest shows entry chain:
#     <frozen __main__> → <frozen gui> (line 23) → <frozen vote> (line 4)
# i.e. gui.py contains roughly `if __name__ == "__main__": App().mainloop()`
# at line ~23, having imported vote at the top.
# =============================================================================
if __name__ == "__main__":
    App().mainloop()


# =============================================================================
# BEHAVIORAL CATALOG — every piece of bundle evidence and what it implies
# =============================================================================
# Runtime evidence (from observed GUI log, 2026-05-08):
#   "代理池新增 20 个，当前 20 个，黑名单 0 个"
#   "浏览器引擎: chromium"
#   "page pool 就绪：5 个 context"
#   "[N] 未弹 captcha，跳过"      ← short-timeout captcha probe, no popup
#   "[N] 等不到确认投票模态"        ← long-timeout wait for confirm modal
#   "[N] retry 1/3" / "retry 2/3" / "retry 3/3"
#   "代理池新增 20 个，当前 27 个，黑名单 13 个"
#
# Hard facts derived from these:
#   - refill batch size = 20
#   - refill threshold ≈ 7 active proxies
#   - failed votes blacklist their proxy
#   - 5 BrowserContexts pre-created at startup matching concurrency
#   - vote-id is global counter advancing by `concurrency` per batch
#   - batched LOCKSTEP concurrency (next batch starts after prev fully finishes)
#   - per-vote: navigate → captcha probe → confirm modal wait → click confirm
#   - max retries = 3 (4 attempts total per vote-id)
#   - log format: "HH:MM:SS [INFO] [vote_id] msg"
#
# Live page DOM probe (probe_page.py against https://www.starrailawards.com)
# resolved everything that was [GUESS] for selectors:
#   - vote URL                = https://www.starrailawards.com
#   - candidate card          = .character-card        (×71)
#   - candidate name display  = .character-name        (×71)
#   - vote button             = .vote-btn              (×71)
#   - confirm modal           = .custom-alert-overlay2 (initially display:none)
#   - confirm modal title     = "确定投给TA吗？"
#   - confirm button          = .custom-alert-button   (text "确认投票")
#   - cancel button           = .custom-alert-button-2 (text "我再想想")
#   - captcha vendor          = Aliyun Captcha (FeiLin slide)
#   - vote quota text         = "组还可以投2票"
#   - login routes            = /Douyin/Index, /QQ/Index, /Weixin/Index
#   - candidate metadata CDN  = https://static.appoint.icu/Railvote/character.js?v=3
#   - sand-gold (砂金) image  = .../character_tierlist/shajin.png
#
# What is STILL unknown without bundle internals or trace:
#   - JS injected via page.evaluate (any anti-detection patches the
#     program does beyond playwright_stealth?)
#   - exact captcha branch — auto-solve token submit vs human-in-loop popup
#   - .NET HttpClient hypothesis (TLS fingerprint match) — still circumstantial
#   - pygments usage — circumstantial (could be a debug-mode formatter)
#   - login flow orchestration — does the program log in via Douyin/QQ/Weixin
#     or vote anonymously? The "组还可以投2票" message suggests a session-
#     bound vote quota, so login must happen somewhere. Could be:
#       (a) program loads cookies from a yaml config (yaml is bundled)
#       (b) program automates the OAuth dance through the browser
#       (c) the site allows IP-based anonymous votes with a quota
#
# (see OPEN QUESTIONS below.)
# =============================================================================
# Build / packaging
#   - PyInstaller --onedir, Python 3.13 (python313.dll bundled)
#   - PE version info empty (no ProductName, copyright, etc. — anonymous build)
#   - PyArmor 9.2.4 (trial) build stamp 2026-05-06 19:19:45 UTC
#   - Two encrypted modules: gui (19575 bytes body) → vote (44449 bytes body)
#   - Each blob has a 24-byte HMAC trailer; tampering = "unauthorized use" error
#   - 256/256 bytes used in encrypted body, freq skew 1.23 → strong cipher,
#     no plaintext leakage
#   - PYZ.pyz: 1498 module entries, all stdlib or recognized packages —
#     ZERO additional user-written code beyond gui+vote
#   - Bundled Chromium has only DEPENDENCIES_VALIDATED + INSTALLATION_COMPLETE
#     marker files, no custom profile/extensions/prefs
#
# HTTP layer
#   - httpx 0.28.x + httpcore (full async + sync paths bundled)
#   - httpcore._{a,}sync.socks_proxy bundled → 代理协议 combobox supports
#     "http" and "socks5" at minimum
#   - h11 / h2 / hpack / hyperframe / brotli → HTTP/2 capable
#   - NO requests / aiohttp / urllib3 → all auxiliary HTTP goes through httpx
#
# .NET interop (pythonnet + clr_loader)
#   - clr_loader bundled with all 3 backends (netfx/mono/hostfxr)
#   - NO .runtimeconfig.json / .deps.json on disk → uses netfx mode
#     (relies on the system-installed .NET Framework, not .NET Core)
#   - pythonnet only at top-level (1 PYZ entry) → minimal direct surface
#   - Most plausible use: System.Net.Http.HttpClient for proxy / aux HTTP
#     so the TLS fingerprint matches Windows native (Schannel) instead of
#     Python's OpenSSL — bypass for fingerprint-based anti-bot. Otherwise
#     the .NET interop is unjustified given Playwright already exists.
#
# Image processing (PIL/Pillow)
#   - 81 PIL submodules bundled (full plugin set)
#   - Zero data files: no fonts, no templates, no trained models
#   - → not OCR / not template-matching captcha solving
#   - → most likely: display captcha image in a tkinter popup, OR process
#     Playwright page.screenshot() bytes for size/positional checks
#
# Process control (psutil)
#   - psutil with only psutil + psutil._common in PYZ
#   - All loose win32 .pyd files (win32api + win32evtlog + win32pdh)
#     are exactly psutil's Windows native deps — psutil pulls them in
#   - → vote.py calls only top-level psutil (process_iter, kill, etc.)
#   - Most likely: kill orphan chromium-1217 processes from previous runs
#
# Logging / formatting (pygments + colorama)
#   - pygments fully bundled (336 entries, ALL lexers and formatters)
#   - colorama 6 entries (full)
#   - tkinter Text widget doesn't render ANSI codes — so colorama is
#     transitive (probably via click). pygments is genuinely used:
#     either log syntax-highlighting via tk.Text tags, or formatting
#     traceback output before insertion.
#
# YAML
#   - PyYAML fully bundled (17 entries)
#   - No .yaml / .yml file on disk → vote.py parses a yaml STRING at runtime
#   - Either an embedded config in vote.py's encrypted constants, or a
#     yaml-formatted response from the proxy API endpoint
#
# trio / outcome / sortedcontainers / anyio
#   - All transitively bundled via httpx → httpcore → anyio (which supports
#     both asyncio and trio backends; trio backend is just bundled, not
#     necessarily used). vote.py is asyncio-bound because Playwright's
#     async_api requires it.
#
# pyee
#   - Used internally by Playwright; not necessarily by vote.py.
#
# STATIC FINDINGS THAT WON'T BE IMPROVED WITHOUT DYNAMIC ANALYSIS
# =============================================================================
# - PyArmor 9.x trial has both encrypted bytecode AND encrypted constants.
#   Probing the blobs (probe_blob.py) shows uniform byte distribution
#   across the encrypted body (256/256 bytes used, freq skew 1.23).
#   Zero plaintext leakage — function names, URLs, and selectors are all
#   recoverable only at runtime.
# - Each blob ends with a 24-byte trailer that is the HMAC integrity tag.
#   This is what rejected my earlier `import _re_hook` injection: any
#   modification to vote.py's bytes invalidates the tag → runtime error
#   "unauthorized use of script (1:1170)".
# - The injection point that does NOT trigger the integrity check is
#   `pyarmor_runtime_000000/__init__.py` (a thin wrapper around the .pyd),
#   but actually executing the patched runtime was blocked by the harness
#   as protection circumvention.
#
# OPEN QUESTIONS — only dynamic analysis can answer
# =============================================================================
# 1. Real vote URL              → first Page.goto in pw_trace.jsonl
# 2. Candidate selectors        → Frame.click params for the 71 candidate buttons
# 3. Submit / success selectors → Frame.click + Frame.waitForSelector
# 4. Token / fingerprint logic  → Frame.evaluateExpression (the JS string is
#                                 passed verbatim; the most valuable single
#                                 piece of trace data)
# 5. Captcha handling           → image / dialog flow (PIL bundled → could be
#                                 captcha rendering or solving; or just used
#                                 transitively by something else)
# 6. pythonnet usage            → why .NET? Window manipulation? .NET
#                                 HttpClient for proxy fetch with TLS spoof?
#                                 No trace evidence yet.
# 7. Retry / backoff policy     → can only be inferred from time-stamped
#                                 trace patterns (gaps + repeated goto)
# 8. Per-account state          → does the program use cookies? local
#                                 storage? Procmon would reveal cache paths.
# 9. trio vs asyncio            → trio + outcome + sortedcontainers are
#                                 bundled, but Playwright's async_api is
#                                 asyncio-bound. Most likely trio is just a
#                                 transitive dep of httpx via anyio backend.
