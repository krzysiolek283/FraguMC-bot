import asyncio
import base64
import io
import json
import os
import random
import re
import time
from datetime import timedelta

import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands, tasks
from mcstatus import BedrockServer, JavaServer

def load_env(path):
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env(os.path.join(os.path.dirname(__file__), ".env"))
TOKEN = os.getenv("DISCORD_TOKEN", "")
if not TOKEN:
    raise SystemExit("Brak DISCORD_TOKEN — ustaw go w pliku .env")

API_KEY = os.getenv("API_KEY", "")
API_PORT = int(os.getenv("API_PORT", "8080"))


# --- Konfiguracja ---
SERVER_NAME = "FraguMC"  # domyslna nazwa, gdy nie podano w /server add
SERVER_FILE = os.path.join(os.path.dirname(__file__), "server.json")

AUTOSTATUS_FILE = os.path.join(os.path.dirname(__file__), "autostatus.json")
AUTOSTATUS_MINUTES = 1

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


def load_server():
    """Zwraca (address, type, name)."""
    if os.path.exists(SERVER_FILE):
        try:
            with open(SERVER_FILE, "r") as f:
                data = json.load(f)
            return (
                data.get("address"),
                data.get("type", "java"),
                data.get("name") or SERVER_NAME,
            )
        except Exception:
            pass
    return None, None, SERVER_NAME


def save_server(address, server_type, name):
    with open(SERVER_FILE, "w") as f:
        json.dump({"address": address, "type": server_type, "name": name}, f)


def clear_server():
    if os.path.exists(SERVER_FILE):
        os.remove(SERVER_FILE)


def detect_server_type(address):
    try:
        JavaServer.lookup(address, timeout=5).status()
        return "java"
    except Exception:
        pass
    try:
        BedrockServer.lookup(address, timeout=5).status()
        return "bedrock"
    except Exception:
        pass
    return None


def fetch_mc_status(address, server_type):
    """Odpytuje serwer MC. Zwraca dict albo {'online': False}."""
    try:
        if server_type == "bedrock":
            server = BedrockServer.lookup(address, timeout=5)
            s = server.status()
            motd = getattr(getattr(s, "motd", ""), "motd", None) or str(getattr(s, "motd", ""))
            return {
                "online": True,
                "motd": motd,
                "players_online": getattr(s, "players_online", 0),
                "players_max": getattr(s, "players_max", 0),
                "version": getattr(s, "version_name", "?"),
                "latency": None,
                "favicon": None,
                "sample": None,
            }
        server = JavaServer.lookup(address, timeout=5)
        s = server.status()
        try:
            motd = s.motd.to_plain()
        except Exception:
            motd = str(s.motd.raw)
        sample = None
        try:
            if s.players.sample:
                sample = [
                    p.name for p in s.players.sample
                    if re.fullmatch(r"[A-Za-z0-9_]{3,16}", p.name or "")
                ] or None
        except Exception:
            pass
        return {
            "online": True,
            "motd": motd,
            "players_online": s.players.online,
            "players_max": s.players.max,
            "version": s.version.name,
            "latency": round(s.latency, 1),
            "favicon": getattr(s, "icon", None),
            "sample": sample,
        }
    except Exception:
        import traceback
        print(f"[mcstatus] blad dla {address} ({server_type}):", flush=True)
        traceback.print_exc()
        return {"online": False}


def build_status_embed(data, address, name):
    if not data.get("online"):
        embed = discord.Embed(
            title=f"{name} — status serwera",
            description="🔴 **Serwer offline**",
            color=discord.Color.red(),
        )
        embed.add_field(name="Adres", value=f"`{address}`", inline=False)
        return embed, None

    embed = discord.Embed(
        title=f"{name} — status serwera",
        color=discord.Color.green(),
    )
    embed.add_field(name="Status", value="🟢 Online", inline=True)
    embed.add_field(
        name="Gracze",
        value=f"**{data['players_online']}**/{data['players_max']}",
        inline=True,
    )
    embed.add_field(name="Wersja", value=str(data["version"]), inline=True)

    favicon_file = None
    if data.get("favicon"):
        try:
            _, encoded = data["favicon"].split(",", 1)
            favicon_file = discord.File(
                fp=io.BytesIO(base64.b64decode(encoded)),
                filename="favicon.png",
            )
            embed.set_thumbnail(url="attachment://favicon.png")
        except Exception:
            pass

    footer = f"Adres: {address}"
    if data.get("latency") is not None:
        footer += f" • Ping: {data['latency']} ms"
    embed.set_footer(text=footer)
    embed.timestamp = discord.utils.utcnow()
    return embed, favicon_file


