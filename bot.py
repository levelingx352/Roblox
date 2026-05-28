import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import asyncio
import json
import io
from datetime import datetime
import os

TOKEN = os.environ.get("DISCORD_TOKEN", "YOUR_BOT_TOKEN_HERE")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


async def fetch(session, url, params=None):
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status == 200:
                return await r.json()
    except Exception:
        pass
    return None

async def get_roblox_data(username: str):
    async with aiohttp.ClientSession() as session:

        user_data = await fetch(
            session,
            "https://users.roblox.com/v1/usernames/users",
        )
        async with session.post(
            "https://users.roblox.com/v1/usernames/users",
            json={"usernames": [username], "excludeBannedUsers": False},
            timeout=aiohttp.ClientTimeout(total=10)
        ) as r:
            if r.status != 200:
                return None
            data = await r.json()
            users = data.get("data", [])
            if not users:
                return None
            user = users[0]
            user_id = user["id"]
            display_name = user.get("displayName", username)
            actual_username = user.get("name", username)

        profile = await fetch(session, f"https://users.roblox.com/v1/users/{user_id}")

        thumb = await fetch(
            session,
            "https://thumbnails.roblox.com/v1/users/avatar-headshot",
            params={"userIds": user_id, "size": "420x420", "format": "Png", "isCircular": False}
        )
        avatar_url = None
        if thumb and thumb.get("data"):
            avatar_url = thumb["data"][0].get("imageUrl")

        friends = await fetch(session, f"https://friends.roblox.com/v1/users/{user_id}/friends/count")
        friends_count = friends.get("count", 0) if friends else 0

        followers = await fetch(session, f"https://friends.roblox.com/v1/users/{user_id}/followers/count")
        following = await fetch(session, f"https://friends.roblox.com/v1/users/{user_id}/followings/count")
        followers_count = followers.get("count", 0) if followers else 0
        following_count = following.get("count", 0) if following else 0

        groups_data = await fetch(session, f"https://groups.roblox.com/v1/users/{user_id}/groups/roles")
        groups = []
        if groups_data and groups_data.get("data"):
            for g in groups_data["data"][:5]:
                groups.append({
                    "name": g["group"]["name"],
                    "role": g["role"]["name"],
                    "id": g["group"]["id"]
                })
        groups_count = len(groups_data["data"]) if groups_data and groups_data.get("data") else 0

        badges = await fetch(
            session,
            f"https://badges.roblox.com/v1/users/{user_id}/badges",
            params={"limit": 10, "sortOrder": "Desc"}
        )
        badges_count = badges.get("total", 0) if badges else 0
        if badges_count == 0 and badges and badges.get("data"):
            badges_count = len(badges["data"])

        friends_list = await fetch(
            session,
            f"https://friends.roblox.com/v1/users/{user_id}/friends",
            params={"userSort": "Alphabetical"}
        )
        friend_names = []
        if friends_list and friends_list.get("data"):
            for f in friends_list["data"][:8]:
                friend_names.append(f.get("name", ""))

        joined_raw = profile.get("created", "") if profile else ""
        joined = ""
        if joined_raw:
            try:
                dt = datetime.fromisoformat(joined_raw.replace("Z", "+00:00"))
                joined = dt.strftime("%Y-%m-%d")
            except Exception:
                joined = joined_raw[:10]

        description = profile.get("description", "") if profile else ""
        is_banned = profile.get("isBanned", False) if profile else False

        return {
            "username": actual_username,
            "display_name": display_name,
            "user_id": user_id,
            "joined": joined,
            "avatar_url": avatar_url,
            "friends_count": friends_count,
            "followers_count": followers_count,
            "following_count": following_count,
            "groups_count": groups_count,
            "groups": groups,
            "badges_count": badges_count,
            "friend_names": friend_names,
            "description": description,
            "is_banned": is_banned,
            "limiteds_count": 0,  
        }


