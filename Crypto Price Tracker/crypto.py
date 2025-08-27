import os
import smtplib
import requests
import pandas as pd
import matplotlib.pyplot as plt
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

import streamlit as st
from streamlit_autorefresh import st_autorefresh

COINGECKO_SIMPLE_PRICE = (
    "https://api.coingecko.com/api/v3/simple/price"
)

DEFAULT_COINS = [
    "bitcoin",
    "ethereum",
    "tether",
    "binancecoin",
    "ripple",
    "cardano",
    "dogecoin",
]
SUPPORTED_FIAT = ["usd", "inr", "eur"]
ALERT_LOG_FILE = "alerts.log"
HISTORY_LEN = 300
st.set_page_config(page_title="Crypto Price Tracker", layout="wide")

@st.cache_data(ttl=10)
def fetch_prices(coin_ids: list, vs_currency: str = "usd") -> dict:
    """Fetch current price & 24h change using CoinGecko simple/price.
    Cached for 10 seconds to be gentle on the API.
    """
    if not coin_ids:
        return {}
    params = {
        "ids": ",".join(coin_ids),
        "vs_currencies": vs_currency,
        "include_24hr_change": "true",
    }
    try:
        r = requests.get(COINGECKO_SIMPLE_PRICE, params=params, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"Failed to fetch prices: {e}")
        return {}


def send_email_alert(subject: str, body: str,
                     smtp_server: str,
                     smtp_port: int,
                     sender: str,
                     password: str,
                     receiver: str):
    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = receiver
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP_SSL(smtp_server, int(smtp_port)) as server:
        server.login(sender, password)
        server.sendmail(sender, receiver, msg.as_string())