def load_autostatus():
    if os.path.exists(AUTOSTATUS_FILE):
        try:
            with open(AUTOSTATUS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_autostatus(data):
    with open(AUTOSTATUS_FILE, "w") as f:
        json.dump(data, f)


async def refresh_autostatus():
    entries = load_autostatus()
    if not entries:
        return
    address, server_type, name = load_server()
    if not address:
        return
    data = await asyncio.to_thread(fetch_mc_status, address, server_type)
    embed, favicon = build_status_embed(data, address, name)
    changed = False
    for channel_id, message_id in list(entries.items()):
        try:
            channel = bot.get_channel(int(channel_id))
            if channel is None:
                channel = await bot.fetch_channel(int(channel_id))
            if not module_on(channel.guild.id if hasattr(channel, "guild") else None, "autostatus"):
                continue
            msg = await channel.fetch_message(int(message_id))
            if favicon:
                favicon.fp.seek(0)
                await msg.edit(embed=embed, attachments=[favicon])
            else:
                await msg.edit(embed=embed, attachments=[])
        except Exception:
            entries.pop(channel_id, None)
            changed = True
    if changed:
        save_autostatus(entries)


@tasks.loop(minutes=AUTOSTATUS_MINUTES)
async def autostatus_loop():
    await refresh_autostatus()


@bot.event
async def on_ready():
    if not getattr(bot, "_api_started", False):
        bot._api_started = True
        bot.loop.create_task(start_api())
    for guild in bot.guilds:
        bot.tree.copy_global_to(guild=guild)
    bot.tree.clear_commands(guild=None)
    await bot.tree.sync()
    for guild in bot.guilds:
        await bot.tree.sync(guild=guild)
    bot.add_view(GiveawayView())
    for _loop in (autostatus_loop, giveaway_loop, tempban_loop):
        if not _loop.is_running():
            _loop.start()
    print(f"Zalogowano jako {bot.user} (ID: {bot.user.id})")


async def require_server(interaction):
    address, server_type, name = load_server()
    if not address:
        await interaction.response.send_message(
            "Brak serwera na liście. Dodaj go komendą `/server add`.", ephemeral=True
        )
        return None, None, None
    return address, server_type, name


@bot.tree.command(name="test", description="Komenda testowa")
async def test_slash(interaction: discord.Interaction):
    await interaction.response.send_message("Wysłano!", ephemeral=True)
    await interaction.channel.send("Bot działa! Wszystko ok.")


@bot.tree.command(name="wiadomosc", description="Wysyla wiadomosc z konta bota")
async def wiadomosc(interaction: discord.Interaction, text: str):
    await interaction.response.send_message("Wysłano!", ephemeral=True)
    await interaction.channel.send(text)


@bot.tree.command(name="status", description="Pokazuje status serwera Minecraft")
async def status(interaction: discord.Interaction):
    address, server_type, name = await require_server(interaction)
    if not address:
        return
    await interaction.response.defer(ephemeral=True)
    data = await asyncio.to_thread(fetch_mc_status, address, server_type)
    embed, favicon = build_status_embed(data, address, name)
    if favicon:
        await interaction.followup.send(embed=embed, file=favicon, ephemeral=True)
    else:
        await interaction.followup.send(embed=embed, ephemeral=True)


PERMS_FILE = os.path.join(os.path.dirname(__file__), "perms.json")

PERM_FUNCS = [
    ("warn", "Warn"),
    ("mute", "Mute"),
    ("kick", "Kick"),
    ("ban", "Ban"),
    ("giveaway", "Giveawaye"),
    ("propozycje", "Propozycje"),
    ("autostatus", "Auto-status"),
    ("server", "Serwer MC"),
    ("config", "Konfiguracja"),
]

MOD_LABELS = {
    "moderacja": "Moderacja",
    "propozycje": "Propozycje",
    "giveawaye": "Giveawaye",
    "autostatus": "Auto-status",
}


def load_perms():
    return load_json(PERMS_FILE, {})


def save_perms(data):
    save_json(PERMS_FILE, data)


def require_perm(func, discord_perm):
    """Check: administrator zawsze moze; potem /permisje; na koncu uprawnienie Discorda."""
    async def predicate(interaction: discord.Interaction):
        if interaction.guild is None:
            return False
        me = interaction.user
        if isinstance(me, discord.Member) and me.guild_permissions.administrator:
            return True
        entry = load_perms().get(str(interaction.guild_id), {}).get(func, {})
        users = {str(x) for x in entry.get("users", [])}
        roles = {str(x) for x in entry.get("roles", [])}
        if users or roles:
            if str(me.id) in users:
                return True
            if isinstance(me, discord.Member) and any(str(r.id) in roles for r in me.roles):
                return True
            return False
        return getattr(me.guild_permissions, discord_perm, False)
    return predicate


autostatus = app_commands.Group(name="autostatus", description="Automatyczny status serwera")


@autostatus.command(name="set", description="Ustawia auto-status na kanale")
@app_commands.guild_only()
@app_commands.check(require_perm("autostatus", "manage_guild"))
async def autoset(interaction: discord.Interaction, channel: discord.TextChannel):
    if not module_on(interaction.guild_id, "autostatus"):
        await interaction.response.send_message("⛔ Moduł auto-statusu jest wyłączony.", ephemeral=True)
        return
    address, server_type, name = await require_server(interaction)
    if not address:
        return
    await interaction.response.defer(ephemeral=True)
    data = await asyncio.to_thread(fetch_mc_status, address, server_type)
    embed, favicon = build_status_embed(data, address, name)
    if favicon:
        msg = await channel.send(embed=embed, file=favicon)
    else:
        msg = await channel.send(embed=embed)
    entries = load_autostatus()
    entries[str(channel.id)] = str(msg.id)
    save_autostatus(entries)
    await interaction.followup.send(f"Auto-status ustawiony na {channel.mention}.", ephemeral=True)


@autostatus.command(name="list", description="Lista kanalow z auto-statusem")
@app_commands.guild_only()
@app_commands.check(require_perm("autostatus", "manage_guild"))
async def autolist(interaction: discord.Interaction):
    entries = load_autostatus()
    if not entries:
        await interaction.response.send_message("Brak ustawionych auto-statusów.", ephemeral=True)
        return
    lines = []
    for channel_id in entries:
        ch = bot.get_channel(int(channel_id))
        lines.append(ch.mention if ch else f"nieznany kanał ({channel_id})")
    await interaction.response.send_message("Auto-status: " + ", ".join(lines), ephemeral=True)


@autostatus.command(name="remove", description="Usuwa auto-status z kanalu")
@app_commands.guild_only()
@app_commands.check(require_perm("autostatus", "manage_guild"))
async def autoremove(interaction: discord.Interaction, channel: discord.TextChannel):
    entries = load_autostatus()
    if str(channel.id) not in entries:
        await interaction.response.send_message("Ten kanał nie ma auto-statusu.", ephemeral=True)
        return
    try:
        msg = await channel.fetch_message(int(entries[str(channel.id)]))
        await msg.delete()
    except Exception:
        pass
    entries.pop(str(channel.id))
    save_autostatus(entries)
    await interaction.response.send_message(f"Usunięto auto-status z {channel.mention}.", ephemeral=True)


bot.tree.add_command(autostatus)


server_group = app_commands.Group(name="server", description="Serwer Minecraft do statusu")


@server_group.command(name="add", description="Dodaje serwer do listy (max 1)")
@app_commands.guild_only()
@app_commands.check(require_perm("server", "manage_guild"))
async def server_add(interaction: discord.Interaction, ip: str, nazwa: str):
    await interaction.response.defer(ephemeral=True)
    address = ip.strip()
    display = nazwa.strip() or SERVER_NAME
    server_type = await asyncio.to_thread(detect_server_type, address)
    if server_type is None:
        save_server(address, "java", display)
        await interaction.followup.send(
            f"⚠️ Zapisano **{display}** (`{address}`), ale nie udało się połączyć. "
            "Sprawdź adres/port.",
            ephemeral=True,
        )
        return
    save_server(address, server_type, display)
    await interaction.followup.send(
        f"✅ Zapisano serwer **{display}** (`{address}`, wykryto: {server_type}).",
        ephemeral=True,
    )


@server_group.command(name="remove", description="Usuwa serwer z listy")
@app_commands.guild_only()
@app_commands.check(require_perm("server", "manage_guild"))
async def server_remove(interaction: discord.Interaction):
    clear_server()
    await interaction.response.send_message("🗑️ Usunięto serwer z listy.", ephemeral=True)


bot.tree.add_command(server_group)


# ================= MODERACJA, GIVEAWAYE, KONFIG =================

WARNS_FILE = os.path.join(os.path.dirname(__file__), "warns.json")
TEMPBANS_FILE = os.path.join(os.path.dirname(__file__), "tempbans.json")
GIVEAWAYS_FILE = os.path.join(os.path.dirname(__file__), "giveaways.json")
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")

MODULES = ["moderacja", "propozycje", "giveawaye", "autostatus"]


def module_on(guild_id, name):
    if guild_id is None:
        return True
    cfg = load_json(CONFIG_FILE, {}).get(str(guild_id), {})
    return cfg.get("modules", {}).get(name, True)


def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return default


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f)


def parse_duration(text):
    """'10m', '1h30m', '1d 2h' -> sekundy. None przy bledzie."""
    if not text:
        return None
    text = text.strip().lower()
    parts = re.findall(r"(\d+)\s*([smhd])", text)
    if not parts:
        return None
    if re.sub(r"\d+\s*[smhd]", "", text).strip():
        return None
    total = sum(int(v) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[u] for v, u in parts)
    return total or None


def format_duration(seconds):
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    if seconds < 86400:
        return f"{seconds // 3600} h"
    return f"{seconds // 86400} d"


def find_kary_channel(guild):
    cfg = load_json(CONFIG_FILE, {}).get(str(guild.id), {})
    ch_id = cfg.get("kary_channel")
    if ch_id:
        ch = guild.get_channel(int(ch_id))
        if ch:
            return ch
    for ch in guild.text_channels:
        if "kary" in ch.name.lower():
            return ch
    return None


