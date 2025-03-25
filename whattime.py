# ---------------------------
# 🔹 Imports and Setup
# ---------------------------
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
    "AEDT": "Australia/Sydney"
}

TIMEZONE_PRESETS = {
    "north_america": {
        "🇺🇸 Eastern": "America/New_York",
        "🇺🇸 Central": "America/Chicago",
        "🇺🇸 Mountain": "America/Denver",
        "🇺🇸 Pacific": "America/Los_Angeles",
        "🇨🇦 Eastern": "America/Toronto"
    },
    "europe": {
        "🇬🇧 London": "Europe/London",
        "🇫🇷 Paris": "Europe/Paris",
        "🇩🇪 Berlin": "Europe/Berlin",
        "🇮🇹 Rome": "Europe/Rome",
        "🇪🇸 Madrid": "Europe/Madrid"
    },
    "nordic": {
        "🇳🇴 Oslo": "Europe/Oslo",
        "🇸🇪 Stockholm": "Europe/Stockholm",
        "🇫🇮 Helsinki": "Europe/Helsinki",
        "🇩🇰 Copenhagen": "Europe/Copenhagen",
        "🇮🇸 Reykjavik": "Atlantic/Reykjavik"
    },
    "asia": {
        "🇯🇵 Tokyo": "Asia/Tokyo",
        "🇰🇷 Seoul": "Asia/Seoul",
        "🇨🇳 Beijing": "Asia/Shanghai",
        "🇸🇬 Singapore": "Asia/Singapore",
        "🇮🇳 New Delhi": "Asia/Kolkata"
    }
}

