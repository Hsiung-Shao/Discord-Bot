import discord
from discord.ext import commands, tasks
import asyncio
import json
import os
from twikit import Client
from config import TWITTER_USERNAME, TWITTER_EMAIL, TWITTER_PASSWORD
from utils.logger import get_logger

logger = get_logger("XTracker", channel="x_tracker")


def _patch_twikit_client_transaction():
    # X 在 2026-03-18 改了 ondemand.s 檔案格式,twikit 2.3.3 的
    # ClientTransaction.get_indices() 無法再萃取 KEY_BYTE indices,
    # 之後每次 API 呼叫都會 raise "'ClientTransaction' object has no
    # attribute 'key'"。在 upstream 修好前(d60/twikit#408、#409、
    # PR #407 尚未合併)以此 patch 跳過 transaction id 計算。
    try:
        from twikit.x_client_transaction.transaction import ClientTransaction
    except ImportError:
        logger.warning("twikit ClientTransaction patch skipped: module not found")
        return

    if getattr(ClientTransaction, "_x_tracker_patched", False):
        return

    original_init = ClientTransaction.init

    async def safe_init(self, session, headers):
        try:
            await original_init(self, session, headers)
        except Exception as e:
            logger.warning(f"ClientTransaction.init fallback (twikit broken): {e}")
            if not hasattr(self, "key"):
                self.key = ""
            if not hasattr(self, "key_bytes"):
                self.key_bytes = []
            if not hasattr(self, "animation_key"):
                self.animation_key = ""

    def safe_generate_transaction_id(self, method, path, response=None,
                                     key=None, animation_key=None, time_now=None):
        return ""

    ClientTransaction.init = safe_init
    ClientTransaction.generate_transaction_id = safe_generate_transaction_id
    ClientTransaction._x_tracker_patched = True
    logger.info("twikit ClientTransaction patched (workaround for X 2026-03-18 breakage)")


def _patch_twikit_user():
    # X 持續改 user legacy schema,缺欄位時 twikit 2.3.3 的 User.__init__
    # 直接拋 KeyError(例如 'pinned_tweet_ids_str'、'urls')。把 __init__
    # 換成全 .get() 版本,缺欄位回 None / 預設值,不影響呼叫端只用到 id 的場景。
    try:
        from twikit import user as user_module
    except ImportError:
        logger.warning("twikit user patch skipped: module not found")
        return

    if getattr(user_module.User, "_x_tracker_patched", False):
        return

    def safe_user_init(self, client, data):
        self._client = client
        legacy = (data or {}).get("legacy") or {}
        entities = legacy.get("entities") or {}
        description_entities = entities.get("description") or {}
        url_entities = entities.get("url") or {}

        self.id = (data or {}).get("rest_id")
        self.created_at = legacy.get("created_at")
        self.name = legacy.get("name", "")
        self.screen_name = legacy.get("screen_name", "")
        self.profile_image_url = legacy.get("profile_image_url_https")
        self.profile_banner_url = legacy.get("profile_banner_url")
        self.url = legacy.get("url")
        self.location = legacy.get("location", "")
        self.description = legacy.get("description", "")
        self.description_urls = description_entities.get("urls") or []
        self.urls = url_entities.get("urls")
        self.pinned_tweet_ids = legacy.get("pinned_tweet_ids_str") or []
        self.is_blue_verified = (data or {}).get("is_blue_verified", False)
        self.verified = legacy.get("verified", False)
        self.possibly_sensitive = legacy.get("possibly_sensitive", False)
        self.can_dm = legacy.get("can_dm", False)
        self.can_media_tag = legacy.get("can_media_tag", False)
        self.want_retweets = legacy.get("want_retweets", False)
        self.default_profile = legacy.get("default_profile", False)
        self.default_profile_image = legacy.get("default_profile_image", False)
        self.has_custom_timelines = legacy.get("has_custom_timelines", False)
        self.followers_count = legacy.get("followers_count", 0)
        self.fast_followers_count = legacy.get("fast_followers_count", 0)
        self.normal_followers_count = legacy.get("normal_followers_count", 0)
        self.following_count = legacy.get("friends_count", 0)
        self.favourites_count = legacy.get("favourites_count", 0)
        self.listed_count = legacy.get("listed_count", 0)
        self.media_count = legacy.get("media_count", 0)
        self.statuses_count = legacy.get("statuses_count", 0)
        self.is_translator = legacy.get("is_translator", False)
        self.translator_type = legacy.get("translator_type", "none")
        self.withheld_in_countries = legacy.get("withheld_in_countries", [])
        self.protected = legacy.get("protected", False)

    user_module.User.__init__ = safe_user_init
    user_module.User._x_tracker_patched = True
    logger.info("twikit User.__init__ patched (fault-tolerant against X schema drift)")


