import discord
from discord import app_commands
import asyncio
import aiosqlite
import logging
import dateparser
import pytz
import os
import time
import random
import socket
from datetime import datetime, timedelta
from flask import Flask
import threading
from dotenv import load_dotenv
from typing import Optional, Tuple, Dict, Union, Any
import re
from fuzzywuzzy import process

# ---------------------------
# 🔹 Environment Configuration
# ---------------------------
load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not TOKEN:
    raise ValueError("DISCORD_BOT_TOKEN is missing from environment variables")

# ---------------------------
# 🔹 Constants & Timeouts
# ---------------------------
DISCORD_TIMEOUT = 60
HEARTBEAT_TIMEOUT = 60
COMMAND_TIMEOUT = 3
RATE_LIMIT_DELAY = 300
INITIAL_RETRY_DELAY = 60
MAX_RETRIES = 3
DB_TIMEOUT = 30
BASE_PORT = int(os.getenv("PORT", 8080))

# ---------------------------
# 🔹 Timezone Mappings
# ---------------------------
COMMON_TIMEZONE_MAPPINGS = {
    # North America
    "EST": "America/New_York",
    "EDT": "America/New_York",
    "CST": "America/Chicago",
    "CDT": "America/Chicago",
    "MST": "America/Denver",
    "MDT": "America/Denver",
    "PST": "America/Los_Angeles",
    "PDT": "America/Los_Angeles",
    # Europe
    "GMT": "Europe/London",
    "BST": "Europe/London",
    "CET": "Europe/Paris",
    "CEST": "Europe/Paris",
    # Asia/Pacific
    "JST": "Asia/Tokyo",
    "AEST": "Australia/Sydney",
    "AEDT": "Australia/Sydney",
    # Common variations
    "CENTRAL": "America/Chicago",
    "EASTERN": "America/New_York",
    "PACIFIC": "America/Los_Angeles",
    "MOUNTAIN": "America/Denver"
}

VALID_TIME_FORMATS = [
    "HH:MM",
    "H:MM AM/PM",
    "YYYY-MM-DD HH:MM",
    "MM/DD HH:MM",
    "tomorrow HH:MM",
    "in X hours/minutes"
]

DEFAULT_TIMEZONES = {
    "UTC": "UTC",
    "🇺🇸 New York": "America/New_York",
    "🇺🇸 Los Angeles": "America/Los_Angeles",
    "🇬🇧 London": "Europe/London",
    "🇩🇪 Berlin": "Europe/Berlin",
    "🇯🇵 Tokyo": "Asia/Tokyo",
    "🇦🇺 Sydney": "Australia/Sydney"
}

# ---------------------------
# 🔹 Logging Configuration
# ---------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("discord")

# ---------------------------
# 🔹 Utility Functions
# ---------------------------
def get_random_delay(base_delay: float, jitter: float = 0.1) -> float:
    """Add jitter to delay to prevent thundering herd"""
    return base_delay * (1 + random.uniform(-jitter, jitter))

async def safe_defer(interaction: discord.Interaction, ephemeral: bool = False) -> bool:
    """Safely defer an interaction with error handling"""
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=ephemeral)
            return True
    except Exception as e:
        logger.error(f"Error deferring interaction: {e}")
    return False

async def safe_followup(interaction: discord.Interaction, 
                       content: Optional[str] = None, 
                       embed: Optional[discord.Embed] = None,
                       ephemeral: bool = False) -> bool:
    """Safely send a followup message with error handling"""
    try:
        await interaction.followup.send(
            content=content,
            embed=embed,
            ephemeral=ephemeral
        )
        return True
    except Exception as e:
        logger.error(f"Error sending followup: {e}")
        return False
