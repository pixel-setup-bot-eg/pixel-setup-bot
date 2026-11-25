import asyncio
import datetime as dt
import io
import json
import logging
import os
import re
import unicodedata
from collections import defaultdict, deque
from pathlib import Path

import discord
from aiohttp import web
from discord import app_commands

BASE = Path(__file__).parent
CFG = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
STATE_FILE = Path(os.environ.get("STATE_FILE", BASE / "state.json"))
MSG = CFG["messages"]
OWNER_NAME = CFG["owner_name"]
TIMEOUT_DAYS = CFG["timeout_days"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pixel-setup")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.invites = True
intents.reactions = True

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# ---------------------------------------------------------------- state
def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(st):
    try:
        STATE_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        log.warning("state save failed: %s", e)


STATE = load_state()


def gstate(guild):
    return STATE.setdefault(str(guild.id), {"roles": {}, "channels": {}})


def get_role(guild, key):
    rid = gstate(guild)["roles"].get(key)
    r = guild.get_role(rid) if rid else None
    if r:
        return r
    for spec in CFG["roles"]:
        if spec["key"] == key:
            return discord.utils.get(guild.roles, name=spec["name"])
    return None


def get_channel(guild, key):
    cid = gstate(guild)["channels"].get(key)
    return guild.get_channel(cid) if cid else None


def hexcolor(s):
    return discord.Colour(int(s.lstrip("#"), 16))


# ---------------------------------------------------------------- /setup
def build_overwrites(guild, access, roles):
    ev = guild.default_role
    pixel, staff = roles["pixel"], roles["staff"]
    member, gaming = roles["member"], roles["gaming"]
    others = [roles["creator"], roles["friends"], gaming, member, staff]
    P = discord.PermissionOverwrite
    ow = {}
    # @everyone sees nothing by default (everyone gets Member on join).
    ow[ev] = P(view_channel=False)
    if access == "locked":
        ow[member] = P(view_channel=True, send_messages=False, add_reactions=False)
        ow[staff] = P(view_channel=True, send_messages=False)
        for r in (roles["creator"], roles["friends"], gaming):
            ow[r] = P(send_messages=False)
    elif access == "readonly":
        ow[member] = P(view_channel=True, send_messages=False, add_reactions=True)
        ow[staff] = P(view_channel=True, send_messages=True)
    elif access == "chat":
        ow[member] = P(view_channel=True, send_messages=True, attach_files=True,
                       embed_links=False, add_reactions=True, read_message_history=True)
        ow[staff] = P(view_channel=True, send_messages=True)
    elif access == "pixel_only":
        ow[ev] = P(view_channel=False)
        for r in others:
            ow[r] = P(view_channel=False)
    elif access == "record":
        ow[ev] = P(view_channel=False)
        for r in others:
            ow[r] = P(view_channel=False, connect=False)
        ow[staff] = P(view_channel=True, connect=True, speak=True, stream=False, use_embedded_activities=False)
        ow[pixel] = P(view_channel=True, connect=True, speak=True, stream=False)
    elif access == "gaming_voice":
        ow[member] = P(view_channel=True, connect=False)
        ow[gaming] = P(view_channel=True, connect=True, speak=True, stream=True)
        ow[staff] = P(view_channel=True, connect=True, speak=True)
    elif access == "public_voice":
        ow[member] = P(view_channel=True, connect=True, speak=True, stream=True, use_voice_activation=True)
    elif access == "spare":
        ow[member] = P(view_channel=True, connect=False)
        ow[staff] = P(view_channel=True, connect=True, speak=True)
    if access != "readonly" and access != "chat":
        pass
    ow[pixel] = ow.get(pixel) or P(view_channel=True)
    return ow


async def ensure_roles(guild):
    roles = {}
    st = gstate(guild)
    for spec in CFG["roles"]:
        r = guild.get_role(st["roles"].get(spec["key"], 0)) or discord.utils.get(guild.roles, name=spec["name"])
        if spec.get("admin"):
            perms = discord.Permissions(administrator=True)
        elif spec["key"] == "member" or spec["key"] == "staff":
            perms = discord.Permissions(**{p: True for p in spec["perms"]})
        else:
            perms = discord.Permissions.none()
        if r is None:
            r = await guild.create_role(name=spec["name"], colour=hexcolor(spec["color"]), hoist=spec.get("hoist", False),
                                        mentionable=spec.get("mentionable", False), permissions=perms,
                                        reason="Pixel setup bot")
        else:
            await r.edit(colour=hexcolor(spec["color"]), hoist=spec.get("hoist", False),
                         mentionable=spec.get("mentionable", False), permissions=perms, reason="Pixel setup bot")
        roles[spec["key"]] = r
        st["roles"][spec["key"]] = r.id
    # @everyone: no permissions at all (Member role carries the basics)
    try:
        await guild.default_role.edit(permissions=discord.Permissions.none(), reason="Pixel setup bot")
    except discord.HTTPException as e:
        log.warning("everyone edit: %s", e)
    # order: Pixel > Staff > Content Creator > Friends > Gaming > Member, all under the bot's own role
    try:
        top = guild.me.top_role.position
        order = ["pixel", "staff", "creator", "friends", "gaming", "member"]
        positions = {}
        base = max(1, top - 1 - (len(order) - 1))
        for i, k in enumerate(reversed(order)):
            positions[roles[k]] = base + i
        await guild.edit_role_positions(positions=positions)
    except Exception as e:
        log.warning("role order: %s", e)
    return roles


async def ensure_layout(guild, roles):
    st = gstate(guild)
    for block in CFG["layout"]:
        cat = discord.utils.get(guild.categories, name=block["category"])
        if cat is None:
            cat = await guild.create_category(block["category"], reason="Pixel setup bot")
        for ch in block["channels"]:
            access = ch["access"]
            ow = build_overwrites(guild, access, roles)
            existing = guild.get_channel(st["channels"].get(ch["key"], 0)) or discord.utils.get(
                guild.text_channels if ch["type"] == "text" else guild.voice_channels, name=ch["name"].lower() if ch["type"] == "text" else ch["name"])
            if existing is None:
                if ch["type"] == "text":
                    existing = await guild.create_text_channel(ch["name"], category=cat, overwrites=ow, reason="Pixel setup bot")
                else:
                    existing = await guild.create_voice_channel(ch["name"], category=cat, overwrites=ow, reason="Pixel setup bot")
            else:
                await existing.edit(category=cat, overwrites=ow, reason="Pixel setup bot")
            st["channels"][ch["key"]] = existing.id
    # keep category order as in config
    for i, block in enumerate(CFG["layout"]):
        cat = discord.utils.get(guild.categories, name=block["category"])
        if cat:
            try:
                await cat.edit(position=i)
            except discord.HTTPException:
                pass
    save_state(STATE)


def fmt(text, **kw):
    for k, v in kw.items():
        text = text.replace("{" + k + "}", str(v))
    return text


async def post_static_messages(guild):
    ch = lambda k: get_channel(guild, k)
    # dhikr banner
    c = ch("dhikr")
    if c and not [m async for m in c.history(limit=1)]:
        await c.send(MSG["dhikr_banner"])
    # rules + roles
    c = ch("rules")
    if c and not [m async for m in c.history(limit=1)]:
        e1 = discord.Embed(title=MSG["rules_title"], description=fmt(MSG["rules_body"], days=TIMEOUT_DAYS), colour=0xFF3D81)
        e2 = discord.Embed(title=MSG["roles_title"], description=MSG["roles_body"], colour=0x7C4DFF)
        await c.send(embeds=[e1, e2])
    # collect role (heart reaction)
    st = gstate(guild)
    c = ch("collect")
    if c:
        msg = None
        if st.get("collect_msg"):
            try:
                msg = await c.fetch_message(st["collect_msg"])
            except discord.HTTPException:
                msg = None
        if msg is None:
            msg = await c.send(embed=discord.Embed(description=MSG["collect_text"], colour=0x7C4DFF))
            await msg.add_reaction("❤️")
            st["collect_msg"] = msg.id
    save_state(STATE)


@tree.command(name="setup", description="يبني السيرفر كله: الرتب والقنوات والصلاحيات")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def setup_cmd(inter: discord.Interaction):
    guild = inter.guild
    if not (inter.user.guild_permissions.administrator or inter.user.id == guild.owner_id):
        await inter.response.send_message("الأمر ده للأدمن بس.", ephemeral=True)
        return
    if not guild.me.guild_permissions.administrator:
        await inter.response.send_message("لازم تدّيني صلاحية Administrator الأول (من Server Settings > Roles).", ephemeral=True)
        return
    await inter.response.send_message("⏳ بدأت أبني السيرفر، ممكن ياخد دقيقة أو اتنين...", ephemeral=True)
    try:
        roles = await ensure_roles(guild)
        owner = guild.owner or await guild.fetch_member(guild.owner_id)
        if owner and roles["pixel"] not in owner.roles:
            await owner.add_roles(roles["pixel"], reason="Pixel setup bot")
        await ensure_layout(guild, roles)
        await post_static_messages(guild)
        await cache_invites(guild)
        # give Member to everyone existing (non-bots)
        n = 0
        for m in guild.members:
            if not m.bot and roles["member"] not in m.roles:
                try:
                    await m.add_roles(roles["member"], reason="Pixel setup bot")
                    n += 1
                    await asyncio.sleep(0.4)
                except discord.HTTPException:
                    pass
        await inter.followup.send(f"✅ خلصت يا معلم. اتعمل كل حاجة، واتدّى Member لـ {n} عضو. الرتبة **{OWNER_NAME}** اتدّت لصاحب السيرفر.", ephemeral=True)
        await log_embed(guild, "⚙️ تم تشغيل /setup", f"بواسطة {inter.user.mention}", 0x00E676)
    except discord.Forbidden:
        await inter.followup.send("❌ مفيش صلاحية كفاية. اتأكد إن رتبة البوت فوق كل الرتب وعنده Administrator وجرب /setup تاني.", ephemeral=True)
    except Exception as e:
        log.exception("setup failed")
        await inter.followup.send(f"❌ حصل خطأ: {e}", ephemeral=True)


# ---------------------------------------------------------------- logging
async def log_embed(guild, title, desc, colour=0x90A4AE, file=None):
    ch = get_channel(guild, "log") or discord.utils.get(guild.text_channels, name=CFG["layout"][4]["channels"][0]["name"].lower())
    if not ch:
        return
    try:
        e = discord.Embed(title=title, description=(desc or "")[:4000], colour=colour, timestamp=discord.utils.utcnow())
        await ch.send(embed=e, file=file) if file else await ch.send(embed=e)
    except discord.HTTPException as e:
        log.warning("log send failed: %s", e)


# ---------------------------------------------------------------- invites
INVITES = {}


async def cache_invites(guild):
    try:
        INVITES[guild.id] = {i.code: (i.uses or 0, i.inviter) for i in await guild.invites()}
    except discord.HTTPException as e:
        log.warning("invites: %s", e)
        INVITES.setdefault(guild.id, {})


async def find_inviter(guild):
    old = INVITES.get(guild.id, {})
    try:
        new = {i.code: (i.uses or 0, i.inviter) for i in await guild.invites()}
    except discord.HTTPException:
        return None, 0
    found = None
    for code, (uses, inviter) in new.items():
        if uses > old.get(code, (0, None))[0]:
            found = (code, inviter)
            break
    INVITES[guild.id] = new
    if not found or not found[1]:
        return None, 0
    total = sum(u for c, (u, inv) in new.items() if inv and inv.id == found[1].id)
    return found[1], total


@client.event
async def on_invite_create(invite):
    INVITES.setdefault(invite.guild.id, {})[invite.code] = (invite.uses or 0, invite.inviter)


@client.event
async def on_invite_delete(invite):
    INVITES.get(invite.guild.id, {}).pop(invite.code, None)


# ---------------------------------------------------------------- moderation
LINK_RE = re.compile(
    r"(https?://|www\.|discord\.gg/|discord\.com/invite/|\b[a-z0-9-]{2,}\.(com|net|org|io|gg|me|ly|xyz|ru|tk|ml|cc|co|info|shop|top|click|live|site|online|link|app|tv|ai|bio|store|club)\b)",
    re.I)
MEDIA_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".mp4", ".mov", ".webm", ".mp3", ".ogg", ".wav", ".m4a")
RECENT = defaultdict(lambda: deque(maxlen=6))


