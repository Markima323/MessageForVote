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
from dataclasses import dataclass
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
    target_character_name: str = ""             # GUI: 目标角色名 (如 白厄)
    target_button_index: int = 0                # GUI: 或 按钮序号 (0-70)
    concurrency: int = 6                        # GUI: 并发数 (3×2 平铺时正好)
    total_votes: int = 200                      # GUI: 总投票次数
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
# How long to wait after clicking .vote-btn for EITHER the captcha or the
# confirm modal to appear. Observed range: 6-20 s typical, hence a 3-min
# margin handles the worst proxies without leaving very-broken proxies
# blocking for too long.
CAPTCHA_APPEAR_TIMEOUT_MS = 180_000   # 3 minutes
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
                 on_resolved: Optional[Callable[[str, int], None]] = None):
        self.cfg = cfg
        self.proxies = proxies
        self.log = log
        # callback fires once per successful card locate; receiver decides
        # how to dedupe / persist
        self.on_resolved = on_resolved or (lambda _name, _idx: None)

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

    async def _new_context(self, browser: Browser, proxy: dict) -> BrowserContext:
        # [STRONG] proxy is per-context (Playwright supports this since 1.29);
        # this is the only way the "page pool" can rotate IPs across votes.
        ctx = await browser.new_context(proxy={"server": proxy["server"]})
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
        """One vote attempt. Returns True iff the confirm modal closed
        successfully (which the page treats as a successful vote).

        Race-based wait: after clicking .vote-btn, EITHER the Aliyun
        captcha pops up OR the confirm modal appears directly (when the
        site's risk model deems the request low-risk). The previous
        version's serial probe could miss the modal if captcha probed
        slow + modal opened after the probe timeout. This races the two.
        """
        page = await ctx.new_page()
        # Tile the window into a slot so multiple concurrent attempts are
        # visible side-by-side (3 cols × 2 rows for concurrency=6).
        # window.moveTo / window.resizeTo are blocked by Chrome on regular
        # windows, so we use CDP (Browser.setWindowBounds) instead — that
        # is the browser-level API and isn't subject to JS sandbox.
        if not self.cfg.headless:
            await self._tile_window_cdp(ctx, page, slot, vote_id)
        try:
            await page.goto(self.VOTE_PAGE_URL, wait_until="domcontentloaded",
                            timeout=NAVIGATE_TIMEOUT_MS)
            card = await self._locate_card(page)
            await card.scroll_into_view_if_needed()
            await card.locator(self.VOTE_BUTTON_SELECTOR).click()

            # ---- race captcha vs confirm modal ----
            # Use the longer CAPTCHA_APPEAR_TIMEOUT_MS here — through slow
            # proxies, the Aliyun SDK can take 30-60 s to boot and render
            # the slider. Aborting early kills the user's manual solve.
            captcha_task = asyncio.create_task(
                page.locator(self.CAPTCHA_MODAL_SELECTOR).first.wait_for(
                    state="visible", timeout=CAPTCHA_APPEAR_TIMEOUT_MS),
                name="captcha")
            modal_task = asyncio.create_task(
                page.locator(self.CONFIRM_MODAL_SELECTOR).filter(
                    has_text=self.CONFIRM_TITLE_TEXT
                ).first.wait_for(state="visible",
                                 timeout=CAPTCHA_APPEAR_TIMEOUT_MS),
                name="modal")
            self.log(f"[INFO] [{vote_id}] 等待 captcha 或确认模态出现 "
                     f"(最多 {CAPTCHA_APPEAR_TIMEOUT_MS // 1000} 秒)")
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
                self.log(f"[INFO] [{vote_id}] 等不到确认投票模态")
                return False

            if winner.get_name() == "captcha":
                self.log(f"[INFO] [{vote_id}] 弹出 captcha，进入处理")
                try:
                    await self._handle_captcha(page)
                except NotImplementedError as e:
                    self.log(f"[WARN] [{vote_id}] captcha 处理未实现: {e}")
                    return False
                # after captcha solved, wait for the confirm modal
                try:
                    await page.locator(self.CONFIRM_MODAL_SELECTOR).filter(
                        has_text=self.CONFIRM_TITLE_TEXT
                    ).first.wait_for(state="visible",
                                     timeout=CONFIRM_MODAL_TIMEOUT_MS)
                except Exception:
                    self.log(f"[INFO] [{vote_id}] 等不到确认投票模态")
                    return False
            else:
                # confirm modal won the race — no captcha this round
                self.log(f"[INFO] [{vote_id}] 未弹 captcha，跳过")

            # ---- click 确认投票 ----
            # First, make sure the captcha mask animation has fully cleared
            # — otherwise its still-fading overlay swallows the click on the
            # confirm button below it (Playwright's actionability check then
            # times out and retry kicks in).
            try:
                await page.locator(".mask-show").first.wait_for(
                    state="hidden", timeout=3_000)
            except Exception:
                pass

            self.log(f"[INFO] [{vote_id}] 准备点击 '确认投票'")
            click_target = (
                page.locator(self.CONFIRM_BUTTON_SELECTOR)
                .filter(has_text="确认投票")
                .first
            )
            clicked = False
            # try a normal click first; if Playwright's actionability check
            # times out (e.g. element animating, transient mask), retry with
            # force=True which skips visibility/stability gates.
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
                    self.log(f"[WARN] [{vote_id}] 点击失败 ({label}): {type(e).__name__}")
            if not clicked:
                return False

            # ---- success modal: "成功投票给X，剩余票数 N" ----
            # Use a broad text-based locator: any visible element whose text
            # contains "成功投票" or "剩余票数". Class-based locators were
            # too narrow and may miss the popup if the page changes class
            # names between rounds.
            success_modal = page.get_by_text("成功投票").first
            self.log(f"[INFO] [{vote_id}] 等待成功提示出现 "
                     f"(最多 {SUCCESS_MODAL_TIMEOUT_MS // 1000} 秒, 调试期)")
            try:
                await success_modal.wait_for(state="visible",
                                             timeout=SUCCESS_MODAL_TIMEOUT_MS)
            except Exception as e:
                # Surface the ACTUAL error so we can tell timeout vs page-
                # detached vs network-closed apart, and dump a snapshot of
                # what was actually on screen at failure time.
                err_type = type(e).__name__
                err_msg = str(e).splitlines()[0][:160] if str(e) else ""
                self.log(f"[WARN] [{vote_id}] 等成功提示失败: {err_type}: {err_msg}")
                await self._save_debug_snapshot(page, vote_id)
                return False

            # Log the actual server-returned text — usually contains 剩余票数,
            # which is useful to know whether the session still has quota.
            try:
                text = (await success_modal.inner_text()).strip().replace("\n", " ")
                self.log(f"[OK] [{vote_id}] {text[:120]}")
            except Exception:
                self.log(f"[OK] [{vote_id}] 投票成功")

            # Click 确定 to dismiss the success modal
            ok_btn = page.locator(
                ".custom-alert-button", has_text="确定"
            ).first
            for label, fn in [
                ("normal", lambda: ok_btn.click(timeout=5_000)),
                ("force",  lambda: ok_btn.click(timeout=5_000, force=True)),
                ("text-fallback",
                 lambda: page.get_by_text("确定", exact=True).first.click(
                     timeout=5_000, force=True)),
            ]:
                try:
                    await fn()
                    self.log(f"[INFO] [{vote_id}] 已关闭成功提示 ({label})")
                    break
                except Exception as e:
                    self.log(f"[WARN] [{vote_id}] 关闭成功提示失败 ({label}): "
                             f"{type(e).__name__}")
            return True
        finally:
            try:
                await page.close()
            except Exception:
                pass

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
            try:
                ok = await self._attempt(ctx, vote_id, slot=slot)
                if ok:
                    return True
                # [HARD] failed proxy gets blacklisted (observed pool drop)
                self.proxies.blacklist(ip_port)
            except Exception as e:
                self.log(f"[ERROR] [{vote_id}] {e!r}")
                self.proxies.blacklist(ip_port)
            finally:
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
                 on_resolved: Optional[Callable[[str, int], None]] = None):
        self.cfg = cfg
        self.log = log
        self.set_status = status
        self.on_resolved = on_resolved
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
            launch_kwargs = dict(headless=self.cfg.headless)
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

                voter = Voter(self.cfg, proxies, self.log, self.on_resolved)
                total = self.cfg.total_votes
                concurrency = self.cfg.concurrency

                # [HARD] BATCHED LOCKSTEP: submit `concurrency` votes,
                # await ALL of them, then advance by `concurrency`.
                vote_id = 0
                while vote_id < total and not self._stop.is_set():
                    batch_end = min(vote_id + concurrency, total)
                    # slot index inside this batch → tile position on screen
                    self._current_tasks = [
                        asyncio.create_task(
                            voter.vote_with_retries(browser, vid, slot=slot),
                            name=f"vote-{vid}")
                        for slot, vid in enumerate(range(vote_id, batch_end))
                    ]
                    results = await asyncio.gather(
                        *self._current_tasks, return_exceptions=True)
                    self._current_tasks = []
                    with self._lock:
                        for r in results:
                            if r is True:
                                self._success += 1
                    self.set_status(
                        f"{batch_end}/{total} (成功 {self._success})")
                    vote_id = batch_end
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
        t = threading.Thread(target=_entry, daemon=True)
        t.start()
        return t


