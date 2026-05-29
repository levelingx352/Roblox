"""
LevelingX Roblox Bot  —  bug-fixed & upgraded
คำสั่ง: !rb <username>   |   /setup #channel  (Admin)
"""

import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import asyncio
import json
import io
from datetime import datetime
import os

# ─── CONFIG ───────────────────────────────────────────────
TOKEN = os.environ.get("DISCORD_TOKEN", "YOUR_BOT_TOKEN_HERE")
# allowed channel จะถูกโหลดจาก env var ALLOWED_CHANNEL_ID
# หรือตั้งค่าด้วย /setup แล้วบันทึกลง config.json
# ──────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ─── config helpers ───────────────────────────────────────
_config: dict = {"allowed_channel": None}

def load_config():
    global _config
    # ลอง env var ก่อน (Render-friendly)
    env_ch = os.environ.get("ALLOWED_CHANNEL_ID")
    if env_ch and env_ch.isdigit():
        _config["allowed_channel"] = int(env_ch)
        return
    # ถ้าไม่มี env ให้ลอง config.json
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            ch = data.get("allowed_channel")
            _config["allowed_channel"] = int(ch) if ch else None
    except Exception:
        _config = {"allowed_channel": None}

def save_config():
    try:
        with open("config.json", "w", encoding="utf-8") as f:
            json.dump({"allowed_channel": _config.get("allowed_channel")}, f)
    except Exception as e:
        print(f"[WARN] save_config failed: {e}")

# ─── Roblox API ───────────────────────────────────────────

