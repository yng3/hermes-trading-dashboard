"""
Trading Dashboard plugin backend.
Calls kraken-cli locally to fetch real account data and paper positions.
"""
import asyncio
import json
import shutil
from fastapi import APIRouter

router = APIRouter()


async def _run(cmd: list[str]) -> dict:
    """Run a command and return parsed JSON, or an error dict."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
    if proc.returncode != 0:
        return {"error": True, "message": stderr.decode().strip() or f"exit {proc.returncode}"}
    try:
        return json.loads(stdout.decode())
    except json.JSONDecodeError:
        return {"error": True, "message": f"non-JSON output: {stdout.decode()[:200]}"}


@router.get("/balance")
async def balance():
    """Real Kraken spot balance."""
    if not shutil.which("kraken-cli"):
        return {"error": True, "message": "kraken-cli not installed"}
    return await _run(["kraken-cli", "balance", "--output", "json"])


@router.get("/trades")
async def trades():
    """Recent closed orders / trade history."""
    if not shutil.which("kraken-cli"):
        return {"error": True, "message": "kraken-cli not installed"}
    return await _run(["kraken-cli", "trades", "closed", "--output", "json"])


@router.get("/positions")
async def positions():
    """Paper futures positions (the boys)."""
    if not shutil.which("kraken-cli"):
        return {"error": True, "message": "kraken-cli not installed"}
    return await _run(["kraken-cli", "futures", "paper", "positions", "--output", "json"])


@router.get("/summary")
async def summary():
    """Combined endpoint: balance + positions in one call."""
    if not shutil.which("kraken-cli"):
        return {"error": True, "message": "kraken-cli not installed"}
    bal, pos = await asyncio.gather(
        _run(["kraken-cli", "balance", "--output", "json"]),
        _run(["kraken-cli", "futures", "paper", "positions", "--output", "json"]),
    )
    return {"balance": bal, "positions": pos}