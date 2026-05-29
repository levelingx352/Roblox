import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiohttp
import asyncio
import json
import io
import time
import re
from datetime import datetime
import os

TOKEN = os.environ.get("DISCORD_TOKEN", "YOUR_BOT_TOKEN_HERE")

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
bot = commands.Bot(command_prefix="!", intents=intents)

_config: dict = {"allowed_channel": None}

def load_config():
    global _config
    env = os.environ.get("ALLOWED_CHANNEL_ID", "")
    if env.isdigit():
        _config["allowed_channel"] = int(env)
        return
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            d = json.load(f)
            ch = d.get("allowed_channel")
            _config["allowed_channel"] = int(ch) if ch else None
    except Exception:
        _config = {"allowed_channel": None}

def save_config():
    try:
        with open("config.json", "w", encoding="utf-8") as f:
            json.dump({"allowed_channel": _config.get("allowed_channel")}, f)
    except Exception as e:
        print(f"[WARN] save_config: {e}")

_cooldowns: dict = {}
_active_searches: set = set()
COOLDOWN_SECONDS = 5

def check_cooldown(user_id: int) -> float:
    last = _cooldowns.get(user_id, 0)
    elapsed = time.time() - last
    remaining = COOLDOWN_SECONDS - elapsed
    return max(0.0, remaining)

def set_cooldown(user_id: int):
    _cooldowns[user_id] = time.time()

def _valid_username(name: str) -> bool:
    return bool(re.match(r'^[A-Za-z0-9_]{3,20}$', name))

