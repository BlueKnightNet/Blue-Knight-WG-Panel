#!/usr/bin/env python3
"""Telegram Bot for Blue Knight WG Panel management (restartable, state-backed)."""
import asyncio
import ipaddress
import logging
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Set

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

import panel

log = logging.getLogger("telegram-bot")

GB = 1073741824

_config = {"token": None, "admin_ids": set()}
_app = None
_loop = None
_thread = None


def configure(token: str | None, admin_ids: Set[int]):
    _config["token"] = token
    _config["admin_ids"] = set(admin_ids)


def bot_token() -> str | None:
    return _config["token"]


def is_admin(chat_id) -> bool:
    return chat_id is not None and chat_id in _config["admin_ids"]


def find_client(state, ref: str):
    clients = state.get("clients", [])
    for c in clients:
        if c["id"] == ref or c["name"] == ref:
            return c
    matches = [c for c in clients if c["id"].startswith(ref)]
    if len(matches) == 1:
        return matches[0]
    return None


async def require_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat = update.effective_chat
    if not is_admin(chat.id if chat else None):
        await update.effective_message.reply_text("Unauthorized chat.")
        return False
    return True


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    text = (
        "Blue Knight Panel\n"
        "/status - server status\n"
        "/clients - list clients\n"
        "/add <name> - create client\n"
        "/remove <id|name> - delete client\n"
        "/pause <id|name> - disable client\n"
        "/resume <id|name> - enable client\n"
        "/quota <id|name> <GB> - set data quota (0 = unlimited)\n"
        "/expire <id|name> <days|YYYY-MM-DD> - set expiry\n"
    )
    await update.effective_message.reply_text(text)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_cmd(update, context)


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    state = await asyncio.to_thread(panel.load)
    peers = {}
    try:
        command, _, iface, _, _ = panel.engine(state)
        dump = await asyncio.to_thread(panel.run, command, "show", iface, "dump")
        for line in dump.splitlines()[1:]:
            p = line.split("\t")
            if len(p) >= 7:
                peers[p[0]] = dict(endpoint=p[2], handshake=int(p[4]), received=int(p[5]), sent=int(p[6]))
    except Exception:
        pass
    metrics = await asyncio.to_thread(panel.client_metrics, state, peers)
    s = metrics["summary"]
    lines = [
        f"Protocol: {state.get('protocol','wireguard')}",
        f"Host: {state['host']}:{state['port']}",
        f"Clients: {s['total']} (enabled {s['enabled']}, recent {s['recent']})",
        f"Rx: {s['received']} B | Tx: {s['sent']} B",
    ]
    await update.effective_message.reply_text("\n".join(lines))


async def clients_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    state = await asyncio.to_thread(panel.load)
    if not state["clients"]:
        await update.effective_message.reply_text("No clients.")
        return
    lines = []
    for c in state["clients"]:
        enabled = c.get("enabled", True)
        quota = c.get("quotaBytes", 0)
        used = c.get("usedBytes", 0)
        exp = c.get("expiresAt", 0)
        exp_str = "never" if not exp else time.strftime("%Y-%m-%d", time.gmtime(exp))
        lines.append(
            f"{c['id']} | {c['name']} | {c['address']} | {'on' if enabled else 'off'} | "
            f"{used//GB}/{quota//GB} GiB | exp {exp_str}"
        )
    await update.effective_message.reply_text("\n".join(lines))


async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /add <name>")
        return
    name = context.args[0]
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", name):
        await update.effective_message.reply_text("Invalid name.")
        return

    async def work() -> str:
        with panel.MUTATION_LOCK:
            state = panel.load()
            await asyncio.to_thread(panel.account_usage, state)
            if any(c["name"] == name for c in state["clients"]):
                return "Name exists."
            command = panel.engine(state)[0]
            private = await asyncio.to_thread(panel.run, command, "genkey")
            public = await asyncio.to_thread(panel.run, command, "pubkey", data=private + "\n")
            psk = await asyncio.to_thread(panel.run, command, "genpsk")
            used = {c["address"] for c in state["clients"]}
            for block in state["unmanaged"]:
                for addr in panel.fields(block).get("AllowedIPs", "").split(","):
                    if addr.strip():
                        for host in ipaddress.ip_network(state["network"]).hosts():
                            if host in ipaddress.ip_network(addr.strip(), strict=False):
                                used.add(str(host))
            address = next((str(a) for a in list(ipaddress.ip_network(state["network"]).hosts())[1:] if str(a) not in used), None)
            if not address:
                return "No addresses remain."
            state["clients"].append(dict(id=secrets.token_hex(8), name=name, private=private, public=public, psk=psk, address=address))
            panel.save(state)
            return f"Created {name} ({address}) id={state['clients'][-1]['id']}"

    try:
        result = await work()
    except Exception as e:
        result = f"Error: {e}"
    await update.effective_message.reply_text(result)