# ---------------------------
# 🔹 Web Server Management
# ---------------------------
class WebServer:
    def __init__(self):
        self.app = Flask(__name__)
        self.thread = None
        self.port = BASE_PORT
        self._running = False
        self._setup_routes()

    def _setup_routes(self):
        @self.app.route('/')
        def home():
            return "✅ Bot is running!"

        @self.app.route('/health')
        def health():
            return {"status": "healthy", "timestamp": datetime.now().isoformat()}

    def find_available_port(self) -> int:
        """Find an available port starting from the base port"""
        for port_offset in range(10):
            test_port = self.port + port_offset
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(('', test_port))
                    return test_port
            except OSError:
                continue
        raise OSError("No available ports found")

    def start(self):
        """Start the web server in a separate thread with error handling"""
        if self._running:
            logger.info("Web server already running")
            return

        def run_server():
            try:
                available_port = self.find_available_port()
                logger.info(f"Starting web server on port {available_port}")
                self.app.run(
                    host="0.0.0.0",
                    port=available_port,
                    threaded=True,
                    use_reloader=False
                )
            except Exception as e:
                logger.error(f"Web server error: {e}")
                self._running = False

        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=run_server, daemon=True)
            self._running = True
            self.thread.start()
            logger.info("Web server thread started")

    def stop(self):
        """Stop the web server gracefully"""
        self._running = False
        if self.thread and self.thread.is_alive():
            logger.info("Stopping web server...")
            self.thread = None

def create_web_server(port: int) -> Flask:
    """Create and configure Flask app"""
    app = Flask(__name__)
    
    @app.route('/')
    def home():
        return "✅ Bot is running!"
    
    @app.route('/health')
    def health():
        return {"status": "healthy", "timestamp": datetime.now().isoformat()}
    
    return app

def run_flask(app: Flask, port: int):
    """Run Flask app with the specified port"""
    try:
        app.run(host='0.0.0.0', port=port, use_reloader=False)
    except Exception as e:
        logger.error(f"Flask server error: {e}")

def start_web_server(port: int) -> threading.Thread:
    """Start web server in a daemon thread"""
    app = create_web_server(port)
    server_thread = threading.Thread(
        target=run_flask,
        args=(app, port),
        daemon=True
    )
    server_thread.start()
    return server_thread

# ---------------------------
# 🔹 Rate Limit Handler
# ---------------------------
class RateLimitHandler:
    def __init__(self):
        self.reset_time = datetime.now()
        self.retry_count = 0
        self.last_attempt = datetime.now()

    def should_retry(self, error: Exception) -> bool:
        """Determine if operation should be retried based on error"""
        if isinstance(error, discord.errors.HTTPException):
            current_time = datetime.now()
            
            if error.status == 429:
                self.retry_count += 1
                if self.retry_count > MAX_RETRIES:
                    logger.error("Maximum retry attempts reached")
                    return False
                
                retry_after = getattr(error, 'retry_after', RATE_LIMIT_DELAY)
                delay = retry_after + get_random_delay(30 * self.retry_count)
                
                self.reset_time = current_time + timedelta(seconds=delay)
                logger.info(f"Rate limited. Waiting {delay:.2f} seconds before retry")
                return True
                
            elif error.status >= 500:
                self.retry_count += 1
                if self.retry_count > MAX_RETRIES:
                    return False
                    
                delay = INITIAL_RETRY_DELAY * (2 ** (self.retry_count - 1))
                self.reset_time = current_time + timedelta(
                    seconds=get_random_delay(delay)
                )
                return True
                
        return False

    def get_wait_time(self) -> float:
        """Get time to wait before next attempt"""
        if self.reset_time > datetime.now():
            return (self.reset_time - datetime.now()).total_seconds()
        return get_random_delay(INITIAL_RETRY_DELAY)