async def _get(s: aiohttp.ClientSession, url: str, params=None):
    try:
        async with s.get(url, params=params,
                         timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status == 200:
                return await r.json()
    except Exception:
        pass
    return None


async def get_roblox_data(username: str) -> dict | None:
    async with aiohttp.ClientSession() as s:

        try:
            async with s.post(
                "https://users.roblox.com/v1/usernames/users",
                json={"usernames": [username], "excludeBannedUsers": False},
                timeout=aiohttp.ClientTimeout(total=8),
            ) as r:
                if r.status != 200:
                    return None
                resp = await r.json()
        except Exception:
            return None

        users = resp.get("data", [])
        if not users:
            return None
        u = users[0]
        uid         = u["id"]
        disp_name   = u.get("displayName", username)
        actual_name = u.get("name", username)

        results = await asyncio.gather(
            _get(s, f"https://users.roblox.com/v1/users/{uid}"),
            _get(s, "https://thumbnails.roblox.com/v1/users/avatar-headshot",
                 {"userIds": uid, "size": "420x420", "format": "Png", "isCircular": "false"}),
            _get(s, "https://thumbnails.roblox.com/v1/users/avatar",
                 {"userIds": uid, "size": "352x352", "format": "Png", "isCircular": "false"}),
            _get(s, f"https://friends.roblox.com/v1/users/{uid}/friends/count"),
            _get(s, f"https://friends.roblox.com/v1/users/{uid}/followers/count"),
            _get(s, f"https://friends.roblox.com/v1/users/{uid}/followings/count"),
            _get(s, f"https://groups.roblox.com/v1/users/{uid}/groups/roles"),
            _get(s, f"https://badges.roblox.com/v1/users/{uid}/badges",
                 {"limit": 100, "sortOrder": "Desc"}),
            _get(s, f"https://friends.roblox.com/v1/users/{uid}/friends",
                 {"userSort": "Alphabetical"}),
            return_exceptions=False,
        )
        (profile, thumb_hs, thumb_body,
         friends_r, followers_r, following_r,
         groups_r, badges_r, friends_list_r) = results

        presence_data = None
        try:
            async with s.post(
                "https://presence.roblox.com/v1/presence/users",
                json={"userIds": [uid]},
                timeout=aiohttp.ClientTimeout(total=6),
            ) as pr:
                if pr.status == 200:
                    presence_data = await pr.json()
        except Exception:
            pass

        def safe_thumb(data):
            try:
                u2 = (data or {}).get("data", [{}])[0].get("imageUrl", "")
                return u2 if u2 and u2.startswith("http") else None
            except Exception:
                return None

        def parse_date(raw):
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                return dt.strftime("%d %b %Y")
            except Exception:
                return raw[:10] if raw else "Unknown"

        avatar_url = safe_thumb(thumb_hs)
        body_url   = safe_thumb(thumb_body)

        friends_count   = (friends_r  or {}).get("count", 0)
        followers_count = (followers_r or {}).get("count", 0)
        following_count = (following_r or {}).get("count", 0)

        groups, groups_count = [], 0
        if groups_r and groups_r.get("data"):
            groups_count = len(groups_r["data"])
            for g in groups_r["data"][:5]:
                groups.append({
                    "name": g["group"]["name"],
                    "role": g["role"]["name"],
                    "id":   g["group"]["id"],
                })

        badges_count = 0
        if badges_r:
            badges_count = badges_r.get("total", 0) or len(badges_r.get("data", []))

        friend_names = []
        if friends_list_r and friends_list_r.get("data"):
            for f in friends_list_r["data"]:
                name = (f.get("name") or "").strip()
                if name:
                    friend_names.append(name)
                if len(friend_names) >= 10:
                    break

        is_online, last_online = False, ""
        if presence_data and presence_data.get("userPresences"):
            p = presence_data["userPresences"][0]
            is_online = p.get("userPresenceType", 0) > 0
            raw = p.get("lastOnline", "")
            if raw:
                try:
                    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                    last_online = dt.strftime("%d/%m/%Y %H:%M UTC")
                except Exception:
                    last_online = raw[:16]

        description, is_banned, joined = "", False, "Unknown"
        if profile:
            description = (profile.get("description") or "").strip()
            is_banned   = profile.get("isBanned", False)
            joined      = parse_date(profile.get("created", ""))

        return {
            "username":        actual_name,
            "display_name":    disp_name,
            "user_id":         uid,
            "joined":          joined,
            "avatar_url":      avatar_url,
            "body_url":        body_url,
            "friends_count":   friends_count,
            "followers_count": followers_count,
            "following_count": following_count,
            "groups_count":    groups_count,
            "groups":          groups,
            "badges_count":    badges_count,
            "friend_names":    friend_names,
            "description":     description,
            "is_banned":       is_banned,
            "is_online":       is_online,
            "last_online":     last_online,
            "limiteds_count":  0,
        }

async def get_friends_data(user_id: int) -> list[dict] | None:
    async with aiohttp.ClientSession() as s:
        friends_r = await _get(s, f"https://friends.roblox.com/v1/users/{user_id}/friends",
                               {"userSort": "Alphabetical"})
        if not friends_r or not friends_r.get("data"):
            return []

        friends = friends_r["data"][:20]
        ids     = [str(f["id"]) for f in friends]

        thumbs = await _get(s, "https://thumbnails.roblox.com/v1/users/avatar-headshot",
                            {"userIds": ",".join(ids), "size": "150x150",
                             "format": "Png", "isCircular": "false"})
        thumb_map = {}
        if thumbs and thumbs.get("data"):
            for t in thumbs["data"]:
                thumb_map[t["targetId"]] = t.get("imageUrl", "")

        result = []
        for f in friends:
            result.append({
                "id":     f["id"],
                "name":   f.get("name", ""),
                "disp":   f.get("displayName", ""),
                "thumb":  thumb_map.get(f["id"], ""),
            })
        return result


BRAND  = "LevelingX"
BANNER = "https://i.imgur.com/roblox_banner_placeholder.png"

def build_main_embed(d: dict, page: str = "main") -> discord.Embed:
    uid  = d["user_id"]
    purl = f"https://www.roblox.com/users/{uid}/profile"

    if d["is_banned"]:
        color = 0xFF2244
    elif d["is_online"]:
        color = 0x00FF88
    else:
        color = 0x7289DA

    status_icon = "🟢" if d["is_online"] else ("🚫" if d["is_banned"] else "⚫")
    banned_tag  = "  `[ BANNED ]`" if d["is_banned"] else ""

    embed = discord.Embed(
        title=f"{status_icon}  {d['username']}{banned_tag}",
        url=purl,
        color=color,
    )

    if d["avatar_url"]:
        embed.set_thumbnail(url=d["avatar_url"])
    if d["body_url"]:
        embed.set_image(url=d["body_url"])

    embed.add_field(name="✨ Display Name",  value=f"`{d['display_name']}`", inline=True)
    embed.add_field(name="🪪 User ID",       value=f"`{uid}`",              inline=True)
    embed.add_field(name="📅 Joined",        value=f"`{d['joined']}`",      inline=True)

    if d["is_online"]:
        status_val = "🟢 **Online**"
    else:
        status_val = f"⚫ Offline\n`{d['last_online'] or 'Unknown'}`"
    embed.add_field(name="🔵 Status",   value=status_val,                   inline=True)
    embed.add_field(name="👥 Friends",  value=f"**{d['friends_count']:,}**", inline=True)
    embed.add_field(name="📈 Followers",value=f"**{d['followers_count']:,}**",inline=True)

    embed.add_field(name="➡️ Following", value=f"**{d['following_count']:,}**", inline=True)
    embed.add_field(name="🏆 Badges",   value=f"**{d['badges_count']:,}**",     inline=True)
    embed.add_field(name="🛡️ Groups",   value=f"**{d['groups_count']}**",       inline=True)

    if d["friend_names"]:
        chunk1 = d["friend_names"][:5]
        chunk2 = d["friend_names"][5:10]
        lines  = ["```"]
        lines += [f"• {n}" for n in chunk1]
        if chunk2:
            lines += [f"• {n}" for n in chunk2]
        if d["friends_count"] > 10:
            lines.append(f"... และอีก {d['friends_count'] - 10:,} คน")
        lines.append("```")
        embed.add_field(
            name=f"💛 เพื่อน ({min(len(d['friend_names']), 10)}/{d['friends_count']})",
            value="\n".join(lines),
            inline=False,
        )
    else:
        embed.add_field(name="💛 เพื่อน", value="`ไม่มีเพื่อน`", inline=False)

    if d["groups"]:
        glines = []
        for g in d["groups"][:4]:
            glines.append(f"╰ **[{g['name']}](https://www.roblox.com/groups/{g['id']})** — *{g['role']}*")
        if d["groups_count"] > 4:
            glines.append(f"*... และอีก {d['groups_count'] - 4} กลุ่ม*")
        embed.add_field(name="🗂️ กลุ่ม", value="\n".join(glines), inline=False)

    if d["description"]:
        bio = d["description"][:250] + ("…" if len(d["description"]) > 250 else "")
        embed.add_field(name="📝 Bio", value=f"> {bio}", inline=False)

    embed.set_footer(
        text=f"Developer | LevelingX  •  {datetime.utcnow().strftime('%d/%m/%Y %H:%M')} UTC"
    )
    embed.timestamp = discord.utils.utcnow()
    return embed


def build_friends_embed(d: dict, friends: list[dict]) -> discord.Embed:
    uid  = d["user_id"]
    purl = f"https://www.roblox.com/users/{uid}/profile"

    embed = discord.Embed(
        title=f"👥  รายชื่อเพื่อนของ  {d['username']}",
        url=purl,
        color=0xFFB347,
        description=f"แสดง **{len(friends)}** จาก **{d['friends_count']:,}** เพื่อนทั้งหมด",
    )
    if d["avatar_url"]:
        embed.set_thumbnail(url=d["avatar_url"])

    if not friends:
        embed.add_field(name="❌", value="ไม่มีเพื่อน หรือ โปรไฟล์เป็น Private", inline=False)
    else:
        col1 = friends[:10]
        col2 = friends[10:20]
        def fmt(lst):
            return "\n".join(
                f"`{i+1:02d}.` [{f['name']}](https://www.roblox.com/users/{f['id']}/profile)"
                for i, f in enumerate(lst)
            ) or "—"
        embed.add_field(name="เพื่อน (1-10)",  value=fmt(col1), inline=True)
        if col2:
            embed.add_field(name="เพื่อน (11-20)", value=fmt(col2), inline=True)

    embed.set_footer(text=f"Developer | LevelingX  •  Friends List  •  {datetime.utcnow().strftime('%d/%m/%Y %H:%M')} UTC")
    embed.timestamp = discord.utils.utcnow()
    return embed


def build_txt(d: dict) -> str:
    sep = "═" * 50
    lines = [
        sep,
        "        ROBLOX USER PROFILE — LevelingX Bot",
        sep,
        f"  Username      : {d['username']}",
        f"  Display Name  : {d['display_name']}",
        f"  User ID       : {d['user_id']}",
        f"  Profile URL   : https://www.roblox.com/users/{d['user_id']}/profile",
        f"  Joined        : {d['joined']}",
        f"  Status        : {'Online' if d['is_online'] else 'Offline'}",
        f"  Last Online   : {d['last_online'] or 'Unknown'}",
        f"  Banned        : {'YES 🚫' if d['is_banned'] else 'NO'}",
        "",
        "  — Bio —",
        f"  {d['description'] or '(ไม่มี bio)'}",
        "",
        "  — Social Stats —",
        f"  Friends       : {d['friends_count']:,}",
        f"  Followers     : {d['followers_count']:,}",
        f"  Following     : {d['following_count']:,}",
        f"  Friend Sample : {', '.join(d['friend_names']) or 'N/A'}",
        "",
        "  — Groups —",
        f"  Total         : {d['groups_count']}",
    ]
    for g in d["groups"]:
        lines.append(f"    • {g['name']} [{g['role']}]  → roblox.com/groups/{g['id']}")
    lines += [
        "",
        "  — Inventory —",
        f"  Badges        : {d['badges_count']:,}",
        f"  Limiteds      : {d['limiteds_count']}",
        "",
        sep,
        f"  Generated : {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
        sep,
    ]
    return "\n".join(lines)


class ProfileView(discord.ui.View):
    def __init__(self, data: dict, current_page: str = "main"):
        super().__init__(timeout=300)
        self.data         = data
        self.current_page = current_page
        self._loading     = False

        self.add_item(discord.ui.Button(
            label="เปิดโปรไฟล์",
            style=discord.ButtonStyle.link,
            url=f"https://www.roblox.com/users/{data['user_id']}/profile",
            emoji="🌐",
            row=1,
        ))

    @discord.ui.button(label="ดูเพื่อนในเกม", style=discord.ButtonStyle.primary,
                       emoji="👥", row=0)
    async def friends_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self._loading:
            await interaction.response.send_message("⏳ กำลังโหลดอยู่ รอแปปนึงครับ", ephemeral=True)
            return

        if self.current_page == "friends":
            self.current_page = "main"
            button.label  = "ดูเพื่อนในเกม"
            button.style  = discord.ButtonStyle.primary
            await interaction.response.edit_message(
                embed=build_main_embed(self.data), view=self
            )
            return

        self._loading = True
        button.label  = "⏳ กำลังโหลด..."
        button.style  = discord.ButtonStyle.secondary
        await interaction.response.edit_message(view=self)

        friends = await get_friends_data(self.data["user_id"])
        self._loading     = False
        self.current_page = "friends"
        button.label  = "◀ กลับหน้าหลัก"
        button.style  = discord.ButtonStyle.success

        await interaction.message.edit(
            embed=build_friends_embed(self.data, friends or []),
            view=self,
        )

    @discord.ui.button(label="ดาวน์โหลดข้อมูล", style=discord.ButtonStyle.secondary,
                       emoji="📄", row=0)
    async def download_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        txt  = build_txt(self.data)
        file = discord.File(
            io.BytesIO(txt.encode("utf-8")),
            filename=f"roblox_{self.data['username']}.txt",
        )
        await interaction.response.send_message(
            f"📁 ข้อมูลของ **{self.data['username']}**",
            file=file,
            ephemeral=True,
        )
        button.disabled = True
        await interaction.message.edit(view=self)

    async def on_timeout(self):
        for child in self.children:
            if hasattr(child, "disabled"):
                child.disabled = True
        try:
            pass
        except Exception:
            pass


_status_index = 0

@tasks.loop(minutes=5)
async def update_status():
    global _status_index
    guild_count = len(bot.guilds)
    statuses = [
        discord.Activity(type=discord.ActivityType.playing,
                         name="Developer | LevelingX"),
        discord.Activity(type=discord.ActivityType.playing,
                         name=f"กำลังถูกใช้งานบน {guild_count:,} เซิร์ฟเวอร์"),
    ]
    await bot.change_presence(
        status=discord.Status.online,
        activity=statuses[_status_index % len(statuses)],
    )
    _status_index += 1


@bot.event
async def on_ready():
    load_config()
    print(f"✅ Bot ready: {bot.user}  (id={bot.user.id})")
    print(f"   Guilds : {len(bot.guilds)}")
    update_status.start()
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"❌ Slash sync error: {e}")

