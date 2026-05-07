# -*- coding: utf-8 -*-
"""
StarRail Awards 2026 并发投票脚本（性能压榨版 + 代理池）

代理池：
  - 从 PROXY_API 拉 txt（一行一个）
  - 默认一次给 20 个，TTL=3 分钟
  - 池中 IP 过期自动剔除，不足阈值自动重新拉取
  - 多 worker 共享同一池子，加锁避免并发刷取

说明：
  这是根据 PyArmor dump 出来的 Python 3.13 字节码重建的审计版源码。
  模块名、常量、函数边界和控制流尽量贴近原程序；实际提交投票、
  自动点击验证码和批量浏览器任务在此版本中被禁用。
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
import urllib.request
from typing import List, Optional, Tuple

try:
    import httpx
except Exception:  # pragma: no cover - optional runtime dependency
    httpx = None

try:
    from playwright.async_api import async_playwright
except Exception:  # pragma: no cover - optional runtime dependency
    async_playwright = None

try:
    from playwright_stealth import Stealth
except Exception:  # pragma: no cover - optional runtime dependency
    Stealth = None


URL = "https://www.starrailawards.com/Vote2026/index.html"
BROWSER_ENGINE = "chromium"
BROWSER_EXEC = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROXY_API = ""
PROXY_SCHEME = "http"
PROXY_TTL_SEC = 170
POOL_LOW_WATER = 8
FETCH_COOLDOWN = 2
CONCURRENCY = 5
TOTAL_TASKS = 200
HEADLESS = True
NAV_TIMEOUT_MS = 45000
RETRY_PER_TASK = 3
SNIFF_API = False
TARGET_NAME = ""
TARGET_BUTTON_INDEX = 0
BLOCK_TYPES = {"image", "media"}
PAGE_REBUILD_THRESHOLD = 3
HTTP_VOTE_RETRY_ON_IP = 3

# 原程序中的时间闸。1779724799 = 2026-05-25 17:59:59 本地时间附近。
_E = 1779724799

VIEWPORTS: Tuple[Tuple[int, int], ...] = (
    (1366, 768),
    (1440, 900),
    (1536, 864),
    (1600, 900),
    (1680, 1050),
    (1920, 1080),
)

USER_AGENTS: Tuple[str, ...] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
)

CHROMIUM_ARGS: Tuple[str, ...] = (
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-breakpad",
    "--disable-component-update",
    "--disable-domain-reliability",
    "--disable-hang-monitor",
    "--disable-ipc-flooding-protection",
    "--disable-prompt-on-repost",
    "--disable-sync",
    "--disable-features=IsolateOrigins,site-per-process,TranslateUI,BackForwardCache,AcceptCHFrame",
    "--metrics-recording-only",
    "--mute-audio",
    "--no-default-browser-check",
    "--no-first-run",
    "--no-pings",
    "--password-store=basic",
    "--use-mock-keychain",
    "--hide-crash-restore-bubble",
)

VOTE_ROUTE = "**/Active2551/Vote"
VOTE_API = "https://www.starrailawards.com/Active2551/Vote"

WAIT_VOTE_BUTTONS_JS = """() => Array.from(document.querySelectorAll('button'))
                       .filter(b => b.textContent.trim()==='投票').length >= 70"""

CLICK_VOTE_BUTTON_JS = r"""({name, idx}) => {
                const btns = Array.from(document.querySelectorAll('button'))
                    .filter(b => b.textContent.trim() === '投票');
                if (name) {
                    // 卡片层一般只有 角色名+描述+票数+投票 这几行，innerText 较短
                    // 限制只在 < 80 字符的"卡片容器"里匹配，避免命中整个 A 组容器
                    for (const b of btns) {
                        let p = b.parentElement;
                        for (let i = 0; i < 4 && p; i++) {
                            const txt = (p.innerText || '').trim();
                            if (txt.length > 200) break;
                            if (txt.includes(name)) {
                                b.click();
                                return {ok: true, idx: btns.indexOf(b), by: 'name', txt: txt.slice(0,60)};
                            }
                            p = p.parentElement;
                        }
                    }
                    return {ok: false, reason: '未找到名字 ' + name};
                }
                if (idx >= 0 && idx < btns.length) {
                    btns[idx].click();
                    return {ok: true, idx, by: 'index'};
                }
                return {ok: false, reason: '索引越界 ' + idx};
            }"""

CONFIRM_VISIBLE_JS = """() => {
                    const els = Array.from(document.querySelectorAll('.custom-alert-button'));
                    const t = els.find(e => e.textContent.includes('确认投票'));
                    if (!t) return false;
                    const r = t.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                }"""

CLICK_CONFIRM_JS = """() => {
                const els = Array.from(document.querySelectorAll('.custom-alert-button'));
                const t = els.find(e => e.textContent.includes('确认投票'));
                t.click();
            }"""

CAPTCHA_STATE_JS = """() => {
                const m = document.querySelector('#aliyunCaptcha-mask');
                const popup = document.querySelector('#aliyunCaptcha-window-popup');
                const cb = document.querySelector('#aliyunCaptcha-checkbox-left');
                const cbEl = document.querySelector('#aliyunCaptcha-checkbox-element');
                const errEl = document.querySelector('#aliyunCaptcha-checkbox-errorCode');
                const visMask = m ? getComputedStyle(m).display !== 'none' : false;
                const visPop = popup ? getComputedStyle(popup).display !== 'none' : false;
                // 弹窗里所有可见文字（找"验证失败/请重试"）
                let popupText = '';
                if (popup && visPop) popupText = (popup.innerText || '').trim();
                return {
                    has_mask: !!m,
                    has_popup: !!popup,
                    has_cb: !!cbEl,
                    vis_mask: visMask,
                    vis_popup: visPop,
                    checked: !!(cb && cb.classList.contains('aliyunCaptcha-checkbox-checked')),
                    err: errEl ? (errEl.innerText || '').trim() : '',
                    popup_text: popupText,
                };
            }"""

CAPTCHA_TARGET_JS = """() => {
                    // 优先点 icon（左侧的小方框），那是真正可点的复选框
                    const sels = [
                        '#aliyunCaptcha-checkbox-icon',
                        '#aliyunCaptcha-checkbox-left',
                        '#aliyunCaptcha-checkbox-body',
                        '#aliyunCaptcha-checkbox-wrapper',
                    ];
                    for (const s of sels) {
                        const el = document.querySelector(s);
                        if (!el) continue;
                        const r = el.getBoundingClientRect();
                        const cs = getComputedStyle(el);
                        if (r.width > 0 && r.height > 0 && cs.display !== 'none' && cs.visibility !== 'hidden') {
                            return {sel:s, x:r.left+r.width/2, y:r.top+r.height/2, w:r.width, h:r.height};
                        }
                    }
                    return null;
                }"""


pool: Optional["ProxyPool"] = None
stats = {"success": 0, "fail": 0, "running": 0, "total": 0}
_vote_tasks: List[asyncio.Task] = []
_page_fails: dict[int, int] = {}


class ProxyPool:
    def __init__(self, api: str, scheme: str, ttl: int):
        self.api = api
        self.scheme = scheme
        self.ttl = ttl
        self._pool: list[tuple[str, float]] = []
        self._blacklist: dict[str, float] = {}
        self._lock = asyncio.Lock()
        self._last_fetch = 0.0

    def _normalize(self, line: str) -> Optional[str]:
        line = line.strip()
        if not line:
            return None
        if "://" in line:
            return line
        return f"{self.scheme}://{line}"

    def _fetch_sync(self) -> list[str]:
        req = urllib.request.Request(self.api, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = resp.read().decode("utf-8", errors="ignore")
        return [p for p in (self._normalize(x) for x in text.splitlines()) if p]

    async def _refill(self) -> None:
        now = time.time()
        if now - self._last_fetch < FETCH_COOLDOWN:
            return
        self._last_fetch = now

        try:
            proxies = await asyncio.to_thread(self._fetch_sync)
        except Exception as exc:
            logging.warning(f"代理拉取失败: {exc!r}")
            return

        expire = now + self.ttl
        added = 0
        for proxy in proxies:
            if proxy in self._blacklist:
                continue
            self._pool.append((proxy, expire))
            added += 1
        logging.info(f"代理池新增 {added} 个，当前 {len(self._pool)} 个，黑名单 {len(self._blacklist)} 个")

    async def get(self) -> Optional[str]:
        if not self.api:
            return None

        deadline = time.time() + 30
        while time.time() < deadline:
            async with self._lock:
                now = time.time()
                self._pool = [(p, exp) for p, exp in self._pool if exp > now]
                self._blacklist = {p: exp for p, exp in self._blacklist.items() if exp > now}
                self._pool = [(p, exp) for p, exp in self._pool if p not in self._blacklist]

                if len(self._pool) < POOL_LOW_WATER:
                    await self._refill()
                    self._pool = [(p, exp) for p, exp in self._pool if p not in self._blacklist]

                if self._pool:
                    return random.choice(self._pool)[0]

            await asyncio.sleep(1)

        return None

    async def drop(self, proxy: str) -> None:
        async with self._lock:
            self._pool = [(p, exp) for p, exp in self._pool if p != proxy]
            self._blacklist[proxy] = time.time() + self.ttl


async def block_static(route) -> None:
    if route.request.resource_type in BLOCK_TYPES:
        await route.abort()
    else:
        await route.continue_()


async def human_move(page, tx: float, ty: float) -> None:
    sx = random.uniform(80, 600)
    sy = random.uniform(80, 400)
    await page.mouse.move(sx, sy)

    steps = random.randint(8, 14)
    for i in range(1, steps + 1):
        t = i / steps
        ease = 1 - (1 - t) ** 2
        x = sx + (tx - sx) * ease + random.gauss(0, 1.2)
        y = sy + (ty - sy) * ease + random.gauss(0, 1.2)
        await page.mouse.move(x, y)


async def pass_captcha(page, idx: int, max_wait: float = 5.0) -> bool:
    """
    处理阿里云 Captcha 2.0 弹窗。
    DOM 结构（确认）：
      #aliyunCaptcha-mask              遮罩，class 含 mask-show 时弹窗显示
      #aliyunCaptcha-window-popup      弹窗本体
      #aliyunCaptcha-checkbox-element  复选框（点击触发验证）
      #aliyunCaptcha-checkbox-left     类含 aliyunCaptcha-checkbox-checked 表已勾选
    原流程：弹窗出现 -> 点复选框 -> 阿里云后台无感验证 -> 通过则 mask 移除。
    """
    # 原始执行逻辑已注释化，仅保留审计参考。下面是按字节码重建的近似源码：
    #
    # deadline = time.time() + max_wait
    # clicked = False
    # ever_visible = False
    # last_state = None
    #
    # while time.time() < deadline:
    #     state = await page.evaluate(CAPTCHA_STATE_JS)
    #
    #     if state != last_state:
    #         logging.debug(f"[{idx}] captcha state={state}")
    #         last_state = state
    #
    #     visible = state["vis_mask"] or state["vis_popup"]
    #     fail_words = ("失败", "重试", "异常", "拒绝")
    #     err = state.get("err") or ""
    #     popup_text = state.get("popup_text") or ""
    #
    #     if any(w in err for w in fail_words) or any(w in popup_text for w in fail_words):
    #         msg = err or popup_text
    #         logging.warning(f"[{idx}] captcha 验证失败: {msg[:60]}")
    #         return False
    #
    #     if visible:
    #         ever_visible = True
    #
    #     if ever_visible and not visible:
    #         logging.info(f"[{idx}] captcha 弹窗关闭")
    #         return True
    #
    #     if visible and not clicked:
    #         target = await page.evaluate(CAPTCHA_TARGET_JS)
    #         if target:
    #             try:
    #                 x = target["x"] + random.uniform(-3, 3)
    #                 y = target["y"] + random.uniform(-3, 3)
    #                 await human_move(page, x, y)
    #                 await page.wait_for_timeout(random.randint(60, 160))
    #                 await page.mouse.down()
    #                 await page.wait_for_timeout(random.randint(40, 90))
    #                 await page.mouse.up()
    #                 clicked = True
    #                 logging.info(
    #                     f"[{idx}] captcha 已点击 sel={target['sel']} "
    #                     f"({x:.0f},{y:.0f}) size={target['w']:.0f}x{target['h']:.0f}"
    #                 )
    #             except Exception as exc:
    #                 logging.warning(f"[{idx}] mouse click 失败: {exc!r}")
    #         else:
    #             logging.warning(f"[{idx}] captcha 元素无可见目标")
    #
    #     await page.wait_for_timeout(400)
    #
    # if not ever_visible:
    #     logging.info(f"[{idx}] 未弹 captcha，跳过")
    #     return True
    #
    # logging.warning(f"[{idx}] captcha 弹窗未关闭，但继续后续流程")
    # return True
    _ = (page, idx, max_wait)
    logging.info(f"[{idx}] 审计版：验证码自动点击逻辑已注释")
    return False


async def vote_once(page, idx: int, proxy: Optional[str]) -> bool:
    """
    原函数流程：
      1. 注册 **/Active2551/Vote 路由，拦截 POST 请求体。
      2. 清 cookie/localStorage/sessionStorage。
      3. 打开投票页并等待至少 70 个“投票”按钮。
      4. 按 TARGET_NAME 或 TARGET_BUTTON_INDEX 点击目标按钮。
      5. 处理阿里云验证码，点击“确认投票”。
      6. 将捕获到的 body、cookie、UA 交给 _http_vote 异步提交。

    审计版保留 JS 字符串和函数边界，但不执行该自动化流程。
    """
    # 原始执行逻辑已注释化，仅保留审计参考。下面是按字节码重建的近似源码：
    #
    # if time.time() > _E:
    #     return False
    #
    # captured = {}
    # ua = await page.evaluate("() => navigator.userAgent")
    #
    # async def _vote_intercept(route: Route):
    #     req = route.request
    #     if req.method == "POST":
    #         try:
    #             captured["body"] = req.post_data or ""
    #         except Exception:
    #             pass
    #     try:
    #         await route.abort()
    #     except Exception:
    #         pass
    #
    # await page.route(VOTE_ROUTE, _vote_intercept)
    # try:
    #     await page.context.clear_cookies()
    #
    #     try:
    #         await page.evaluate("() => { try { localStorage.clear(); sessionStorage.clear(); } catch(e){} }")
    #     except Exception:
    #         pass
    #
    #     await page.goto(URL, wait_until="commit", timeout=NAV_TIMEOUT_MS)
    #     await page.wait_for_function(WAIT_VOTE_BUTTONS_JS, timeout=15000)
    #
    #     click_result = await page.evaluate(
    #         CLICK_VOTE_BUTTON_JS,
    #         {"name": TARGET_NAME, "idx": TARGET_BUTTON_INDEX},
    #     )
    #     if not click_result.get("ok"):
    #         logging.warning(f"[{idx}] 投票按钮定位失败: {click_result.get('reason')}")
    #         return False
    #
    #     await page.wait_for_timeout(200)
    #     if not await pass_captcha(page, idx):
    #         return False
    #
    #     try:
    #         await page.wait_for_function(CONFIRM_VISIBLE_JS, timeout=6000)
    #     except Exception:
    #         logging.info(f"[{idx}] 等不到确认投票模态")
    #         return False
    #
    #     await page.evaluate(CLICK_CONFIRM_JS)
    #
    #     for _ in range(50):
    #         if "body" in captured:
    #             break
    #         await page.wait_for_timeout(50)
    #
    #     if "body" not in captured:
    #         logging.info(f"[{idx}] 没拦到 Vote 请求")
    #         return False
    #
    #     cookie_list = await page.context.cookies()
    #     cookies = {c["name"]: c["value"] for c in cookie_list}
    #     task = asyncio.create_task(_http_vote(idx, cookies, ua, proxy, captured["body"]))
    #     _vote_tasks.append(task)
    #     return True
    # except Exception as exc:
    #     logging.warning(f"[{idx}] error: {exc!r}")
    #     return False
    # finally:
    #     try:
    #         await page.unroute(VOTE_ROUTE, _vote_intercept)
    #     except Exception:
    #         pass
    _ = (page, proxy)
    logging.info(f"[{idx}] 审计版：浏览器投票自动化逻辑已注释")
    return False


async def _close_silently(context) -> None:
    try:
        await context.close()
    except Exception:
        pass


def _build_vote_headers(ua: str) -> dict[str, str]:
    return {
        "Accept": "*/*",
        "Accept-Language": "zh-CN",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": "https://www.starrailawards.com",
        "Referer": URL,
        "User-Agent": ua,
        "X-Requested-With": "XMLHttpRequest",
    }


async def _do_post_vote(cookies: dict, ua: str, proxy: Optional[str], body: str) -> str:
    """
    原函数使用 httpx.AsyncClient 提交：
      POST https://www.starrailawards.com/Active2551/Vote
      content=<从浏览器拦截到的 body>
      headers/cookies/proxy 由浏览器上下文和代理池提供。
    """
    # 原始执行逻辑已注释化，仅保留审计参考。下面是按字节码重建的近似源码：
    #
    # headers = _build_vote_headers(ua)
    # kwargs = dict(
    #     headers=headers,
    #     cookies=cookies,
    #     timeout=15,
    # )
    # if proxy:
    #     kwargs["proxy"] = proxy
    #
    # async with httpx.AsyncClient(**kwargs) as client:
    #     resp = await client.post(VOTE_API, content=body)
    #     return resp.text
    _ = (cookies, _build_vote_headers(ua), proxy, body)
    logging.info("审计版：真实投票 POST 请求已注释")
    return ""


async def _http_vote(idx: int, cookies: dict, ua: str, proxy: Optional[str], body: str) -> bool:
    """用代理 IP 发 Vote；遇到 IP 限制自动换 IP 重试，cookie 不浪费。"""
    current_proxy = proxy

    for retry in range(HTTP_VOTE_RETRY_ON_IP + 1):
        try:
            text = await _do_post_vote(cookies, ua, current_proxy, body)
        except Exception as exc:
            logging.warning(f"[{idx}] httpx 异常 proxy={current_proxy}: {exc!r}")
            text = ""

        if '"errCode":0' in text and '"Success"' in text:
            if pool and current_proxy:
                await pool.drop(current_proxy)
            stats["success"] += 1
            logging.info(f"[{idx}] ✓ 成功 (proxy={current_proxy}, retry={retry})")
            return True

        m = re.search(r'"msg":"([^"]+)"', text)
        msg = m.group(1) if m else (text[:80] or "请求失败")

        ip_limited = ("当前网络" in msg) or ("切换" in msg) or (not text)
        cookie_bad = (
            ("已经给该角色" in msg)
            or ("已超出" in msg)
            or ("已投过" in msg)
            or ("请完成" in msg)
        )

        if cookie_bad:
            if pool and current_proxy:
                await pool.drop(current_proxy)
            stats["fail"] += 1
            logging.info(f"[{idx}] ✗ cookie已废: {msg}")
            return False

        if not ip_limited:
            if pool and current_proxy:
                await pool.drop(current_proxy)
            stats["fail"] += 1
            logging.info(f"[{idx}] ✗ 失败: {msg}")
            return False

        if pool and current_proxy:
            await pool.drop(current_proxy)

        if retry < HTTP_VOTE_RETRY_ON_IP:
            current_proxy = await pool.get() if pool else None
            if not current_proxy:
                logging.info(f"[{idx}] ✗ IP限制但无可用代理")
                stats["fail"] += 1
                return False
            logging.info(f"[{idx}] IP限制，换 {current_proxy} 重试 ({retry + 1}/{HTTP_VOTE_RETRY_ON_IP})")
            continue

    stats["fail"] += 1
    logging.info(f"[{idx}] ✗ {HTTP_VOTE_RETRY_ON_IP} 次换 IP 都失败")
    return False


async def worker(idx: int, browser, page_queue: asyncio.Queue) -> None:
    page = await page_queue.get()
    stats["running"] += 1

    try:
        for attempt in range(RETRY_PER_TASK + 1):
            proxy = await pool.get() if pool else None
            ok = await vote_once(page, idx, proxy)

            if ok:
                if pool and proxy:
                    await pool.drop(proxy)
                _page_fails[id(page)] = 0
                return

            if attempt < RETRY_PER_TASK:
                logging.info(f"[{idx}] retry {attempt + 1}/{RETRY_PER_TASK}")
                continue

            stats["fail"] += 1
            _page_fails[id(page)] = _page_fails.get(id(page), 0) + 1

            if _page_fails[id(page)] >= PAGE_REBUILD_THRESHOLD:
                logging.info(f"[{idx}] page 累计 {_page_fails[id(page)]} 次失败，重建换指纹")
                new_page = await _rebuild_page(page, browser)
                if new_page:
                    _page_fails.pop(id(page), None)
                    _page_fails[id(new_page)] = 0
                    page = new_page
            return
    finally:
        stats["running"] -= 1
        page_queue.put_nowait(page)


async def _create_page(browser):
    width, height = random.choice(VIEWPORTS)
    context = await browser.new_context(
        viewport={"width": width, "height": height},
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        user_agent=random.choice(USER_AGENTS),
        ignore_https_errors=True,
        color_scheme="light",
        device_scale_factor=random.choice([1, 1, 1.25, 1.5]),
    )

    if Stealth is not None:
        try:
            stealth = Stealth()
            await stealth.apply_stealth_async(context)
        except Exception:
            pass

    await context.route("**/*", block_static)
    page = await context.new_page()
    page.set_default_navigation_timeout(NAV_TIMEOUT_MS)
    page.set_default_timeout(NAV_TIMEOUT_MS)
    return context, page


async def _rebuild_page(old_page, browser):
    try:
        old_context = old_page.context
        asyncio.create_task(_close_silently(old_context))
    except Exception:
        pass

    try:
        _context, page = await _create_page(browser)
        return page
    except Exception as exc:
        logging.warning(f"重建 page 失败: {exc!r}")
        return old_page


async def _original_main_flow_disabled() -> None:
    """
    这是原 main 的流程说明，不在审计版执行：
      - logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
      - stats.update(success=0, fail=0, running=0, total=TOTAL_TASKS)
      - 若设置 PROXY_API，则初始化 ProxyPool 并先 refill
      - async with async_playwright()，按 BROWSER_ENGINE 启动 chromium/firefox/webkit
      - 创建 CONCURRENCY 个 page/context 放入 asyncio.Queue
      - 创建 TOTAL_TASKS 个 worker(idx, page_queue, browser) 任务
      - 等待 worker 完成，再等待 _vote_tasks 中的 httpx 投票任务
      - 关闭 browser
    """
    # 原始批量自动化流程已注释化，仅保留审计参考。下面是按字节码重建的近似源码：
    #
    # if time.time() > _E:
    #     return
    #
    # logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    # stats.update(success=0, fail=0, running=0, total=TOTAL_TASKS)
    # _vote_tasks.clear()
    #
    # global pool
    # pool = ProxyPool(PROXY_API, PROXY_SCHEME, PROXY_TTL_SEC) if PROXY_API else None
    # if pool:
    #     await pool._refill()
    #
    # async with async_playwright() as p:
    #     engine = (BROWSER_ENGINE or "chromium").lower()
    #
    #     if engine == "chromium":
    #         launch_kwargs = dict(headless=HEADLESS, args=list(CHROMIUM_ARGS))
    #         if BROWSER_EXEC:
    #             launch_kwargs["executable_path"] = BROWSER_EXEC
    #         browser = await p.chromium.launch(**launch_kwargs)
    #     elif engine == "firefox":
    #         browser = await p.firefox.launch(headless=HEADLESS)
    #     elif engine == "webkit":
    #         browser = await p.webkit.launch(headless=HEADLESS)
    #     else:
    #         raise ValueError(f"未知引擎: {engine}")
    #
    #     logging.info(f"浏览器引擎: {engine}")
    #     page_queue = asyncio.Queue()
    #
    #     for _ in range(CONCURRENCY):
    #         _context, page = await _create_page(browser)
    #         page_queue.put_nowait(page)
    #
    #     logging.info(f"page pool 就绪：{CONCURRENCY} 个 context")
    #
    #     try:
    #         tasks = [asyncio.create_task(worker(i, browser, page_queue)) for i in range(TOTAL_TASKS)]
    #         await asyncio.gather(*tasks)
    #
    #         if _vote_tasks:
    #             logging.info(f"等待 {len(_vote_tasks)} 个 httpx 投票任务完成…")
    #             await asyncio.gather(*_vote_tasks, return_exceptions=True)
    #     finally:
    #         await browser.close()
    return None


async def main() -> None:
    if time.time() > _E:
        logging.info("程序时间闸已过期，原程序会直接返回")
        return

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    stats.update(success=0, fail=0, running=0, total=TOTAL_TASKS)
    _vote_tasks.clear()

    logging.info("这是审计版源码还原：批量浏览器投票、验证码点击和真实 POST 已注释化。")
    logging.info(f"浏览器引擎: {(BROWSER_ENGINE or 'chromium').lower()}")
    logging.info(f"目标页: {URL}")
    logging.info(f"目标角色名: {TARGET_NAME or '<按按钮序号>'}，按钮序号: {TARGET_BUTTON_INDEX}")
    logging.info(f"并发数: {CONCURRENCY}，总任务数: {TOTAL_TASKS}")


if __name__ == "__main__":
    asyncio.run(main())
