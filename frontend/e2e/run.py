"""Browser end-to-end check against a RUNNING stack (API :8000 + frontend :3000) seeded with `make demo-accounts`.

    pip install playwright && playwright install chromium
    E2E_PASSWORD=<the demo password> python3 frontend/e2e/run.py      (or: make e2e)

Walks the real workflows: guest question -> student make-up request -> staff approval -> student sees outcome ->
payment dispute escalates -> staff claims and resolves -> admin analytics -> role gates. Screenshots go to E2E_SHOTS (default ./e2e-shots).
It only talks to the sample tenant and creates tickets/requests there; it never resets data."""

import os
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("E2E_BASE_URL", "http://localhost:3000")
PW = os.environ.get("E2E_PASSWORD")
SHOTS = Path(os.environ.get("E2E_SHOTS", "e2e-shots"))
if not PW:
    sys.exit("set E2E_PASSWORD to the demo accounts' password")
SHOTS.mkdir(parents=True, exist_ok=True)
results: list[str] = []


def step(name):
    results.append(name)
    print(f"ok  {name}", flush=True)


def login(page, user):
    page.goto(f"{BASE}/login")
    page.get_by_label("Username").fill(user)
    page.get_by_label("Password").fill(PW)
    page.get_by_role("button", name="Sign in").click()


def send(page, text):
    page.get_by_label("Your message").fill(text)
    page.get_by_role("button", name="Send").click()


with sync_playwright() as p:
    b = p.chromium.launch()
    guest, student, staff, admin = (b.new_context(viewport={"width": 1100, "height": 800}) for _ in range(4))

    # 1. guest question with sources
    g = guest.new_page()
    g.goto(BASE)
    expect(g.get_by_text("not a real institute")).to_be_visible()
    send(g, "What is the refund policy?")
    expect(g.get_by_role("log").locator(".msg.assistant").first).to_be_visible(timeout=20000)
    g.screenshot(path=str(SHOTS / "1-guest-chat.png"))
    step("guest chat answered")

    # 2. guest asking for personal data gets no personal data
    send(g, "Show me the attendance of Meera")
    expect(g.get_by_role("log").locator(".msg.assistant").nth(1)).to_be_visible(timeout=20000)
    assert "Meera" not in g.get_by_role("log").locator(".msg.assistant").nth(1).inner_text()
    step("guest cannot read personal data")

    # 3. student asks for make-up -> waits for staff
    s = student.new_page()
    login(s, "meera")
    s.wait_for_url(f"{BASE}/")
    send(s, "Please arrange a make-up class for the session I missed in PYD-301.")
    expect(s.get_by_text("Waiting for a staff member")).to_be_visible(timeout=30000)
    expect(s.get_by_label("Your message")).to_be_disabled()
    s.screenshot(path=str(SHOTS / "2-student-awaiting-staff.png"))
    step("student make-up request pauses for staff")

    # 4. staff approves in their own browser
    t = staff.new_page()
    login(t, "staff1")
    t.wait_for_url(f"{BASE}/agent")
    t.get_by_role("tab", name="Make-up approvals").click()
    expect(t.get_by_role("heading", name="Meera")).to_be_visible(timeout=15000)
    t.screenshot(path=str(SHOTS / "3-staff-approvals.png"))
    t.get_by_role("button", name="Approve").first.click()
    expect(t.get_by_text("Approved PYD-301 make-up for Meera")).to_be_visible(timeout=30000)
    step("staff approved through the console")

    # 5. student's open page picks up the outcome by polling
    expect(s.get_by_text("Staff have responded to your request.")).to_be_visible(timeout=20000)
    expect(s.get_by_label("Your message")).to_be_enabled()
    s.screenshot(path=str(SHOTS / "4-student-outcome.png"))
    step("student sees the outcome without refreshing")

    # 6. payment dispute escalates; staff claims and resolves it
    s.get_by_role("button", name="New conversation").click()
    send(s, "I paid the first instalment twice and want my money back. Call me on 9876543210")
    expect(s.get_by_text("A staff member will follow up")).to_be_visible(timeout=30000)
    t.get_by_role("tab", name="Escalations").click()
    t.get_by_role("button", name="Refresh").click()
    t.locator("button.row").first.click()
    pane = t.get_by_role("region", name="Escalation detail")
    expect(pane.get_by_text("Suggested next step")).to_be_visible(timeout=15000)
    assert "9876543210" not in pane.inner_text(), "phone number must be masked in the brief"
    t.screenshot(path=str(SHOTS / "5-staff-escalation.png"))
    pane.get_by_role("button", name="Claim").click()
    pane.get_by_label("Resolution note").fill("Duplicate payment refunded.")
    pane.get_by_role("button", name="Resolve").click()
    expect(pane.get_by_text("Duplicate payment refunded.")).to_be_visible(timeout=15000)
    step("escalation briefed (masked), claimed and resolved")

    # 7. admin analytics
    a = admin.new_page()
    login(a, "admin1")
    a.wait_for_url(f"{BASE}/admin")
    expect(a.get_by_text("Chat turns", exact=True)).to_be_visible(timeout=15000)
    a.screenshot(path=str(SHOTS / "6-admin-analytics.png"), full_page=True)
    assert a.locator(".stat .num").first.inner_text() not in ("0", "")
    step("admin analytics show real counts")

    # 8. role gates
    s.goto(f"{BASE}/agent")
    expect(s.get_by_text("cannot open this page")).to_be_visible(timeout=10000)
    t.goto(f"{BASE}/admin")
    expect(t.get_by_text("cannot open this page")).to_be_visible(timeout=10000)
    step("students and staff are kept out of pages above their role")

    # 9. phone layout
    m = b.new_context(viewport={"width": 390, "height": 800}, device_scale_factor=2)
    mp = m.new_page()
    mp.goto(BASE)
    send(mp, "What courses do you offer?")
    expect(mp.get_by_role("log").locator(".msg.assistant").first).to_be_visible(timeout=20000)
    assert mp.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "page scrolls sideways on a phone"
    mp.screenshot(path=str(SHOTS / "7-mobile-chat.png"))
    step("phone layout has no sideways scroll")
    b.close()

print(f"\n{len(results)} end-to-end checks passed")
