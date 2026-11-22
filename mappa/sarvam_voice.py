"""Sarvam AI Multilingual Field Translation & Voice Radio Brief (Bulbul TTS + Sarvam Translate).

Sits directly in MAPPA-Neo's core operational flow for BOTH user segments:
  - NDRF / SDRF Rescue Teams: Translates live blocked-road & cut-off settlement alerts into Hindi /
    regional Indian languages and synthesizes a playable WAV radio voice briefing.
  - PWD / BRO Road Clearance Crews: Translates the #1 Neo4j excavator dispatch order & debris estimate
    into Hindi / regional languages so field operators can play or share over radio / WhatsApp.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

SARVAM_BASE = "https://api.sarvam.ai"

SUPPORTED_LANGUAGES = {
    "hi-IN": "Hindi (हिन्दी)",
    "en-IN": "English (India)",
    "bn-IN": "Bengali (বাংলা)",
    "mr-IN": "Marathi (मराठी)",
    "ta-IN": "Tamil (தமிழ்)",
    "te-IN": "Telugu (తెలుగు)",
    "gu-IN": "Gujarati (ગુજરાતી)",
    "pa-IN": "Punjabi (ਪੰਜਾਬੀ)",
    "kn-IN": "Kannada (ಕನ್ನಡ)",
    "ml-IN": "Malayalam (മലയാളം)",
    "od-IN": "Odia (ଓଡ଼ିଆ)",
}


def sarvam_status() -> dict[str, Any]:
    key = os.environ.get("SARVAM_API_KEY", "").strip()
    return {
        "configured": bool(key),
        "provider": "sarvam_ai",
        "translate_model": "sarvam-translate:v1",
        "tts_model": "bulbul:v3",
        "languages": list(SUPPORTED_LANGUAGES.keys()),
    }


def build_field_brief_text(mission: dict[str, Any], iso: dict[str, Any], pq: dict[str, Any], role: str = "commander") -> str:
    rep = mission.get("summary", {})
    loc = mission.get("location") or mission.get("name") or "the survey area"
    r_len = rep.get("roads", {}).get("length_m_by_status", {})
    blocked_m = int(round(r_len.get("BLOCKED", 0)))
    iso_cnt = iso.get("isolated_count", 0)
    iso_pop = iso.get("isolated_population", 0)
    iso_names = [f["name"] for f in iso.get("isolated_facilities", [])[:3]]
    top = next((q for q in pq.get("queue", []) if q.get("currently_blocked")), None)

    if role == "engineer":
        total_rubble = int(sum(q.get("est_debris_vol_m3", 0) for q in pq.get("queue", []) if q.get("currently_blocked")))
        if top:
            return (
                f"Road clearance priority order for {loc}. "
                f"{blocked_m} metres of road are blocked with about {total_rubble} cubic metres of debris. "
                f"Priority one: send excavators to Road {top['road_name']}, {int(round(top['length_m']))} metres long, "
                f"about {int(round(top['est_debris_vol_m3']))} cubic metres of rubble. "
                f"Clearing this stretch helps reconnect about {top['population_reconnected']} people."
            )
        return f"Road clearance update for {loc}. All priority road stretches are currently marked open."

    # NDRF / SDRF Field Rescue Brief
    places_str = ", ".join(iso_names) if iso_names else "no mapped settlements"
    top_str = f"Avoid Road {top['road_name']}." if top else "Main corridors are open."
    return (
        f"MAPPA rescue alert for {loc}. "
        f"{blocked_m} metres of road are blocked. "
        f"{iso_cnt} settlements and about {iso_pop} people are cut off, including {places_str}. "
        f"{top_str} Check the safe detour on your map before moving vehicles."
    )


def _call_translate(client: httpx.Client, key: str, text_en: str, target_lang: str) -> tuple[str, str | None]:
    if target_lang == "en-IN":
        return text_en, None
    headers = {"api-subscription-key": key, "Content-Type": "application/json"}
    for model in ("sarvam-translate:v1", "mayura:v1"):
        r = client.post(
            f"{SARVAM_BASE}/translate",
            headers=headers,
            json={
                "input": text_en,
                "source_language_code": "en-IN",
                "target_language_code": target_lang,
                "model": model,
            },
        )
        if r.status_code < 400:
            data = r.json()
            txt = data.get("translated_text") or data.get("output") or text_en
            return txt, None
    return text_en, f"Sarvam translate HTTP {r.status_code}: {r.text[:160]}"


def _call_tts(client: httpx.Client, key: str, text: str, target_lang: str) -> tuple[str | None, str | None, str]:
    headers = {"api-subscription-key": key, "Content-Type": "application/json"}
    # Try bulbul:v3 (stable) then bulbul:v2 / bulbul:v4-flash payloads
    attempts = [
        ("bulbul:v3", {"text": text[:1400], "target_language_code": target_lang, "model": "bulbul:v3", "speaker": "shubh"}),
        ("bulbul:v3-inputs", {"inputs": [text[:1400]], "target_language_code": target_lang, "model": "bulbul:v3", "speaker": "shubh"}),
        ("bulbul:v2", {"inputs": [text[:500]], "target_language_code": target_lang, "model": "bulbul:v2", "speaker": "anushka"}),
    ]
    last_err = None
    for label, payload in attempts:
        r = client.post(f"{SARVAM_BASE}/text-to-speech", headers=headers, json=payload)
        if r.status_code < 400:
            data = r.json()
            audios = data.get("audios") or ([data["audio"]] if data.get("audio") else [])
            if audios:
                return f"data:audio/wav;base64,{audios[0]}", None, label.split("-")[0]
        last_err = f"Sarvam TTS ({label}) HTTP {r.status_code}: {r.text[:160]}"
    return None, last_err, "bulbul:v3"


def generate_voice_brief(
    mission: dict[str, Any],
    iso: dict[str, Any],
    pq: dict[str, Any],
    language_code: str = "hi-IN",
    role: str = "commander",
) -> dict[str, Any]:
    if language_code not in SUPPORTED_LANGUAGES:
        language_code = "hi-IN"
    english_text = build_field_brief_text(mission, iso, pq, role=role)
    key = os.environ.get("SARVAM_API_KEY", "").strip()

    if not key:
        return {
            "mode": "unconfigured",
            "language_code": language_code,
            "language_name": SUPPORTED_LANGUAGES[language_code],
            "english_text": english_text,
            "translated_text": english_text,
            "audio_data_uri": None,
            "error": "SARVAM_API_KEY is not configured on this server. Add SARVAM_API_KEY to enable Hindi/Indic translation & Bulbul voice audio.",
        }

    try:
        with httpx.Client(timeout=30.0) as client:
            translated, t_err = _call_translate(client, key, english_text, language_code)
            audio_uri, tts_err, tts_model = _call_tts(client, key, translated, language_code)
        err = "; ".join(e for e in (t_err, tts_err) if e) or None
        return {
            "mode": "sarvam_live" if audio_uri else "sarvam_partial",
            "language_code": language_code,
            "language_name": SUPPORTED_LANGUAGES[language_code],
            "english_text": english_text,
            "translated_text": translated,
            "audio_data_uri": audio_uri,
            "models": {"translate": "sarvam-translate:v1", "tts": tts_model},
            "error": err,
        }
    except Exception as exc:
        return {
            "mode": "sarvam_error",
            "language_code": language_code,
            "language_name": SUPPORTED_LANGUAGES[language_code],
            "english_text": english_text,
            "translated_text": english_text,
            "audio_data_uri": None,
            "error": f"Could not reach Sarvam AI ({type(exc).__name__}).",
        }