async def log_kara(guild, embed):
    ch = find_kary_channel(guild)
    if ch is None:
        return False
    try:
        await ch.send(embed=embed)
        return True
    except Exception:
        return False


def kara_embed(rodzaj, uzytkownik, mod, powod, czas=None, kolor=discord.Color.red(), extra=None):
    embed = discord.Embed(title=f"{rodzaj} — {uzytkownik}", color=kolor)
    try:
        embed.set_thumbnail(url=uzytkownik.display_avatar.url)
    except Exception:
        pass
    embed.add_field(name="Użytkownik", value=f"{uzytkownik.mention} (`{uzytkownik.id}`)", inline=False)
    embed.add_field(name="Moderator", value=mod.mention, inline=True)
    if czas:
        embed.add_field(name="Czas", value=czas, inline=True)
    embed.add_field(name="Powód", value=powod, inline=False)
    if extra:
        embed.add_field(name="Info", value=extra, inline=False)
    embed.timestamp = discord.utils.utcnow()
    return embed


@bot.tree.error
async def on_tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    try:
        if isinstance(error, app_commands.MissingPermissions):
            msg = "⛔ Nie masz uprawnień do tej komendy."
        elif isinstance(error, app_commands.CheckFailure):
            msg = "⛔ Brak uprawnień (sprawdź /permisje lub uprawnienia na serwerze)."
        elif isinstance(error, app_commands.NoPrivateMessage):
            msg = "⛔ Tej komendy można użyć tylko na serwerze."
        else:
            msg = f"⚠️ Błąd: {error}"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass


# ---------- Moderacja ----------

@bot.tree.command(name="warn", description="Daje ostrzeżenie użytkownikowi")
@app_commands.guild_only()
@app_commands.check(require_perm("warn", "manage_messages"))
async def warn_cmd(interaction: discord.Interaction, uzytkownik: discord.Member, powod: str = "Brak powodu"):
    if not module_on(interaction.guild_id, "moderacja"):
        await interaction.response.send_message("⛔ Moduł moderacji jest wyłączony.", ephemeral=True)
        return
    warns = load_json(WARNS_FILE, {})
    g = warns.setdefault(str(interaction.guild_id), {})
    u = g.setdefault(str(uzytkownik.id), [])
    u.append({"reason": powod, "mod": str(interaction.user.id), "ts": int(time.time())})
    save_json(WARNS_FILE, warns)
    embed = kara_embed("⚠️ WARN", uzytkownik, interaction.user, powod,
                       extra=f"Liczba warnów użytkownika: {len(u)}",
                       kolor=discord.Color.orange())
    ok = await log_kara(interaction.guild, embed)
    await interaction.response.send_message(
        f"✅ {uzytkownik.mention} otrzymał warna ({len(u)})."
        + (" Zapisano na kanale kar." if ok else " ⚠️ Nie znaleziono kanału kar (ustaw w /config)."),
        ephemeral=True,
    )


@bot.tree.command(name="mute", description="Wycisza użytkownika (timeout)")
@app_commands.guild_only()
@app_commands.check(require_perm("mute", "moderate_members"))
async def mute_cmd(interaction: discord.Interaction, uzytkownik: discord.Member,
                   czas: str = "10m", powod: str = "Brak powodu"):
    if not module_on(interaction.guild_id, "moderacja"):
        await interaction.response.send_message("⛔ Moduł moderacji jest wyłączony.", ephemeral=True)
        return
    seconds = parse_duration(czas)
    if not seconds or seconds > 28 * 86400:
        await interaction.response.send_message("⚠️ Zły format czasu (np. 10m, 1h, 2d, max 28d).", ephemeral=True)
        return
    try:
        await uzytkownik.timeout(timedelta(seconds=seconds), reason=powod)
    except discord.Forbidden:
        await interaction.response.send_message("⛔ Nie mogę wyciszyć (rola bota za nisko lub brak uprawnień).", ephemeral=True)
        return
    embed = kara_embed("🔇 MUTE", uzytkownik, interaction.user, powod, czas=format_duration(seconds))
    ok = await log_kara(interaction.guild, embed)
    await interaction.response.send_message(
        f"✅ Wyciszono {uzytkownik.mention} na {format_duration(seconds)}."
        + ("" if ok else " ⚠️ Nie znaleziono kanału kar (ustaw w /config)."),
        ephemeral=True,
    )


@bot.tree.command(name="kick", description="Wyrzuca użytkownika z serwera")
@app_commands.guild_only()
@app_commands.check(require_perm("kick", "kick_members"))
async def kick_cmd(interaction: discord.Interaction, uzytkownik: discord.Member, powod: str = "Brak powodu"):
    if not module_on(interaction.guild_id, "moderacja"):
        await interaction.response.send_message("⛔ Moduł moderacji jest wyłączony.", ephemeral=True)
        return
    try:
        await uzytkownik.kick(reason=powod)
    except discord.Forbidden:
        await interaction.response.send_message("⛔ Nie mogę wyrzucić (rola bota za nisko lub brak uprawnień).", ephemeral=True)
        return
    embed = kara_embed("👢 KICK", uzytkownik, interaction.user, powod, kolor=discord.Color.dark_red())
    ok = await log_kara(interaction.guild, embed)
    await interaction.response.send_message(
        f"✅ Wyrzucono {uzytkownik.mention}."
        + ("" if ok else " ⚠️ Nie znaleziono kanału kar (ustaw w /config)."),
        ephemeral=True,
    )


@bot.tree.command(name="ban", description="Banuje użytkownika (opcjonalnie na czas)")
@app_commands.guild_only()
@app_commands.check(require_perm("ban", "ban_members"))
async def ban_cmd(interaction: discord.Interaction, uzytkownik: discord.Member,
                  powod: str = "Brak powodu", czas: str = None):
    if not module_on(interaction.guild_id, "moderacja"):
        await interaction.response.send_message("⛔ Moduł moderacji jest wyłączony.", ephemeral=True)
        return
    seconds = parse_duration(czas) if czas else None
    if czas and not seconds:
        await interaction.response.send_message("⚠️ Zły format czasu (np. 10m, 1h, 7d).", ephemeral=True)
        return
    try:
        await interaction.guild.ban(uzytkownik, reason=powod)
    except discord.Forbidden:
        await interaction.response.send_message("⛔ Nie mogę zbanować (rola bota za nisko lub brak uprawnień).", ephemeral=True)
        return
    extra = None
    if seconds:
        bans = load_json(TEMPBANS_FILE, {})
        g = bans.setdefault(str(interaction.guild_id), {})
        g[str(uzytkownik.id)] = time.time() + seconds
        save_json(TEMPBANS_FILE, bans)
        extra = f"Automatyczne odbanowanie za: {format_duration(seconds)}"
    embed = kara_embed("🔨 BAN", uzytkownik, interaction.user, powod,
                       czas=format_duration(seconds) if seconds else None, extra=extra)
    ok = await log_kara(interaction.guild, embed)
    await interaction.response.send_message(
        f"✅ Zbanowano {uzytkownik.mention}" + (f" na {format_duration(seconds)}." if seconds else ".")
        + ("" if ok else " ⚠️ Nie znaleziono kanału kar (ustaw w /config)."),
        ephemeral=True,
    )


