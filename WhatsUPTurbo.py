#!/usr/bin/env python3

"""
WhatsApp Firefox Bulk Sender – v1.0 (release)

• Session via persistent Firefox profile ONLY (default: ./whatsup_profile)
• Headless bootstrap: if --headless, first open a visible window to detect login, then relaunch headless
• Text-only fast path with WA prefill URL
• Media + (optional) caption (Arabic/English multiline)
• Robust chat composer targeting (excludes caption editor)
• Run artifacts: runs/YYYY-MM-DD_HH-MM-SS/{summary.txt, config.json, sent_numbers.txt, Filed.txt}
"""

import json
import time
import argparse
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from selenium import webdriver
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, SessionNotCreatedException
from webdriver_manager.firefox import GeckoDriverManager

WA_URL = "https://web.whatsapp.com/"

# ------------------------- Console -------------------------
try:
    from colorama import init as _cinit, Fore as F, Style as S
    _cinit()
    GREEN, YELL, RED, CYAN, DIM, R = F.GREEN, F.YELLOW, F.RED, F.CYAN, S.DIM, S.RESET_ALL
except Exception:
    GREEN = YELL = RED = CYAN = DIM = R = ""

def ok(msg):    print(f"{GREEN}✅ {msg}{R}")
def info(msg):  print(f"{CYAN}ℹ️  {msg}{R}")
def warn(msg):  print(f"{YELL}⚠️  {msg}{R}")
def err(msg):   print(f"{RED}❌ {msg}{R}")
def item(label, value): print(f"   • {label}: {value}")

