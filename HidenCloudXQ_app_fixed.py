#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, re, sys, time, random, requests
from playwright.sync_api import sync_playwright

# --- 环境变量 ---
COOKIE_VALUE = os.environ.get('COOKIE_VALUE', '') or ""  # remember_web cookie 值，必填
EMAIL = os.environ.get('EMAIL', '') or ""                 # 登录邮箱,可选，作为备用,TG通知需要填写
PASSWORD = os.environ.get('PASSWORD', '') or ""           # 登录密码,可选，作为备用
TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN', '') or ""   # Telegram Bot Token,可选
TG_CHAT_ID = os.environ.get('TG_CHAT_ID', '') or ""       # Telegram Chat ID,可选

BASE_URL = "https://dash.hidencloud.com"
LOGIN_URL = f"{BASE_URL}/auth/login"

# --- 代理配置（由工作流 shell 脚本写入 $GITHUB_ENV）---
IS_PROXY = os.environ.get('IS_PROXY', 'false').lower() == 'true'
PROXY_SERVER = os.environ.get('PROXY_SERVER') or "socks5://127.0.0.1:1080"
REQUESTS_PROXIES = {"http": PROXY_SERVER, "https": PROXY_SERVER} if IS_PROXY else None


# 日志
def log(message):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {
  get: () => undefined
});
window.chrome = {
  runtime: {}
};
"""


def get_current_ip(proxy_server=None):
    """获取当前出口IP"""
    proxies = {"http": proxy_server, "https": proxy_server} if (proxy_server and IS_PROXY) else None
    try:
        resp = requests.get("https://api.ip.sb/ip", proxies=proxies, timeout=15)
        if resp.status_code == 200:
            return resp.text.strip()
        return "获取失败"
    except Exception as e:
        log(f"❌ 获取出口IP失败: {e}")
        return "获取失败"


def send_telegram_notification(status, old_due, new_due):
    """发送 Telegram 通知"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        log("⚠️ Telegram 未配置，跳过通知")
        return False

    local_time = time.gmtime(time.time() + 8 * 3600)
    now = time.strftime("%Y-%m-%d %H:%M:%S", local_time)

    if '@' in EMAIL:
        name, domain = EMAIL.split('@', 1)
        if len(name) > 4:
            masked_email = f"{name[:2]}****{name[-2:]}@{domain}"
        else:
            masked_email = f"{name}@{domain}"
    else:
        masked_email = EMAIL[:2] + '****'

    text = (
        f"🎉 HidenCloud 续期通知\n\n"
        f"{status}\n"
        f"👤 账号: {masked_email}\n"
        f"📅 续期前到期：{old_due}\n"
        f"📅 续期后到期：{new_due}\n"
        f"🕒 续期时间：{now}"
    )

    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"}

    try:
        resp = requests.post(url, json=payload, timeout=10, proxies=REQUESTS_PROXIES)
        if resp.status_code == 200:
            log("✅ Telegram 通知发送成功")
            return True
        else:
            log(f"❌ Telegram 通知失败: {resp.text}")
            return False
    except Exception as e:
        log(f"❌ Telegram 通知异常: {e}")
        return False


