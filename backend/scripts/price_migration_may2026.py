"""
One-time script: May 2026 price increase
  - weekly:  $11 → $15
  - monthly: $39.99 → $49.99

For each active subscriber on these plans:
  1. Send a notification email
  2. Migrate their Stripe subscription to the new price (effective next renewal, no immediate charge)
"""

import asyncio
import os
import smtplib
import sys
import stripe
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import db

SUPPORT_EMAIL = "reversepicksx@gmail.com"

NEW_PRICES = {
    "weekly":  {"old": "$11/week",      "new": "$15/week",      "lookup": "reversepicks_weekly_v2",  "amount": 1500, "interval": "week",  "interval_count": 1},
    "monthly": {"old": "$39.99/month",  "new": "$49.99/month",  "lookup": "reversepicks_monthly_v2", "amount": 4999, "interval": "month", "interval_count": 1},
}


def get_or_create_stripe_price(plan_key: str) -> str:
    info = NEW_PRICES[plan_key]
    prices = stripe.Price.list(lookup_keys=[info["lookup"]], expand=["data.product"])
    if prices.data:
        print(f"  [Stripe] Reusing existing price {prices.data[0].id} for {plan_key}")
        return prices.data[0].id
    price = stripe.Price.create(
        unit_amount=info["amount"],
        currency="usd",
        recurring={"interval": info["interval"], "interval_count": info["interval_count"]},
        product_data={"name": f"ReversePicks {plan_key.capitalize()}"},
        lookup_key=info["lookup"],
    )
    print(f"  [Stripe] Created new price {price.id} for {plan_key}")
    return price.id