def norm(text):
    t = unicodedata.normalize("NFKC", text or "").lower()
    return re.sub(r"[\u200b-\u200f\u202a-\u202e]", "", t)


def is_exempt(member):
    if getattr(member, "guild_permissions", None) and member.guild_permissions.administrator:
        return True
    names = {r.name for r in getattr(member, "roles", [])}
    return bool(names & {"Pixel", "Staff"})


def is_image(att):
    ct = (att.content_type or "").lower()
    return ct.startswith("image/") or att.filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"))


def violation_of(msg, in_ticket=False):
    text = norm(msg.content)
    if LINK_RE.search(text):
        return "link"
    if any(k in text for k in CFG["scam_keywords"]):
        return "scam"
    if len(msg.mentions) >= 5 or "@everyone" in text and not is_exempt(msg.author):
        return "mentions"
    atts = msg.attachments
    if atts:
        imgs = [a for a in atts if is_image(a)]
        if len(imgs) >= CFG["image_flood_min"] and not text.strip() and not in_ticket:
            return "images"
        if CFG["block_non_media_files"]:
            if any(not a.filename.lower().endswith(MEDIA_EXT) and not is_image(a) for a in atts):
                return "file"
    return None


async def punish(msg, kind):
    guild, member = msg.guild, msg.author
    what = MSG["labels"][kind]
    content = (msg.content or "")[:300]
    try:
        await msg.delete()
    except discord.HTTPException as e:
        log.warning("delete failed: %s", e)
    try:
        await member.timeout(dt.timedelta(days=TIMEOUT_DAYS), reason=what)
    except discord.HTTPException as e:
        log.warning("timeout failed: %s", e)
    log.info("punish %s (%s) kind=%s timeout=%sd", member, member.id, kind, TIMEOUT_DAYS)
    dm_ok = True
    try:
        await member.send(fmt(MSG["dm_warning"], guild=guild.name, what=what, days=TIMEOUT_DAYS))
    except discord.HTTPException:
        dm_ok = False
    await log_embed(guild, f"🚫 {what}",
                    f"العضو: {member.mention} (`{member.id}`)\nالقناة: {msg.channel.mention}\n"
                    f"العقوبة: تايم آوت {TIMEOUT_DAYS} أيام\nالـ DM: {'اتبعت' if dm_ok else 'مقفول'}\n"
                    f"المحتوى: {content or '—'}", 0xFF5252)


