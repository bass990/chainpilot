import os
from dotenv import load_dotenv

load_dotenv()

MODEL                   = "claude-sonnet-4-6"
MAX_TOKENS              = 4096
POLL_INTERVAL_SECONDS   = 15
STOCK_THRESHOLD_PERCENT = 0.20
PRICE_SPIKE_THRESHOLD   = 0.15
SUPPLIER_DELAY_DAYS     = 3

# AUTO_EXECUTE_BELOW_USD is now managed dynamically by backend/trust_engine.py
# The trust engine starts at $50,000 and adjusts based on recommendation outcomes.
# This constant is kept for reference only.
AUTO_EXECUTE_BELOW_USD  = 50_000

SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
SMTP_SERVER       = os.getenv("SMTP_SERVER", "")
SMTP_PORT         = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER         = os.getenv("SMTP_USER", "")
SMTP_PASS         = os.getenv("SMTP_PASS", "")
SMTP_FROM         = os.getenv("SMTP_FROM", "chainpilot@demo.com")

SEVERITY_LEVELS = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
if not ANTHROPIC_API_KEY:
    raise ValueError("ANTHROPIC_API_KEY not found. Create a .env file with your key.")