def build_email(recipient: str, plan_key: str) -> MIMEMultipart:
    info = NEW_PRICES[plan_key]
    plan_name = plan_key.capitalize()

    subject = f"ReversePicks — Upcoming Price Update ({info['new']})"

    plain = f"""Hi,

We wanted to give you a heads-up: the ReversePicks {plan_name} plan is moving from {info['old']} to {info['new']}.

This change takes effect on your next scheduled renewal — you won't be charged anything extra today.

We appreciate your support. The model keeps getting sharper and we're committed to making every pick count.

— ReversePicks
reversepicksx@gmail.com
"""

    html = f"""
<div style="font-family:sans-serif;max-width:600px;margin:0 auto;padding:32px;background:#0a0a0a;color:#fff;border-radius:8px">
  <h2 style="color:#39FF14;margin:0 0 4px">ReversePicks</h2>
  <p style="color:#666;font-size:13px;margin:0 0 28px">Elite Prop Intelligence</p>

  <p style="font-size:15px;line-height:1.6;color:#ddd">Hi,</p>

  <p style="font-size:15px;line-height:1.6;color:#ddd">
    We wanted to give you a heads-up: the ReversePicks
    <strong style="color:#fff">{plan_name} plan</strong>
    is moving from
    <strong style="color:#aaa;text-decoration:line-through">{info['old']}</strong>
    to
    <strong style="color:#39FF14">{info['new']}</strong>.
  </p>

  <div style="background:#111;border:1px solid #222;border-radius:6px;padding:16px 20px;margin:24px 0">
    <p style="margin:0 0 6px;color:#888;font-size:12px;text-transform:uppercase;letter-spacing:1px">What this means for you</p>
    <p style="margin:0;font-size:14px;color:#ddd;line-height:1.6">
      Your current billing period is <strong>not affected</strong>. The new rate of
      <strong style="color:#39FF14">{info['new']}</strong> will apply starting on your
      <strong>next scheduled renewal</strong>. No action needed.
    </p>
  </div>

  <p style="font-size:14px;line-height:1.6;color:#aaa">
    We appreciate your support — the model keeps getting sharper and we're committed
    to making every pick count.
  </p>

  <p style="font-size:14px;color:#aaa;margin-top:28px">
    — ReversePicks<br>
    <a href="mailto:reversepicksx@gmail.com" style="color:#39FF14">reversepicksx@gmail.com</a>
  </p>
</div>
"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SUPPORT_EMAIL
    msg["To"] = recipient
    msg["Reply-To"] = SUPPORT_EMAIL
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))
    return msg


def send_email(smtp_server: smtplib.SMTP, recipient: str, msg: MIMEMultipart) -> bool:
    try:
        smtp_server.sendmail(SUPPORT_EMAIL, recipient, msg.as_string())
        return True
    except Exception as e:
        print(f"  [Email] FAILED to {recipient}: {e}")
        return False


async def run():
    stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not stripe.api_key:
        print("ERROR: STRIPE_SECRET_KEY not set.")
        return

    smtp_password = os.environ.get("GMAIL_APP_PASSWORD", "")
    if not smtp_password:
        print("ERROR: GMAIL_APP_PASSWORD not set.")
        return

    print("=" * 60)
    print("ReversePicks price migration — May 2026")
    print("=" * 60)

    # Pre-create new Stripe prices
    print("\n[1] Creating new Stripe prices...")
    new_price_ids = {}
    for plan_key in ("weekly", "monthly"):
        new_price_ids[plan_key] = get_or_create_stripe_price(plan_key)
    print(f"  Price IDs: {new_price_ids}")

    # Fetch all active subscribers to notify
    print("\n[2] Fetching active subscribers from DB...")
    subscribers = {}
    for plan_key in ("weekly", "monthly"):
        docs = await db.stripe_subscriptions.find(
            {"status": "active", "planKey": plan_key},
            {"_id": 0, "email": 1, "stripeSubscriptionId": 1}
        ).to_list(length=500)
        subscribers[plan_key] = docs
        print(f"  {plan_key}: {len(docs)} active subscribers")

    # Connect to Gmail SMTP once
    print("\n[3] Connecting to Gmail SMTP...")
    smtp = smtplib.SMTP("smtp.gmail.com", 587)
    smtp.starttls()
    smtp.login(SUPPORT_EMAIL, smtp_password)
    print("  Connected.")

    results = {"emails_ok": 0, "emails_fail": 0, "stripe_ok": 0, "stripe_fail": 0}

    # Process each plan
    for plan_key in ("weekly", "monthly"):
        info = NEW_PRICES[plan_key]
        new_price_id = new_price_ids[plan_key]
        docs = subscribers[plan_key]

        print(f"\n[4] Processing {len(docs)} {plan_key} subscribers...")

        for doc in docs:
            email = doc.get("email", "")
            sub_id = doc.get("stripeSubscriptionId", "")
            print(f"  → {email}")

            # Send notification email
            msg = build_email(email, plan_key)
            ok = send_email(smtp, email, msg)
            if ok:
                results["emails_ok"] += 1
                print(f"    [Email] ✓ sent ({info['old']} → {info['new']})")
            else:
                results["emails_fail"] += 1

            # Migrate Stripe subscription to new price (next renewal, no proration)
            if sub_id:
                try:
                    stripe_sub = stripe.Subscription.retrieve(sub_id)
                    item_id = stripe_sub["items"]["data"][0]["id"]
                    stripe.Subscription.modify(
                        sub_id,
                        items=[{"id": item_id, "price": new_price_id}],
                        proration_behavior="none",
                    )
                    results["stripe_ok"] += 1
                    print(f"    [Stripe] ✓ migrated to {new_price_id}")
                except Exception as e:
                    results["stripe_fail"] += 1
                    print(f"    [Stripe] FAILED: {e}")
            else:
                print(f"    [Stripe] SKIP — no stripeSubscriptionId on record")

    smtp.quit()

    print("\n" + "=" * 60)
    print("DONE")
    print(f"  Emails sent:    {results['emails_ok']}")
    print(f"  Emails failed:  {results['emails_fail']}")
    print(f"  Stripe updated: {results['stripe_ok']}")
    print(f"  Stripe failed:  {results['stripe_fail']}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run())
