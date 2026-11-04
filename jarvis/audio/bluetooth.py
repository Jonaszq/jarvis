"""Pilnuje połączenia z głośnikiem Bluetooth (JBL wyłącza się sam przy bezczynności
albo łapie telefon). Co kilkanaście sekund sprawdza i w razie potrzeby łączy ponownie."""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("jarvis.bt")


async def _btctl(*args: str, timeout: float = 15.0) -> str:
    proc = await asyncio.create_subprocess_exec(
        "bluetoothctl", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.DEVNULL,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return ""
    return out.decode(errors="replace")


async def is_connected(mac: str) -> bool:
    return "Connected: yes" in await _btctl("info", mac)


async def ensure_connected(mac: str) -> bool:
    if await is_connected(mac):
        return True
    out = await _btctl("connect", mac, timeout=20)
    ok = "Connection successful" in out or await is_connected(mac)
    if ok:
        log.info("głośnik %s połączony ponownie", mac)
    return ok


async def keep_connected(mac: str, every_s: float = 15.0) -> None:
    was_ok = True
    while True:
        try:
            ok = await ensure_connected(mac)
            if not ok and was_ok:
                log.warning("głośnik %s niedostępny (wyłączony albo połączony z telefonem)", mac)
            was_ok = ok
        except FileNotFoundError:
            log.warning("brak bluetoothctl, nie pilnuję głośnika")
            return
        except Exception:
            log.exception("błąd przy sprawdzaniu głośnika")
        await asyncio.sleep(every_s)
