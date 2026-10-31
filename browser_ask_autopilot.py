

import os
import sys
import time
from playwright.sync_api import sync_playwright
from backend.app.auth import create_access_token

token = create_access_token({"sub": "admin", "role": "manager"})
print("Generated token:", token[:25] + "...")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1400, "height": 900})
    page = context.new_page()

    # Pre-populate localStorage with auth tokens before loading page
    page.goto("http://127.0.0.1:8000/")
    page.evaluate(f"""() => {{
        localStorage.setItem("garage_access_token", "{token}");
        localStorage.setItem("garage_user_role", "manager");
        localStorage.setItem("garage_is_logged_in", "true");
        localStorage.setItem("garage_username", "admin");
    }}""")

    # Navigate to admin.html
    print("Navigating to http://127.0.0.1:8000/admin.html...")
    page.goto("http://127.0.0.1:8000/admin.html")
    page.wait_for_load_state("networkidle")

    # Open VTV AutoPilot modal
    print("Opening VTV AutoPilot modal...")
    page.evaluate("() => { if (typeof openAdminAICopilot === 'function') openAdminAICopilot(); }")
    page.wait_for_selector("#modal-ai-result", state="visible", timeout=5000)

    # Type question into modal-ai-question-input
    question = "Xe biển số 30A-999.99 đang sửa đến công đoạn nào rồi?"
    print(f"Typing question: '{question}'...")
    input_selector = "#modal-ai-question-input"
    page.fill(input_selector, question)
    time.sleep(0.5)

    # Click submit
    print("Submitting question...")
    page.click("button[onclick='submitAdminAIQuestion()']")

    # Wait for response in modal-ai-chat-body
    print("Waiting for AI response...")
    # Wait for the AI bubble with response to appear (not the loading spinner)
    page.wait_for_selector("#modal-ai-chat-body .garage-ai-msg-row.ai-msg div:has-text('KẾT QUẢ TRA CỨU'), #modal-ai-chat-body .garage-ai-msg-row.ai-msg div:has-text('BÁO CÁO TIẾN ĐỘ')", timeout=20000)
    time.sleep(1)

    # Take screenshot of the modal card
    modal = page.locator("#modal-ai-result .modal-card")
    screenshot_modal = "autopilot_modal_answered.png"
    modal.screenshot(path=screenshot_modal)
    print(f"Modal screenshot saved to {screenshot_modal}")

    # Full page screenshot
    screenshot_full = "admin_browser_autopilot.png"
    page.screenshot(path=screenshot_full)
    print(f"Full page screenshot saved to {screenshot_full}")

    # Get the text of the AI response
    ai_bubbles = page.locator("#modal-ai-chat-body .garage-ai-msg-row.ai-msg").all()
    if ai_bubbles:
        last_bubble = ai_bubbles[-1]
        print("\n=== AI RESPONSE ON BROWSER ===")
        print(last_bubble.inner_text())

    browser.close()
    print("Playwright automation finished successfully!")