# ---------------------------
# 🔹 Timezone Handler
# ---------------------------
class TimezoneHandler:
    @staticmethod
    async def find_timezone(user_input: str) -> Tuple[Optional[str], list]:
        """Find the closest matching timezone with improved fuzzy matching"""
        try:
            user_input = user_input.strip().upper()
            
            # Check common abbreviations first
            if user_input in COMMON_TIMEZONE_MAPPINGS:
                return COMMON_TIMEZONE_MAPPINGS[user_input], []

            # Check for exact matches in pytz timezones
            if user_input in pytz.all_timezones:
                return user_input, []

            # Run fuzzy matching in a thread pool to avoid blocking
            def do_fuzzy_match():
                # Check for fuzzy matches in common names
                common_match = process.extractOne(
                    user_input,
                    COMMON_TIMEZONE_MAPPINGS.keys(),
                    score_cutoff=85
                )
                if common_match:
                    return COMMON_TIMEZONE_MAPPINGS[common_match[0]], []

                # Fuzzy match against all pytz timezones
                matches = process.extract(user_input, pytz.all_timezones, limit=3)
                if matches and matches[0][1] >= 85:
                    return matches[0][0], [m[0] for m in matches[1:]]
                
                return None, [m[0] for m in matches]

            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, do_fuzzy_match)
            
        except Exception as e:
            logger.error(f"Error in find_timezone: {e}")
            return None, []

# ---------------------------
# 🔹 Time Parser
# ---------------------------
class TimeParser:
    @staticmethod
    async def parse_time(input_text: str, base_timezone: str) -> Optional[datetime]:
        """Enhanced time parsing with timezone awareness"""
        try:
            # Clean input
            input_text = re.sub(r'\s+', ' ', input_text.strip())
            
            # Parse with user's timezone
            tz = pytz.timezone(base_timezone)
            settings = {
                'RELATIVE_BASE': datetime.now(tz),
                'TIMEZONE': base_timezone,
                'RETURN_AS_TIMEZONE_AWARE': True,
                'PREFER_DATES_FROM': 'future'
            }
            
            # Run parsing in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            parsed_dt = await loop.run_in_executor(
                None,
                lambda: dateparser.parse(input_text, settings=settings)
            )
            
            if parsed_dt:
                return parsed_dt.astimezone(pytz.UTC)
            return None
        except Exception as e:
            logger.error(f"Time parsing error: {e}")
            return None

# ---------------------------
# 🔹 Database Management
# ---------------------------
class TimezoneDB:
    def __init__(self, db_path: str = "timezones.db"):
        self.db_path = db_path
        self.timezone_handler = TimezoneHandler()

    async def setup(self):
        """Initialize database with proper indices"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY,
                        timezone TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                await db.commit()
                logger.info("Database initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

    async def get_timezone(self, user_id: int) -> Optional[str]:
        """Get user timezone with error handling"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute(
                    "SELECT timezone FROM users WHERE user_id = ?", 
                    (user_id,)
                ) as cursor:
                    result = await cursor.fetchone()
                    return result[0] if result else None
        except Exception as e:
            logger.error(f"Database error in get_timezone: {e}")
            return None

    async def set_timezone(self, user_id: int, timezone_input: str) -> Tuple[bool, Optional[str], list]:
        """Set user timezone with enhanced matching"""
        try:
            matched_timezone, suggestions = await self.timezone_handler.find_timezone(timezone_input)
            
            if matched_timezone:
                try:
                    async with aiosqlite.connect(self.db_path) as db:
                        await db.execute("""
                            INSERT INTO users (user_id, timezone) 
                            VALUES (?, ?) 
                            ON CONFLICT(user_id) 
                            DO UPDATE SET 
                                timezone = ?,
                                updated_at = CURRENT_TIMESTAMP
                        """, (user_id, matched_timezone, matched_timezone))
                        await db.commit()
                    return True, matched_timezone, []
                except Exception as e:
                    logger.error(f"Database error in set_timezone: {e}")
                    return False, None, []
            
            return False, None, suggestions
        except Exception as e:
            logger.error(f"Error in set_timezone: {e}")
            return False, None, []