@bot.event
async def on_command_error(ctx: commands.Context, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ ใช้คำสั่งแต่ทำไมมึงไม่ใส่่ชื่อในเกมว่ะไอเหี้ย: `!rb <username>`", delete_after=8)
        return
    print(f"[ERROR] {ctx.command}: {error}")


@bot.command(name="rb")
async def rb(ctx: commands.Context, *, username: str = None):

    if ctx.author.bot:
        return

    if username and username.strip().lower() == "help":
        embed = discord.Embed(
            title="📖  วิธีใช้คำสั่ง",
            description=(
                "```!rb <username>```\n"
                "ค้นหาข้อมูลโปรไฟล์ Roblox\n\n"
                "**ตัวอย่าง:**\n"
                "`!rb kuy56`\n"
                "`!rb sixseven_67`"
            ),
            color=0x5865F2,
        )
        embed.set_footer(text="Developer | LevelingX")
        embed.timestamp = discord.utils.utcnow()
        await ctx.send(embed=embed)
        return

    allowed = _config.get("allowed_channel")
    if allowed and ctx.channel.id != int(allowed):
        try:
            await ctx.message.delete()
        except Exception:
            pass
        await ctx.send(f"⛔ มึงใช้ได้เฉพาะ <#{allowed}> อย่ามึนให้มันมาก", delete_after=7)
        return

    if not username:
        await ctx.send("❌ ใส่ชื่อด้วยดิ ไม่ใส่ชื่อกูจะค้นให้ยังไง `!rb <username>`", delete_after=8)
        return

    username = username.strip()
    if not _valid_username(username):
        embed = discord.Embed(
            title="❌ ชื่อที่มึงให้ค้น มันไม่ถูก",
            description="ชื่อ Roblox ต้องยาว **3-20** ตัวอักษร\nใช้ได้เฉพาะ `A-Z`, `0-9`, `_`",
            color=0xFF4444,
        )
        await ctx.send(embed=embed, delete_after=10)
        return

    uid = ctx.author.id
    remaining = check_cooldown(uid)
    if remaining > 0:
        await ctx.send(
            f"⏳ รอ **{remaining:.1f}** มึงรีบไปไหนว่ะไอสัส!",
            delete_after=5,
        )
        return

    if uid in _active_searches:
        await ctx.send("⏳ กำลังค้นให้อยู่ เร่งพ่อมึงตาย", delete_after=5)
        return

    _active_searches.add(uid)
    set_cooldown(uid)

    loading = discord.Embed(
        description=f"⏳ กูกำลังค้น **{username}** …",
        color=0x5865F2,
    )
    msg = await ctx.send(embed=loading)

    try:
        data = await get_roblox_data(username)
    except Exception as e:
        print(f"[ERROR] get_roblox_data: {e}")
        data = None
    finally:
        _active_searches.discard(uid)

    if not data:
        err = discord.Embed(
            title="❌ กูค้นไม่เจอว่ะ",
            description=f"กูไม่เจอ **{username}** บน Roblox\nมึงลองเช็กละส่งมาใหม่",
            color=0xFF4444,
        )
        await msg.edit(embed=err)
        return

    await msg.edit(embed=build_main_embed(data), view=ProfileView(data))


@bot.tree.command(name="setup", description="ตั้งช่องที่ให้ใช้คำสั่งได้")
@app_commands.describe(channel="ช่องที่อนุญาต")
@app_commands.checks.has_permissions(administrator=True)
async def setup_cmd(interaction: discord.Interaction, channel: discord.TextChannel):
    _config["allowed_channel"] = channel.id
    save_config()
    embed = discord.Embed(
        title="✅ ตั้งค่าสำเร็จ!",
        description=(
            f"คำสั่ง `!rb` ใช้ได้เฉพาะ {channel.mention}\n\n"
            f"ถ้าใครพิมพ์ผิดช่อง บอทจะลบข้อความและแจ้งเตือนอัตโนมัติ"
        ),
        color=0x00E676,
    )
    embed.set_footer(text=f"{BRAND} • Setup")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@setup_cmd.error
async def setup_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "❌ มึงไม่มีสิทธิ์แอดมิน มึงใช้คำสั่งกูไม่ได้หรอก", ephemeral=True
        )
    else:
        await interaction.response.send_message(f"❌ Error: {error}", ephemeral=True)


from aiohttp import web as _web

async def _ping(request):
    guilds = len(bot.guilds)
    users  = sum(g.member_count or 0 for g in bot.guilds)
    return _web.Response(
        text=f"✅ LevelingX Bot alive | guilds={guilds} | users={users}"
    )

async def start_webserver():
    app = _web.Application()
    app.router.add_get("/", _ping)
    runner = _web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    await _web.TCPSite(runner, "0.0.0.0", port).start()
    print(f"🌐 Keep-alive → port {port}")


async def main():
    load_config()
    await start_webserver()
    await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