@tasks.loop(minutes=1)
async def tempban_loop():
    bans = load_json(TEMPBANS_FILE, {})
    if not bans:
        return
    now = time.time()
    changed = False
    for guild_id, users in list(bans.items()):
        guild = bot.get_guild(int(guild_id))
        if guild is None:
            continue
        for user_id, ts in list(users.items()):
            if ts <= now:
                try:
                    await guild.unban(discord.Object(id=int(user_id)), reason="Koniec czasowego bana")
                except Exception:
                    pass
                users.pop(user_id, None)
                changed = True
    if changed:
        save_json(TEMPBANS_FILE, bans)


# ---------- Giveawaye ----------

gw_lock = asyncio.Lock()


async def set_participation(message_id, user_id, desired=None):
    """Dodaje/usuwa uczestnika i odswieza licznik. Zwraca joined albo None gdy nieaktywny."""
    async with gw_lock:
        all_gw = load_json(GIVEAWAYS_FILE, {})
        gw = all_gw.get(str(message_id))
        if not gw or gw.get("ended"):
            return None
        parts = gw["participants"]
        uid = str(user_id)
        if desired is None:
            desired = uid not in parts
        if desired and uid not in parts:
            parts.append(uid)
        elif not desired and uid in parts:
            parts.remove(uid)
        save_json(GIVEAWAYS_FILE, all_gw)
        try:
            channel = bot.get_channel(gw["channel"])
            if channel is None:
                channel = await bot.fetch_channel(gw["channel"])
            msg = await channel.fetch_message(int(message_id))
            await msg.edit(
                embed=giveaway_embed(gw["prize"], gw["winners"], gw["ends_at"], len(parts)),
                view=GiveawayView(),
            )
        except Exception:
            pass
        return desired


class GiveawayView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Weź udział 🎉", style=discord.ButtonStyle.green, custom_id="gw_join")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not module_on(interaction.guild_id, "giveawaye"):
            await interaction.response.send_message("⛔ Moduł giveawayów jest wyłączony.", ephemeral=True)
            return
        all_gw = load_json(GIVEAWAYS_FILE, {})
        gw = all_gw.get(str(interaction.message.id))
        uid = str(interaction.user.id)
        if not gw or gw.get("ended"):
            await interaction.response.send_message("Ten giveaway już się zakończył.", ephemeral=True)
            return
        if uid in gw["participants"]:
            await interaction.response.send_message(
                "Już jesteś zapisany! Czy chcesz się wypisać?",
                view=UnjoinView(str(interaction.message.id)),
                ephemeral=True,
            )
            return
        result = await set_participation(interaction.message.id, interaction.user.id, True)
        if result is None:
            await interaction.response.send_message("Ten giveaway już się zakończył.", ephemeral=True)
        else:
            await interaction.response.send_message(
                "Zapisano! Powodzenia 🍀",
                view=UnjoinView(str(interaction.message.id)),
                ephemeral=True,
            )


class UnjoinView(discord.ui.View):
    def __init__(self, message_id):
        super().__init__(timeout=None)
        self.message_id = message_id

    @discord.ui.button(label="Wypisz się", style=discord.ButtonStyle.red, custom_id="gw_leave")
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not module_on(interaction.guild_id, "giveawaye"):
            await interaction.response.send_message("⛔ Moduł giveawayów jest wyłączony.", ephemeral=True)
            return
        result = await set_participation(self.message_id, interaction.user.id, False)
        if result is None:
            await interaction.response.edit_message(content="Ten giveaway już się zakończył.", view=None)
        else:
            await interaction.response.edit_message(content="Wypisano z giveawaya.", view=None)


def giveaway_embed(nagroda, winners_count, ends_at, participants=0):
    embed = discord.Embed(title="🎉 GIVEAWAY", description=f"**{nagroda}**", color=discord.Color.gold())
    embed.add_field(name="Zwycięzców", value=str(winners_count), inline=True)
    embed.add_field(name="Koniec", value=f"<t:{int(ends_at)}:R>", inline=True)
    embed.add_field(name="Uczestnicy", value=str(participants), inline=True)
    embed.add_field(name="Jak wziąć udział?", value="Kliknij przycisk poniżej 👇", inline=False)
    return embed


async def end_giveaway(message_id):
    all_gw = load_json(GIVEAWAYS_FILE, {})
    gw = all_gw.get(str(message_id))
    if not gw or gw.get("ended"):
        return None
    try:
        channel = bot.get_channel(gw["channel"])
        if channel is None:
            channel = await bot.fetch_channel(gw["channel"])
        msg = await channel.fetch_message(int(message_id))
    except Exception:
        gw["ended"] = True
        save_json(GIVEAWAYS_FILE, all_gw)
        return None
    parts = gw["participants"]
    winners = random.sample(parts, min(gw["winners"], len(parts))) if parts else []
    if winners:
        mentions = ", ".join(f"<@{w}>" for w in winners)
        embed = discord.Embed(title="🎉 GIVEAWAY ZAKOŃCZONY", color=discord.Color.gold())
        embed.add_field(name="Nagroda", value=gw["prize"], inline=False)
        embed.add_field(name="Zwycięzcy", value=mentions, inline=False)
        await msg.edit(embed=embed, view=None)
        await channel.send(f"🎉 Gratulacje {mentions}! Wygrałeś: **{gw['prize']}**")
    else:
        embed = discord.Embed(title="🎉 GIVEAWAY ZAKOŃCZONY", description="Brak uczestników.", color=discord.Color.greyple())
        await msg.edit(embed=embed, view=None)
        await channel.send("Giveaway zakończony — nikt nie wziął udziału.")
    gw["ended"] = True
    gw["winners_ids"] = winners
    save_json(GIVEAWAYS_FILE, all_gw)
    return winners


@tasks.loop(seconds=5)
async def giveaway_loop():
    all_gw = load_json(GIVEAWAYS_FILE, {})
    now = time.time()
    for mid, gw in list(all_gw.items()):
        if not gw.get("ended") and gw.get("ends_at", 0) <= now:
            if not module_on(gw.get("guild"), "giveawaye"):
                continue
            await end_giveaway(mid)


giveaway_group = app_commands.Group(name="giveaway", description="Giveawaye")


@giveaway_group.command(name="start", description="Startuje giveaway")
@app_commands.guild_only()
@app_commands.check(require_perm("giveaway", "manage_messages"))
async def gstart(interaction: discord.Interaction, czas: str,
                 zwyciezcy: app_commands.Range[int, 1, 20], nagroda: str,
                 kanal: discord.TextChannel = None):
    if not module_on(interaction.guild_id, "giveawaye"):
        await interaction.response.send_message("⛔ Moduł giveawayów jest wyłączony.", ephemeral=True)
        return
    seconds = parse_duration(czas)
    if not seconds or seconds < 30:
        await interaction.response.send_message("⚠️ Czas min. 30s (format np. 10m, 1h, 2d).", ephemeral=True)
        return
    channel = kanal or interaction.channel
    if kanal is None:
        dch = load_json(CONFIG_FILE, {}).get(str(interaction.guild_id), {}).get("giveaway_channel")
        if dch:
            found = interaction.guild.get_channel(int(dch))
            if found is not None:
                channel = found
    ends_at = time.time() + seconds
    msg = await channel.send(embed=giveaway_embed(nagroda, zwyciezcy, ends_at), view=GiveawayView())
    all_gw = load_json(GIVEAWAYS_FILE, {})
    all_gw[str(msg.id)] = {
        "guild": interaction.guild_id,
        "channel": channel.id,
        "prize": nagroda,
        "winners": zwyciezcy,
        "ends_at": ends_at,
        "participants": [],
        "ended": False,
    }
    save_json(GIVEAWAYS_FILE, all_gw)
    await interaction.response.send_message(f"✅ Giveaway wystartowany na {channel.mention}.", ephemeral=True)