async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /remove <id|name>")
        return
    ref = context.args[0]

    async def work() -> str:
        with panel.MUTATION_LOCK:
            state = panel.load()
            c = find_client(state, ref)
            if not c:
                return "Client not found."
            await asyncio.to_thread(panel.delete_clients, state, [c])
            return f"Removed {c['name']} ({c['id']})"

    try:
        result = await work()
    except Exception as e:
        result = f"Error: {e}"
    await update.effective_message.reply_text(result)


async def set_enabled(update: Update, context: ContextTypes.DEFAULT_TYPE, enabled: bool):
    if not await require_admin(update, context):
        return
    if not context.args:
        await update.effective_message.reply_text(f"Usage: /{'resume' if enabled else 'pause'} <id|name>")
        return
    ref = context.args[0]

    async def work() -> str:
        with panel.MUTATION_LOCK:
            state = panel.load()
            c = find_client(state, ref)
            if not c:
                return "Client not found."
            c["enabled"] = enabled
            panel.save(state)
            return f"{'Enabled' if enabled else 'Disabled'} {c['name']} ({c['id']})"

    try:
        result = await work()
    except Exception as e:
        result = f"Error: {e}"
    await update.effective_message.reply_text(result)


async def pause_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await set_enabled(update, context, False)


async def resume_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await set_enabled(update, context, True)


async def quota_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    if len(context.args) < 2:
        await update.effective_message.reply_text("Usage: /quota <id|name> <GB>")
        return
    ref, gb_text = context.args[0], context.args[1]
    try:
        gb = int(gb_text)
        if gb < 0:
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text("GB must be a non-negative integer.")
        return

    async def work() -> str:
        with panel.MUTATION_LOCK:
            state = panel.load()
            c = find_client(state, ref)
            if not c:
                return "Client not found."
            c["quotaBytes"] = gb * GB
            c["limitBlocked"] = panel.limit_reason(c)
            panel.save(state)
            return f"Quota for {c['name']} set to {gb} GiB."

    try:
        result = await work()
    except Exception as e:
        result = f"Error: {e}"
    await update.effective_message.reply_text(result)


async def expire_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, context):
        return
    if len(context.args) < 2:
        await update.effective_message.reply_text("Usage: /expire <id|name> <days|YYYY-MM-DD>")
        return
    ref, value = context.args[0], context.args[1]

    async def work() -> str:
        now = int(time.time())
        try:
            if re.fullmatch(r"\d+", value):
                exp = now + int(value) * 86400
            else:
                exp = int(datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            return "Invalid date. Use days or YYYY-MM-DD."
        with panel.MUTATION_LOCK:
            state = panel.load()
            c = find_client(state, ref)
            if not c:
                return "Client not found."
            c["expiresAt"] = exp
            c["limitBlocked"] = panel.limit_reason(c)
            panel.save(state)
            return f"Expiry for {c['name']} set to {time.strftime('%Y-%m-%d', time.gmtime(exp))}."

    try:
        result = await work()
    except Exception as e:
        result = f"Error: {e}"
    await update.effective_message.reply_text(result)


def build_application(token: str, admin_ids: Set[int]):
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("clients", clients_cmd))
    app.add_handler(CommandHandler("add", add_cmd))
    app.add_handler(CommandHandler("remove", remove_cmd))
    app.add_handler(CommandHandler("pause", pause_cmd))
    app.add_handler(CommandHandler("resume", resume_cmd))
    app.add_handler(CommandHandler("quota", quota_cmd))
    app.add_handler(CommandHandler("expire", expire_cmd))
    return app


class BotManager:
    def __init__(self):
        self.app = None
        self._loop = None
        self._thread = None

    def _run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self.app.run_polling(close_loop=False))
        except Exception:
            log.exception("Telegram bot polling stopped unexpectedly")

    def start(self, token: str, admin_ids: Set[int]):
        self.stop()
        configure(token, admin_ids)
        self.app = build_application(token, admin_ids)
        self._thread = threading.Thread(target=self._run, daemon=True, name="telegram-bot")
        self._thread.start()
        log.info("Telegram bot polling started.")

    def stop(self):
        global _app, _loop, _thread
        if self._loop and self.app:
            try:
                asyncio.run_coroutine_threadsafe(self.app.stop(), self._loop).result(timeout=5)
            except Exception:
                pass
        self.app = None
        self._loop = None
        self._thread = None

    def reconfigure(self, token: str | None, admin_ids: Set[int]):
        if not token:
            self.stop()
            configure(None, set())
            return
        self.start(token, admin_ids)

    def status(self) -> dict:
        return {
            "configured": bool(_config["token"]),
            "admin_chat_ids": sorted(_config["admin_ids"]),
        }


BOT = BotManager()
