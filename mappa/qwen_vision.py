"""Qwen2.5-VL Visual Verification Module for MAPPA-Neo.

Crops an aligned side-by-side BEFORE vs. AFTER aerial image patch around any road cut or clicked
map coordinate, then sends it to Qwen2.5-VL (via OpenRouter, Alibaba DashScope, or local Ollama
OpenAI-compatible endpoint) to verify whether a flagged road is truly blocked (landslide debris,
flood washout, bridge collapse) or a classical optical false alarm (shadow / seasonal change).
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageDraw


def qwen_status() -> dict[str, Any]:
    base = os.environ.get("QWEN_API_BASE", "").strip()
    key = os.environ.get("QWEN_API_KEY", "").strip()
    model = os.environ.get("QWEN_MODEL", "qwen/qwen2.5-vl-72b-instruct:free").strip()
    is_local = "127.0.0.1" in base or "localhost" in base
    configured = bool(base and (key or is_local))
    return {
        "configured": configured,
        "provider": "local_ollama" if is_local else ("cloud_api" if configured else "unconfigured"),
        "model": model if configured else None,
        "api_base": base if configured else None,
    }


def crop_before_after_patch(
    out_dir: Path,
    bounds_wgs84: list[list[float]],
    lat: float,
    lon: float,
    half_px: int = 140,
) -> tuple[str, dict[str, Any]]:
    """Crop side-by-side BEFORE vs. AFTER patch centered at (lat, lon). Returns (data_uri_png, meta)."""
    pre_path = out_dir / "web_pre.png"
    post_path = out_dir / "web_post.png"
    if not pre_path.exists() or not post_path.exists():
        raise FileNotFoundError("Mission before/after raster overlays are missing.")

    (south, west), (north, east) = bounds_wgs84
    pre_im = Image.open(pre_path).convert("RGB")
    post_im = Image.open(post_path).convert("RGB")
    w, h = post_im.size
    if pre_im.size != (w, h):
        pre_im = pre_im.resize((w, h), Image.BILINEAR)

    fx = (lon - west) / max(east - west, 1e-9)
    fy = (north - lat) / max(north - south, 1e-9)
    cx = int(round(max(0.0, min(1.0, fx)) * (w - 1)))
    cy = int(round(max(0.0, min(1.0, fy)) * (h - 1)))

    # Keep crop within image bounds
    hp = min(half_px, w // 2 - 1, h // 2 - 1)
    x0 = max(0, min(w - 2 * hp, cx - hp))
    y0 = max(0, min(h - 2 * hp, cy - hp))
    box = (x0, y0, x0 + 2 * hp, y0 + 2 * hp)

    c_pre = pre_im.crop(box).resize((320, 320), Image.BICUBIC)
    c_post = post_im.crop(box).resize((320, 320), Image.BICUBIC)

    # Crosshair relative position inside 320x320 crop
    rx = int(round((cx - x0) / max(2 * hp, 1) * 320))
    ry = int(round((cy - y0) / max(2 * hp, 1) * 320))

    # Build clean 656x356 side-by-side comparison canvas
    header_h = 32
    gap = 8
    canvas = Image.new("RGB", (320 * 2 + gap, 320 + header_h), (17, 24, 39))
    canvas.paste(c_pre, (0, header_h))
    canvas.paste(c_post, (320 + gap, header_h))

    draw = ImageDraw.Draw(canvas)
    draw.text((10, 9), "BEFORE (Pre-Event Reference)", fill=(209, 213, 219))
    draw.text((320 + gap + 10, 9), "AFTER (Post-Disaster)", fill=(252, 165, 165))

    # Draw subtle target ring on both panels
    for ox in (0, 320 + gap):
        px, py = ox + rx, header_h + ry
        r = 16
        draw.ellipse((px - r, py - r, px + r, py + r), outline=(239, 68, 68), width=2)
        draw.line((px - 22, py, px - 10, py), fill=(255, 255, 255), width=2)
        draw.line((px + 10, py, px + 22, py), fill=(255, 255, 255), width=2)
        draw.line((px, py - 22, px, py - 10), fill=(255, 255, 255), width=2)
        draw.line((px, py + 10, px, py + 22), fill=(255, 255, 255), width=2)

    buf = io.BytesIO()
    canvas.save(buf, format="PNG", optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}", {"center_px": [cx, cy], "lat": round(lat, 6), "lon": round(lon, 6)}


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return {
        "verdict": "POSSIBLE_DAMAGE",
        "damage_type": "unknown",
        "severity": "MODERATE",
        "confidence": 0.5,
        "summary": text[:300] or "Visual inspection completed.",
        "recommended_action": "Verify on the ground before committing heavy equipment.",
    }


def inspect_with_qwen(
    out_dir: Path,
    bounds_wgs84: list[list[float]],
    lat: float,
    lon: float,
    road_name: str = "Road segment",
    mappa_status: str = "BLOCKED",
) -> dict[str, Any]:
    """Crop Before/After patch and run Qwen2.5-VL visual verification if configured."""
    data_uri, meta = crop_before_after_patch(out_dir, bounds_wgs84, lat, lon)
    st = qwen_status()

    if not st["configured"]:
        return {
            "mode": "visual_crop_only",
            "model": None,
            "patch_data_uri": data_uri,
            "meta": meta,
            "verdict": "UNVERIFIED_NO_QWEN",
            "damage_type": "visual_check_required",
            "severity": "MODERATE",
            "confidence": None,
            "summary": "Qwen-VL API key is not configured on this server yet. Inspect the side-by-side Before/After photo above directly.",
            "recommended_action": "Set QWEN_API_BASE, QWEN_API_KEY, and QWEN_MODEL in environment variables (or run local Ollama qwen2.5vl).",
        }

    prompt = (
        f"You are an emergency disaster-mapping analyst inspecting a side-by-side aerial/satellite comparison "
        f"centered on the red target crosshair for '{road_name}' (classical change detector flagged it as {mappa_status}). "
        f"Left image = BEFORE disaster. Right image = AFTER disaster.\n"
        f"Compare the exact area inside and around the red crosshair carefully. Distinguish real disaster road blockage "
        f"(flood washout, river scour, mud/rock landslide debris, bridge collapse) from false alarms "
        f"(mountain shadow, cloud, lighting/seasonal colour difference).\n"
        f"Respond ONLY with a valid JSON object matching this exact schema:\n"
        f'{{"verdict": "CONFIRMED_BLOCKED" | "POSSIBLE_DAMAGE" | "FALSE_ALARM_CLEAR", '
        f'"damage_type": "flood_washout" | "landslide_debris" | "bridge_collapse" | "road_scour" | "shadow_or_seasonal" | "clear", '
        f'"severity": "CRITICAL" | "MODERATE" | "LOW" | "NONE", '
        f'"confidence": 0.0 to 1.0, '
        f'"summary": "1-2 plain-English sentences describing what visibly changed between BEFORE and AFTER at the crosshair.", '
        f'"recommended_action": "Short actionable instruction for rescue or road clearance crew."}}'
    )

    api_url = st["api_base"].rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    key = os.environ.get("QWEN_API_KEY", "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if "openrouter.ai" in api_url:
        headers["HTTP-Referer"] = "https://github.com/kavixsri/mappa_neo"
        headers["X-Title"] = "MAPPA-Neo Disaster Mapping"

    payload = {
        "model": st["model"],
        "temperature": 0.1,
        "max_tokens": 320,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }
        ],
    }

    try:
        with httpx.Client(timeout=35.0) as client:
            r = client.post(api_url, headers=headers, json=payload)
            if r.status_code >= 400:
                return {
                    "mode": "qwen_error",
                    "model": st["model"],
                    "patch_data_uri": data_uri,
                    "meta": meta,
                    "verdict": "API_ERROR",
                    "damage_type": "error",
                    "severity": "NONE",
                    "confidence": None,
                    "summary": f"Qwen-VL endpoint returned HTTP {r.status_code}: {r.text[:180]}",
                    "recommended_action": "Check QWEN_API_KEY / QWEN_MODEL or inspect the Before/After crop manually.",
                }
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
            parsed = _extract_json(str(content))
            return {
                "mode": "qwen_live",
                "model": st["model"],
                "patch_data_uri": data_uri,
                "meta": meta,
                "verdict": parsed.get("verdict", "POSSIBLE_DAMAGE"),
                "damage_type": parsed.get("damage_type", "unknown"),
                "severity": parsed.get("severity", "MODERATE"),
                "confidence": parsed.get("confidence"),
                "summary": parsed.get("summary", ""),
                "recommended_action": parsed.get("recommended_action", ""),
            }
    except Exception as exc:
        return {
            "mode": "qwen_error",
            "model": st["model"],
            "patch_data_uri": data_uri,
            "meta": meta,
            "verdict": "API_ERROR",
            "damage_type": "error",
            "severity": "NONE",
            "confidence": None,
            "summary": f"Could not reach Qwen-VL ({type(exc).__name__}). Showing local Before/After crop.",
            "recommended_action": "Verify visually from the Before/After crop above.",
        }