def log_alert(message: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {message}\n"
    with open(ALERT_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line)


def get_env_or_default(key: str, default: str = "") -> str:
    val = os.environ.get(key)
    return val if val is not None else default


with st.sidebar:
    st.title("⚙️ Settings")

    coins = st.multiselect(
        "Select coins (CoinGecko IDs)",
        options=DEFAULT_COINS,
        default=["bitcoin", "ethereum", "dogecoin"],
        help="Use CoinGecko coin IDs. You can type to add other valid IDs."
    )
    # Allow adding custom coin IDs
    custom_coin = st.text_input("Add custom coin ID (press Enter)")
    if custom_coin:
        if custom_coin not in coins:
            coins.append(custom_coin.lower())
        st.experimental_rerun()

    vs_currency = st.selectbox("Currency", SUPPORTED_FIAT, index=0)

    refresh_sec = st.slider("Auto-refresh (seconds)", 5, 300, 20)
    st.caption("Note: API responses are cached for ~10s to avoid rate limits.")

    st.divider()
    st.subheader("📧 Email Alerts")
    use_env = st.checkbox("Use environment variables for SMTP creds", value=True)

    if use_env:
        smtp_server = get_env_or_default("SMTP_SERVER", "smtp.gmail.com")
        smtp_port = int(get_env_or_default("SMTP_PORT", "465") or 465)
        email_sender = get_env_or_default("EMAIL_SENDER", "")
        email_password = get_env_or_default("EMAIL_PASSWORD", "")
        email_receiver = get_env_or_default("EMAIL_RECEIVER", "")
        st.write(":bulb: Set SMTP_SERVER, SMTP_PORT, EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECEIVER in your environment for best security.")
    else:
        smtp_server = st.text_input("SMTP Server", value="smtp.gmail.com")
        smtp_port = st.number_input("SMTP Port", value=465)
        email_sender = st.text_input("Sender Email")
        email_password = st.text_input("Email Password / App Password", type="password")
        email_receiver = st.text_input("Receiver Email")

    st.caption("Gmail requires an App Password with 2FA enabled, or use your provider's SMTP.")

    # Test email button
    if st.button("Send Test Email"):
        if not all([smtp_server, smtp_port, email_sender, email_password, email_receiver]):
            st.error("Please fill in all email fields before testing.")
        else:
            try:
                send_email_alert(
                    subject="Crypto Tracker Test Email",
                    body="This is a test email from your Crypto Price Tracker.",
                    smtp_server=smtp_server,
                    smtp_port=int(smtp_port),
                    sender=email_sender,
                    password=email_password,
                    receiver=email_receiver,
                )
                st.success("Test email sent!")
            except Exception as e:
                st.error(f"Failed to send test email: {e}")

# -------------------- Main Layout --------------------
st.title("🪙 Crypto Price Tracker")
colA, colB = st.columns([3, 2])

if "history" not in st.session_state:
    st.session_state.history = {}
if "last_alert_state" not in st.session_state:
    # Track last condition to avoid repeat spamming: {coin: {"above": bool, "below": bool}}
    st.session_state.last_alert_state = {}

# Editable thresholds table
st.subheader("🎯 Alert Thresholds")
if "thresholds" not in st.session_state:
    # DataFrame: coin | lower | upper
    df_init = pd.DataFrame({
        "coin": coins if coins else [],
        "lower": [None] * len(coins),
        "upper": [None] * len(coins),
    })
    st.session_state.thresholds = df_init
else:
    # Ensure new coins appear in the table
    existing = set(st.session_state.thresholds["coin"].tolist())
    for c in coins:
        if c not in existing:
            st.session_state.thresholds = pd.concat([
                st.session_state.thresholds,
                pd.DataFrame({"coin": [c], "lower": [None], "upper": [None]})
            ], ignore_index=True)

editable_df = st.data_editor(
    st.session_state.thresholds,
    num_rows="dynamic",
    use_container_width=True,
    hide_index=True,
)
# Persist the edited thresholds
st.session_state.thresholds = editable_df

st.divider()

# Fetch prices
price_data = fetch_prices(coins, vs_currency=vs_currency)

# Build a display table
rows = []
now = datetime.now()
for c in coins:
    d = price_data.get(c, {})
    price = d.get(vs_currency)
    change_24h = d.get(f"{vs_currency}_24h_change")

    # Update in-session history for trend lines
    hist = st.session_state.history.get(c, [])
    if price is not None:
        hist.append(price)
        if len(hist) > HISTORY_LEN:
            hist = hist[-HISTORY_LEN:]
        st.session_state.history[c] = hist

    rows.append({
        "coin": c,
        f"price ({vs_currency})": price,
        "24h change %": round(change_24h, 3) if change_24h is not None else None,
        "updated": now.strftime("%H:%M:%S"),
    })

prices_df = pd.DataFrame(rows)

# Display current prices
with colA:
    st.subheader("📊 Live Prices")
    st.dataframe(prices_df, use_container_width=True)

# Charts
with colB:
    st.subheader("📈 Trends (session)")
    if not coins:
        st.info("Select at least one coin in the sidebar.")
    else:
        # Draw a small line chart per coin using matplotlib
        for c in coins:
            hist = st.session_state.history.get(c, [])
            if len(hist) < 2:
                continue
            fig, ax = plt.subplots()
            ax.plot(hist)
            ax.set_title(f"{c} — last {len(hist)} pts")
            ax.set_xlabel("Time steps")
            ax.set_ylabel(f"Price ({vs_currency.upper()})")
            st.pyplot(fig)

# -------------------- Alert Logic --------------------
triggered_alerts = []

# Merge thresholds into lookup
th_map = {row["coin"]: row for _, row in st.session_state.thresholds.iterrows()}

for c in coins:
    d = price_data.get(c, {})
    price = d.get(vs_currency)
    if price is None:
        continue

    th = th_map.get(c)
    if th is None:
        continue

    lower = th.get("lower")
    upper = th.get("upper")

    # Prepare last state
    last_state = st.session_state.last_alert_state.get(c, {"above": False, "below": False})

    crossed_below = (lower is not None) and (price <= float(lower))
    crossed_above = (upper is not None) and (price >= float(upper))

    # Trigger only on edge crossing (not continuous)
    if crossed_below and not last_state.get("below", False):
        msg = f"{c} at {price} {vs_currency.upper()} is <= lower threshold {lower}"
        triggered_alerts.append((c, msg))
        last_state["below"] = True
    elif not crossed_below:
        last_state["below"] = False

    if crossed_above and not last_state.get("above", False):
        msg = f"{c} at {price} {vs_currency.upper()} is >= upper threshold {upper}"
        triggered_alerts.append((c, msg))
        last_state["above"] = True
    elif not crossed_above:
        last_state["above"] = False

    st.session_state.last_alert_state[c] = last_state

# Display & act on alerts
if triggered_alerts:
    st.warning("\n".join([m for _, m in triggered_alerts]))

    # Log to file + send email(s)
    for c, message in triggered_alerts:
        log_alert(message)
        # Email if configured
        if all([smtp_server, smtp_port, email_sender, email_password, email_receiver]):
            try:
                send_email_alert(
                    subject=f"Crypto Alert: {c}",
                    body=message,
                    smtp_server=smtp_server,
                    smtp_port=int(smtp_port),
                    sender=email_sender,
                    password=email_password,
                    receiver=email_receiver,
                )
                st.toast(f"Email sent for {c}")
            except Exception as e:
                st.error(f"Email failed for {c}: {e}")
        else:
            st.info("Email not sent (missing SMTP configuration).")

# -------------------- Alert Log Viewer --------------------
st.divider()
st.subheader("📝 Alert Log (latest 50 lines)")
if os.path.exists(ALERT_LOG_FILE):
    try:
        with open(ALERT_LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        tail = lines[-50:]
        st.code("".join(tail) if tail else "<empty>")
    except Exception as e:
        st.error(f"Failed to read log: {e}")
else:
    st.caption("No alerts logged yet.")

# -------------------- Auto Refresh --------------------
# Refresh every N seconds (user sets from sidebar)
st_autorefresh(interval=refresh_sec * 1000, key="crypto-refresh")