@client.event
async def on_message(msg):
    if msg.author.bot or not msg.guild or not isinstance(msg.author, discord.Member):
        return
    if is_exempt(msg.author):
        return
    st = gstate(msg.guild)
    kind = violation_of(msg)
    if not kind:
        key = (msg.guild.id, msg.author.id)
        now = msg.created_at.timestamp()
        dq = RECENT[key]
        dq.append((now, norm(msg.content)))
        same = [1 for t, c in dq if c and c == dq[-1][1] and now - t < 20]
        if len(same) >= 4:
            kind = "repeat"
    if kind:
        await punish(msg, kind)


@client.event
async def on_message_delete(msg):
    if msg.guild and not msg.author.bot:
        await log_embed(msg.guild, "🗑️ رسالة اتمسحت",
                        f"من: {msg.author.mention}\nفي: {msg.channel.mention}\n{(msg.content or '—')[:1500]}", 0xFF9800)


@client.event
async def on_message_edit(before, after):
    if after.guild and not after.author.bot and before.content != after.content:
        await log_embed(after.guild, "✏️ رسالة اتعدلت",
                        f"من: {after.author.mention} في {after.channel.mention}\n**قبل:** {before.content[:900]}\n**بعد:** {after.content[:900]}", 0xFFC107)
        # re-check edited messages for link/scam
        if isinstance(after.author, discord.Member) and not is_exempt(after.author):
            k = violation_of(after)
            if k:
                await punish(after, k)


