import os
import sys
sys.stdout.reconfigure(encoding='utf-8')
import time
import shutil
from playwright.sync_api import sync_playwright

print("Starting Playwright automation for Customer GarageAI...")

artifact_dir = r"C:\Users\Duong Ngan\.gemini\antigravity\brain\6f566d49-9e25-4964-90e4-b20b3c762aa7"

with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    context = browser.new_context(viewport={"width": 1400, "height": 900})
    page = context.new_page()

    # Navigate to index.html (Customer Portal)
    print("Navigating to http://127.0.0.1:8000/...")
    page.goto("http://127.0.0.1:8000/")
    page.wait_for_load_state("networkidle")

    # Open GarageAI modal
    print("Opening GarageAI modal...")
    page.evaluate("() => { if (typeof openAIAssistantModal === 'function') openAIAssistantModal('Trợ lý GarageAI', ''); }")
    page.wait_for_selector("#modal-ai-result", state="visible", timeout=5000)
    time.sleep(1)

    # Question from the user's screenshot
    question = "Điều hòa xe tôi có mùi ẩm khi mới bật, nhưng vẫn lạnh. Nên kiểm tra gì trước, có cần nạp gas hoặc tháo taplo ngay không?"
    print(f"Typing question: {question}")
    page.fill("#modal-ai-question-input", question)
    time.sleep(0.5)

    # Click send
    print("Sending question...")
    page.click(".garage-ai-send-btn")

    # Wait for answer
    print("Waiting for AI response...")
    page.wait_for_selector("#modal-ai-chat-body .garage-ai-msg-row.user-msg ~ .garage-ai-msg-row.ai-msg", timeout=35000)
    time.sleep(2)

    # Take modal card screenshot
    modal = page.locator("#modal-ai-result .modal-card")
    modal_img = "customer_modal_answered.png"
    modal.screenshot(path=modal_img)
    print(f"Saved modal screenshot to {modal_img}")

    # Full page screenshot
    full_img = "customer_full_answered.png"
    page.screenshot(path=full_img)
    print(f"Saved full page screenshot to {full_img}")

    # Copy to artifact directory
    if os.path.exists(artifact_dir):
        dest_modal = os.path.join(artifact_dir, "customer_modal_answered.png")
        shutil.copy2(modal_img, dest_modal)
        dest_full = os.path.join(artifact_dir, "customer_full_answered.png")
        shutil.copy2(full_img, dest_full)
        print("Copied screenshots to artifact directory.")

    # Print the last AI response text
    ai_msgs = page.locator("#modal-ai-chat-body .garage-ai-msg-row.ai-msg").all()
    if ai_msgs:
        print("\n=== AI RESPONSE ON BROWSER ===")
        print(ai_msgs[-1].inner_text())

    browser.close()
    print("\nPlaywright automation completed successfully!")
