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
import json
from datetime import datetime, timedelta
from flask import Flask
import threading
from dotenv import load_dotenv
from typing import Optional, Tuple, Dict, Union, Any, List
import re
from fuzzywuzzy import process
from urllib.parse import quote

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
# 🔹 Calendar Templates
# ---------------------------
CALENDAR_TEMPLATES = {
    "gaming": {
        "title_prefix": "🎮 Gaming: ",
        "duration": 180,  # 3 hours
        "description": "Gaming session organized via Discord"
    },
    "meeting": {
        "title_prefix": "📅 Meeting: ",
        "duration": 60,  # 1 hour
        "description": "Meeting scheduled via Discord"
    },
    "event": {
        "title_prefix": "🎉 Event: ",
        "duration": 120,  # 2 hours
        "description": "Event scheduled via Discord"
    },
    "raid": {
        "title_prefix": "⚔️ Raid: ",
        "duration": 240,  # 4 hours
        "description": "Raid scheduled via Discord"
    }
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
# 🔹 Time Formatting
# ---------------------------
class TimeFormatter:
    """Handles Discord timestamp formatting"""
    
    def __init__(self, dt: datetime):
        self.timestamp = int(dt.timestamp())
        
    def get_all_formats(self) -> Dict[str, str]:
        """Get all Discord timestamp formats"""
        formats = {
            "Standard": "",
            "Short Time": "t",
            "Long Time": "T",
            "Short Date": "d",
            "Long Date": "D",
            "Short Date/Time": "f",
            "Long Date/Time": "F",
            "Relative": "R"
        }
        return {
            name: self.format(style) 
            for name, style in formats.items()
        }
        
    def format(self, style: str = "") -> str:
        """Format timestamp with given style"""
        if style:
            return f"<t:{self.timestamp}:{style}>"
        return f"<t:{self.timestamp}>"

# ---------------------------
# 🔹 Calendar Formatting
# ---------------------------
class CalendarFormatter:
    """Handles calendar formatting and link generation"""
    
    @staticmethod
    def create_google_calendar_link(event_time: datetime, title: str, duration: int, description: str = "") -> str:
        """Create Google Calendar link"""
        end_time = event_time + timedelta(minutes=duration)
        
        # Format dates for Google Calendar
        start = event_time.strftime('%Y%m%dT%H%M%SZ')
        end = end_time.strftime('%Y%m%dT%H%M%SZ')
        
        base_url = "https://calendar.google.com/calendar/render"
        params = {
            "action": "TEMPLATE",
            "text": quote(title),
            "dates": f"{start}/{end}",
            "details": quote(description)
        }
        
        return f"{base_url}?{'&'.join(f'{k}={v}' for k, v in params.items())}"

    @staticmethod
    def create_calendar_text(event_time: datetime, title: str, duration: int, template: str, description: str = "") -> str:
        """Create a formatted text block with calendar details"""
        end_time = event_time + timedelta(minutes=duration)
        timestamp = int(event_time.timestamp())
        
        template_info = CALENDAR_TEMPLATES.get(template, CALENDAR_TEMPLATES["event"])
        emoji = template_info["title_prefix"].split()[0]
        
        return f"""
{emoji} **{title}**
📅 When: {event_time.strftime('%A, %B %d, %Y')}
🕒 Time: {event_time.strftime('%I:%M %p')} 
⏱️ Duration: {duration} minutes
🔚 Ends: {end_time.strftime('%I:%M %p')}
📝 Description: {description}

🔗 **Add to Calendar:**
• [Click to Add to Calendar]({CalendarFormatter.create_google_calendar_link(event_time, title, duration, description)})
(Works with Google Calendar, Apple Calendar, and other calendar apps)

⏰ **Discord Timestamps** (Copy/Paste these):
• Standard: `<t:{timestamp}>`
• Relative: `<t:{timestamp}:R>`
• Short: `<t:{timestamp}:t>`
• Long: `<t:{timestamp}:F>`

Shows as:
• <t:{timestamp}>
• <t:{timestamp}:R>
"""
    @staticmethod
    def create_calendar_embed(event_time: datetime, title: str, duration: int, template: str, description: str = "") -> discord.Embed:
        """Create a Discord embed with calendar details"""
        end_time = event_time + timedelta(minutes=duration)
        
        template_info = CALENDAR_TEMPLATES.get(template, CALENDAR_TEMPLATES["event"])
        emoji = template_info["title_prefix"].split()[0]
        
        embed = discord.Embed(
            title=f"{emoji} {title}",
            description=description,
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="📅 When",
            value=event_time.strftime('%A, %B %d, %Y'),
            inline=True
        )
        
        embed.add_field(
            name="🕒 Time",
            value=event_time.strftime('%I:%M %p'),
            inline=True
        )
        
        embed.add_field(
            name="⏱️ Duration",
            value=f"{duration} minutes",
            inline=True
        )
        
        calendar_link = CalendarFormatter.create_google_calendar_link(
            event_time, title, duration, description
        )
        
        embed.add_field(
            name="🔗 Calendar Link",
            value=f"[Add to Calendar]({calendar_link})",
            inline=False
        )
        
        return embed

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
                # User timezone settings
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY,
                        timezone TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                # Server timezone settings
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS server_timezones (
                        server_id INTEGER,
                        display_name TEXT,
                        timezone TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (server_id, display_name)
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
async def get_server_timezones(self, server_id: int) -> Dict[str, str]:
        """Get timezone display list for specific server"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute(
                    "SELECT display_name, timezone FROM server_timezones WHERE server_id = ?",
                    (server_id,)
                ) as cursor:
                    results = await cursor.fetchall()
                    return {row[0]: row[1] for row in results}
        except Exception as e:
            logger.error(f"Error getting server timezones: {e}")
            return {}

async def set_server_timezone(self, server_id: int, display_name: str, timezone: str) -> Tuple[bool, str]:
        """Add or update server timezone, maintaining 5 timezone limit"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Check current count
                async with db.execute(
                    "SELECT COUNT(*) FROM server_timezones WHERE server_id = ?",
                    (server_id,)
                ) as cursor:
                    count = (await cursor.fetchone())[0]
                
                # If adding new and at limit
                if count >= 5:
                    return False, "Server timezone list is at maximum capacity (5). Remove one first."

                # Add new timezone
                await db.execute("""
                    INSERT OR REPLACE INTO server_timezones (server_id, display_name, timezone)
                    VALUES (?, ?, ?)
                """, (server_id, display_name, timezone))
                await db.commit()
                return True, "Timezone added successfully"

        except Exception as e:
            logger.error(f"Error setting server timezone: {e}")
            return False, f"Error setting timezone: {str(e)}"

async def remove_server_timezone(self, server_id: int, display_name: str) -> Tuple[bool, str]:
        """Remove a timezone from server's list"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("""
                    DELETE FROM server_timezones 
                    WHERE server_id = ? AND display_name = ?
                """, (server_id, display_name))
                await db.commit()
                return True, "Timezone removed successfully"
        except Exception as e:
            logger.error(f"Error removing server timezone: {e}")
            return False, f"Error removing timezone: {str(e)}"

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
    def __init__(self):
        self.cache = {}
        self._cache_lifetime = timedelta(hours=1)

    def _clean_cache(self):
        """Remove old cache entries"""
        now = datetime.now()
        self.cache = {
            k: v for k, v in self.cache.items() 
            if v[1] + self._cache_lifetime > now
        }

    async def parse_time(self, input_text: str, base_timezone: str) -> Optional[datetime]:
        """Parse time with timezone awareness"""
        self._clean_cache()
        
        # Create cache key
        cache_key = f"{input_text}:{base_timezone}"
        
        # Check cache
        if cache_key in self.cache:
            parsed_time, cache_time = self.cache[cache_key]
            if cache_time + self._cache_lifetime > datetime.now():
                return parsed_time
        try:
            # Clean input
            input_text = re.sub(r'\s+', ' ', input_text.strip().lower())
            
            # Handle special cases
            if input_text == "now":
                result = datetime.now(pytz.timezone(base_timezone))
            else:
                # Parse with user's timezone
                tz = pytz.timezone(base_timezone)
                settings = {
                    'RELATIVE_BASE': datetime.now(tz),
                    'TIMEZONE': base_timezone,
                    'RETURN_AS_TIMEZONE_AWARE': True,
                    'PREFER_DATES_FROM': 'future'
                }
                
                loop = asyncio.get_event_loop()
                parsed_dt = await loop.run_in_executor(
                    None,
                    lambda: dateparser.parse(input_text, settings=settings)
                )
                
                if parsed_dt:
                    # Convert to UTC and cache
                    utc_time = parsed_dt.astimezone(pytz.UTC)
                    self.cache[cache_key] = (utc_time, datetime.now())
                    return utc_time
            
            return None
            
        except Exception as e:
            logger.error(f"Time parsing error: {e}")
            return None

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
        self.calendar_formatter = CalendarFormatter()

    async def setup_hook(self):
        """Called when the bot starts"""
        await self.db.setup()
        await self.register_commands()
        try:
            await self.tree.sync()
            logger.info("✅ Successfully synced commands!")
        except Exception as e:
            logger.error(f"🚨 Command sync failed: {e}")

    async def format_time_conversions(self, dt: datetime, server_id: Optional[int] = None) -> str:
        """Format time conversions for timezone list"""
        conversions = []
        try:
            # Get server-specific timezones if in a server
            server_timezones = await self.db.get_server_timezones(server_id) if server_id else {}
            timezones_to_show = server_timezones or DEFAULT_TIMEZONES

            for name, tz_str in timezones_to_show.items():
                local_time = dt.astimezone(pytz.timezone(tz_str))
                clock_hour = local_time.hour % 12 or 12
                clock_emoji = f":clock{clock_hour}:"
                conversions.append(
                    f"{clock_emoji} **{name}**: {local_time.strftime('%H:%M %Z')} ({local_time.strftime('%m/%d')})"
                )
            
            return "\n".join(conversions)
        except Exception as e:
            logger.error(f"Error formatting time conversions: {e}")
            return "Error formatting time conversions"

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
            """Set timezone command"""
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
                await interaction.followup.send(
                    "❌ An error occurred while processing your request.",
                    ephemeral=True
                )
        @self.tree.command(
            name="event",
            description="Convert an event time to different time zones"
        )
        @app_commands.describe(
            time="Time to convert (e.g., '3pm tomorrow', '15:00', 'in 2 hours')"
        )
        async def event_command(interaction: discord.Interaction, time: str):
            """Handle time conversion requests"""
            try:
                await interaction.response.defer()
                
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

                parsed_time = await self.time_parser.parse_time(time, user_timezone)
                if not parsed_time:
                    await interaction.followup.send(
                        "❌ Could not understand that time format. Try something like:\n" +
                        "• `3pm tomorrow`\n" +
                        "• `15:00`\n" +
                        "• `in 2 hours`",
                        ephemeral=True
                    )
                    return

                # Create response embed
                local_time = parsed_time.astimezone(pytz.timezone(user_timezone))
                embed = discord.Embed(
                    title="🌍 Time Conversion",
                    description=(
                        f"**🕒 Time ({user_timezone})** → "
                        f"`{local_time.strftime('%b %d, %H:%M %Z')}`\n\n"
                        f"{await self.format_time_conversions(parsed_time, interaction.guild_id)}"
                    ),
                    color=discord.Color.blue()
                )
                
                embed.set_footer(text=f"Requested by {interaction.user.name}")
                await interaction.followup.send(embed=embed)

            except Exception as e:
                logger.error(f"Error in event command: {e}")
                await interaction.followup.send(
                    "❌ Error processing command",
                    ephemeral=True
                )

        @self.tree.command(
            name="format_time",
            description="Get time in different formats with calendar link"
        )
        @app_commands.describe(
            time="Time to format (e.g., '3pm tomorrow', '15:00')",
            title="Event title",
            template="Event template for duration and formatting",
            description="Optional event description"
        )
        @app_commands.choices(template=[
            app_commands.Choice(name="Gaming Session (3 hours)", value="gaming"),
            app_commands.Choice(name="Meeting (1 hour)", value="meeting"),
            app_commands.Choice(name="Event (2 hours)", value="event"),
            app_commands.Choice(name="Raid (4 hours)", value="raid")
        ])
        async def format_time(
            interaction: discord.Interaction,
            time: str,
            title: str,
            template: str = "event",
            description: str = ""
        ):
            try:
                await interaction.response.defer(ephemeral=True)
                
                user_timezone = await self.db.get_timezone(interaction.user.id)
                if not user_timezone:
                    await interaction.followup.send(
                        "❌ Please set your timezone first with /timezone",
                        ephemeral=True
                    )
                    return

                parsed_time = await self.time_parser.parse_time(time, user_timezone)
                if not parsed_time:
                    await interaction.followup.send(
                        "❌ Could not understand that time format",
                        ephemeral=True
                    )
                    return

                template_info = CALENDAR_TEMPLATES.get(template, CALENDAR_TEMPLATES["event"])
                full_title = f"{template_info['title_prefix']}{title}"
                
                # Create formatted text and embed
                calendar_text = self.calendar_formatter.create_calendar_text(
                    parsed_time,
                    full_title,
                    template_info["duration"],
                    template,
                    description or template_info["description"]
                )

                embed = self.calendar_formatter.create_calendar_embed(
                    parsed_time,
                    full_title,
                    template_info["duration"],
                    template,
                    description or template_info["description"]
                )

                await interaction.followup.send(
                    content=calendar_text,
                    embed=embed,
                    ephemeral=True
                )

            except Exception as e:
                logger.error(f"Error in format_time: {e}")
                await interaction.followup.send(
                    "❌ Error formatting time",
                    ephemeral=True
                )
        @self.tree.command(
            name="timestamps",
            description="Get Discord timestamp formats for a time"
        )
        @app_commands.describe(
            time="Time to format (e.g., '3pm tomorrow', '15:00')"
        )
        async def timestamps(interaction: discord.Interaction, time: str):
            try:
                await interaction.response.defer(ephemeral=True)
                
                user_timezone = await self.db.get_timezone(interaction.user.id)
                if not user_timezone:
                    await interaction.followup.send(
                        "❌ Please set your timezone first with /timezone",
                        ephemeral=True
                    )
                    return

                parsed_time = await self.time_parser.parse_time(time, user_timezone)
                if not parsed_time:
                    await interaction.followup.send(
                        "❌ Could not understand that time format",
                        ephemeral=True
                    )
                    return

                # Format timestamps
                time_formatter = TimeFormatter(parsed_time)
                formats = time_formatter.get_all_formats()
                
                # Create response
                embed = discord.Embed(
                    title="Discord Timestamp Formats",
                    description="Copy and paste these codes into your message",
                    color=discord.Color.blue()
                )

                for name, code in formats.items():
                    embed.add_field(
                        name=name,
                        value=f"Code: `{code}`\nShows as: {code}",
                        inline=False
                    )

                await interaction.followup.send(
                    embed=embed,
                    ephemeral=True
                )

            except Exception as e:
                logger.error(f"Error in timestamps: {e}")
                await interaction.followup.send(
                    "❌ Error formatting timestamps",
                    ephemeral=True
                )

# ---------------------------
# 🔹 Main Execution
# ---------------------------
async def start_bot():
    """Start the bot with proper error handling"""
    bot = WhatTimeBot()
    try:
        logger.info("Starting bot...")
        await bot.start(TOKEN)
    except KeyboardInterrupt:
        logger.info("Shutting down bot gracefully...")
        await bot.close()
    except Exception as e:
        logger.error(f"Error running bot: {e}")
        await bot.close()
        raise

def main():
    """Main entry point with error handling and retry logic"""
    retry_count = 0
    base_delay = INITIAL_RETRY_DELAY
    
    while retry_count < MAX_RETRIES:
        try:
            # Start bot
            asyncio.run(start_bot())
            break

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