def handle_cloudflare(page):
    """
    综合处理各种安全验证：
    1. 旧式 Cloudflare iframe 验证
    2. Cloudflare Turnstile
    3. HidenCloud 自定义 Security Verification 页面
    """
    # ---- 1. 旧式 iframe 验证 (challenges.cloudflare.com) ----
    iframe_selector = 'iframe[src*="challenges.cloudflare.com"]'
    if page.locator(iframe_selector).count() > 0:
        log("⚠️ 检测到 Cloudflare iframe 验证...")
        start_time = time.time()
        while time.time() - start_time < 60:
            if page.locator(iframe_selector).count() == 0:
                log("✅ Cloudflare 验证通过！")
                return True
            try:
                frame = page.frame_locator(iframe_selector)
                checkbox = frame.locator('input[type="checkbox"]')
                if checkbox.is_visible():
                    log("🖱️ 点击验证复选框...")
                    time.sleep(random.uniform(0.5, 1.5))
                    checkbox.click()
                    log("⏳ 已点击，等待验证结果...")
                    time.sleep(5)
                else:
                    time.sleep(1)
            except Exception:
                pass
        log("❌ 验证超时。")
        return False

    # ---- 2. Turnstile 验证 ----
    turnstile_selector = (
        'div[class*="cf-turnstile"], '
        'iframe[src*="challenges.cloudflare.com/cdn-cgi/challenge-platform"]'
    )
    if page.locator(turnstile_selector).count() > 0:
        log("⚠️ 检测到 Cloudflare Turnstile 验证，等待自动通过...")
        start_time = time.time()
        while time.time() - start_time < 60:
            page.wait_for_timeout(3000)
            if page.locator(turnstile_selector).count() == 0:
                log("✅ Turnstile 验证已通过！")
                return True
            # Turnstile 通常自动旋转通过，也可以尝试点击
            try:
                iframe = page.frame_locator('iframe[src*="challenges.cloudflare.com/cdn-cgi/challenge-platform"]')
                checkbox = iframe.locator('input[type="checkbox"], div[role="checkbox"]').first
                if checkbox.is_visible():
                    log("🖱️ 尝试点击 Turnstile 复选框...")
                    checkbox.click()
                    page.wait_for_timeout(3000)
            except Exception:
                pass
        log("❌ Turnstile 验证超时。")
        return False

    # ---- 3. HidenCloud 自定义 Security Verification 页面 ----
    if "Security Verification" in page.title():
        log("⚠️ 检测到 Security Verification 页面，等待自动跳转...")
        page.screenshot(path="security_verification.png")

        start_time = time.time()
        while time.time() - start_time < 90:
            page.wait_for_timeout(3000)
            current_title = page.title()
            current_url = page.url
            log(f"   ⏳ 当前页面: title='{current_title}', url={current_url}")

            if "Security Verification" not in current_title:
                log("✅ 安全验证已通过！")
                page.wait_for_timeout(2000)  # 等页面稳定
                return True

            # 尝试查找并点击可能的验证按钮/链接
            try:
                # 可能有 "Verify" / "Submit" / "Continue" 按钮
                for btn_text in ["Verify", "Submit", "Continue", "验证", "确认"]:
                    btn = page.locator(f'button:has-text("{btn_text}")').first
                    if btn.is_visible(timeout=1000):
                        log(f"🖱️ 尝试点击 '{btn_text}' 按钮...")
                        btn.click()
                        page.wait_for_timeout(5000)
                        break
                else:
                    # 也可能有链接形式的验证
                    verify_link = page.locator('a:has-text("click here"), a:has-text("here"), a:has-text("verify")').first
                    if verify_link.is_visible(timeout=1000):
                        log("🖱️ 点击验证链接...")
                        verify_link.click()
                        page.wait_for_timeout(5000)
            except Exception:
                pass

        log("❌ Security Verification 超时（90秒），可能需要更换代理IP")
        page.screenshot(path="security_verification_timeout.png")
        return False

    return True