async def _get(session: aiohttp.ClientSession, url: str, params=None):
    """GET แล้ว return dict หรือ None ถ้า error"""
    try:
        async with session.get(url, params=params,
                               timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status == 200:
                return await r.json()
    except Exception:
        pass
    return None

async def get_roblox_data(username: str) -> dict | None:
    async with aiohttp.ClientSession() as s:

        # 1. Resolve username → user_id
        try:
            async with s.post(
                "https://users.roblox.com/v1/usernames/users",
                json={"usernames": [username], "excludeBannedUsers": False},
                timeout=aiohttp.ClientTimeout(total=8)
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
        uid          = u["id"]
        disp_name    = u.get("displayName", username)
        actual_name  = u.get("name", username)

        # 2. Run independent requests concurrently  ← bug-fix: faster + safer
        (
            profile,
            thumb_hs,
            thumb_body,
            friends_r,
            followers_r,
            following_r,
            groups_r,
            badges_r,
            friends_list_r,
        ) = await asyncio.gather(
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

        # 3. Presence (separate — different base domain, may need cookie)
        presence_data = None
        try:
            async with s.post(
                "https://presence.roblox.com/v1/presence/users",
                json={"userIds": [uid]},
                timeout=aiohttp.ClientTimeout(total=6)
            ) as pr:
                if pr.status == 200:
                    presence_data = await pr.json()
        except Exception:
            pass

        # ── Parse avatar URLs (validate they're real images) ──
        def safe_thumb(data, key="imageUrl"):
            try:
                url = data["data"][0].get(key, "") if data and data.get("data") else ""
                return url if url and url.startswith("http") else None
            except Exception:
                return None

        avatar_url = safe_thumb(thumb_hs)
        body_url   = safe_thumb(thumb_body)

        # ── Parse counts ──
        friends_count   = (friends_r  or {}).get("count", 0)
        followers_count = (followers_r or {}).get("count", 0)
        following_count = (following_r or {}).get("count", 0)

        # ── Groups ──
        groups      = []
        groups_count = 0
        if groups_r and groups_r.get("data"):
            groups_count = len(groups_r["data"])
            for g in groups_r["data"][:5]:
                groups.append({
                    "name": g["group"]["name"],
                    "role": g["role"]["name"],
                    "id":   g["group"]["id"],
                })

        # ── Badges ──
        badges_count = 0
        if badges_r:
            badges_count = badges_r.get("total", 0) or len(badges_r.get("data", []))

        # ── Friend names ──
        friend_names = []
        if friends_list_r and friends_list_r.get("data"):
            friend_names = [f.get("name", "") for f in friends_list_r["data"][:8]]

        # ── Presence ──
        is_online   = False
        last_online = ""
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

        # ── Join date ──
        joined = "Unknown"
        if profile and profile.get("created"):
            try:
                dt = datetime.fromisoformat(profile["created"].replace("Z", "+00:00"))
                joined = dt.strftime("%d %b %Y")
            except Exception:
                joined = profile["created"][:10]

        description = ""
        is_banned   = False
        if profile:
            description = (profile.get("description") or "").strip()
            is_banned   = profile.get("isBanned", False)

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


# ─── Embed builder ────────────────────────────────────────

def build_embed(d: dict) -> discord.Embed:
    if d["is_banned"]:
        color = 0xFF3333
    elif d["is_online"]:
        color = 0x00E676
    else:
        color = 0x5865F2   # blurple

    profile_url = f"https://www.roblox.com/users/{d['user_id']}/profile"
    status_dot  = "🟢" if d["is_online"] else "🔴" if d["is_banned"] else "⚫"
    banned_str  = "  **[ 🚫 BANNED ]**" if d["is_banned"] else ""

    # BUG-FIX: title ต้องมีถ้าจะใช้ url ใน embed
    embed = discord.Embed(
        title=f"{status_dot}  {d['username']}{banned_str}",
        url=profile_url,
        color=color,
    )

    # ── Headshot ──
    if d["avatar_url"]:
        embed.set_thumbnail(url=d["avatar_url"])

    # ── Full body (image) — only if URL is valid ──
    if d["body_url"]:
        embed.set_image(url=d["body_url"])

    # ── Display name + ID ──
    embed.add_field(
        name="✨ Display Name",
        value=f"`{d['display_name']}`",
        inline=True,
    )
    embed.add_field(
        name="🪪 User ID",
        value=f"`{d['user_id']}`",
        inline=True,
    )
    embed.add_field(name="\u200b", value="\u200b", inline=True)

    # ── Join date & status ──
    online_val = "🟢 **Online**" if d["is_online"] else f"⚫ Offline\n`{d['last_online'] or 'Unknown'}`"
    embed.add_field(name="📅 เข้าร่วมเมื่อ", value=f"`{d['joined']}`", inline=True)
    embed.add_field(name="🔵 สถานะ",          value=online_val,          inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)

    # ── Social stats ──
    embed.add_field(name="👥 Friends",    value=f"**{d['friends_count']:,}**",   inline=True)
    embed.add_field(name="📈 Followers",  value=f"**{d['followers_count']:,}**", inline=True)
    embed.add_field(name="➡️ Following",  value=f"**{d['following_count']:,}**", inline=True)

    # ── Friend sample ──
    if d["friend_names"]:
        sample = "  •  ".join(d["friend_names"][:6])
        if len(d["friend_names"]) > 6:
            sample += "  ..."
        embed.add_field(name="💛 เพื่อนบางส่วน", value=sample, inline=False)

    # ── Groups ──
    if d["groups"]:
        lines = [f"**{d['groups_count']}** กลุ่มทั้งหมด"]
        for g in d["groups"][:4]:
            lines.append(
                f"╰ [{g['name']}](https://www.roblox.com/groups/{g['id']}) — *{g['role']}*"
            )
        group_val = "\n".join(lines)
    else:
        group_val = "*ไม่ได้เข้ากลุ่ม*"
    embed.add_field(name="🛡️ กลุ่ม", value=group_val, inline=False)

    # ── Badges / Limiteds ──
    embed.add_field(name="🏆 Badges",   value=f"**{d['badges_count']:,}** ชิ้น",    inline=True)
    embed.add_field(name="💎 Limiteds", value=f"**{d['limiteds_count']}** Limited", inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)

    # ── Bio ──
    if d["description"]:
        bio = d["description"][:300] + ("..." if len(d["description"]) > 300 else "")
        embed.add_field(name="📝 Bio", value=f"> {bio}", inline=False)

    embed.set_footer(
        text=f"LevelingX Bot  •  {datetime.utcnow().strftime('%d/%m/%Y %H:%M')} UTC"
    )
    embed.timestamp = discord.utils.utcnow()
    return embed


def build_txt(d: dict) -> str:
    sep = "=" * 48
    lines = [
        sep,
        "        ROBLOX USER PROFILE  —  LevelingX Bot",
        sep,
        f"  Username      : {d['username']}",
        f"  Display Name  : {d['display_name']}",
        f"  User ID       : {d['user_id']}",
        f"  Profile URL   : https://www.roblox.com/users/{d['user_id']}/profile",
        f"  Joined        : {d['joined']}",
        f"  Status        : {'Online' if d['is_online'] else 'Offline'}",
        f"  Last Online   : {d['last_online'] or 'Unknown'}",
        f"  Banned        : {'YES' if d['is_banned'] else 'NO'}",
        "",
        "  — Bio —",
        f"  {d['description'] or '(none)'}",
        "",
        "  — Social —",
        f"  Friends       : {d['friends_count']:,}",
        f"  Followers     : {d['followers_count']:,}",
        f"  Following     : {d['following_count']:,}",
        f"  Friend Sample : {', '.join(d['friend_names']) or 'N/A'}",
        "",
        "  — Groups —",
        f"  Total         : {d['groups_count']}",
    ]
    for g in d["groups"]:
        lines.append(f"    • {g['name']} [{g['role']}]")
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


# ─── UI View ──────────────────────────────────────────────

class ProfileView(discord.ui.View):
    def __init__(self, data: dict):
        super().__init__(timeout=300)
        self.data = data
        self.add_item(discord.ui.Button(
            label="เปิดโปรไฟล์",
            style=discord.ButtonStyle.link,
            url=f"https://www.roblox.com/users/{data['user_id']}/profile",
            emoji="🌐",
        ))

    @discord.ui.button(label="ดาวน์โหลดข้อมูล", style=discord.ButtonStyle.secondary, emoji="📄")
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


# ─── Bot events ───────────────────────────────────────────

@bot.event
async def on_ready():
    load_config()
    print(f"✅ Logged in as {bot.user}  (id={bot.user.id})")
    await bot.change_presence(
        status=discord.Status.online,
        activity=discord.Activity(
            type=discord.ActivityType.playing,
            name="Developer | LevelingX",
        ),
    )
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"❌ Slash sync error: {e}")


# ─── !rb command ──────────────────────────────────────────

@bot.command(name="rb")
async def rb(ctx: commands.Context, *, username: str = None):
    """ค้นหาข้อมูล Roblox  →  !rb <username>"""

    # ── channel guard ──
    allowed = _config.get("allowed_channel")
    if allowed and ctx.channel.id != allowed:
        try:
            await ctx.message.delete()
        except Exception:
            pass
        warn = await ctx.send(
            f"⛔ คำสั่งนี้ใช้ได้เฉพาะ <#{allowed}> นะครับ!",
            delete_after=7,
        )
        return

    if not username:
        await ctx.send("❌ ใส่ชื่อด้วยนะ: `!rb <username>`", delete_after=8)
        return

    # ── loading message (BUG-FIX: ไม่ใช้ custom emoji ที่ไม่มีจริง) ──
    loading_embed = discord.Embed(
        description=f"⏳ กำลังค้นหา **{username}** …",
        color=0x5865F2,
    )
    msg = await ctx.send(embed=loading_embed)

    data = await get_roblox_data(username)

    if not data:
        err_embed = discord.Embed(
            title="❌ ไม่พบผู้ใช้",
            description=f"ไม่เจอ **{username}** บน Roblox\nลองเช็คตัวสะกดอีกครั้ง",
            color=0xFF4444,
        )
        await msg.edit(embed=err_embed)
        return

    await msg.edit(embed=build_embed(data), view=ProfileView(data))


# ─── /setup command ───────────────────────────────────────

@bot.tree.command(name="setup", description="[Admin] ตั้งช่องที่ให้ใช้คำสั่ง !rb ได้")
@app_commands.describe(channel="ช่องที่อนุญาต")
@app_commands.checks.has_permissions(administrator=True)
async def setup_cmd(interaction: discord.Interaction, channel: discord.TextChannel):
    _config["allowed_channel"] = channel.id
    save_config()

    embed = discord.Embed(
        title="✅ ตั้งค่าสำเร็จ!",
        description=(
            f"คำสั่ง `!rb` จะใช้ได้เฉพาะ {channel.mention} เท่านั้น\n\n"
            f"ถ้าใครพิมพ์ผิดช่อง บอทจะลบข้อความและแจ้งเตือนอัตโนมัติครับ"
        ),
        color=0x00E676,
    )
    embed.set_footer(text="LevelingX Bot • Setup")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@setup_cmd.error
async def setup_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "❌ ต้องมีสิทธิ์ **Administrator** ครับ", ephemeral=True
        )
    else:
        await interaction.response.send_message(
            f"❌ เกิดข้อผิดพลาด: {error}", ephemeral=True
        )


# ─── Keep-alive web server (สำหรับ Render) ───────────────

from aiohttp import web as _web

async def _handle(request):
    return _web.Response(text="✅ LevelingX Bot is running!")

async def start_webserver():
    app = _web.Application()
    app.router.add_get("/", _handle)
    runner = _web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    await _web.TCPSite(runner, "0.0.0.0", port).start()
    print(f"🌐 Keep-alive web server → port {port}")


# ─── Entry point ──────────────────────────────────────────

async def main():
    load_config()
    await start_webserver()
    await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