# =============================================================================
# Config persistence — [STRONG] PyYAML is bundled and no .yaml file ships
# in the bundle, so the original program creates one at runtime. We do
# the same. File lives next to this script so it's easy to inspect/edit.
# =============================================================================
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "config.yaml")


def load_config() -> Config:
    if not os.path.isfile(CONFIG_FILE):
        return Config()
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        # only accept fields that exist on Config (defensive)
        valid = {f.name for f in Config.__dataclass_fields__.values()}
        return Config(**{k: v for k, v in data.items() if k in valid})
    except Exception:
        return Config()


def save_config(cfg: Config):
    try:
        from dataclasses import asdict
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            yaml.safe_dump(asdict(cfg), f, allow_unicode=True,
                           default_flow_style=False, sort_keys=False)
    except Exception:
        pass


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
        self.var_headless    = tk.BooleanVar(value=cfg.headless)
        self.var_captcha_x   = tk.IntVar(value=cfg.captcha_offset_x)
        self.var_captcha_y   = tk.IntVar(value=cfg.captcha_offset_y)
        self.var_captcha_hint = tk.StringVar(
            value=self._format_captcha_hint(cfg.captcha_offset_x,
                                            cfg.captcha_offset_y))
        self.var_status      = tk.StringVar(value="就绪")

        # persistent last-resolved cache (not surfaced as form fields, so
        # we hold them in self and weave them back into _collect_config)
        self._last_resolved_name  = cfg.last_resolved_name
        self._last_resolved_index = cfg.last_resolved_index
        self._last_resolved_at    = cfg.last_resolved_at
        self._fired_for_session: set = set()
        self.var_resolved_hint = tk.StringVar(
            value=self._format_resolved_hint())

        self._build()
        self.runner: Optional[VoteRunner] = None
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
            ("目标角色名 (如 白厄):",              "entry",    self.var_char_name),
            ("或 按钮序号 (0-70, 留空名字时):",    "spinbox",  self.var_btn_index, (0, 70)),
            ("并发数:",                           "spinbox",  self.var_concurrency, (1, 100)),
            ("总投票次数:",                       "spinbox",  self.var_total, (1, 100_000)),
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

        ttk.Checkbutton(cfg_frame, text="无头模式（生产推荐）",
                        variable=self.var_headless).grid(
            row=len(rows), column=1, sticky="w", padx=8, pady=3)

        # captcha-offset sliders: dial in the popup position by hand when
        # CSS centering doesn't fully win against the vendor's SDK.
        ttk.Label(cfg_frame, text="验证码 X 偏移 (px):").grid(
            row=len(rows) + 1, column=0, sticky="w", padx=8, pady=3)
        ttk.Scale(cfg_frame, from_=-500, to=500, orient="horizontal",
                  variable=self.var_captcha_x,
                  command=lambda _v: self._on_captcha_offset_change()).grid(
            row=len(rows) + 1, column=1, sticky="we", padx=8)

        ttk.Label(cfg_frame, text="验证码 Y 偏移 (px):").grid(
            row=len(rows) + 2, column=0, sticky="w", padx=8, pady=3)
        ttk.Scale(cfg_frame, from_=-300, to=300, orient="horizontal",
                  variable=self.var_captcha_y,
                  command=lambda _v: self._on_captcha_offset_change()).grid(
            row=len(rows) + 2, column=1, sticky="we", padx=8)

        ttk.Label(cfg_frame, textvariable=self.var_captcha_hint,
                  foreground="#888").grid(
            row=len(rows) + 3, column=0, columnspan=2, sticky="w",
            padx=8, pady=(0, 4))

        # last-resolved hint (small grey text under the captcha sliders)
        ttk.Label(cfg_frame, textvariable=self.var_resolved_hint,
                  foreground="#888").grid(
            row=len(rows) + 4, column=0, columnspan=2, sticky="w",
            padx=8, pady=(4, 6))

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
            target_character_name=self.var_char_name.get(),
            target_button_index=int(self.var_btn_index.get()),
            concurrency=int(self.var_concurrency.get()),
            total_votes=int(self.var_total.get()),
            browser_engine="chromium",  # field retained for config-yaml compat
            browser_path=self.var_browser.get(),
            headless=bool(self.var_headless.get()),
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
        self.after(0, lambda: self.var_status.set(s))

    def on_start(self):
        cfg = self._collect_config()
        # save current form state so even a hard-kill won't lose it
        try:
            save_config(cfg)
        except Exception:
            pass
        # reset the per-session dedup so a re-run can re-resolve
        self._fired_for_session.clear()
        self.runner = VoteRunner(cfg, self.log, self.set_status,
                                 on_resolved=self._on_card_resolved)
        self.runner.run_in_thread()
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.set_status("运行中")

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
