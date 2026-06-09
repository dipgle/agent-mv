#!/usr/bin/env python3
"""
Cross-platform environment checker.

Runs same on Win 10/11, macOS, Linux. Verifies prerequisites for the pipeline:
  - Python version, package imports
  - External binaries: ffmpeg, ffprobe, sqlite3, git, ollama
  - Network endpoints: Ollama, ComfyUI, LiteLLM
  - GPU detection (NVIDIA via nvidia-smi, Apple via system_profiler)
  - Disk free + RAM

Run:
    python scripts/check_env.py            # quick check, exit 0 on success
    python scripts/check_env.py --verbose  # full report
"""

from __future__ import annotations
import argparse
import importlib
import os
import platform
import shutil
import socket
import subprocess
import sys
from pathlib import Path

# Force UTF-8 output on Windows console (cp1252 is default and chokes on emoji)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


OK = "[ OK ]"
WARN = "[WARN]"
FAIL = "[FAIL]"

REQUIRED_BINARIES = ["python3" if sys.platform != "win32" else "python",
                     "ffmpeg", "ffprobe", "sqlite3", "git"]
OPTIONAL_BINARIES = ["ollama", "huggingface-cli"]

REQUIRED_PACKAGES = ["openai", "requests", "PIL"]
OPTIONAL_PACKAGES = ["litellm", "crewai", "huggingface_hub"]

ENDPOINTS = {
    "ollama":   ("localhost", 11434),
    "comfyui":  ("localhost", 8188),
    "litellm":  ("localhost", 4000),
}


def _check_bin(name: str) -> tuple[str, str]:
    p = shutil.which(name)
    if p:
        return OK, f"{name} -> {p}"
    return FAIL, f"{name} not found on PATH"


def _check_package(name: str) -> tuple[str, str]:
    try:
        m = importlib.import_module(name)
        ver = getattr(m, "__version__", "?")
        return OK, f"import {name} ({ver})"
    except ImportError as e:
        return FAIL, f"import {name}: {e}"


def _check_port(host: str, port: int) -> tuple[str, str]:
    try:
        with socket.create_connection((host, port), timeout=2):
            return OK, f"{host}:{port} reachable"
    except (socket.timeout, ConnectionRefusedError, OSError):
        return WARN, f"{host}:{port} not listening (start the service if needed)"


def _check_gpu() -> tuple[str, str]:
    if sys.platform == "darwin":
        try:
            out = subprocess.check_output(
                ["system_profiler", "SPDisplaysDataType"],
                text=True, timeout=5)
            chip_lines = [l for l in out.splitlines() if "Chipset Model" in l]
            if chip_lines:
                return OK, "macOS GPU: " + ", ".join(l.strip() for l in chip_lines)
            return WARN, "macOS GPU info unavailable"
        except Exception as e:
            return WARN, f"macOS GPU detect failed: {e}"
    # Windows / Linux: try nvidia-smi
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
                 "--format=csv,noheader"],
                text=True, timeout=5)
            return OK, "NVIDIA GPU: " + out.strip().replace("\n", " | ")
        except Exception as e:
            return WARN, f"nvidia-smi failed: {e}"
    return WARN, "No NVIDIA GPU detected (CPU-only mode = very slow)"


def _check_disk() -> tuple[str, str]:
    try:
        usage = shutil.disk_usage(".")
        free_gb = usage.free / 1024**3
        if free_gb < 50:
            return FAIL, f"only {free_gb:.1f} GB free (need >= 200 GB for full setup)"
        if free_gb < 200:
            return WARN, f"{free_gb:.1f} GB free (recommended >= 200 GB)"
        return OK, f"{free_gb:.1f} GB free"
    except Exception as e:
        return WARN, f"disk usage check failed: {e}"


def _check_ram() -> tuple[str, str]:
    try:
        if sys.platform == "win32":
            out = subprocess.check_output(
                ["wmic", "ComputerSystem", "get", "TotalPhysicalMemory"],
                text=True)
            for line in out.splitlines():
                line = line.strip()
                if line.isdigit():
                    gb = int(line) / 1024**3
                    return (OK if gb >= 16 else WARN,
                            f"RAM: {gb:.1f} GB total")
        elif sys.platform == "darwin":
            out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True)
            gb = int(out.strip()) / 1024**3
            return (OK if gb >= 16 else WARN, f"RAM: {gb:.1f} GB total")
        else:
            # Linux: /proc/meminfo
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        kb = int(line.split()[1])
                        gb = kb / 1024**2
                        return (OK if gb >= 16 else WARN, f"RAM: {gb:.1f} GB total")
        return WARN, "RAM detection unavailable"
    except Exception as e:
        return WARN, f"RAM check failed: {e}"


def _check_python() -> tuple[str, str]:
    v = sys.version_info
    if (v.major, v.minor) < (3, 10):
        return FAIL, f"Python {sys.version.split()[0]} (need >= 3.10)"
    return OK, f"Python {sys.version.split()[0]}"


def _check_devlog() -> tuple[str, str]:
    p = Path("logs/devlog.sqlite")
    if not p.exists():
        return WARN, "logs/devlog.sqlite missing — run from project root or init via adopt"
    return OK, f"devlog: {p.stat().st_size // 1024} KB"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    print(f"OS: {platform.system()} {platform.release()} ({platform.machine()})")
    print()

    sections = [
        ("Python runtime",    [_check_python()]),
        ("Required binaries", [_check_bin(b) for b in REQUIRED_BINARIES]),
        ("Optional binaries", [_check_bin(b) for b in OPTIONAL_BINARIES]),
        ("Required packages", [_check_package(p) for p in REQUIRED_PACKAGES]),
        ("Optional packages", [_check_package(p) for p in OPTIONAL_PACKAGES]),
        ("Service endpoints", [_check_port(h, p) for h, p in ENDPOINTS.values()]),
        ("Hardware",          [_check_gpu(), _check_ram(), _check_disk()]),
        ("Project state",     [_check_devlog()]),
    ]

    n_fail = 0
    for title, results in sections:
        print(f"=== {title} ===")
        for status, msg in results:
            if status == FAIL:
                n_fail += 1
            if args.verbose or status != OK:
                print(f"  {status} {msg}")
            else:
                print(f"  {status} {msg.split(':')[0] if ':' in msg else msg}")
        print()

    print(f"Summary: {n_fail} blocker(s)")
    sys.exit(0 if n_fail == 0 else 1)


if __name__ == "__main__":
    main()