def wait_and_handle_verification(page, target_selector=None, timeout=60):
    """
    等待页面元素出现，同时持续处理安全验证。
    如果提供了 target_selector，则等待该元素出现。
    安全验证通过后自动返回。
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        # 先处理验证
        if "Security Verification" in page.title():
            handle_cloudflare(page)

        if page.locator('iframe[src*="challenges.cloudflare.com"]').count() > 0:
            handle_cloudflare(page)

        if target_selector and page.locator(target_selector).count() > 0:
            return True

        page.wait_for_timeout(2000)

    return bool(target_selector is None or page.locator(target_selector).count() > 0)


def login(page):
    """尝试登录：先 Cookie，后邮箱密码"""

    # ---- 1. Cookie 登录 ----
    if COOKIE_VALUE:
        log("📇 尝试 Cookie 登录...")
        try:
            page.context.add_cookies([{
                'name': 'remember_web_59ba36addc2b2f9401580f014c7f58ea4e30989d',
                'value': COOKIE_VALUE,
                'domain': 'dash.hidencloud.com',
                'path': '/',
                'expires': int(time.time()) + 3600 * 24 * 365,
                'httpOnly': True,
                'secure': True,
                'sameSite': 'Lax'
            }])
            page.goto(f"{BASE_URL}/dashboard", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2000)

            # 处理安全验证
            handle_cloudflare(page)
            page.wait_for_timeout(2000)

            page_title = page.title()
            current_url = page.url
            log(f"📝 Cookie 登录后: title='{page_title}', url={current_url}")

            # ❗关键修复：Security Verification 不等于 Cookie 失效
            if "Security Verification" in page_title:
                log("⏳ 遇到安全验证，Cookie 可能仍然有效，等待验证通过...")
                if handle_cloudflare(page):
                    page.wait_for_timeout(3000)
                    page_title = page.title()
                    current_url = page.url
                    log(f"📝 验证通过后: title='{page_title}', url={current_url}")

            if "auth/login" not in current_url:
                log(f"✅ Cookie 登录成功！当前已到达 dashboard 页面")
                return True

            # 真的被重定向到登录页了
            if "Security Verification" in page_title:
                log("⏳ 仍在验证页，但 URL 指向登录页 — Cookie 可能已失效")
            else:
                log("❌ Cookie 失效，请更换")

        except Exception as e:
            log(f"⚠️ Cookie 登录异常: {e}")

    # ---- 2. 邮箱密码登录 ----
    if not EMAIL or not PASSWORD:
        log("❌ 未配置 EMAIL/PASSWORD 作为备用登录方式")
        return False

    log("💣 尝试账号密码登录...")
    try:
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2000)

        # 处理可能的安全验证
        handle_cloudflare(page)

        current_title = page.title()
        log(f"📝 登录页当前 Title: {current_title}")

        # 如果被验证页拦截，等待验证通过
        if "Security Verification" in current_title:
            log("⏳ 登录页被安全验证拦截，等待通过...")
            if not handle_cloudflare(page):
                log("❌ 安全验证超时，无法到达登录表单")
                page.screenshot(path="login_blocked_by_verification.png")
                return False
            page.wait_for_timeout(3000)
            log(f"📝 验证通过后 Title: {page.title()}")

        # 等待邮箱输入框出现（最多 30 秒）
        log("⏳ 等待登录表单加载...")
        try:
            page.wait_for_selector('input[name="email"]', state="visible", timeout=30000)
        except Exception:
            log("❌ 登录表单未出现，可能页面结构已变化")
            page.screenshot(path="login_form_not_found.png")
            return False

        page.fill('input[name="email"]', EMAIL)
        page.fill('input[name="password"]', PASSWORD)
        time.sleep(0.5)

        handle_cloudflare(page)
        page.click('button[type="submit"]')
        time.sleep(3)

        handle_cloudflare(page)

        page.wait_for_url(f"{BASE_URL}/*", timeout=30000)
        page.goto(f"{BASE_URL}/dashboard", wait_until="domcontentloaded", timeout=60000)
        handle_cloudflare(page)

        page_title = page.title()
        log(f"📝 密码登录后 Title: {page_title}")

        if "auth/login" in page.url:
            log("❌ 登录失败。")
            page.screenshot(path="login_fail.png")
            return False

        log(f"✅ 账号密码登录成功！当前已到达 dashboard 页面")
        return True

    except Exception as e:
        log(f"❌ 登录异常: {e}")
        page.screenshot(path="login_fail.png")
        return False


def get_server_id(page):
    """从 dashboard 页面抓取 Server ID"""
    try:
        handle_cloudflare(page)
        time.sleep(3)
        html = page.content()
        log(f"📝 页面长度: {len(html)}, URL: {page.url}")

        # 方案1: 从 href 链接中提取 /service/数字/manage
        matches = re.findall(r'/service/(\d+)/manage', html)
        if matches:
            server_id = matches[0]
            log(f"✅ 从链接中获取到 Server ID: {server_id}")
            return server_id

        # 方案2: 从 span 标签中提取 #数字 (如 "Free Server #218079")
        matches = re.findall(r'#(\d{4,})', html)
        if matches:
            server_id = matches[0]
            log(f"✅ 从文本 #号中获取到 Server ID: {server_id}")
            return server_id

        log("❌ 所有 URL 均未找到 Server ID")
        page.screenshot(path="server_id_error.png")
        return None
    except Exception as e:
        log(f"❌ 获取 Server ID 失败: {e}")
        page.screenshot(path="server_id_error.png")
        return None


def get_due_date(page, SERVICE_URL):
    """获取当前到期时间"""
    try:
        if SERVICE_URL not in page.url:
            page.goto(SERVICE_URL, wait_until="domcontentloaded", timeout=60000)
            handle_cloudflare(page)

        body_text = page.locator("body").inner_text()
        patterns = [
            r"Due date\s+(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})",
            r"Due date\s*\n\s*(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})",
            r"Due date.*?(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})",
        ]
        for pattern in patterns:
            match = re.search(pattern, body_text, re.IGNORECASE | re.DOTALL)
            if match:
                due_date = match.group(1).strip()
                log(f"📅 获取到 Due Date: {due_date}")
                return due_date

        log("⚠️ 未找到 Due Date")
        return "未知"
    except Exception as e:
        log(f"❌ 获取 Due Date 失败: {e}")
        return "未知"


def renew_service(page, SERVICE_URL):
    """执行续费流程"""
    try:
        log("➡ 进入续期流程...")

        if page.url != SERVICE_URL:
            page.goto(SERVICE_URL, wait_until="domcontentloaded", timeout=60000)
            handle_cloudflare(page)

        log("🖱️ 准备点击 'Renew' 按钮...")
        renew_btn = page.locator('button:has-text("Renew")')
        create_btn = page.locator('button:has-text("Create Invoice")')
        modal_opened = False

        for i in range(3):
            try:
                renew_btn.wait_for(state="visible", timeout=10000)
                renew_btn.scroll_into_view_if_needed()
                log(f"🖱️ 第 {i+1} 次尝试点击 'Renew'...")
                renew_btn.click()

                time.sleep(2)
                page_text = page.locator("body").inner_text()
                if "Renewal Restricted" in page_text or "can only renew" in page_text.lower():
                    log("⚠️ 未到续期时间，无法续期。")
                    page.screenshot(path="renew_not_allowed.png")
                    return "NOT_TIME"

                log("🖲️ 等待弹窗出现...")
                try:
                    create_btn.wait_for(state="visible", timeout=5000)
                    modal_opened = True
                    log("✅ 弹窗已成功弹出！")
                    break
                except Exception:
                    log("⚠️ 弹窗未出现，可能是点击未响应，准备重试...")
                    time.sleep(2)
            except Exception as e:
                log(f"❌ 点击尝试出错: {e}")

        if not modal_opened:
            log("❌ 错误：尝试多次后，续费弹窗仍未出现。")
            page.screenshot(path="renew_modal_failed.png")
            return False

        handle_cloudflare(page)
        log("🖱️ 点击 'Create Invoice'...")
        create_btn.click()

        new_invoice_url = None
        start_wait = time.time()
        while time.time() - start_wait < 90:
            if "/payment/invoice/" in page.url:
                new_invoice_url = page.url
                log(f"🎉 页面已跳转: {new_invoice_url}")
                break
            if page.locator('iframe[src*="challenges.cloudflare.com"]').count() > 0:
                log("⚠️ 遇到拦截，尝试处理...")
                handle_cloudflare(page)
            time.sleep(1)

        if not new_invoice_url:
            log("❌ 未能进入发票页面，超时。")
            page.screenshot(path="renew_stuck_invoice.png")
            return False

        if page.url != new_invoice_url:
            page.goto(new_invoice_url)
            handle_cloudflare(page)

        log("🔎 查找 'Pay' 按钮...")
        pay_btn = page.locator('a:has-text("Pay"):visible, button:has-text("Pay"):visible').first
        pay_btn.wait_for(state="visible", timeout=30000)
        pay_btn.click()
        log("✅ 'Pay' 按钮已点击。")

        time.sleep(5)

        # 返回服务管理页面以获取新的到期时间
        page.goto(SERVICE_URL, wait_until="domcontentloaded", timeout=60000)
        handle_cloudflare(page)
        return True

    except Exception as e:
        log(f"❌ 续费异常: {e}")
        page.screenshot(path="renew_error.png")
        return False


def main():
    # 检查必要环境变量
    if not COOKIE_VALUE and not (EMAIL and PASSWORD):
        log("❌ 缺少登录凭证（COOKIE_VALUE 和 EMAIL/PASSWORD 至少配置一组）")
        sys.exit(1)

    global SERVICE_URL
    SERVICE_URL = None

    with sync_playwright() as p:
        browser = None
        try:
            if IS_PROXY:
                log(f"⚙️ 代理已启用: {PROXY_SERVER}")
            else:
                log("🌐 直连模式（未使用代理）")

            current_ip = get_current_ip(PROXY_SERVER if IS_PROXY else None)
            log(f"🎯 当前出口IP: {current_ip}")

            log("🚀 启动浏览器...")
            browser = p.chromium.launch(
                channel="chrome",
                headless=False,
                args=[
                    '--no-sandbox',
                    '--disable-blink-features=AutomationControlled',
                    '--disable-infobars'
                ]
            )

            context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent=(
                    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
                ),
                proxy={"server": PROXY_SERVER} if IS_PROXY else None
            )
            page = context.new_page()
            page.add_init_script(STEALTH_JS)

            # --- 登录 ---
            if not login(page):
                sys.exit(1)

            # --- 获取 Server ID ---
            server_id = get_server_id(page)
            if not server_id:
                log("❌ 无法获取 Server ID，退出。")
                sys.exit(1)

            SERVICE_URL = f"{BASE_URL}/service/{server_id}/manage"

            # --- 续期前状态 ---
            old_due = get_due_date(page, SERVICE_URL)
            log(f"📆 续费前到期时间：{old_due}")

            # --- 执行续期 ---
            renew_result = renew_service(page, SERVICE_URL)
            new_due = old_due

            if renew_result == "NOT_TIME":
                log("⏳ 未到续期时间，目前无法续期")
                status = "⏳ 未到续期时间"
            elif renew_result is False:
                log("❌ 续费失败，脚本退出。")
                status = "❌ 续期失败"
            else:
                new_due = get_due_date(page, SERVICE_URL)
                log(f"📆 续费后到期时间：{new_due}")
                status = "✅ 续期成功"

            # --- 通知 ---
            send_telegram_notification(status, old_due, new_due)

            if renew_result == "NOT_TIME":
                sys.exit(0)
            elif renew_result is False:
                sys.exit(1)
            else:
                sys.exit(0)

        except Exception as e:
            log(f"❌ 浏览器启动出错: {e}")
            sys.exit(1)
        finally:
            if browser:
                browser.close()


if __name__ == "__main__":
    main()