# ---------------------------
# 🔹 Bot Implementation
# ---------------------------
class WhatTimeBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.dm_messages = True
        super().__init__(intents=intents)
        
        self.tree = app_commands.CommandTree(self)
        self.db = TimezoneDB()
        self.time_parser = TimeParser()

    async def setup_hook(self):
        """This is called when the bot starts, before it connects to Discord"""
        await self.db.setup()  # Initialize database
        await self.register_commands()  # Register commands
        try:
            await self.tree.sync()  # Sync commands when bot starts
            logger.info("✅ Successfully synced commands!")
        except Exception as e:
            logger.error(f"🚨 Command sync failed: {e}")

    def format_time_conversions(self, dt: datetime) -> str:
        """Format time conversions for all default timezones"""
        conversions = []
        for name, tz_str in DEFAULT_TIMEZONES.items():
            local_time = dt.astimezone(pytz.timezone(tz_str))
            # Add animated clock emoji based on hour
            clock_hour = local_time.hour % 12 or 12
            clock_emoji = f":clock{clock_hour}:"
            conversions.append(f"{clock_emoji} **{name}**: {local_time.strftime('%H:%M %Z')} ({local_time.strftime('%m/%d')})")
        return "\n".join(conversions)

    async def on_ready(self):
        """Called when the bot successfully connects"""
        logger.info(f"🤖 Logged in as {self.user} (ID: {self.user.id})")
        logger.info("🎭 Bot is now ready and waiting for commands!")

    async def register_commands(self):
        """Register slash commands"""
        @self.tree.command(
            name="timezone",
            description="Set your timezone for event time conversions"
        )
        @app_commands.describe(
            timezone="Your timezone (e.g., 'CST', 'EST', 'America/Chicago')"
        )
        async def timezone_command(interaction: discord.Interaction, timezone: str):
            """Set timezone with improved matching - all messages are ephemeral"""
            try:
                await interaction.response.defer(ephemeral=True)
                success, matched_timezone, suggestions = await self.db.set_timezone(
                    interaction.user.id,
                    timezone
                )

                if success:
                    embed = discord.Embed(
                        title="✅ Timezone Updated",
                        description=(
                            f"Your timezone has been set to **{matched_timezone}**\n"
                            f"Input recognized: `{timezone}`\n\n"
                            f"You can now use `/event` to convert times!"
                        ),
                        color=discord.Color.green()
                    )
                    await interaction.followup.send(embed=embed, ephemeral=True)
                else:
                    if suggestions:
                        suggestions_text = "\n".join([f"• `{tz}`" for tz in suggestions])
                        embed = discord.Embed(
                            title="❌ Invalid Timezone",
                            description=(
                                f"Couldn't find a timezone matching `{timezone}`.\n\n"
                                f"Did you mean one of these?\n{suggestions_text}\n\n"
                                "You can also use common abbreviations like `CST`, `EST`, `PST`"
                            ),
                            color=discord.Color.red()
                        )
                    else:
                        embed = discord.Embed(
                            title="❌ Invalid Timezone",
                            description=(
                                "Please enter a valid timezone.\n"
                                "Examples: `CST`, `EST`, `America/Chicago`, `Europe/London`"
                            ),
                            color=discord.Color.red()
                        )
                    await interaction.followup.send(embed=embed, ephemeral=True)
            except Exception as e:
                logger.error(f"Error in timezone command: {e}")
                await safe_followup(interaction, 
                    "❌ An error occurred while processing your request.",
                    ephemeral=True
                )

        @self.tree.command(
            name="event",
            description="Convert an event time to different time zones"
        )
        @app_commands.describe(
            details="Time to convert (e.g., '3pm tomorrow', '15:00', 'in 2 hours')"
        )
        async def event_command(interaction: discord.Interaction, details: str):
            """Handle time conversion requests"""
            try:
                await interaction.response.defer(ephemeral=True)
                
                user_timezone = await self.db.get_timezone(interaction.user.id)
                
                if not user_timezone:
                    embed = discord.Embed(
                        title="❌ Timezone Required",
                        description=(
                            "Please set your timezone first using `/timezone`\n"
                            "Example: `/timezone CST` or `/timezone America/Chicago`"
                        ),
                        color=discord.Color.red()
                    )
                    await interaction.followup.send(embed=embed, ephemeral=True)
                    return

                # If we have a timezone, delete the ephemeral message and continue with public messages
                await interaction.delete_original_response()

                # Parse the time
                parsed_time = await self.time_parser.parse_time(details, user_timezone)
                if not parsed_time:
                    formats_text = "\n".join([f"• `{fmt}`" for fmt in VALID_TIME_FORMATS])
                    embed = discord.Embed(
                        title="❌ Invalid Time Format",
                        description=(
                            f"Couldn't understand the time format. Try these:\n{formats_text}\n\n"
                            "Examples:\n"
                            "• `/event 3pm tomorrow`\n"
                            "• `/event in 2 hours`\n"
                            "• `/event 15:00`"
                        ),
                        color=discord.Color.red()
                    )
                    await interaction.followup.send(embed=embed, ephemeral=True)
                    return

                # Add animated loading message
                loading_msg = await interaction.followup.send("🔄 Converting time zones...", ephemeral=False)
                await asyncio.sleep(1)  # Short delay for effect
                
                # Create response embed
                event_conversions = self.format_time_conversions(parsed_time)
                local_time = parsed_time.astimezone(pytz.timezone(user_timezone))
                
                embed = discord.Embed(
                    title="🌍 Global Time Conversion",
                    description=(
                        f"**🕒 Event Time ({user_timezone})** → "
                        f"`{local_time.strftime('%b %d, %H:%M %Z')}`\n\n"
                        f"{event_conversions}"
                    ),
                    color=discord.Color.blue()
                )
                
                embed.set_footer(text=f"🎯 Requested by {interaction.user.name} • ⚡ Instant conversion")
                
                # Add some flair with random tips
                tips = [
                    "💡 Tip: You can use '3pm tomorrow' or 'in 2 hours'!",
                    "💡 Tip: Try using '15:00' for 24-hour format!",
                    "💡 Tip: You can specify dates like 'next Monday 2pm'!",
                    "💡 Tip: Use '/timezone' to update your timezone!",
                ]
                embed.add_field(name="", value=random.choice(tips), inline=False)
                
                try:
                    # Delete loading message and send final embed
                    await loading_msg.delete()
                    await interaction.followup.send(embed=embed, ephemeral=False)
                except discord.NotFound:
                    # If the loading message is already gone, just send the embed
                    await interaction.followup.send(embed=embed, ephemeral=False)

            except Exception as e:
                logger.error(f"Error in event command: {e}")
                await safe_followup(interaction,
                    "❌ An error occurred while processing your request.",
                    ephemeral=True
                )
# ---------------------------
# 🔹 Main Execution
# ---------------------------
def main():
    """Main entry point with error handling and retry logic"""
    retry_count = 0
    base_delay = INITIAL_RETRY_DELAY
    port = int(os.getenv("PORT", 8080))
    
    while retry_count < MAX_RETRIES:
        try:
            # Start web server before bot
            server_thread = start_web_server(port)
            logger.info("✅ Web server started successfully")

            # Create and start bot
            bot = WhatTimeBot()
            
            # Start the bot
            asyncio.run(bot.start(TOKEN))
            break  # Exit loop on success

        except Exception as e:
            retry_count += 1
            logger.error(f"❌ Error on attempt {retry_count}: {e}")
            
            if retry_count < MAX_RETRIES:
                wait_time = base_delay * (2 ** (retry_count - 1))
                logger.info(f"🔁 Retrying in {wait_time:.1f} seconds...")
                time.sleep(wait_time)
            else:
                logger.critical("🚨 Max retries reached. Bot shutting down.")
                raise SystemExit(1)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("🛑 Bot shutdown requested by user")
    except SystemExit:
        logger.critical("🚨 Bot shutdown due to fatal error")
    except Exception as e:
        logger.critical(f"Unexpected error: {e}")
        raise
