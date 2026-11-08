"""
Which language model writes the coach's words.

  Ollama   on this computer: the default, and nothing leaves the machine.
  Online   any service that speaks the OpenAI chat-completions API, with
           the user's own key: Google Gemini (a free key from Google AI
           Studio), OpenAI, Groq, OpenRouter, or another address.

Whichever writes, the words are checked the same way: the model is given
facts computed by code, and every sentence it returns goes through the
same board checks, rewrite request and removal. An online model writes
better or worse; it is not trusted more.

An online model is named "online:<model>" wherever chesscoach passes model
names around (reviews, the coach, `chesscoach eval -m online:...`), so the
two kinds can be compared on the same answer keys.

What is sent: the prompts, which hold positions, moves and facts; never
the players' names, events or dates (the prompts are built without them,
and names known to chesscoach are blanked out before sending as a second
guard). On Google's free tier, Google may use what is sent to improve its
products, and people may read it: the app says so when it is chosen.

Free quotas are small (Gemini's free Flash models: tens of requests a day),
so with `online_one_request` a review asks for all of its comments in one
request, and at most one more for the rewrites the checks ask for.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Optional

from . import __version__, config

PREFIX = "online:"
SERVICES = {
    "gemini": ("Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai"),
    "openai": ("OpenAI", "https://api.openai.com/v1"),
    "groq": ("Groq", "https://api.groq.com/openai/v1"),
    "openrouter": ("OpenRouter", "https://openrouter.ai/api/v1"),
    "custom": ("Other (OpenAI-compatible)", ""),
}
KEY_PAGES = {
    "gemini": "aistudio.google.com/apikey",
    "openai": "platform.openai.com/api-keys",
    "groq": "console.groq.com/keys",
    "openrouter": "openrouter.ai/keys",
}
TIMEOUT = 180                      # seconds: a whole review's comments in one answer takes a while
# Output room. Thinking models (Gemini's Flash) count their thinking in it, so
# an online call gets more than Ollama's num_predict; capped below what the
# usual services allow for one answer.
MIN_TOKENS = 4000
MAX_TOKENS = 16000
PRIVACY = {
    "gemini": ("On Google's free tier, Google may use what chesscoach sends to improve its "
               "products, and people may read it. chesscoach sends positions, moves and the "
               "engine's facts, never the players' names. Google's API is for people aged 18 "
               "and over; in the EU, the UK and Switzerland only a paid key may be used."),
    "": ("chesscoach sends positions, moves and the engine's facts to the service you "
         "chose, never the players' names. What the service does with them is set by "
         "its terms."),
}


class LLMError(Exception):
    """An online model didn't answer; `kind`: quota, key, model, offline, bad."""

    def __init__(self, message: str, kind: str = "bad"):
        super().__init__(message)
        self.kind = kind


# --- names --------------------------------------------------------------------

def is_online(model: str) -> bool:
    return str(model or "").startswith(PREFIX)


def model_id(model: str) -> str:
    return model[len(PREFIX):] if is_online(model) else model


def active_model() -> str:
    """The model the settings choose: an Ollama name, or "online:<model>"."""
    s = config.settings
    if s.coach_llm == "online":
        return PREFIX + (s.online_model or "").strip()
    return s.model


def service() -> str:
    return config.settings.online_service if config.settings.online_service in SERVICES else "custom"


def label(model: str) -> str:
    """For the page and the reports: "Google Gemini: gemini-x" or the Ollama name."""
    if not is_online(model):
        return model
    return f"{SERVICES[service()][0]}: {model_id(model)}"


def base_url(svc: Optional[str] = None, url: Optional[str] = None) -> str:
    svc = svc if svc is not None else service()
    url = (url if url is not None else config.settings.online_url or "").strip()
    return (url or SERVICES.get(svc, ("", ""))[1]).rstrip("/")


def key() -> str:
    return (config.settings.online_key or "").strip()


def one_request(model: str) -> bool:
    """A review's comments in one request (for small free quotas)."""
    return is_online(model) and bool(config.settings.online_one_request)


def privacy_note(svc: Optional[str] = None) -> str:
    return PRIVACY.get(svc if svc is not None else service(), PRIVACY[""])


def check(model: str) -> None:
    """Raise config.ConfigError, in plain words, when an online model can't be
    asked: before a review spends minutes on Stockfish."""
    if not is_online(model):
        return
    name = SERVICES[service()][0]
    if not model_id(model):
        raise config.ConfigError(f"Choose which {name} model writes the comments (Settings, "
                                 "Coach: online).")
    if not base_url():
        raise config.ConfigError("Give the address of the online service (Settings).")
    if not key():
        where = KEY_PAGES.get(service())
        raise config.ConfigError(f"The {name} key is missing: paste it in Settings"
                                 + (f" (get one at {where})." if where else "."))


# --- the names guard ----------------------------------------------------------

_private: set = set()


def keep_private(*names: str) -> None:
    """Names never to send (the players of the game in hand)."""
    for n in names:
        n = (n or "").strip()
        if len(n) >= 3 and n.strip("?.- ") and n.lower() not in ("player", "white", "black"):
            _private.add(n)


def _scrub(text: str) -> str:
    names = set(_private)
    s = config.settings
    for n in (s.player_name, s.lichess_user, s.chesscom_user):
        n = (n or "").strip()
        if len(n) >= 3 and n.lower() not in ("player", "white", "black"):
            names.add(n)
    for n in sorted(names, key=len, reverse=True):
        text = re.sub(rf"(?<!\w){re.escape(n)}(?!\w)", "the player", text, flags=re.I)
    return text