def build_embed(data: dict) -> discord.Embed:
    color = 0xFF4444 if data["is_banned"] else 0x00CFFF

    embed = discord.Embed(
        title="<:roblox:0> ROBLOX USER PROFILE" if False else "🎮 ROBLOX USER PROFILE",
        color=color
    )

    if data["avatar_url"]:
        embed.set_thumbnail(url=data["avatar_url"])

    embed.add_field(
        name="📌 Username",
        value=f"`{data['username']}`{'  🔴 BANNED' if data['is_banned'] else ''}",
        inline=True
    )
    embed.add_field(
        name="✨ Display Name",
        value=f"`{data['display_name']}`",
        inline=True
    )
    embed.add_field(
        name="🪪 User ID",
        value=f"`{data['user_id']}`",
        inline=True
    )

    embed.add_field(
        name="📅 วันที่เข้าร่วม (Joined)",
        value=f"📆 `{data['joined']}`",
        inline=False
    )

    friend_sample = ", ".join(data["friend_names"][:7]) + ("..." if len(data["friend_names"]) >= 7 else "")
    embed.add_field(
        name="📊 สถิติทางสังคม (Social Connect)",
        value=(
            f"👥 เพื่อนทั้งหมด: **{data['friends_count']}** คน\n"
            f"📈 ผู้ติดตาม: **{data['followers_count']}** | กำลังติดตาม: **{data['following_count']}**\n"
            f"💛 ตัวอย่างรายชื่อเพื่อน:\n"
            f"┗ {friend_sample if friend_sample else '_ไม่มีเพื่อน_'}"
        ),
        inline=False
    )

    group_text = f"🏰 **{data['groups_count']}** กลุ่ม"
    if data["groups"]:
        for g in data["groups"][:3]:
            group_text += f"\n┗ [{g['name']}](https://www.roblox.com/groups/{g['id']}) — *{g['role']}*"
    embed.add_field(name="🛡️ กลุ่มที่เข้าร่วม", value=group_text, inline=False)

    embed.add_field(
        name="🏆 ความสำเร็จ",
        value=f"🥇 **{data['badges_count']}** ชิ้น",
        inline=True
    )
    embed.add_field(
        name="💎 ของสะสม",
        value=f"✨ **{data['limiteds_count']}** Limited",
        inline=True
    )

    embed.set_footer(text="🔒 ดึงข้อมูลสำเร็จเรียบร้อย • กดปุ่มด้านล่างเพื่อรับไฟล์ดิบ")
    embed.timestamp = discord.utils.utcnow()

    return embed


def build_txt(data: dict) -> str:
    lines = [
        "=" * 40,
        "   ROBLOX USER PROFILE - LevelingX Bot",
        "=" * 40,
        f"Username      : {data['username']}",
        f"Display Name  : {data['display_name']}",
        f"User ID       : {data['user_id']}",
        f"Joined        : {data['joined']}",
        f"Banned        : {'YES' if data['is_banned'] else 'NO'}",
        "",
        "--- Social ---",
        f"Friends       : {data['friends_count']}",
        f"Followers     : {data['followers_count']}",
        f"Following     : {data['following_count']}",
        f"Friend Sample : {', '.join(data['friend_names'])}",
        "",
        "--- Groups ---",
        f"Total Groups  : {data['groups_count']}",
    ]
    for g in data["groups"]:
        lines.append(f"  - {g['name']} ({g['role']})")

    lines += [
        "",
        "--- Inventory ---",
        f"Badges        : {data['badges_count']}",
        f"Limiteds      : {data['limiteds_count']}",
        "",
        "=" * 40,
        f"Generated by LevelingX Bot | {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
    ]
    return "\n".join(lines)



class DownloadView(discord.ui.View):
    def __init__(self, data: dict):
        super().__init__(timeout=120)
        self.data = data

    @discord.ui.button(label="ดาวน์โหลดข้อมูลทั้งหมด 📄", style=discord.ButtonStyle.secondary)
    async def download(self, interaction: discord.Interaction, button: discord.ui.Button):
        txt = build_txt(self.data)
        file = discord.File(
            io.BytesIO(txt.encode("utf-8")),
            filename=f"roblox_{self.data['username']}.txt"
        )
        await interaction.response.send_message(
            f"📁 ข้อมูลของ **{self.data['username']}**",
            file=file,
            ephemeral=True
        )
        button.disabled = True
        await interaction.message.edit(view=self)



@bot.event
async def on_ready():
    print(f"✅ บอทออนไลน์แล้ว: {bot.user}")
    await bot.change_presence(
        status=discord.Status.online,
        activity=discord.Activity(
            type=discord.ActivityType.playing,
            name="Developer | LevelingX"
        )
    )
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} slash commands")
    except Exception as e:
        print(f"❌ Sync error: {e}")


@bot.command(name="rb")
async def roblox_prefix(ctx, *, username: str = None):
    if not username:
        await ctx.send("❌ กรุณาใส่ชื่อ: `!rb <username>`")
        return

    msg = await ctx.send(f"🔍 กำลังค้นหา **{username}**...")
    data = await get_roblox_data(username)

    if not data:
        await msg.edit(content=f"❌ ไม่พบผู้ใช้ชื่อ **{username}** บน Roblox")
        return

    embed = build_embed(data)
    view = DownloadView(data)
    await msg.edit(content=None, embed=embed, view=view)


@bot.tree.command(name="roblox", description="ค้นหาข้อมูล Roblox user")
@app_commands.describe(username="ชื่อ Roblox ที่ต้องการค้นหา")
async def roblox_slash(interaction: discord.Interaction, username: str):
    await interaction.response.defer()
    data = await get_roblox_data(username)

    if not data:
        await interaction.followup.send(f"❌ ไม่พบผู้ใช้ชื่อ **{username}** บน Roblox")
        return

    embed = build_embed(data)
    view = DownloadView(data)
    await interaction.followup.send(embed=embed, view=view)


from aiohttp import web

async def handle(request):
    return web.Response(text="Bot is alive!")

async def start_webserver():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 8080)))
    await site.start()
    print("🌐 Web server started")

async def main():
    await start_webserver()
    await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