# ---------------------------------------------------------------- member events
@client.event
async def on_member_join(member):
    guild = member.guild
    if member.bot:
        await log_embed(guild, "🤖 بوت اتضاف", member.mention, 0x90A4AE)
        return
    role = get_role(guild, "member")
    if role:
        try:
            await member.add_roles(role, reason="auto Member")
        except discord.HTTPException as e:
            log.warning("auto role failed: %s", e)
    inviter, uses = await find_inviter(guild)
    chans = {k: get_channel(guild, k) for k in ("welcome", "invites", "rules", "collect", "chat")}
    mention = lambda k: chans[k].mention if chans[k] else ""
    if chans["welcome"]:
        await chans["welcome"].send(fmt(MSG["welcome"], user=member.mention, guild=guild.name, count=guild.member_count,
                                        rules=mention("rules"), collect=mention("collect"), chat=mention("chat")))
    text = (fmt(MSG["invite_known"], user=member.mention, inviter=inviter.display_name, uses=uses) if inviter
            else fmt(MSG["invite_unknown"], user=member.mention))
    if chans["invites"]:
        await chans["invites"].send(text, allowed_mentions=discord.AllowedMentions.none())
    await log_embed(guild, "📥 عضو دخل", text, 0x00E676)


@client.event
async def on_member_remove(member):
    await log_embed(member.guild, "📤 عضو خرج", f"{member} (`{member.id}`)", 0xFF7043)