# ------------------------- CLI -------------------------
def parse_arguments():
    p = argparse.ArgumentParser(
        description="Send WhatsApp messages/files to multiple numbers via Firefox automation.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 whatsapp_sender.py --numbers 966000000001,966000000002 --message "Hello!"
  python3 whatsapp_sender.py --numbers-file numbers.txt --file Video.mp4 --caption @cap.txt
"""
    )
    nums = p.add_mutually_exclusive_group(required=True)
    nums.add_argument('--numbers', help='Comma-separated phone numbers (e.g., 9665...,9665...)')
    nums.add_argument('--numbers-file', help='Text file with one number per line')

    p.add_argument('--message', help='Message text OR "@file.txt" (UTF-8)')
    p.add_argument('--caption', help='Caption text OR "@file.txt" (UTF-8)')
    p.add_argument('--file', help='Path to media/document to send (<=50MB)')

    p.add_argument('--delay', type=float, default=3.0, help='Delay between numbers in seconds (default: 3.0)')
    p.add_argument('--headless', action='store_true', help='Run sending headless (uses visible bootstrap for login)')
    p.add_argument('--login-timeout', type=int, default=45, help='Seconds to wait for login (default: 45)')
    p.add_argument('--profile-dir', default='whatsup_profile', help='Persistent Firefox profile dir (default: whatsup_profile)')
    p.add_argument('--version', action='version', version='WhatsApp Firefox Bulk Sender v1.0')
    return p.parse_args()

# ------------------------- Run artifacts -------------------------
def make_run_dir():
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    run_dir = Path("runs") / ts
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir

def write_run_files(run_dir: Path, stats: dict, successes: list, fails: list, cfg: dict):
    run_dir.joinpath("summary.txt").write_text(
        "\n".join([
            f"Timestamp: {datetime.now().isoformat(timespec='seconds')}",
            f"Total:     {stats.get('total', 0)}",
            f"Success:   {stats.get('success', 0)}",
            f"Failed:    {stats.get('failed', 0)}",
            ""
        ]), encoding="utf-8"
    )
    if successes:
        run_dir.joinpath("sent_numbers.txt").write_text("\n".join(successes), encoding="utf-8")
    if fails:
        run_dir.joinpath("Filed.txt").write_text("\n".join(fails), encoding="utf-8")
    run_dir.joinpath("config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

# ------------------------- Utils -------------------------
def _read_text_file(path):
    try:
        return Path(path).read_text(encoding='utf-8')
    except FileNotFoundError:
        err(f"File not found: {path}"); return None
    except Exception as e:
        err(f"Cannot read {path}: {e}"); return None

def resolve_text_arg(value):
    if not value: return None
    v = value.strip()
    if v.startswith('@'):
        return _read_text_file(v[1:])
    return v

def validate_file(file_path):
    if not file_path: return None, None
    p = Path(file_path)
    if not p.exists():
        err(f"File not found: {p}"); return None, None
    image = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
    video = {'.mp4', '.avi', '.mov', '.mkv', '.webm'}
    doc   = {'.pdf', '.doc', '.docx', '.txt', '.xlsx', '.pptx', '.csv'}
    audio = {'.mp3', '.wav', '.ogg', '.m4a'}
    ext = p.suffix.lower()
    if   ext in image: ftype = "image"
    elif ext in video: ftype = "video"
    elif ext in doc:   ftype = "document"
    elif ext in audio: ftype = "audio"
    else:
        err(f"Unsupported file type: {ext}"); return None, None
    size_mb = p.stat().st_size / (1024*1024)
    if size_mb > 50:
        err(f"File too large: {size_mb:.1f}MB (max 50MB)"); return None, None
    ok(f"File: {p.name} ({ftype}, {size_mb:.1f}MB)")
    return str(p.resolve()), ftype

# ------------------------- Login detect -------------------------
def _any_visible(driver, locators):
    for by, sel in locators:
        try:
            el = driver.find_element(by, sel)
            if el.is_displayed():
                return True
        except NoSuchElementException:
            continue
    return False

def check_logged_in(driver):
    locators = [
        (By.CSS_SELECTOR, "div[data-testid='conversation-panel-messages']"),
        (By.CSS_SELECTOR, "div[data-testid='chatlist-panel']"),
        (By.CSS_SELECTOR, "button[aria-label='New chat']"),
        (By.CSS_SELECTOR, "div[role='textbox'][contenteditable='true']"),
        (By.XPATH, "//*[@id='pane-side']"),
        (By.XPATH, "//*[@id='main']"),
    ]
    return _any_visible(driver, locators)

def wait_for_qr_and_login(driver, first_number_for_probe: str | None, hard_timeout=45):
    info("Opening WhatsApp Web")
    try:
        driver.get(WA_URL)
    except Exception as e:
        warn(f"Cannot navigate to {WA_URL}: {e}")
    try:
        driver.get(f"{WA_URL}send?phone=123")
    except Exception:
        pass
    deadline = time.time() + int(hard_timeout or 45)
    while time.time() < deadline:
        if check_logged_in(driver):
            ok("Login detected"); return True
        time.sleep(2)
    if first_number_for_probe:
        try:
            driver.get(f"{WA_URL}send?phone={first_number_for_probe}")
            for _ in range(10):
                if check_logged_in(driver):
                    ok("Login detected (via probe)"); return True
                time.sleep(2)
        except Exception:
            pass
    warn("Login not detected; aborting send"); return False

# ------------------------- Chat composer -------------------------
def find_chat_composer(driver, wait_secs: int = 12):
    xp = (
        "//*[@id='main']//footer"
        "//div[@contenteditable='true' and @role='textbox' and @data-lexical-editor='true'"
        " and not(contains(translate(@aria-label,'CAPTION','caption'),'caption'))"
        " and not(contains(translate(@aria-placeholder,'CAPTION','caption'),'caption'))]"
    )
    try:
        return WebDriverWait(driver, wait_secs).until(EC.visibility_of_element_located((By.XPATH, xp)))
    except TimeoutException:
        pass
    fallbacks = [
        "//*[@id='main']"
        "//div[@contenteditable='true' and @role='textbox' and @data-lexical-editor='true'"
        " and not(contains(translate(@aria-label,'CAPTION','caption'),'caption'))"
        " and not(contains(translate(@aria-placeholder,'CAPTION','caption'),'caption'))]",
        "//div[@contenteditable='true' and @role='textbox']",
    ]
    for xp in fallbacks:
        try:
            candidates = driver.find_elements(By.XPATH, xp)
            ranked = []
            for el in candidates:
                try:
                    if not el.is_displayed(): continue
                    aria = (el.get_attribute("aria-label") or "") + " " + (el.get_attribute("aria-placeholder") or "")
                    if "caption" in aria.lower(): continue
                    box = el.rect
                    ranked.append((box.get("width", 0) * box.get("height", 0), el))
                except Exception:
                    continue
            if ranked:
                ranked.sort(reverse=True, key=lambda t: t[0])
                return ranked[0][1]
        except Exception:
            continue
    return None

# ------------------------- Message senders -------------------------
def send_message_via_url(driver, number: str, message: str) -> bool:
    if not message: return True
    try:
        text_norm = driver.execute_script(
            "return String(arguments[0]||'').split('\\r\\n').join('\\n').split('\\r').join('\\n');",
            message,
        )
    except Exception:
        text_norm = (message or "").replace("\r\n", "\n").replace("\r", "\n")
    url = f"{WA_URL}send?phone={number}&text={quote(text_norm)}"
    driver.get(url)
    box = find_chat_composer(driver, wait_secs=15)
    if not box:
        err("Message composer not found (prefill path)")
        return False
    got = driver.execute_script("return (arguments[0].innerText||'').replace('\\r','');", box) or ""
    if not got.strip():
        return send_message_improved(driver, message)
    ActionChains(driver).send_keys(Keys.ENTER).perform()
    time.sleep(0.4)
    return True

def send_message_improved(driver, message: str) -> bool:
    if not message: return True
    box = find_chat_composer(driver)
    if not box:
        err("Message composer not found"); return False
    text_norm = driver.execute_script(
        "return String(arguments[0]||'').split('\\r\\n').join('\\n').split('\\r').join('\\n');",
        message,
    )
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", box)
        driver.execute_script("arguments[0].click();", box)
        time.sleep(0.05)
        driver.execute_script("""
            const el = arguments[0];
            el.focus();
            el.innerHTML = '';
            while (el.firstChild) el.removeChild(el.firstChild);
            el.setAttribute('dir','auto');
            const txt = String(arguments[1] ?? '');
            const lines = txt.split('\\n');
            for (let i=0;i<lines.length;i++){
                if (i) el.appendChild(document.createElement('br'));
                el.appendChild(document.createTextNode(lines[i]));
            }
            el.dispatchEvent(new InputEvent('input', {bubbles:true}));
        """, box, text_norm)
        ActionChains(driver).send_keys(Keys.ENTER).perform()
        time.sleep(0.5)
        return True
    except Exception as e:
        err(f"Cannot send text: {e}")
        return False

# ------------------------- Caption helpers -------------------------
def _hard_clear_editor(driver, el):
    driver.execute_script("""
        const el = arguments[0];
        el.focus();
        el.innerHTML = '';
        const sel = window.getSelection();
        sel.removeAllRanges();
        const range = document.createRange();
        range.selectNodeContents(el);
        sel.addRange(range);
        document.execCommand('delete', false, null);
        el.dispatchEvent(new InputEvent('input', {bubbles:true}));
    """, el)

def _set_text_content(driver, el, text_norm):
    return driver.execute_script("""
        const el = arguments[0];
        const txt = String(arguments[1] ?? '');
        el.setAttribute('dir','auto');
        el.focus();
        while (el.firstChild) el.removeChild(el.firstChild);
        const lines = txt.split('\\n');
        for (let i=0;i<lines.length;i++){
            if (i) el.appendChild(document.createElement('br'));
            el.appendChild(document.createTextNode(lines[i]));
        }
        el.dispatchEvent(new InputEvent('input', {bubbles:true}));
        return (el.innerText || el.textContent || '').split('\\r').join('');
    """, el, text_norm)

def _looks_duplicate(got, want):
    g = (got or "").strip()
    w = (want or "").strip()
    if not g or not w: return False
    if g == w: return False
    if g == w + w or g == (w + "\n" + w) or g == (w + " " + w): return True
    return g.count(w) >= 2

def _fallback_type(driver, el, text_norm):
    try:
        driver.execute_script("arguments[0].click();", el)
        time.sleep(0.05)
        _hard_clear_editor(driver, el)
        actions = ActionChains(driver)
        lines = text_norm.split('\n')
        for i, line in enumerate(lines):
            if line: actions.send_keys(line)
            if i < len(lines) - 1:
                actions.key_down(Keys.SHIFT).send_keys(Keys.ENTER).key_up(Keys.SHIFT)
        actions.perform()
        time.sleep(0.2)
        return True
    except Exception:
        return False

def _clear_composer_if_matches(driver, text_norm):
    try:
        boxes = driver.find_elements(By.CSS_SELECTOR, "div[contenteditable='true'][role='textbox']")
        for el in boxes:
            try:
                aria = (el.get_attribute("aria-label") or "") + " " + (el.get_attribute("aria-placeholder") or "")
                if "caption" in aria.lower(): continue
                got = (el.text or el.get_attribute("innerText") or "").replace("\r", "")
                if got.strip() and (got.strip() == text_norm.strip() or got.strip() == (text_norm.strip() + text_norm.strip())):
                    _hard_clear_editor(driver, el)
            except Exception:
                continue
    except Exception:
        pass

def add_caption(driver, caption):
    if not caption: return True
    caption_norm = driver.execute_script(
        "return String(arguments[0]||'').split('\\r\\n').join('\\n').split('\\r').join('\\n');",
        caption
    ).strip("\n")
    CAPTION_LOCATORS = [
        (By.XPATH, "//div[@role='textbox' and @contenteditable='true' and @data-lexical-editor='true' and @aria-label='Add a caption']"),
        (By.XPATH, "//div[contains(@class,'lexical-rich-text-input')]//div[@contenteditable='true' and @role='textbox' and @data-lexical-editor='true']"),
        (By.XPATH, "//div[@contenteditable='true' and (@aria-label='Add a caption' or @aria-placeholder='Add a caption') and @role='textbox']"),
    ]
    box = None
    for by, sel in CAPTION_LOCATORS:
        try:
            box = WebDriverWait(driver, 10).until(EC.visibility_of_element_located((by, sel)))
            if box.is_displayed() and box.is_enabled():
                break
        except TimeoutException:
            continue
    if not box:
        warn("Caption editor not found; sending without caption")
        return True
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", box)
    driver.execute_script("arguments[0].click();", box)
    time.sleep(0.05)
    _hard_clear_editor(driver, box)
    got = _set_text_content(driver, box, caption_norm).strip('\n')
    if _looks_duplicate(got, caption_norm):
        _hard_clear_editor(driver, box)
        got = _set_text_content(driver, box, caption_norm).strip('\n')
    if not got.strip():
        _fallback_type(driver, box, caption_norm)
    _clear_composer_if_matches(driver, caption_norm)
    return True

# ------------------------- File send -------------------------
def click_attachment_button(driver):
    xpath = "//*[@id='main']/footer/div[1]/div/span/div/div[2]/div/div[1]/button/span"
    try:
        btn = WebDriverWait(driver, 12).until(EC.element_to_be_clickable((By.XPATH, xpath)))
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
        driver.execute_script("arguments[0].click();", btn)
        time.sleep(0.9)
        return True
    except TimeoutException:
        err("Attachment button not found")
        return False

def upload_file_via_input(driver, file_path):
    xpath = "/html/body/div[1]/div/div[1]/span[6]/div/ul/div/div/div[2]/li/div/input"
    try:
        inp = WebDriverWait(driver, 12).until(EC.presence_of_element_located((By.XPATH, xpath)))
        inp.send_keys(file_path)
        return True
    except TimeoutException:
        err("File input not found")
        return False

def click_send_button(driver, wait_secs=14):
    js_click = """
        const sels = [
          'div[role="button"][aria-label="Send"]:not([aria-disabled="true"])',
          'button[aria-label="Send"]:not([aria-disabled="true"])',
          'div[aria-label="Send"]',
          'button[aria-label="Send"]'
        ];
        let el=null;
        for (const s of sels){ el=document.querySelector(s); if(el) break; }
        if(!el) return 'NO_EL';
        el.scrollIntoView({block:'center', inline:'center'});
        const r = el.getBoundingClientRect();
        const cx = Math.floor(r.left + r.width/2), cy = Math.floor(r.top + r.height/2);
        const top = document.elementFromPoint(cx, cy);
        let covered = true, t=top;
        while (t) { if (t === el) { covered = false; break; } t = t.parentElement; }
        if (!covered) { el.click(); return 'CLICKED'; }
        return 'COVERED';
    """
    deadline = time.time() + wait_secs
    while time.time() < deadline:
        try:
            res = driver.execute_script(js_click)
            if res == 'CLICKED':
                return True
        except Exception:
            pass
        time.sleep(0.4)
    try:
        ActionChains(driver).send_keys(Keys.ENTER).perform()
        time.sleep(0.6)
        return True
    except Exception:
        return False

def send_file_to_whatsapp(driver, file_path, caption=None):
    print(f"{DIM}📎 Sending: {Path(file_path).name}{R}")
    if not click_attachment_button(driver): return False
    if not upload_file_via_input(driver, file_path): return False
    time.sleep(2.0)
    if caption:
        add_caption(driver, caption)
    if not click_send_button(driver):
        err("Send button not clickable (overlay/disabled)")
        return False
    time.sleep(1.0)
    return True

# ------------------------- Inputs -------------------------
def load_numbers_from_file(file_path):
    try:
        nums = []
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith('#'): continue
                nums.append(s)
        return nums
    except FileNotFoundError:
        err(f"Numbers file not found: {file_path}"); return []
    except Exception as e:
        err(f"Cannot read numbers file {file_path}: {e}"); return []

def validate_phone_numbers(numbers):
    valid, invalid = [], []
    for num in numbers:
        clean = num.strip().replace('+','').replace('-','').replace(' ','')
        if clean.isdigit() and clean.startswith('966') and len(clean) == 12:
            valid.append(clean)
        else:
            invalid.append(num)
    return valid, invalid

# ------------------------- Bulk -------------------------
def send_bulk_messages(driver, numbers, message=None, file_path=None, file_type=None, caption=None, delay=3.0):
    total = len(numbers)
    successes, fails = [], []

    print(f"\n{CYAN}=== RUN START ==={R}")
    if file_path:
        item("File", f"{Path(file_path).name} ({file_type})")
        if caption:
            first = caption.strip().splitlines()[0] if caption.strip() else ""
            if len(first) > 60: first = first[:60] + "…"
            item("Caption", f"{first}" + (" (…multiline)" if "\n" in caption else ""))
    if message:
        mp = message.strip().splitlines()[0] if message.strip() else ""
        if len(mp) > 60: mp = mp[:60] + "…"
        item("Message", f"{mp}" + (" (…multiline)" if "\n" in message else ""))
    item("Recipients", f"{total}")
    print(f"{CYAN}================={R}\n")

    for idx, number in enumerate(numbers, 1):
        print(f"{CYAN}[{idx}/{total}] → {number}{R}")
        ok_send = False

        for attempt in range(1, 4):
            try:
                if attempt > 1:
                    print(f"{YELL}  ↻ Retry {attempt}/3{R}")

                if message and not file_path:
                    if send_message_via_url(driver, number, message):
                        ok_send = True
                        break
                    else:
                        err("  Message prefill failed; trying DOM path…")
                        driver.get(f"{WA_URL}send?phone={number}")
                        for _ in range(12):
                            time.sleep(1)
                            if check_logged_in(driver): break
                        time.sleep(0.4)
                        if send_message_improved(driver, message):
                            ok_send = True
                            break
                        else:
                            continue
                else:
                    driver.get(f"{WA_URL}send?phone={number}")
                    for _ in range(15):
                        time.sleep(1)
                        if check_logged_in(driver): break
                    time.sleep(0.4)

                    if file_path:
                        if not send_file_to_whatsapp(driver, file_path, caption):
                            err("  Media send failed")
                            continue

                    if message:
                        if not send_message_improved(driver, message):
                            err("  Text send failed")
                            continue

                    ok_send = True
                    break

            except Exception as e:
                err(f"  {e}")
                time.sleep(1.0)

        if ok_send:
            ok("  Delivered\n"); successes.append(number)
        else:
            err("  Failed\n"); fails.append(number)

        if idx < total:
            time.sleep(max(1.0, delay))

    return successes, fails

# ------------------------- Driver + Headless bootstrap -------------------------
def build_driver(headless=False, profile_dir: str = 'whatsup_profile'):
    opts = FirefoxOptions()
    if headless:
        opts.add_argument("-headless")
    profile_path = Path(profile_dir).resolve()
    profile_path.mkdir(parents=True, exist_ok=True)
    opts.add_argument("-profile")
    opts.add_argument(str(profile_path))
    service = Service(GeckoDriverManager().install())
    driver = webdriver.Firefox(service=service, options=opts)
    driver.set_page_load_timeout(120)
    return driver

def close_quietly(driver):
    try: driver.quit()
    except Exception: pass

def start_driver_with_retry(headless: bool, profile_dir: str, attempts: int = 3, pause: float = 1.2):
    last = None
    for _ in range(attempts):
        try:
            return build_driver(headless=headless, profile_dir=profile_dir)
        except SessionNotCreatedException as e:
            last = e
            time.sleep(pause)
        except Exception as e:
            last = e
            time.sleep(pause)
    raise last if last else RuntimeError("Failed to start Firefox driver")

def ensure_logged_in_via_bootstrap(profile_dir: str, first_probe: str | None, login_timeout: int) -> bool:
    """
    Open a visible Firefox with the target profile to let the user scan QR.
    Close it after login is detected. Returns True on success.
    """
    info("Opening visible bootstrap for login")
    try:
        drv = start_driver_with_retry(headless=False, profile_dir=profile_dir)
    except Exception as e:
        err(f"Bootstrap driver failed to start: {e}")
        return False

    try:
        if not wait_for_qr_and_login(drv, first_probe, hard_timeout=login_timeout):
            err("Login not detected in bootstrap")
            return False
        if not check_logged_in(drv):
            err("Login still not detected after bootstrap")
            return False
        ok("Login confirmed in profile")
        return True
    finally:
        close_quietly(drv)
        time.sleep(1.2)  # allow profile locks to release

# ------------------------- Main -------------------------
def main():
    args = parse_arguments()
    run_dir = make_run_dir()

    if args.numbers:
        numbers = [n.strip() for n in args.numbers.split(',') if n.strip()]
    else:
        numbers = load_numbers_from_file(args.numbers_file)

    valid_numbers, invalid_numbers = validate_phone_numbers(numbers)
    if invalid_numbers:
        warn("Invalid numbers (skipped):"); [print(f"  - {n}") for n in invalid_numbers]
    if not valid_numbers:
        err("No valid numbers after validation"); return

    message = resolve_text_arg(args.message)
    caption = resolve_text_arg(args.caption)
    file_path, file_type = validate_file(args.file) if args.file else (None, None)
    if not message and not file_path:
        err("Nothing to send (no message and no file)"); return

    print("\n" + CYAN + "— Pre-flight —" + R)
    item("Valid Numbers", f"{len(valid_numbers)}")
    if file_path: item("File", f"{Path(file_path).name} ({file_type})")
    if caption:
        prev = caption.strip().splitlines()[0] if caption.strip() else ""
        item("Caption", prev[:60] + ("…" if len(prev) > 60 else ""))
    if message:
        prev = message.strip().splitlines()[0] if message.strip() else ""
        item("Message", prev[:60] + ("…" if len(prev) > 60 else ""))
    print()

    try:
        proceed = input("Proceed with sending? (y/n): ").strip().lower()
        if proceed not in ("y", "yes"):
            err("Cancelled"); return
    except KeyboardInterrupt:
        err("Cancelled"); return

    try:
        if args.headless:
            # 1) Ensure login in a visible window
            first_probe = valid_numbers[0] if valid_numbers else None
            if not ensure_logged_in_via_bootstrap(args.profile_dir, first_probe, args.login_timeout):
                err("Login not detected; not sending.")
                return
            # 2) Relaunch headless with the same profile
            info("Starting Firefox (headless)")
            driver = start_driver_with_retry(headless=True, profile_dir=args.profile_dir)
        else:
            # Normal visible run
            info("Starting Firefox")
            driver = start_driver_with_retry(headless=False, profile_dir=args.profile_dir)
            first_probe = valid_numbers[0] if valid_numbers else None
            if not wait_for_qr_and_login(driver, first_probe, hard_timeout=args.login_timeout):
                err("Login not detected; not sending.")
                close_quietly(driver)
                return
            if not check_logged_in(driver):
                err("Login not detected; not sending.")
                close_quietly(driver)
                return

        # Do the sends
        successes, fails = send_bulk_messages(
            driver=driver,
            numbers=valid_numbers,
            message=message,
            file_path=file_path,
            file_type=file_type,
            caption=caption,
            delay=args.delay
        )

        print(f"{CYAN}=== RUN SUMMARY ==={R}")
        item("Total",   f"{len(valid_numbers)}")
        item("Success", f"{len(successes)}")
        item("Failed",  f"{len(fails)}")
        if fails:
            print("Failed Numbers:"); [print(f"  - {n}") for n in fails]
        print(f"{CYAN}==================={R}\n")

        cfg = {
            "args": {
                "numbers": args.numbers,
                "numbers_file": args.numbers_file,
                "file": args.file,
                "delay": args.delay,
                "headless": args.headless,
                "profile_dir": args.profile_dir,
                "login_timeout": args.login_timeout,
            },
            "message_preview": (message or "")[:200],
            "caption_preview": (caption or "")[:200],
        }
        write_run_files(
            run_dir=run_dir,
            stats={"total": len(valid_numbers), "success": len(successes), "failed": len(fails)},
            successes=successes,
            fails=fails,
            cfg=cfg
        )
        ok(f"Run artifacts saved in: {run_dir.resolve()}")
        print("Done.")
        input("Press Enter to close Firefox...")

    except Exception as e:
        err(str(e)); input("Press Enter to close...")

    finally:
        try: driver.quit()
        except Exception: pass

if __name__ == "__main__":
    main()