def find_giveaway(guild_id, channel_id, active):
    all_gw = load_json(GIVEAWAYS_FILE, {})
    cands = [(mid, g) for mid, g in all_gw.items()
             if g.get("guild") == guild_id and g.get("channel") == channel_id
             and (not g.get("ended")) == active]
    cands.sort(key=lambda x: x[1].get("ends_at", 0), reverse=True)
    return cands[0] if cands else (None, None)


@giveaway_group.command(name="end", description="Wcześniej kończy giveaway")
@app_commands.guild_only()
@app_commands.check(require_perm("giveaway", "manage_messages"))
async def gend(interaction: discord.Interaction, wiadomosc_id: str = None):
    if not module_on(interaction.guild_id, "giveawaye"):
        await interaction.response.send_message("⛔ Moduł giveawayów jest wyłączony.", ephemeral=True)
        return
    if wiadomosc_id:
        winners = await end_giveaway(wiadomosc_id.strip())
        if winners is None:
            await interaction.response.send_message("⚠️ Nie znaleziono aktywnego giveawaya o tym ID.", ephemeral=True)
        else:
            await interaction.response.send_message("✅ Giveaway zakończony.", ephemeral=True)
        return
    mid, _ = find_giveaway(interaction.guild_id, interaction.channel_id, True)
    if not mid:
        await interaction.response.send_message("⚠️ Brak aktywnego giveawaya na tym kanale (lub podaj ID wiadomości).", ephemeral=True)
        return
    await end_giveaway(mid)
    await interaction.response.send_message("✅ Giveaway zakończony.", ephemeral=True)


@giveaway_group.command(name="reroll", description="Losuje nowych zwycięzców")
@app_commands.guild_only()
@app_commands.check(require_perm("giveaway", "manage_messages"))
async def greroll(interaction: discord.Interaction, wiadomosc_id: str = None):
    if not module_on(interaction.guild_id, "giveawaye"):
        await interaction.response.send_message("⛔ Moduł giveawayów jest wyłączony.", ephemeral=True)
        return
    all_gw = load_json(GIVEAWAYS_FILE, {})
    gw = None
    if wiadomosc_id:
        gw = all_gw.get(wiadomosc_id.strip())
    else:
        cands = [(mid, g) for mid, g in all_gw.items()
                 if g.get("guild") == interaction.guild_id and g.get("ended") and g.get("participants")]
        cands.sort(key=lambda x: x[1].get("ends_at", 0), reverse=True)
        if cands:
            _, gw = cands[0]
    if not gw or not gw.get("participants"):
        await interaction.response.send_message("⚠️ Brak zakończonego giveawaya z uczestnikami.", ephemeral=True)
        return
    winners = random.sample(gw["participants"], min(gw["winners"], len(gw["participants"])))
    mentions = ", ".join(f"<@{w}>" for w in winners)
    await interaction.channel.send(f"🎉 Ponowne losowanie! Wygrał(a): {mentions} — **{gw['prize']}**")
    await interaction.response.send_message("✅ Wylosowano ponownie.", ephemeral=True)


bot.tree.add_command(giveaway_group)


# ---------- Panel konfiguracyjny ----------

class KaryChannelSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(placeholder="Wybierz kanał kar", channel_types=[discord.ChannelType.text])

    async def callback(self, interaction: discord.Interaction):
        ch = self.values[0]
        cfg = load_json(CONFIG_FILE, {})
        g = cfg.setdefault(str(interaction.guild_id), {})
        g["kary_channel"] = str(ch.id)
        save_json(CONFIG_FILE, cfg)
        await interaction.response.send_message(f"✅ Kanał kar ustawiony: {ch.mention}", ephemeral=True)


# ---------- Interaktywny /config ----------

class ConfigHubSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="Wybierz opcję do konfiguracji…",
            options=[
                discord.SelectOption(label="Propozycje", value="prop", description="Kanał i emotki do głosowania", emoji="💡"),
                discord.SelectOption(label="Kary", value="kary", description="Kanał logów kar", emoji="🚫"),
                discord.SelectOption(label="Status", value="status", description="Serwer i kanał statusu", emoji="⛏️"),
                discord.SelectOption(label="Giveawaye", value="gw", description="Ustawienia giveawayów", emoji="🎉"),
                discord.SelectOption(label="Moduły", value="mod", description="Włączanie/wyłączanie modułów", emoji="🧩"),
            ],
        )

    async def callback(self, interaction: discord.Interaction):
        v = self.values[0]
        if v == "prop":
            await interaction.response.edit_message(
                content="💡 **Propozycje** — wybierz kanał (jeden) i ustaw emotki do głosowania.",
                embed=None, view=PropConfigView())
        elif v == "kary":
            await interaction.response.edit_message(
                content="🚫 **Kary** — wybierz kanał logów kar.",
                embed=None, view=KaryConfigView())
        elif v == "status":
            await interaction.response.edit_message(
                content="⛏️ **Status** — serwer Minecraft i kanał auto-statusu.",
                embed=None, view=await StatusConfigView.create(interaction.guild))
        elif v == "gw":
            await interaction.response.edit_message(
                content="🎉 **Giveawaye** — domyślny kanał giveawayów.",
                embed=None, view=GwConfigView())
        else:
            await interaction.response.edit_message(
                content="🧩 **Moduły** — kliknij, aby włączyć/wyłączyć.",
                embed=None, view=ModulesConfigView(interaction.guild_id))


def hub_view():
    v = discord.ui.View(timeout=600)
    v.add_item(ConfigHubSelect())
    return v


class PropChannelSingleSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(placeholder="Kanał propozycji (jeden)",
                         channel_types=[discord.ChannelType.text], min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        ch = self.values[0]
        cfg = load_json(CONFIG_FILE, {})
        g = cfg.setdefault(str(interaction.guild_id), {})
        g["propozycje"] = [str(ch.id)]
        save_json(CONFIG_FILE, cfg)
        await interaction.response.send_message(f"✅ Kanał propozycji: {ch.mention}", ephemeral=True)


class EmojiModal(discord.ui.Modal, title="Emotki do głosowania"):
    def __init__(self, current):
        super().__init__()
        self.tak = discord.ui.TextInput(label="Emotka TAK", default=current[0], max_length=20)
        self.moze = discord.ui.TextInput(label="Emotka MOŻE", default=current[1], max_length=20)
        self.nie = discord.ui.TextInput(label="Emotka NIE", default=current[2], max_length=20)
        self.add_item(self.tak)
        self.add_item(self.moze)
        self.add_item(self.nie)

    async def on_submit(self, interaction: discord.Interaction):
        emos = [self.tak.value.strip(), self.moze.value.strip(), self.nie.value.strip()]
        if any(not e for e in emos):
            await interaction.response.send_message("⚠️ Wszystkie 3 emotki są wymagane.", ephemeral=True)
            return
        cfg = load_json(CONFIG_FILE, {})
        g = cfg.setdefault(str(interaction.guild_id), {})
        g["propozycje_emojis"] = emos
        save_json(CONFIG_FILE, cfg)
        await interaction.response.send_message(f"✅ Emotki: {' '.join(emos)}", ephemeral=True)


class PropConfigView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)
        self.add_item(ConfigHubSelect())
        self.add_item(PropChannelSingleSelect())

    @discord.ui.button(label="Emotki TAK / MOŻE / NIE", style=discord.ButtonStyle.secondary, emoji="😀")
    async def emojis(self, interaction: discord.Interaction, button: discord.ui.Button):
        cur = load_json(CONFIG_FILE, {}).get(str(interaction.guild_id), {}).get(
            "propozycje_emojis", ["✅", "➖", "❌"])
        if not isinstance(cur, list) or len(cur) != 3:
            cur = ["✅", "➖", "❌"]
        await interaction.response.send_modal(EmojiModal(cur))


