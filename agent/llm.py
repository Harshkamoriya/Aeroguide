"""
agent/llm.py — Unified LLM client.

Priority order:
  1. Groq  (cloud, free tier)  — if GROQ_API_KEY is set
  2. Ollama (local)            — fallback for local dev
  3. None  (caller uses keyword fallback)

Groq free tier: 14,400 req/day, 6,000 tokens/min — more than enough for demo.
Get key at: https://console.groq.com
"""

import os
import logging
import requests

logger = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL   = os.getenv("GROQ_MODEL",   "llama-3.1-8b-instant")
GROQ_URL     = "https://api.groq.com/openai/v1/chat/completions"

OLLAMA_URL   = os.getenv("OLLAMA_URL",   "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:1b")


def chat(messages: list[dict],
         max_tokens:   int   = 150,
         temperature:  float = 0.4,
         json_mode:    bool  = False) -> str | None:
    """
    Send a chat request. Returns the model's text reply, or None on failure.
    Tries Groq first, then Ollama.
    """
    if GROQ_API_KEY:
        result = _groq(messages, max_tokens, temperature, json_mode)
        if result is not None:
            return result
        logger.warning("[LLM] Groq failed — trying Ollama fallback")

    return _ollama(messages, max_tokens, temperature, json_mode)


def _groq(messages, max_tokens, temperature, json_mode) -> str | None:
    body = {
        "model":       GROQ_MODEL,
        "messages":    messages,
        "temperature": temperature,
        "max_tokens":  max_tokens,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    try:
        resp = requests.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type":  "application/json",
            },
            json=body,
            timeout=(5, 30),
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        logger.debug(f"[LLM/Groq] {content[:100]}")
        return content
    except requests.HTTPError as e:
        logger.error(f"[LLM/Groq] HTTP {e.response.status_code}: {e.response.text[:120]}")
    except (requests.ConnectionError, requests.Timeout):
        logger.warning("[LLM/Groq] Unreachable")
    except Exception as e:
        logger.error(f"[LLM/Groq] {e}")
    return None


def _ollama(messages, max_tokens, temperature, json_mode) -> str | None:
    body = {
        "model":      OLLAMA_MODEL,
        "keep_alive": -1,
        "messages":   messages,
        "stream":     False,
        "options":    {"temperature": temperature, "num_predict": max_tokens},
    }
    if json_mode:
        body["format"] = "json"
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json=body,
            timeout=(5, 180),
        )
        resp.raise_for_status()
        content = resp.json()["message"]["content"]
        logger.debug(f"[LLM/Ollama] {content[:100]}")
        return content
    except (requests.ConnectionError, requests.Timeout):
        logger.warning("[LLM/Ollama] Unreachable — is 'ollama serve' running?")
    except Exception as e:
        logger.error(f"[LLM/Ollama] {e}")
    return None