@client.event
async def on_member_ban(guild, user):
    await log_embed(guild, "🔨 باند", f"{user} (`{user.id}`)", 0xD50000)


@client.event
async def on_member_unban(guild, user):
    await log_embed(guild, "♻️ فك باند", f"{user} (`{user.id}`)", 0x00C853)


@client.event
async def on_member_update(before, after):
    g = after.guild
    if before.roles != after.roles:
        added = [r.mention for r in after.roles if r not in before.roles]
        removed = [r.mention for r in before.roles if r not in after.roles]
        await log_embed(g, "🎭 تغيير رتب", f"{after.mention}\n➕ {' '.join(added) or '—'}\n➖ {' '.join(removed) or '—'}", 0x7C4DFF)
    if before.timed_out_until != after.timed_out_until:
        await log_embed(g, "⏱️ تايم آوت", f"{after.mention}: {after.timed_out_until or 'اتشال'}", 0xFFAB00)
    if before.nick != after.nick:
        await log_embed(g, "📝 تغيير اسم", f"{after.mention}: {before.nick} ← {after.nick}", 0x90A4AE)


@client.event
async def on_voice_state_update(member, before, after):
    if before.channel == after.channel:
        return
    if after.channel and not before.channel:
        t = f"دخل {after.channel.name}"
    elif before.channel and not after.channel:
        t = f"خرج من {before.channel.name}"
    else:
        t = f"انتقل من {before.channel.name} إلى {after.channel.name}"
    await log_embed(member.guild, "🎙️ صوت", f"{member.mention} {t}", 0x29B6F6)


@client.event
async def on_guild_channel_create(ch):
    await log_embed(ch.guild, "➕ قناة جديدة", ch.mention if hasattr(ch, "mention") else ch.name, 0x00E676)


@client.event
async def on_guild_channel_delete(ch):
    await log_embed(ch.guild, "➖ قناة اتمسحت", ch.name, 0xFF5252)


@client.event
async def on_guild_role_create(role):
    await log_embed(role.guild, "➕ رتبة جديدة", role.name, 0x00E676)


@client.event
async def on_guild_role_delete(role):
    await log_embed(role.guild, "➖ رتبة اتمسحت", role.name, 0xFF5252)


# ---------------------------------------------------------------- reaction role
def heart(payload):
    return str(payload.emoji) in ("❤️", "❤", "♥️", "♥", "💖", "💗", "💕", "💜", "💙", "💚", "🧡", "💛", "🤍", "🖤", "🤎", "😍", "🥰")


@client.event
async def on_raw_reaction_add(payload):
    if not payload.guild_id or payload.user_id == client.user.id:
        return
    guild = client.get_guild(payload.guild_id)
    if not guild or gstate(guild).get("collect_msg") != payload.message_id or not heart(payload):
        return
    role = get_role(guild, "gaming")
    member = guild.get_member(payload.user_id) or await guild.fetch_member(payload.user_id)
    if role and member and role not in member.roles:
        await member.add_roles(role, reason="reaction role")


@client.event
async def on_raw_reaction_remove(payload):
    if not payload.guild_id:
        return
    guild = client.get_guild(payload.guild_id)
    if not guild or gstate(guild).get("collect_msg") != payload.message_id or not heart(payload):
        return
    role = get_role(guild, "gaming")
    member = guild.get_member(payload.user_id)
    if role and member and role in member.roles:
        await member.remove_roles(role, reason="reaction role removed")


# ---------------------------------------------------------------- lifecycle
async def health_server():
    async def ok(_):
        return web.Response(text="ok")
    app = web.Application()
    app.router.add_get("/", ok)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", "8080"))
    await web.TCPSite(runner, "0.0.0.0", port).start()


@client.event
async def setup_hook():
    await health_server()


@client.event
async def on_ready():
    log.info("Pixel setup bot online as %s", client.user)
    for g in client.guilds:
        tree.copy_global_to(guild=g)
        try:
            await tree.sync(guild=g)
        except Exception as e:
            log.warning("sync %s: %s", g.id, e)
        await cache_invites(g)
        log.info("guild %s ready", g.name)


@client.event
async def on_guild_join(guild):
    tree.copy_global_to(guild=guild)
    await tree.sync(guild=guild)
    await cache_invites(guild)
    log.info("joined guild %s", guild.name)


if __name__ == "__main__":
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN env var is missing")
    client.run(token, log_handler=None)