class KaryConfigView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)
        self.add_item(ConfigHubSelect())
        self.add_item(KaryChannelSelect())


class ServerModal(discord.ui.Modal, title="Serwer Minecraft"):
    def __init__(self, address, name):
        super().__init__()
        self.adres = discord.ui.TextInput(label="Adres (IP/domena)", default=address or "", max_length=100)
        self.nazwa = discord.ui.TextInput(label="Nazwa", default=name or "", required=False, max_length=50)
        self.add_item(self.adres)
        self.add_item(self.nazwa)

    async def on_submit(self, interaction: discord.Interaction):
        address = self.adres.value.strip()
        name = self.nazwa.value.strip() or SERVER_NAME
        if not address:
            await interaction.response.send_message("⚠️ Podaj adres.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        server_type = await asyncio.to_thread(detect_server_type, address)
        if server_type is None:
            save_server(address, "java", name)
            await interaction.followup.send(
                f"⚠️ Zapisano **{name}** (`{address}`), ale nie udało się połączyć.", ephemeral=True)
        else:
            save_server(address, server_type, name)
            await interaction.followup.send(
                f"✅ Zapisano **{name}** (`{address}`, {server_type}).", ephemeral=True)


class AutoAddSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(placeholder="Dodaj auto-status na kanale…",
                         channel_types=[discord.ChannelType.text], min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if not module_on(interaction.guild_id, "autostatus"):
            await interaction.response.send_message("⛔ Moduł auto-statusu jest wyłączony.", ephemeral=True)
            return
        address, server_type, name = load_server()
        if not address:
            await interaction.response.send_message("Najpierw ustaw serwer (przycisk powyżej).", ephemeral=True)
            return
        channel = self.values[0]
        await interaction.response.defer(ephemeral=True)
        data = await asyncio.to_thread(fetch_mc_status, address, server_type)
        embed, favicon = build_status_embed(data, address, name)
        if favicon:
            msg = await channel.send(embed=embed, file=favicon)
        else:
            msg = await channel.send(embed=embed)
        entries = load_autostatus()
        entries[str(channel.id)] = str(msg.id)
        save_autostatus(entries)
        await interaction.followup.send(f"Auto-status ustawiony na {channel.mention}.", ephemeral=True)


class StatusConfigView(discord.ui.View):
    def __init__(self, remove_options):
        super().__init__(timeout=600)
        self.add_item(ConfigHubSelect())
        self.add_item(AutoAddSelect())
        if remove_options:
            sel = discord.ui.Select(placeholder="Usuń auto-status…",
                                    options=remove_options, min_values=1, max_values=1)

            async def rm_cb(interaction: discord.Interaction):
                cid = sel.values[0]
                entries = load_autostatus()
                try:
                    ch = interaction.guild.get_channel(int(cid))
                    if ch is None:
                        ch = await interaction.guild.fetch_channel(int(cid))
                    msg = await ch.fetch_message(int(entries[cid]))
                    await msg.delete()
                except Exception:
                    pass
                entries.pop(cid, None)
                save_autostatus(entries)
                await interaction.response.send_message("🗑️ Usunięto auto-status.", ephemeral=True)

            sel.callback = rm_cb
            self.add_item(sel)

    @discord.ui.button(label="Ustaw serwer", style=discord.ButtonStyle.primary, emoji="⛏️")
    async def srv(self, interaction: discord.Interaction, button: discord.ui.Button):
        address, _, name = load_server()
        await interaction.response.send_modal(ServerModal(address or "", name or ""))

    @classmethod
    async def create(cls, guild):
        opts = []
        for cid in load_autostatus():
            try:
                ch = guild.get_channel(int(cid))
                if ch is not None:
                    opts.append(discord.SelectOption(label="#" + ch.name, value=str(cid)))
            except Exception:
                pass
        return cls(opts[:25])


class GwDefaultSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(placeholder="Domyślny kanał giveawayów…",
                         channel_types=[discord.ChannelType.text], min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        ch = self.values[0]
        cfg = load_json(CONFIG_FILE, {})
        g = cfg.setdefault(str(interaction.guild_id), {})
        g["giveaway_channel"] = str(ch.id)
        save_json(CONFIG_FILE, cfg)
        await interaction.response.send_message(f"✅ Domyślny kanał giveawayów: {ch.mention}", ephemeral=True)


class GwConfigView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)
        self.add_item(ConfigHubSelect())
        self.add_item(GwDefaultSelect())

    @discord.ui.button(label="Wyczyść domyślny kanał", style=discord.ButtonStyle.secondary)
    async def clear(self, interaction: discord.Interaction, button: discord.ui.Button):
        cfg = load_json(CONFIG_FILE, {})
        cfg.setdefault(str(interaction.guild_id), {}).pop("giveaway_channel", None)
        save_json(CONFIG_FILE, cfg)
        await interaction.response.send_message("🗑️ Wyczyszczono domyślny kanał.", ephemeral=True)


class ModulesConfigView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=600)
        self.guild_id = guild_id
        self.add_item(ConfigHubSelect())
        mods = load_json(CONFIG_FILE, {}).get(str(guild_id), {}).get("modules", {})
        for key in MODULES:
            on = mods.get(key, True)
            btn = discord.ui.Button(
                label=f"{'✅' if on else '❌'} {MOD_LABELS[key]}",
                style=discord.ButtonStyle.success if on else discord.ButtonStyle.secondary,
                custom_id=f"cfgmod_{key}",
            )

            async def cb(interaction: discord.Interaction, k=key):
                cfg = load_json(CONFIG_FILE, {})
                g = cfg.setdefault(str(interaction.guild_id), {})
                m = g.setdefault("modules", {})
                m[k] = not m.get(k, True)
                save_json(CONFIG_FILE, cfg)
                await interaction.response.edit_message(
                    content="🧩 **Moduły** — kliknij, aby włączyć/wyłączyć.",
                    view=ModulesConfigView(interaction.guild_id))

            btn.callback = cb
            self.add_item(btn)


@bot.tree.command(name="config", description="Panel konfiguracyjny bota")
@app_commands.guild_only()
@app_commands.check(require_perm("config", "manage_guild"))
async def config_cmd(interaction: discord.Interaction):
    embed = discord.Embed(
        title="⚙️ Konfiguracja bota",
        description="Wybierz opcję, którą chcesz skonfigurować:\n\n"
                    "💡 **Propozycje** — kanał i emotki do głosowania\n"
                    "🚫 **Kary** — kanał logów kar\n"
                    "⛏️ **Status** — serwer i kanał statusu\n"
                    "🎉 **Giveawaye** — ustawienia giveawayów\n"
                    "🧩 **Moduły** — włączanie i wyłączanie modułów",
        color=discord.Color.blurple())
    await interaction.response.send_message(embed=embed, view=hub_view(), ephemeral=True)


# ---------- /permisje ----------

perms_group = app_commands.Group(name="permisje", description="Uprawnienia do funkcji bota")


def _perm_mentions(entry):
    parts = [f"<@{u}>" for u in entry.get("users", [])]
    parts += [f"<@&{r}>" for r in entry.get("roles", [])]
    return ", ".join(parts) if parts else "—"


@perms_group.command(name="daj", description="Daje dostęp do funkcji (użytkownik/ranga)")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.choices(funkcja=[app_commands.Choice(name=label, value=key) for key, label in PERM_FUNCS])
async def perm_daj(interaction: discord.Interaction, funkcja: str,
                   uzytkownik: discord.Member = None, ranga: discord.Role = None):
    if uzytkownik is None and ranga is None:
        await interaction.response.send_message("⚠️ Podaj użytkownika albo rangę.", ephemeral=True)
        return
    perms = load_perms()
    g = perms.setdefault(str(interaction.guild_id), {})
    e = g.setdefault(funkcja, {"users": [], "roles": []})
    added = []
    if uzytkownik is not None and str(uzytkownik.id) not in e["users"]:
        e["users"].append(str(uzytkownik.id))
        added.append(uzytkownik.mention)
    if ranga is not None and str(ranga.id) not in e["roles"]:
        e["roles"].append(str(ranga.id))
        added.append(ranga.mention)
    save_perms(perms)
    label = dict(PERM_FUNCS)[funkcja]
    await interaction.response.send_message(
        f"✅ **{label}**: {'dodano ' + ', '.join(added) if added else '— już mieli dostęp'}.",
        ephemeral=True)


@perms_group.command(name="usun", description="Zabiera dostęp (puste = reset funkcji)")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.choices(funkcja=[app_commands.Choice(name=label, value=key) for key, label in PERM_FUNCS])
async def perm_usun(interaction: discord.Interaction, funkcja: str,
                    uzytkownik: discord.Member = None, ranga: discord.Role = None):
    perms = load_perms()
    g = perms.setdefault(str(interaction.guild_id), {})
    label = dict(PERM_FUNCS)[funkcja]
    if uzytkownik is None and ranga is None:
        g.pop(funkcja, None)
        save_perms(perms)
        await interaction.response.send_message(
            f"🗑️ **{label}**: zresetowano do uprawnień Discorda.", ephemeral=True)
        return
    e = g.setdefault(funkcja, {"users": [], "roles": []})
    if uzytkownik is not None and str(uzytkownik.id) in e["users"]:
        e["users"].remove(str(uzytkownik.id))
    if ranga is not None and str(ranga.id) in e["roles"]:
        e["roles"].remove(str(ranga.id))
    save_perms(perms)
    await interaction.response.send_message(f"✅ **{label}**: zabrano dostęp.", ephemeral=True)


@perms_group.command(name="lista", description="Lista uprawnień do funkcji")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.choices(funkcja=[app_commands.Choice(name=label, value=key) for key, label in PERM_FUNCS])
async def perm_lista(interaction: discord.Interaction, funkcja: str = None):
    g = load_perms().get(str(interaction.guild_id), {})
    embed = discord.Embed(title="🔑 Uprawnienia do funkcji", color=discord.Color.blurple())
    if funkcja:
        label = dict(PERM_FUNCS)[funkcja]
        embed.add_field(name=label, value=_perm_mentions(g.get(funkcja, {})), inline=False)
    else:
        for key, label in PERM_FUNCS:
            embed.add_field(name=label, value=_perm_mentions(g.get(key, {})), inline=False)
    embed.set_footer(text="Pusto = decydują uprawnienia Discorda. Administratorzy zawsze mogą.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


bot.tree.add_command(perms_group)


# ---------- Propozycje ----------

def get_propozycje(guild_id):
    cfg = load_json(CONFIG_FILE, {})
    return cfg.get(str(guild_id), {}).get("propozycje", [])


propozycje_group = app_commands.Group(name="propozycje", description="Propozycje")
prop_kanal = app_commands.Group(name="kanal", description="Kanały propozycji")


@prop_kanal.command(name="dodaj", description="Dodaje kanał propozycji")
@app_commands.guild_only()
@app_commands.check(require_perm("propozycje", "manage_guild"))
async def prop_dodaj(interaction: discord.Interaction, kanal: discord.TextChannel):
    cfg = load_json(CONFIG_FILE, {})
    g = cfg.setdefault(str(interaction.guild_id), {})
    lst = g.setdefault("propozycje", [])
    if str(kanal.id) in lst:
        await interaction.response.send_message("Ten kanał już jest na liście.", ephemeral=True)
        return
    lst.append(str(kanal.id))
    save_json(CONFIG_FILE, cfg)
    await interaction.response.send_message(f"✅ Dodano kanał propozycji: {kanal.mention}", ephemeral=True)


@prop_kanal.command(name="usun", description="Usuwa kanał propozycji")
@app_commands.guild_only()
@app_commands.check(require_perm("propozycje", "manage_guild"))
async def prop_usun(interaction: discord.Interaction, kanal: discord.TextChannel):
    cfg = load_json(CONFIG_FILE, {})
    g = cfg.setdefault(str(interaction.guild_id), {})
    lst = g.setdefault("propozycje", [])
    if str(kanal.id) not in lst:
        await interaction.response.send_message("Tego kanału nie ma na liście.", ephemeral=True)
        return
    lst.remove(str(kanal.id))
    save_json(CONFIG_FILE, cfg)
    await interaction.response.send_message(f"🗑️ Usunięto kanał propozycji: {kanal.mention}", ephemeral=True)


@prop_kanal.command(name="lista", description="Lista kanałów propozycji")
@app_commands.guild_only()
@app_commands.check(require_perm("propozycje", "manage_guild"))
async def prop_lista(interaction: discord.Interaction):
    ids = get_propozycje(interaction.guild_id)
    if not ids:
        await interaction.response.send_message("Brak kanałów propozycji.", ephemeral=True)
        return
    lines = []
    for cid in ids:
        ch = interaction.guild.get_channel(cid)
        lines.append(ch.mention if ch else f"nieznany kanał ({cid})")
    await interaction.response.send_message("Kanały propozycji: " + ", ".join(lines), ephemeral=True)


propozycje_group.add_command(prop_kanal)
bot.tree.add_command(propozycje_group)


@bot.event
async def on_message(message: discord.Message):
    if message.guild is None or message.author.bot:
        return
    if message.channel.id not in get_propozycje(message.guild.id):
        return
    if not module_on(message.guild.id, "propozycje"):
        return
    tresc = (message.content or "").strip()
    if not tresc:
        return
    autor = message.author
    embed = discord.Embed(description=tresc, color=discord.Color.blurple())
    try:
        embed.set_author(name=autor.display_name, icon_url=autor.display_avatar.url)
    except Exception:
        pass
    embed.set_footer(text=f"Zaproponował: {autor} • {autor.id}")
    embed.timestamp = discord.utils.utcnow()
    try:
        post = await message.channel.send(embed=embed)
    except Exception:
        return
    try:
        await message.delete()
    except Exception:
        pass
    emos = load_json(CONFIG_FILE, {}).get(str(message.guild.id), {}).get(
        "propozycje_emojis", ["✅", "➖", "❌"])
    if not isinstance(emos, list) or len(emos) != 3 or any(not isinstance(e, str) or not e for e in emos):
        emos = ["✅", "➖", "❌"]
    for emoji in emos:
        try:
            await post.add_reaction(emoji)
        except Exception:
            pass
    try:
        tytul = tresc.split("\n")[0][:80] or "Propozycja"
        await post.create_thread(name=f"💡 {tytul}", auto_archive_duration=1440)
    except Exception:
        pass


# ================= API DO PANELU =================

@web.middleware
async def cors_middleware(request, handler):
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, X-API-Key",
    }
    if request.method == "OPTIONS":
        return web.Response(headers=headers)
    resp = await handler(request)
    resp.headers.update(headers)
    return resp

async def api_auth(request):
    return bool(API_KEY) and request.headers.get("X-API-Key", "") == API_KEY


def api_ok(data=None):
    return web.json_response({"ok": True, **(data or {})})


def api_err(msg, status=400):
    return web.json_response({"ok": False, "error": msg}, status=status)


async def api_overview(request):
    if not await api_auth(request):
        return api_err("Brak autoryzacji", 401)
    address, server_type, mc_name = load_server()
    active_gw = {mid: {"prize": g["prize"], "channel": g["channel"],
                       "ends_at": g["ends_at"], "participants": len(g["participants"])}
                 for mid, g in load_json(GIVEAWAYS_FILE, {}).items() if not g.get("ended")}
    return api_ok({
        "guilds": [{"id": str(g.id), "name": g.name} for g in bot.guilds],
        "mc_server": {"address": address, "type": server_type, "name": mc_name},
        "autostatus": load_autostatus(),
        "config": load_json(CONFIG_FILE, {}),
        "giveaways": active_gw,
    })


async def api_set_server(request):
    if not await api_auth(request):
        return api_err("Brak autoryzacji", 401)
    try:
        body = await request.json()
    except Exception:
        return api_err("Zły JSON")
    address = (body.get("address") or "").strip()
    name = (body.get("name") or "").strip() or SERVER_NAME
    if not address:
        return api_err("Brak adresu")
    server_type = await asyncio.to_thread(detect_server_type, address)
    if server_type is None:
        return api_err("Nie udało się połączyć z serwerem. Sprawdź adres.")
    save_server(address, server_type, name)
    return api_ok({"address": address, "type": server_type, "name": name})


async def api_remove_server(request):
    if not await api_auth(request):
        return api_err("Brak autoryzacji", 401)
    clear_server()
    return api_ok()


async def api_set_kary(request):
    if not await api_auth(request):
        return api_err("Brak autoryzacji", 401)
    try:
        body = await request.json()
    except Exception:
        return api_err("Zły JSON")
    try:
        guild = bot.get_guild(int(body.get("guild_id")))
        channel = guild.get_channel(int(body.get("channel_id")))
    except Exception:
        return api_err("Zły serwer lub kanał")
    if guild is None or channel is None:
        return api_err("Nie znaleziono serwera lub kanału")
    cfg = load_json(CONFIG_FILE, {})
    g = cfg.setdefault(str(guild.id), {})
    g["kary_channel"] = str(channel.id)
    save_json(CONFIG_FILE, cfg)
    return api_ok({"kary_channel": str(channel.id)})


async def api_prop(request):
    if not await api_auth(request):
        return api_err("Brak autoryzacji", 401)
    try:
        body = await request.json()
    except Exception:
        return api_err("Zły JSON")
    action = body.get("action")
    try:
        guild_id = str(int(body.get("guild_id")))
        channel_id = str(int(body.get("channel_id")))
    except Exception:
        return api_err("Zły serwer lub kanał")
    cfg = load_json(CONFIG_FILE, {})
    g = cfg.setdefault(guild_id, {})
    lst = g.setdefault("propozycje", [])
    if action == "add":
        if channel_id not in lst:
            lst.append(channel_id)
    elif action == "remove":
        if channel_id in lst:
            lst.remove(channel_id)
    else:
        return api_err("Akcja: add albo remove")
    save_json(CONFIG_FILE, cfg)
    return api_ok({"propozycje": lst})


async def api_channels(request):
    if not await api_auth(request):
        return api_err("Brak autoryzacji", 401)
    try:
        guild = bot.get_guild(int(request.query.get("guild_id")))
    except Exception:
        guild = None
    if guild is None:
        return api_err("Nie znaleziono serwera")
    return api_ok({"channels": [{"id": str(c.id), "name": c.name}
                                for c in guild.text_channels]})


async def api_mc_status(request):
    if not await api_auth(request):
        return api_err("Brak autoryzacji", 401)
    address, server_type, name = load_server()
    if not address:
        return api_err("Brak serwera na liście")
    data = await asyncio.to_thread(fetch_mc_status, address, server_type)
    data.pop("favicon", None)
    data.pop("sample", None)
    return api_ok({"server": {"address": address, "type": server_type, "name": name},
                   "status": data})


async def api_modules(request):
    if not await api_auth(request):
        return api_err("Brak autoryzacji", 401)
    try:
        body = await request.json()
    except Exception:
        return api_err("Zły JSON")
    name = body.get("module")
    if name not in MODULES:
        return api_err("Nieznany moduł")
    try:
        guild_id = str(int(body.get("guild_id")))
    except Exception:
        return api_err("Zły serwer")
    enabled = bool(body.get("enabled"))
    cfg = load_json(CONFIG_FILE, {})
    g = cfg.setdefault(guild_id, {})
    mods = g.setdefault("modules", {})
    mods[name] = enabled
    save_json(CONFIG_FILE, cfg)
    return api_ok({"module": name, "enabled": enabled})


async def start_api():
    if not API_KEY:
        print("API panelu wylaczone (brak API_KEY).")
        return
    app = web.Application(middlewares=[cors_middleware])
    app.add_routes([
        web.get("/overview", api_overview),
        web.post("/server", api_set_server),
        web.delete("/server", api_remove_server),
        web.post("/kary", api_set_kary),
        web.post("/propozycje", api_prop),
        web.get("/channels", api_channels),
        web.get("/mcstatus", api_mc_status),
        web.post("/modules", api_modules),
    ])
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", API_PORT).start()
    print(f"API panelu slucha na porcie {API_PORT}", flush=True)


def migrate_config():
    """ID Discorda jako tekst (JS gubi precyzje duzych liczb)."""
    try:
        cfg = load_json(CONFIG_FILE, {})
        changed = False
        for g in cfg.values():
            if not isinstance(g, dict):
                continue
            if "kary_channel" in g and not isinstance(g["kary_channel"], str):
                g["kary_channel"] = str(g["kary_channel"])
                changed = True
            if isinstance(g.get("propozycje"), list):
                new_lst = [x if isinstance(x, str) else str(x) for x in g["propozycje"]]
                if new_lst != g["propozycje"]:
                    g["propozycje"] = new_lst
                    changed = True
        if changed:
            save_json(CONFIG_FILE, cfg)
    except Exception:
        pass


migrate_config()

bot.run(TOKEN)