# --- the call -----------------------------------------------------------------

def _openai_messages(messages: list) -> list:
    """Ollama-style messages (what the coach builds) in OpenAI's shape: tool
    calls get ids, and each tool result names the call it answers."""
    out, pending = [], []
    for m in messages:
        role = m.get("role")
        content = _scrub(m.get("content") or "")
        if role == "assistant" and m.get("tool_calls"):
            calls = []
            for k, c in enumerate(m["tool_calls"]):
                fn = c.get("function", {})
                cid = c.get("id") or f"call_{len(out)}_{k}"
                args = fn.get("arguments", {})
                calls.append({"id": cid, "type": "function",
                              "function": {"name": fn.get("name", ""),
                                           "arguments": args if isinstance(args, str)
                                           else json.dumps(args)}})
                pending.append((fn.get("name", ""), cid))
            out.append({"role": "assistant", "content": content, "tool_calls": calls})
        elif role == "tool":
            name = m.get("tool_name") or m.get("name") or ""
            k = next((i for i, (n, _) in enumerate(pending) if n == name), 0 if pending else None)
            cid = pending.pop(k)[1] if k is not None else "call_0"
            out.append({"role": "tool", "tool_call_id": cid, "content": content})
        else:
            out.append({"role": role, "content": content})
    return out


def _error(code: int, body: str, svc: str) -> LLMError:
    try:
        data = json.loads(body)
        data = data[0] if isinstance(data, list) and data else data
        detail = (data.get("error") or {}).get("message", "") if isinstance(data, dict) else ""
    except ValueError:
        detail = ""
    name = SERVICES.get(svc, SERVICES["custom"])[0]
    if code == 429:
        reset = " (Gemini's free quota starts again at midnight, US Pacific time)" \
            if svc == "gemini" else ""
        return LLMError(f"{name} says the quota is used up for now{reset}. Try later, or "
                        "switch the coach to Ollama in Settings.", "quota")
    if code in (401, 403):
        return LLMError(f"{name} refused the key in Settings: check it, or create a new one."
                        + (f" ({detail})" if detail else ""), "key")
    if code == 404:
        return LLMError(f"{name} doesn't know that model: pick one with 'List models' in "
                        "Settings." + (f" ({detail})" if detail else ""), "model")
    return LLMError(f"{name} answered with an error ({code})"
                    + (f": {detail}" if detail else "."), "bad")


def _request(url: str, payload: Optional[dict], api_key: str, svc: Optional[str] = None) -> dict:
    svc = svc or service()
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json",
               "User-Agent": f"chesscoach/{__version__}"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise _error(e.code, e.read().decode("utf-8", errors="replace"), svc) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise LLMError(f"{SERVICES.get(svc, SERVICES['custom'])[0]} can't be reached: no "
                       "internet connection?", "offline") from None
    except ValueError:
        raise LLMError("The online model sent an answer chesscoach could not read.") from None


def chat(model: str, messages: list, tools: Optional[list] = None,
         max_tokens: Optional[int] = None) -> dict:
    """One chat call to the online model, answered in Ollama's shape
    ({"message": {"content", "tool_calls"}, "prompt_eval_count", "eval_count"})
    so the coach's code reads both alike. Raises LLMError."""
    check(model)
    s = config.settings
    room = min(MAX_TOKENS, max(MIN_TOKENS, int(max_tokens or s.num_predict)))
    payload = {"model": model_id(model), "messages": _openai_messages(messages),
               "temperature": s.temperature, "top_p": 0.9, "max_tokens": room}
    if tools:
        payload["tools"] = tools
    body = _request(base_url() + "/chat/completions", payload, key())
    try:
        choice = body["choices"][0]
        msg = choice["message"]
    except (KeyError, IndexError, TypeError):
        raise LLMError("The online model sent no answer.") from None
    calls = []
    for c in msg.get("tool_calls") or []:
        fn = c.get("function", {})
        args = fn.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        calls.append({"id": c.get("id", ""), "function": {"name": fn.get("name", ""),
                                                          "arguments": args}})
    usage = body.get("usage") or {}
    text = msg.get("content") or ""
    if isinstance(text, list):                      # some services send parts
        text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
    if not (text or "").strip() and not calls:
        why = choice.get("finish_reason") or ""
        raise LLMError("The online model ran out of room before writing (its thinking used it "
                       "up); try a Flash-Lite model." if why == "length" else
                       f"The online model returned no text{f' ({why})' if why else ''}.")
    return {"message": {"role": "assistant", "content": text, "tool_calls": calls},
            "prompt_eval_count": usage.get("prompt_tokens"),
            "eval_count": usage.get("completion_tokens")}


def list_models(svc: str, url: str, api_key: str) -> list:
    """The models the service offers this key (Settings' 'List models')."""
    if not (api_key or "").strip():
        raise LLMError("Paste the key first.", "key")
    base = base_url(svc, url)
    if not base:
        raise LLMError("Give the address of the service first.", "bad")
    body = _request(base + "/models", None, api_key.strip(), svc)
    names = []
    for m in body.get("data") or body.get("models") or []:
        name = m.get("id") or m.get("name") if isinstance(m, dict) else None
        if name:
            names.append(name[len("models/"):] if name.startswith("models/") else name)
    return sorted(set(names))
