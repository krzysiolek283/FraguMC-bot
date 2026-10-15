import asyncio
import base64
import io
import json
import os
import re

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
AUTOSTATUS_MINUTES = 5

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
    motd = (data.get("motd") or "—").strip() or "—"
    if len(motd) > 900:
        motd = motd[:900] + "…"
    embed.add_field(name="Opis (MOTD)", value=motd, inline=False)

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
    if not autostatus_loop.is_running():
        autostatus_loop.start()
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
        await interaction.channel.send(embed=embed, file=favicon)
    else:
        await interaction.channel.send(embed=embed)
    await interaction.followup.send("Wysłano!", ephemeral=True)


@bot.tree.command(name="gracze", description="Lista graczy online")
async def gracze(interaction: discord.Interaction):
    address, server_type, name = await require_server(interaction)
    if not address:
        return
    await interaction.response.defer(ephemeral=True)
    data = await asyncio.to_thread(fetch_mc_status, address, server_type)
    if not data.get("online"):
        await interaction.channel.send(f"🔴 Serwer **{name}** jest offline.")
    elif not data.get("sample"):
        await interaction.channel.send(
            f"🟢 Na serwerze **{name}** jest **{data['players_online']}** graczy, "
            "ale serwer nie udostępnia ich nicków."
        )
    else:
        names = "\n".join(f"• {n}" for n in data["sample"])
        embed = discord.Embed(
            title=f"{name} — gracze online ({data['players_online']}/{data['players_max']})",
            description=names,
            color=discord.Color.green(),
        )
        await interaction.channel.send(embed=embed)
    await interaction.followup.send("Wysłano!", ephemeral=True)


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

bot.run(TOKEN)
