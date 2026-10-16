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


autostatus = app_commands.Group(name="autostatus", description="Automatyczny status serwera")


@autostatus.command(name="set", description="Ustawia auto-status na kanale")
async def autoset(interaction: discord.Interaction, channel: discord.TextChannel):
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
async def server_remove(interaction: discord.Interaction):
    clear_server()
    await interaction.response.send_message("🗑️ Usunięto serwer z listy.", ephemeral=True)


bot.tree.add_command(server_group)


# ================= MODERACJA, GIVEAWAYE, KONFIG =================

WARNS_FILE = os.path.join(os.path.dirname(__file__), "warns.json")
TEMPBANS_FILE = os.path.join(os.path.dirname(__file__), "tempbans.json")
GIVEAWAYS_FILE = os.path.join(os.path.dirname(__file__), "giveaways.json")
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")


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
@app_commands.checks.has_permissions(manage_messages=True)
async def warn_cmd(interaction: discord.Interaction, uzytkownik: discord.Member, powod: str = "Brak powodu"):
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
@app_commands.checks.has_permissions(moderate_members=True)
async def mute_cmd(interaction: discord.Interaction, uzytkownik: discord.Member,
                   czas: str = "10m", powod: str = "Brak powodu"):
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
@app_commands.checks.has_permissions(kick_members=True)
async def kick_cmd(interaction: discord.Interaction, uzytkownik: discord.Member, powod: str = "Brak powodu"):
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
@app_commands.checks.has_permissions(ban_members=True)
async def ban_cmd(interaction: discord.Interaction, uzytkownik: discord.Member,
                  powod: str = "Brak powodu", czas: str = None):
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
            await end_giveaway(mid)


giveaway_group = app_commands.Group(name="giveaway", description="Giveawaye")


@giveaway_group.command(name="start", description="Startuje giveaway")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_messages=True)
async def gstart(interaction: discord.Interaction, czas: str,
                 zwyciezcy: app_commands.Range[int, 1, 20], nagroda: str,
                 kanal: discord.TextChannel = None):
    seconds = parse_duration(czas)
    if not seconds or seconds < 30:
        await interaction.response.send_message("⚠️ Czas min. 30s (format np. 10m, 1h, 2d).", ephemeral=True)
        return
    channel = kanal or interaction.channel
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
@app_commands.checks.has_permissions(manage_messages=True)
async def gend(interaction: discord.Interaction, wiadomosc_id: str = None):
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
@app_commands.checks.has_permissions(manage_messages=True)
async def greroll(interaction: discord.Interaction, wiadomosc_id: str = None):
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
        g["kary_channel"] = ch.id
        save_json(CONFIG_FILE, cfg)
        await interaction.response.send_message(f"✅ Kanał kar ustawiony: {ch.mention}", ephemeral=True)


class ConfigView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(KaryChannelSelect())


@bot.tree.command(name="config", description="Panel konfiguracyjny bota")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def config_cmd(interaction: discord.Interaction):
    address, _, mc_name = load_server()
    cfg = load_json(CONFIG_FILE, {}).get(str(interaction.guild_id), {})
    kary_ch = None
    if cfg.get("kary_channel"):
        kary_ch = interaction.guild.get_channel(int(cfg["kary_channel"]))
    if kary_ch is None:
        kary_ch = find_kary_channel(interaction.guild)
    embed = discord.Embed(title="⚙️ Konfiguracja bota", color=discord.Color.blurple())
    embed.add_field(name="Kanał kar", value=kary_ch.mention if kary_ch else "— (wybierz poniżej)", inline=False)
    embed.add_field(name="Serwer MC", value=f"**{mc_name}** (`{address}`)" if address else "— (dodaj przez /server add)", inline=False)
    n_auto = len(load_autostatus())
    embed.add_field(name="Auto-statusy", value=str(n_auto), inline=True)
    n_gw = sum(1 for g in load_json(GIVEAWAYS_FILE, {}).values() if not g.get("ended"))
    embed.add_field(name="Aktywne giveawaye", value=str(n_gw), inline=True)
    await interaction.response.send_message(embed=embed, view=ConfigView(), ephemeral=True)


# ---------- Propozycje ----------

def get_propozycje(guild_id):
    cfg = load_json(CONFIG_FILE, {})
    return cfg.get(str(guild_id), {}).get("propozycje", [])


propozycje_group = app_commands.Group(name="propozycje", description="Propozycje")
prop_kanal = app_commands.Group(name="kanal", description="Kanały propozycji")


@prop_kanal.command(name="dodaj", description="Dodaje kanał propozycji")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def prop_dodaj(interaction: discord.Interaction, kanal: discord.TextChannel):
    cfg = load_json(CONFIG_FILE, {})
    g = cfg.setdefault(str(interaction.guild_id), {})
    lst = g.setdefault("propozycje", [])
    if kanal.id in lst:
        await interaction.response.send_message("Ten kanał już jest na liście.", ephemeral=True)
        return
    lst.append(kanal.id)
    save_json(CONFIG_FILE, cfg)
    await interaction.response.send_message(f"✅ Dodano kanał propozycji: {kanal.mention}", ephemeral=True)


@prop_kanal.command(name="usun", description="Usuwa kanał propozycji")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def prop_usun(interaction: discord.Interaction, kanal: discord.TextChannel):
    cfg = load_json(CONFIG_FILE, {})
    g = cfg.setdefault(str(interaction.guild_id), {})
    lst = g.setdefault("propozycje", [])
    if kanal.id not in lst:
        await interaction.response.send_message("Tego kanału nie ma na liście.", ephemeral=True)
        return
    lst.remove(kanal.id)
    save_json(CONFIG_FILE, cfg)
    await interaction.response.send_message(f"🗑️ Usunięto kanał propozycji: {kanal.mention}", ephemeral=True)


@prop_kanal.command(name="lista", description="Lista kanałów propozycji")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
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
    for emoji in ("✅", "➖", "❌"):
        try:
            await post.add_reaction(emoji)
        except Exception:
            pass
    try:
        tytul = tresc.split("\n")[0][:80] or "Propozycja"
        await post.create_thread(name=f"💡 {tytul}", auto_archive_duration=1440)
    except Exception:
        pass


bot.run(TOKEN)