_patch_twikit_client_transaction()
_patch_twikit_user()

class XTracker(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data_file = "data/x_tracker.json"
        self.cookies_file = "data/twitter_cookies.json"
        self.config = self.load_config()
        
        # Initialize Twikit Client
        self.client = Client('en-US')
        self.login_lock = asyncio.Lock()
        self.is_logged_in = False
        
        # Start background task
        self.check_updates_task.start()

    def load_config(self) -> dict:
        default_config = {
            "tracking": {}
        }

        if not os.path.exists(self.data_file):
            self.save_config(default_config)
            return default_config
        
        try:
            with open(self.data_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
                # Cleanup old instances config if present
                if "instances" in config:
                    del config["instances"]
                    self.save_config(config)
                return config
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            return default_config

    def save_config(self, config: dict = None):
        if config is None:
            config = self.config
        
        os.makedirs(os.path.dirname(self.data_file), exist_ok=True)
        with open(self.data_file, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=4)

    async def ensure_login(self):
        """Ensure the client is logged in, using cookies if available."""
        async with self.login_lock:
            if self.is_logged_in:
                return True

            try:
                # Try loading cookies first
                if os.path.exists(self.cookies_file):
                    logger.info("Loading saved cookies...")
                    try:
                        with open(self.cookies_file, 'r', encoding='utf-8') as f:
                            cookies_data = json.load(f)
                        
                        # Handle Cookie-Editor format (list of dicts)
                        if isinstance(cookies_data, list):
                            cookies_dict = {c['name']: c['value'] for c in cookies_data if 'name' in c and 'value' in c}
                            self.client.set_cookies(cookies_dict)
                        else:
                            # Assume it's already in the format twikit expects or a simple dict
                            self.client.load_cookies(self.cookies_file)
                            
                        self.is_logged_in = True
                        logger.info("Restored session from cookies.")
                        return True
                    except Exception as e:
                        logger.error(f"Failed to parse cookies: {e}")
                        # Don't return False yet, try password login as fallback (though likely to fail if blocked)
                
                # Fallback to password login
                if not TWITTER_USERNAME or not TWITTER_PASSWORD:
                    logger.error("Twitter credentials not found in environment variables!")
                    return False

                logger.info(f"Logging in as {TWITTER_USERNAME}...")
                await self.client.login(
                    auth_info_1=TWITTER_USERNAME,
                    auth_info_2=TWITTER_EMAIL,
                    password=TWITTER_PASSWORD
                )
                self.client.save_cookies(self.cookies_file)
                self.is_logged_in = True
                logger.info("Login successful and cookies saved.")
                return True

            except Exception as e:
                logger.error(f"Login failed: {e}")
                self.is_logged_in = False
                return False

    def cog_unload(self):
        self.check_updates_task.cancel()

    @tasks.loop(minutes=60)
    async def check_updates_task(self):
        await self.check_all_users()

    @check_updates_task.before_loop
    async def before_check_updates(self):
        await self.bot.wait_until_ready()
        # Initial login attempt
        await self.ensure_login()

    async def check_all_users(self):
        if not self.is_logged_in:
            if not await self.ensure_login():
                logger.warning("Skipping check_all_users due to login failure.")
                return

        tracking_data = self.config.get("tracking", {})
        if not tracking_data:
            return

        for username, data in tracking_data.items():
            try:
                await self.check_user(username, data)
                # Random delay to avoid bot detection
                await asyncio.sleep(5) 
            except Exception as e:
                logger.error(f"Error checking {username}: {e}")

    async def check_user(self, username: str, data: dict):
        try:
            # Get user ID first (more reliable) or use screen name directly
            # Twikit get_user_tweets takes user_id. 
            # We might need to cache user_ids to avoid fetching user profile every time.
            user_id = data.get("user_id")
            
            if not user_id:
                user = await self.client.get_user_by_screen_name(username)
                user_id = user.id
                self.config["tracking"][username]["user_id"] = user_id
                self.save_config()

            # Fetch latest tweets
            tweets = await self.client.get_user_tweets(user_id, 'Tweets', count=1)
            
            if not tweets:
                return

            latest_tweet = tweets[0]
            tweet_id = latest_tweet.id
            
            last_id = data.get("last_id")
            
            if tweet_id != last_id:
                logger.info(f"New post found for {username}: {tweet_id}")
                
                self.config["tracking"][username]["last_id"] = tweet_id
                self.save_config()

                channel_ids = data.get("channel_ids", [])
                # Construct X link
                twitter_link = f"https://x.com/{username}/status/{tweet_id}"
                message = f"📢 **{username}** 發布了新貼文！\n{twitter_link}"
                
                for channel_id in channel_ids:
                    try:
                        channel = self.bot.get_channel(channel_id)
                        if channel:
                            await channel.send(message)
                    except Exception as e:
                        logger.error(f"Failed to send notification to {channel_id}: {e}")

        except Exception as e:
            logger.error(f"Failed to fetch tweets for {username}: {e}")
            # If 401/403, maybe session expired
            if "401" in str(e) or "403" in str(e):
                logger.warning("Session might be expired, clearing cookies.")
                if os.path.exists(self.cookies_file):
                    os.remove(self.cookies_file)
                self.is_logged_in = False

    @commands.hybrid_group(name="xtrack", invoke_without_command=True, fallback="help")
    async def xtrack(self, ctx):
        """X (Twitter) 追蹤系統指令 (Twikit v2)"""
        await ctx.send_help(ctx.command)

    async def _resolve_channel(self, ctx, channel_input):
        """解析頻道參數，支援同伺服器頻道和跨伺服器頻道 ID"""
        if channel_input is None:
            # 不提供參數，使用當前頻道
            return ctx.channel
        
        if isinstance(channel_input, discord.TextChannel):
            # 提供了頻道物件（同伺服器）
            return channel_input
        
        # 嘗試作為頻道 ID（跨伺服器）
        try:
            channel_id = int(str(channel_input))
            channel = await self.bot.fetch_channel(channel_id)
            return channel
        except (ValueError, discord.NotFound, discord.Forbidden):
            # 如果解析失敗，嘗試作為同伺服器的頻道名稱或提及
            try:
                converter = commands.TextChannelConverter()
                return await converter.convert(ctx, str(channel_input))
            except:
                raise commands.BadArgument(f"無法解析頻道：{channel_input}")

    @xtrack.command(name="add")
    @commands.has_permissions(administrator=False)
    async def add_tracker(self, ctx, username: str, channel_input=None):
        """新增追蹤用戶。用法: !xtrack add <username> [channel/頻道ID]"""
        try:
            target_channel = await self._resolve_channel(ctx, channel_input)
        except Exception as e:
            await ctx.send(f"❌ 無法解析頻道：{e}")
            return
        
        username = username.replace("@", "")
        
        if username in self.config["tracking"]:
            if target_channel.id not in self.config["tracking"][username]["channel_ids"]:
                 self.config["tracking"][username]["channel_ids"].append(target_channel.id)
                 self.save_config()
                 channel_mention = target_channel.mention if hasattr(target_channel, 'mention') else f"頻道 {target_channel.id}"
                 await ctx.send(f"✅ 已將 {channel_mention} 加入 **{username}** 的通知列表。")
            else:
                 channel_mention = target_channel.mention if hasattr(target_channel, 'mention') else f"頻道 {target_channel.id}"
                 await ctx.send(f"ℹ️ {channel_mention} 已經在追蹤 **{username}** 了。")
        else:
            self.config["tracking"][username] = {
                "channel_ids": [target_channel.id],
                "last_id": None
            }
            self.save_config()
            await ctx.send(f"✅ 開始追蹤 **{username}**！(將在下次檢查時驗證用戶ID)")

    @xtrack.command(name="remove")
    @commands.has_permissions(administrator=False)
    async def remove_tracker(self, ctx, username: str, channel_input=None):
        """移除追蹤。用法: !xtrack remove <username> [channel/頻道ID]"""
        username = username.replace("@", "")
        
        if username not in self.config["tracking"]:
            await ctx.send(f"❌ 找不到追蹤記錄：**{username}**")
            return

        try:
            target_channel = await self._resolve_channel(ctx, channel_input)
        except Exception as e:
            await ctx.send(f"❌ 無法解析頻道：{e}")
            return
        
        if target_channel.id in self.config["tracking"][username]["channel_ids"]:
            self.config["tracking"][username]["channel_ids"].remove(target_channel.id)
            if not self.config["tracking"][username]["channel_ids"]:
                del self.config["tracking"][username]
                await ctx.send(f"✅ 已停止追蹤 **{username}** (無剩餘訂閱頻道)。")
            else:
                channel_mention = target_channel.mention if hasattr(target_channel, 'mention') else f"頻道 {target_channel.id}"
                await ctx.send(f"✅ 已從 {channel_mention} 移除 **{username}** 的通知。")
            self.save_config()
        else:
            channel_mention = target_channel.mention if hasattr(target_channel, 'mention') else f"頻道 {target_channel.id}"
            await ctx.send(f"ℹ️ {channel_mention} 並沒有追蹤 **{username}**。")

    @xtrack.command(name="list")
    async def list_trackers(self, ctx):
        """列出所有追蹤中的用戶"""
        if not self.config["tracking"]:
            await ctx.send("📭 目前沒有追蹤任何用戶。")
            return

        embed = discord.Embed(title="X (Twitter) 追蹤清單", color=discord.Color.blue())
        
        status = "🟢 已登入" if self.is_logged_in else "🔴 未登入 (檢查 .env)"
        embed.set_footer(text=f"系統狀態: {status}")

        for username, data in self.config["tracking"].items():
            channels = [f"<#{cid}>" for cid in data["channel_ids"]]
            channel_text = ", ".join(channels) if channels else "無"
            embed.add_field(name=f"@{username}", value=f"發送到: {channel_text}", inline=False)
            
        await ctx.send(embed=embed)

    @xtrack.command(name="check")
    @commands.has_permissions(administrator=True)
    async def force_check(self, ctx):
        """強制立即檢查更新"""
        await ctx.defer()
        await ctx.send("🔄 正在檢查更新...")
        await self.check_all_users()
        await ctx.send("✅ 檢查完成。")

    @xtrack.command(name="test")
    async def test_push(self, ctx, channel: discord.TextChannel = None):
        """測試推送功能到指定頻道或所有追蹤頻道。用法: !xtrack test [頻道]"""
        await ctx.defer()
        if channel:
            # 測試單一指定頻道
            try:
                target_channel = await self.bot.fetch_channel(channel.id) if hasattr(channel, 'id') else channel
                test_message = "🧪 **X/Twitter 推送測試**\n這是一條測試訊息，用於測試推送功能是否正常運作！"
                await target_channel.send(test_message)
                await ctx.send(f"✅ 測試訊息已發送到 {channel.mention}")
            except Exception as e:
                await ctx.send(f"❌ 測試發送失敗: {e}")
                logger.error(f"X/Twitter 測試推送失敗: {e}")
        else:
            # 測試所有追蹤用戶的頻道
            tracking_data = self.config.get("tracking", {})
            if not tracking_data:
                await ctx.send("❌ 沒有配置任何追蹤用戶，請先使用 `!xtrack add` 添加追蹤")
                return
            
            all_channel_ids = set()
            for username, data in tracking_data.items():
                channel_ids = data.get("channel_ids", [])
                all_channel_ids.update(channel_ids)
            
            if not all_channel_ids:
                await ctx.send("❌ 沒有配置任何推送頻道")
                return
            
            success_count = 0
            failed_count = 0
            test_message = "🧪 **X/Twitter 推送測試**\n這是一條測試訊息，用於測試多頻道推送功能是否正常運作！"
            
            for channel_id in all_channel_ids:
                try:
                    target_channel = await self.bot.fetch_channel(channel_id)
                    await target_channel.send(test_message)
                    success_count += 1
                except Exception as e:
                    failed_count += 1
                    logger.error(f"X/Twitter 測試推送失敗 (頻道 {channel_id}): {e}")
            
            await ctx.send(f"✅ 測試完成！成功: {success_count}，失敗: {failed_count}")

async def setup(bot: commands.Bot):
    await bot.add_cog(XTracker(bot))