DEFAULT_TIMEZONES = {
    "UTC": "UTC",
    "🇺🇸 Eastern": "America/New_York",
    "🇺🇸 Pacific": "America/Los_Angeles",
    "🇬🇧 London": "Europe/London",
    "🇩🇪 Berlin": "Europe/Berlin",
    "🇫🇮 Helsinki": "Europe/Helsinki"
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
# 🔹 UI Components
# ---------------------------
class TimestampView(discord.ui.View):
    def __init__(self, timestamp: int):
        super().__init__()
        self.timestamp = timestamp
        
        # Add buttons for each timestamp format
        self.add_item(TimestampButton(f"<t:{timestamp}>", "Standard"))
        self.add_item(TimestampButton(f"<t:{timestamp}:R>", "Relative"))
        self.add_item(TimestampButton(f"<t:{timestamp}:t>", "Short Time"))
        self.add_item(TimestampButton(f"<t:{timestamp}:F>", "Long Format"))

class TimestampButton(discord.ui.Button):
    def __init__(self, timestamp_code: str, label: str):
        super().__init__(
            label=f"Copy {label}",
            style=discord.ButtonStyle.secondary,
            custom_id=f"timestamp_{label}"
        )
        self.timestamp_code = timestamp_code

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            self.timestamp_code,
            ephemeral=True
        )
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
    def __init__(self, db_path: str = "data/timezones.db"):
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
                
                # Create indices for better query performance
                await db.execute("""
                    CREATE INDEX IF NOT EXISTS idx_server_timezones_server_id 
                    ON server_timezones(server_id)
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
                    return {row[0]: row[1] for row in results} if results else {}
        except Exception as e:
            logger.error(f"Error getting server timezones: {e}")
            return {}

    async def set_server_timezone(self, server_id: int, display_name: str, timezone: str) -> Tuple[bool, str]:
        """Add or update server timezone, maintaining 5 timezone limit"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Check if this display name already exists for this server
                async with db.execute(
                    "SELECT timezone FROM server_timezones WHERE server_id = ? AND display_name = ?",
                    (server_id, display_name)
                ) as cursor:
                    existing = await cursor.fetchone()

                # If not updating existing, check the limit
                if not existing:
                    async with db.execute(
                        "SELECT COUNT(*) FROM server_timezones WHERE server_id = ?",
                        (server_id,)
                    ) as cursor:
                        count = (await cursor.fetchone())[0]
                    
                    if count >= 5:
                        return False, "Server timezone list is at maximum capacity (5). Remove one first."

                # Add or update timezone
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

    async def clear_server_timezones(self, server_id: int) -> bool:
        """Clear all timezone displays for a server"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("""
                    DELETE FROM server_timezones 
                    WHERE server_id = ?
                """, (server_id,))
                await db.commit()
                return True
        except Exception as e:
            logger.error(f"Error clearing server timezones: {e}")
            return False
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
                matches = process.extract(
                    user_input,
                    pytz.all_timezones,
                    limit=3
                )
                if matches and matches[0][1] >= 85:
                    return matches[0][0], [m[0] for m in matches[1:]]
                
                return None, [m[0] for m in matches]

            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, do_fuzzy_match)
            
        except Exception as e:
            logger.error(f"Error in find_timezone: {e}")
            return None, []

    @staticmethod
    def validate_timezone(timezone: str) -> bool:
        """Validate if a timezone string is valid"""
        try:
            pytz.timezone(timezone)
            return True
        except pytz.exceptions.UnknownTimeZoneError:
            return False

    @staticmethod
    def get_current_time(timezone: str) -> Optional[datetime]:
        """Get current time in specified timezone"""
        try:
            tz = pytz.timezone(timezone)
            return datetime.now(tz)
        except Exception as e:
            logger.error(f"Error getting current time for timezone {timezone}: {e}")
            return None
# ---------------------------
# 🔹 Time Parser
# ---------------------------
class TimeParser:
    def __init__(self):
        self.cache = {}
        self._cache_lifetime = timedelta(hours=1)
        
        # Compile regex patterns once during initialization
        self.time_pattern = re.compile(
            r'^(\d{1,2}):(\d{2})(?:\s*(am|pm))?$',
            re.IGNORECASE
        )
        self.relative_pattern = re.compile(
            r'^in\s+(\d+)\s*(hour|hr|minute|min)s?$',
            re.IGNORECASE
        )
        self.simple_hour_pattern = re.compile(
            r'^(\d{1,2})\s*(am|pm)$',
            re.IGNORECASE
        )

    def _clean_cache(self):
        """Remove old cache entries"""
        now = datetime.now()
        self.cache = {
            k: v for k, v in self.cache.items() 
            if v[1] + self._cache_lifetime > now
        }
        if len(self.cache) > 1000:  # Limit cache size
            sorted_items = sorted(
                self.cache.items(), 
                key=lambda x: x[1][1]
            )[-1000:]
            self.cache = dict(sorted_items)

    async def parse_time(self, input_text: str, base_timezone: str) -> Optional[datetime]:
        """Parse time with timezone awareness and improved date handling"""
        try:
            # Clean input
            input_text = re.sub(r'\s+', ' ', input_text.strip().lower())
            
            # Get current time in user's timezone
            tz = pytz.timezone(base_timezone)
            now = datetime.now(tz)
            
            # Handle special cases
            if input_text == "now":
                return now

            # Handle simple hour format (e.g., "3pm", "11am")
            simple_hour_match = self.simple_hour_pattern.match(input_text)
            if simple_hour_match:
                hour, meridian = simple_hour_match.groups()
                hour = int(hour)
                if meridian.lower() == 'pm' and hour < 12:
                    hour += 12
                elif meridian.lower() == 'am' and hour == 12:
                    hour = 0
                
                result = now.replace(
                    hour=hour,
                    minute=0,
                    second=0,
                    microsecond=0
                )
                
                # Handle times that have passed today
                if result < now:
                    time_diff = now - result
                    if time_diff > timedelta(hours=12):
                        result = result + timedelta(days=1)
                return result

            # Handle HH:MM format
            time_match = self.time_pattern.match(input_text)
            if time_match:
                hour, minute, meridian = time_match.groups()
                hour = int(hour)
                minute = int(minute)
                
                # Handle 12-hour format if AM/PM is specified
                if meridian:
                    if meridian.lower() == 'pm' and hour < 12:
                        hour += 12
                    elif meridian.lower() == 'am' and hour == 12:
                        hour = 0
                
                if 0 <= hour <= 23 and 0 <= minute <= 59:
                    result = now.replace(
                        hour=hour,
                        minute=minute,
                        second=0,
                        microsecond=0
                    )
                    
                    # Always default to today's date for time-only input
                    # Only move to tomorrow if the time has passed and it's more than 12 hours ago
                    if result < now:
                        time_diff = now - result
                        if time_diff > timedelta(hours=12):
                            result = result + timedelta(days=1)
                    
                    return result

            # Handle relative time expressions
            relative_match = self.relative_pattern.match(input_text)
            if relative_match:
                amount, unit = relative_match.groups()
                amount = int(amount)
                if unit.startswith(('hour', 'hr')):
                    return now + timedelta(hours=amount)
                elif unit.startswith(('minute', 'min')):
                    return now + timedelta(minutes=amount)

            # Use dateparser for more complex expressions
            settings = {
                'RELATIVE_BASE': now,
                'TIMEZONE': base_timezone,
                'RETURN_AS_TIMEZONE_AWARE': True,
                'PREFER_DATES_FROM': 'current_period',
                'PREFER_DAY_OF_MONTH': 'current'
            }

            # Add special handling for month + day format like "March 30"
            month_day_pattern = re.compile(
                r'(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})',
                re.IGNORECASE
            )
            month_day_match = month_day_pattern.search(input_text)

            if month_day_match:
                logger.info(f"Found month_day pattern: {month_day_match.groups()}")
                month_name, day = month_day_match.groups()
                day = int(day)
                
                # Map month names to numbers
                month_map = {
                    'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
                    'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12
                }
                month = month_map[month_name.lower()]
                
                # Extract time from the remaining string
                time_part = input_text.replace(month_day_match.group(), '').strip()
                time_match = self.time_pattern.match(time_part)
                simple_hour_match = self.simple_hour_pattern.match(time_part)
                
                # Default time values
                hour, minute = 0, 0
                
                # Extract time if specified
                if time_match:
                    hour, minute, meridian = time_match.groups()
                    hour = int(hour)
                    minute = int(minute)
                    if meridian and meridian.lower() == 'pm' and hour < 12:
                        hour += 12
                    elif meridian and meridian.lower() == 'am' and hour == 12:
                        hour = 0
                elif simple_hour_match:
                    hour, meridian = simple_hour_match.groups()
                    hour = int(hour)
                    if meridian.lower() == 'pm' and hour < 12:
                        hour += 12
                    elif meridian.lower() == 'am' and hour == 12:
                        hour = 0
                
                # Create the datetime object
                year = now.year
                # Handle if the date has already passed this year
                if month < now.month or (month == now.month and day < now.day):
                    year += 1
                    
                try:
                    # Create the datetime with the extracted information
                    result = datetime(year, month, day, hour, minute, 0, 0)
                    # Convert to user's timezone
                    result = result.replace(tzinfo=tz)
                    logger.info(f"Created specific date: {result}")
                    return result
                except ValueError as e:
                    logger.error(f"Error creating date: {e}")
                    # Continue with regular parsing if this fails
            
            parsed_dt = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: dateparser.parse(input_text, settings=settings)
            )

            if parsed_dt:
                # Check if parsed date is different from today
                is_different_date = (
                    parsed_dt.year != now.year or
                    parsed_dt.month != now.month or
                    parsed_dt.day != now.day
                )
            
                if is_different_date or 'tomorrow' in input_text.lower():
                    return parsed_dt.astimezone(pytz.UTC)

                # Check if the input only specified a time (no date indicators)
                has_date_indicator = any(word in input_text for word in [
                    'tomorrow', 'today', 'next', 'tuesday', 'wednesday', 
                    'thursday', 'friday', 'saturday', 'sunday', 'monday',
                    'yesterday', 'week', 'month', 'tmr', 'tmrw'
                ])
                
                if not has_date_indicator:
                    # Use today's date with the parsed time
                    result = now.replace(
                        hour=parsed_dt.hour,
                        minute=parsed_dt.minute,
                        second=0,
                        microsecond=0
                    )
                    
                    # Same logic as above for handling times that have passed
                    if result < now:
                        time_diff = now - result
                        if time_diff > timedelta(hours=12):
                            result = result + timedelta(days=1)
                    
                    return result
                
                return parsed_dt.astimezone(pytz.UTC)
            
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
        self.timezone_handler = TimezoneHandler()

    async def setup_hook(self):
        """Called when the bot starts"""
        await self.db.setup()
        await self.register_commands()
        try:
            await self.tree.sync()
            logger.info("✅ Successfully synced commands!")
        except Exception as e:
            logger.error(f"🚨 Command sync failed: {e}")

    # ---------------------------
    # 🔹 Autocomplete Handlers
    # ---------------------------
    async def timezone_autocomplete(
        self,  # Added self parameter to fix autocomplete
        interaction: discord.Interaction,
        current: str,
    ) -> List[app_commands.Choice[str]]:
        """Provide autocomplete suggestions for timezone input"""
        if not current:
            suggestions = [
                ("US Eastern (EST)", "EST"),
                ("US Central (CST)", "CST"),
                ("US Pacific (PST)", "PST"),
                ("UK (GMT/BST)", "GMT"),
                ("Central Europe", "CET")
            ]
        else:
            current = current.upper()
            matches = []
            
            # Check common abbreviations first
            common_matches = process.extract(
                current,
                COMMON_TIMEZONE_MAPPINGS.keys(),
                limit=3
            )
            matches.extend([
                (f"{name} ({COMMON_TIMEZONE_MAPPINGS[name]})", name)
                for name, score in common_matches
                if score > 60
            ])

            # Then check full timezone names
            tz_matches = process.extract(
                current,
                pytz.all_timezones,
                limit=3
            )
            matches.extend([
                (name.replace("_", " "), name)
                for name, score in tz_matches
                if score > 60
            ])
            
            suggestions = list(dict.fromkeys(matches))
            
        return [
            app_commands.Choice(name=display, value=value)
            for display, value in suggestions[:25]  # Discord limits to 25 choices
        ]

    async def timezone_name_autocomplete(
        self,  # Added self parameter to fix autocomplete
        interaction: discord.Interaction,
        current: str,
    ) -> List[app_commands.Choice[str]]:
        """Provide autocomplete for existing timezone names"""
        if not interaction.guild_id:
            return []
        
        try:
            current_zones = await self.db.get_server_timezones(interaction.guild_id)
            if not current_zones:
                return []

            if not current:
                suggestions = [
                    app_commands.Choice(name=name, value=name)
                    for name in current_zones.keys()
                ]
            else:
                matches = process.extract(
                    current,
                    current_zones.keys(),
                    limit=5
                )
                suggestions = [
                    app_commands.Choice(name=name, value=name)
                    for name, score in matches 
                    if score > 60
                ]
            
            return suggestions[:25]
        except Exception as e:
            logger.error(f"Error in timezone_name_autocomplete: {e}")
            return []
# ---------------------------
# 🔹 Core Command Implementations
# ---------------------------
    async def register_commands(self):
        @self.tree.command(
            name="timezone",
            description="Set your timezone for time conversions"
        )
        @app_commands.describe(
            timezone="Your timezone (e.g., 'EST', 'CST', 'America/Chicago')"
        )
        @app_commands.autocomplete(timezone=self.timezone_autocomplete)  # Fixed autocomplete reference
        async def timezone_command(interaction: discord.Interaction, timezone: str):
            """Set user timezone"""
            try:
                await interaction.response.defer(ephemeral=True)
                
                success, matched_timezone, suggestions = await self.db.set_timezone(
                    interaction.user.id, 
                    timezone
                )

                if success:
                    tz = pytz.timezone(matched_timezone)
                    current_time = datetime.now(tz)
                    
                    embed = discord.Embed(
                        title="✅ Timezone Set",
                        description=(
                            f"Your timezone has been set to `{matched_timezone}`\n"
                            f"Current time: `{current_time.strftime('%I:%M %p %Z')}`"
                        ),
                        color=discord.Color.green()
                    )
                    await interaction.followup.send(embed=embed, ephemeral=True)
                else:
                    if suggestions:
                        suggestions_text = "\n".join([
                            f"• `{tz}` - {tz.replace('_', ' ')}" 
                            for tz in suggestions[:5]
                        ])
                        embed = discord.Embed(
                            title="❌ Invalid Timezone",
                            description=(
                                f"Could not find timezone: `{timezone}`\n\n"
                                "**Did you mean:**\n"
                                f"{suggestions_text}\n\n"
                                "_Try using the autocomplete suggestions when typing!_"
                            ),
                            color=discord.Color.red()
                        )
                    else:
                        embed = discord.Embed(
                            title="❌ Error",
                            description="Could not set timezone. Please try again with a valid timezone.",
                            color=discord.Color.red()
                        )
                    await interaction.followup.send(embed=embed, ephemeral=True)
                    
            except Exception as e:
                logger.error(f"Error in timezone command: {e}")
                await interaction.followup.send(
                    "❌ An error occurred while setting your timezone.",
                    ephemeral=True
                )

        @self.tree.command(
            name="event",
            description="Convert an event time to different time zones"
        )
        @app_commands.describe(
            time="Time to convert (e.g., '3pm', '15:00', 'in 2 hours')"
        )
        async def event_command(interaction: discord.Interaction, time: str):
            """Handle time conversion requests"""
            try:
                # Check timezone first
                user_timezone = await self.db.get_timezone(interaction.user.id)
                if not user_timezone:
                    await interaction.response.send_message(
                        "❌ Please set your timezone first using `/timezone`",
                        ephemeral=True
                    )
                    return

                # Parse time before sending response
                parsed_time = await self.time_parser.parse_time(time, user_timezone)
                if not parsed_time:
                    await interaction.response.send_message(
                        "❌ Could not understand that time format. Try something like:\n"
                        "• `3pm`\n"
                        "• `15:00`\n"
                        "• `in 2 hours`\n"
                        "• `tomorrow 3pm`",
                        ephemeral=True
                    )
                    return

                # If we got here, both timezone and time are valid
                # This is the only command response that won't be ephemeral
                await interaction.response.defer(ephemeral=False)

                # Create response embed
                local_time = parsed_time.astimezone(pytz.timezone(user_timezone))
                embed = discord.Embed(
                    title="🌍 Time Conversion",
                    description=(
                        f"**🕒 Time ({user_timezone})** → "
                        f"`{local_time.strftime('%b %d, %I:%M %p %Z')}`\n\n"
                        f"{await self.format_time_conversions(parsed_time, interaction.guild_id)}"
                    ),
                    color=discord.Color.blue()
                )
                embed.set_footer(text=f"Requested by {interaction.user.name}")
                
                # Add timestamp for copying
                timestamp = int(parsed_time.timestamp())
                embed.description += f"\n\nDiscord Timestamp: `<t:{timestamp}:F>`"
                
                await interaction.followup.send(embed=embed)

            except Exception as e:
                logger.error(f"Error in event command: {e}")
                await interaction.followup.send(
                    "❌ An error occurred while converting the time.",
                    ephemeral=True
                )
# ---------------------------
# 🔹 Server Management Commands
# ---------------------------
        @self.tree.command(
            name="add_timezone",
            description="Add a timezone to the server's display (max 5)"
        )
        @app_commands.describe(
            timezone="Timezone to add (e.g., 'EST', 'America/Chicago')",
            display_name="Optional custom display name"
        )
        @app_commands.autocomplete(timezone=self.timezone_autocomplete)  # Fixed autocomplete reference
        async def add_timezone(interaction: discord.Interaction, timezone: str, display_name: str = None):
            try:
                await interaction.response.defer(ephemeral=True)
                
                if not interaction.guild_id:
                    await interaction.followup.send(
                        "❌ This command can only be used in servers",
                        ephemeral=True
                    )
                    return

                matched_timezone, suggestions = await self.timezone_handler.find_timezone(timezone)
                if not matched_timezone:
                    suggestions_text = "\n".join([
                        f"• `{tz}` - {tz.replace('_', ' ')}" 
                        for tz in suggestions[:5]
                    ])
                    await interaction.followup.send(
                        f"❌ Invalid timezone. Did you mean:\n{suggestions_text}",
                        ephemeral=True
                    )
                    return

                if not display_name:
                    if timezone.upper() in COMMON_TIMEZONE_MAPPINGS:
                        display_name = f"🌐 {timezone.upper()}"
                    else:
                        city = matched_timezone.split('/')[-1].replace('_', ' ')
                        region = matched_timezone.split('/')[0]
                        display_name = f"{region} - {city}"

                success, message = await self.db.set_server_timezone(
                    interaction.guild_id,
                    display_name,
                    matched_timezone
                )

                if success:
                    now = datetime.now(pytz.UTC)
                    preview = await self.format_time_conversions(now, interaction.guild_id)
                    
                    embed = discord.Embed(
                        title="✅ Timezone Added",
                        description=(
                            f"Added **{display_name}** (`{matched_timezone}`)\n\n"
                            "Current display:\n"
                            f"{preview}"
                        ),
                        color=discord.Color.green()
                    )
                    await interaction.followup.send(embed=embed, ephemeral=True)
                else:
                    await interaction.followup.send(
                        f"❌ {message}",
                        ephemeral=True
                    )

            except Exception as e:
                logger.error(f"Error in add_timezone: {e}")
                await interaction.followup.send(
                    "❌ An error occurred while adding the timezone.",
                    ephemeral=True
                )

        @self.tree.command(
            name="remove_timezone",
            description="Remove a timezone from the server's display"
        )
        @app_commands.describe(
            display_name="Display name of timezone to remove"
        )
        @app_commands.autocomplete(display_name=self.timezone_name_autocomplete)  # Fixed autocomplete reference
        async def remove_timezone(interaction: discord.Interaction, display_name: str):
            try:
                await interaction.response.defer(ephemeral=True)
                
                if not interaction.guild_id:
                    await interaction.followup.send(
                        "❌ This command can only be used in servers",
                        ephemeral=True
                    )
                    return

                success, message = await self.db.remove_server_timezone(
                    interaction.guild_id,
                    display_name
                )

                if success:
                    now = datetime.now(pytz.UTC)
                    preview = await self.format_time_conversions(now, interaction.guild_id)
                    
                    embed = discord.Embed(
                        title="✅ Timezone Removed",
                        description=(
                            f"Removed **{display_name}**\n\n"
                            "Current display:\n"
                            f"{preview}"
                        ),
                        color=discord.Color.green()
                    )
                    await interaction.followup.send(embed=embed, ephemeral=True)
                else:
                    await interaction.followup.send(
                        f"❌ {message}",
                        ephemeral=True
                    )

            except Exception as e:
                logger.error(f"Error in remove_timezone: {e}")
                await interaction.followup.send(
                    "❌ An error occurred while removing the timezone.",
                    ephemeral=True
                )
# ---------------------------
# 🔹 Utility Commands
# ---------------------------
        @self.tree.command(
            name="set_display",
            description="Set which timezones to display in time conversions"
        )
        @app_commands.describe(
            preset="Choose a preset group of timezones"
        )
        @app_commands.choices(preset=[
            app_commands.Choice(name="🌎 North America", value="north_america"),
            app_commands.Choice(name="🇪🇺 Europe", value="europe"),
            app_commands.Choice(name="❄️ Nordic", value="nordic"),
            app_commands.Choice(name="🌏 Asia", value="asia")
        ])
        async def set_display(interaction: discord.Interaction, preset: str):
            try:
                await interaction.response.defer(ephemeral=True)
                
                if not interaction.guild_id:
                    await interaction.followup.send(
                        "❌ This command can only be used in servers",
                        ephemeral=True
                    )
                    return

                preset_zones = TIMEZONE_PRESETS.get(preset, {})
                if not preset_zones:
                    await interaction.followup.send(
                        "❌ Invalid preset selected",
                        ephemeral=True
                    )
                    return

                await self.db.clear_server_timezones(interaction.guild_id)

                for display_name, timezone in preset_zones.items():
                    await self.db.set_server_timezone(
                        interaction.guild_id,
                        display_name,
                        timezone
                    )

                now = datetime.now(pytz.UTC)
                preview = await self.format_time_conversions(now, interaction.guild_id)

                embed = discord.Embed(
                    title="✅ Timezone Display Updated",
                    description=f"Set to {preset.replace('_', ' ').title()} preset:\n\n{preview}",
                    color=discord.Color.green()
                )

                await interaction.followup.send(embed=embed, ephemeral=True)

            except Exception as e:
                logger.error(f"Error in set_display: {e}")
                await interaction.followup.send(
                    "❌ Error setting timezone display",
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
                
                embed = self.calendar_formatter.create_calendar_embed(
                    parsed_time,
                    full_title,
                    template_info["duration"],
                    template,
                    description or template_info["description"]
                )

                timestamp = int(parsed_time.timestamp())
                view = TimestampView(timestamp)

                preview_msg = (
                    "⏰ **Timestamps will show as:**\n"
                    f"**Standard**: <t:{timestamp}>\n"
                    f"**Relative**: <t:{timestamp}:R>\n"
                    f"**Short Time**: <t:{timestamp}:t>\n"
                    f"**Long Format**: <t:{timestamp}:F>\n\n"
                    "**Click the buttons below to copy the timestamp codes!**"
                )

                await interaction.followup.send(embed=embed, ephemeral=True)
                await interaction.followup.send(preview_msg, view=view, ephemeral=True)

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

                timestamp = int(parsed_time.timestamp())
                view = TimestampView(timestamp)

                preview_msg = (
                    "⏰ **Timestamps will show as:**\n"
                    f"**Standard**: <t:{timestamp}>\n"
                    f"**Relative**: <t:{timestamp}:R>\n"
                    f"**Short Time**: <t:{timestamp}:t>\n"
                    f"**Long Format**: <t:{timestamp}:F>\n\n"
                    "**Click the buttons below to copy the timestamp codes!**"
                )

                await interaction.followup.send(preview_msg, view=view, ephemeral=True)

            except Exception as e:
                logger.error(f"Error in timestamps: {e}")
                await interaction.followup.send(
                    "❌ Error formatting timestamps",
                    ephemeral=True
                )
# ---------------------------
# 🔹 Bot Helper Methods
# ---------------------------
    async def format_time_conversions(self, dt: datetime, server_id: Optional[int] = None) -> str:
        """Format time conversions for timezone list"""
        conversions = []
        try:
            # Get server-specific timezones if in a server
            server_timezones = await self.db.get_server_timezones(server_id) if server_id else {}
            timezones_to_show = server_timezones or DEFAULT_TIMEZONES

            for name, tz_str in timezones_to_show.items():
                tz = pytz.timezone(tz_str)
                local_time = dt.astimezone(tz)
            
                # Get timezone abbreviation, checking DST status
                is_dst = local_time.dst().total_seconds() > 0
            
                # Special handling for common timezones
                tz_abbr = local_time.strftime('%Z')
                if tz_str == "Europe/London":
                    tz_abbr = "BST" if is_dst else "GMT"
                elif tz_str == "America/New_York":
                    tz_abbr = "EDT" if is_dst else "EST"
                elif tz_str == "America/Chicago":
                    tz_abbr = "CDT" if is_dst else "CST"
                elif tz_str == "America/Denver":
                    tz_abbr = "MDT" if is_dst else "MST"
                elif tz_str == "America/Los_Angeles":
                    tz_abbr = "PDT" if is_dst else "PST"
            
                clock_hour = local_time.hour % 12 or 12
                clock_emoji = f":clock{clock_hour}:"
            
                conversions.append(
                    f"{clock_emoji} **{name}**: {local_time.strftime('%H:%M')} {tz_abbr} ({local_time.strftime('%m/%d')})"
                )
        
            return "\n".join(conversions)
        except Exception as e:
            logger.error(f"Error formatting time conversions: {e}")
            return "Error formatting time conversions"

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

    # Add this at the very start of the main() function
    current_time = datetime.now()
    print(f"System reports current time as: {current_time}")
    print(f"System timezone info: {time.tzname}")
    
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