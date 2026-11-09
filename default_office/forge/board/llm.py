"""The board's model: what writes words for any feature of the board that wants them (the Agent Room's tarot reader).
Bring your own: a Claude API key, or a model of your own. Stdlib only, like the rest of the board.

Configured in the project .env (the board's server.py loads it before importing this; run by hand, this reads it too):

    LLM_KEY=sk-ant-...                   a Claude API key: the board writes with Claude (LLM_MODEL blank: claude-opus-5-5)

or a model of your own, any OpenAI-compatible chat API (vLLM behind a gateway, Ollama, OpenAI's own):

    LLM_URL=http://<host>:<port>/v1      the API's base, /v1 and all (chat requests miss without it)
    LLM_MODEL=vllm/Qwen/Qwen3.8-27B-FP8  the model id, as GET <LLM_URL>/models lists it
    LLM_KEY=                             a bearer key, if it wants one (blank: none sent)

LLM_URL set: that API, whatever LLM_KEY is. Blank: LLM_KEY is a Claude API key. Neither: every call is an LLMError
saying NEED, and configured() is False (the tarot reader says NEED too).

Use it from Python:

    import llm
    llm.chat([{"role": "user", "content": "Say hello in five words."}])           # -> the whole answer
    for piece in llm.stream(messages, max_tokens=400, temperature=0.9): ...       # -> the answer as it comes

`max_tokens` is the answer's. No thinking by default. With your own model: `reasoning_effort: "none"` (the gateway drops
vLLM's chat_template_kwargs, and Qwen3.8 ignores /no_think, so that is the switch that works); think=True lets it
reason first. With Claude, which always thinks a little: effort low (high with think=True), and room for the thinking
on top of max_tokens (THINK_ROOM: it counts toward the limit); `temperature` is not sent (Claude takes only its default
sampling); a request its safety checks decline is answered by the model Anthropic names for it (`fallbacks`), and one
none will answer is an LLMError. Either way the reasoning is never returned, only the answer. Every failure is an
LLMError, whose message says why in words a page can show.

From a terminal: `python3 forge/board/llm.py "a prompt"` streams the answer; `--models` lists the models; `--think`.
"""
import json
import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

TIMEOUT = 60   # seconds a request may sit with nothing coming back (connecting, or between pieces of a stream)
NEED = "I need an LLM key, like a Claude API key, to function: put yours in LLM_KEY in the project .env, then bin/board-restart"

# Claude (the Messages API, plain HTTP).
CLAUDE_URL = "https://api.anthropic.com/v1"
CLAUDE_MODEL = "claude-opus-5-5"
CLAUDE_VERSION = "2026-10-21"   # the anthropic-version header
# The models whose declined requests go to Anthropic's fallback for that kind of decline (`fallbacks: "default"`, its beta).
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
THINK_ROOM = {False: 2048, True: 16000}   # tokens for Claude's thinking, on top of max_tokens (think False, True)
RETRY = {408, 409, 429, 500, 502, 503, 504, 529}   # Claude's answers worth asking again (busy, overloaded, its errors)
RETRIES = 2   # times; before anything has come back, and never after a timeout


class LLMError(Exception):
    pass


def _load_env():
    """Run by hand (not under server.py, which loads it first), the project .env, under the process environment."""
    path = os.environ.get("REPO_ENV") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".env")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                k, sep, v = line.strip().partition("=")
                v = v.strip()
                v = v[1:-1] if len(v) > 1 and v[0] == v[-1] and v[0] in "'\"" else v
                if sep and v and k.strip() and not k.lstrip().startswith("#"):
                    os.environ.setdefault(k.strip(), v)
    except OSError:
        pass


def config():
    """{kind, url, model, key} as the environment has them now (read at each call: an edit to .env counts at the next
    start). kind: "openai" (LLM_URL, a model of your own), "claude" (LLM_KEY alone, or LLM_URL at Anthropic's API), or
    None (neither: NEED)."""
    url = (os.environ.get("LLM_URL") or "").strip().rstrip("/")
    model, key = (os.environ.get("LLM_MODEL") or "").strip(), (os.environ.get("LLM_KEY") or "").strip()
    if url and urlparse(url).hostname != urlparse(CLAUDE_URL).hostname:
        return {"kind": "openai", "url": url, "model": model, "key": key}
    if key:
        return {"kind": "claude", "url": CLAUDE_URL, "model": model or CLAUDE_MODEL, "key": key}
    return {"kind": None, "url": "", "model": model, "key": ""}


def configured():
    c = config()
    return c["kind"] == "claude" or bool(c["kind"] == "openai" and c["model"])


def _who(c):
    return "Claude" if c["kind"] == "claude" else "the local model"


def _detail(raw):
    """An error's body, in words: the API's own message when it is JSON that has one."""
    text = raw.decode("utf-8", "replace").strip()
    try:
        err = json.loads(text).get("error")
        text = (err.get("message") if isinstance(err, dict) else err) or text
    except (ValueError, AttributeError):
        pass
    return str(text)[:300]


def _wait(e, attempt):
    """Seconds before asking Claude again: what it said (retry-after, at most 8), else 0.5, then 1."""
    try:
        return min(8.0, float(e.headers.get("retry-after")))
    except (AttributeError, TypeError, ValueError):
        return 0.5 * 2 ** attempt


def _request(path, body=None, timeout=TIMEOUT):
    c = config()
    if not c["kind"]:
        raise LLMError(NEED)
    claude = c["kind"] == "claude"
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream" if body and body.get("stream") else "application/json"}
    if claude:
        headers.update({"x-api-key": c["key"], "anthropic-version": CLAUDE_VERSION})
        if body and "fallbacks" in body:
            headers["anthropic-beta"] = FALLBACK_BETA
    elif c["key"]:
        headers["Authorization"] = "Bearer " + c["key"]
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(RETRIES + 1 if claude else 1):
        req = Request(c["url"] + path, data=data, headers=headers, method="POST" if data is not None else "GET")
        try:
            return urlopen(req, timeout=timeout)
        except HTTPError as e:
            detail = _detail(e.read()[:2000])
            if claude and e.code in RETRY and attempt < RETRIES:
                time.sleep(_wait(e, attempt))
                continue
            if claude and e.code in (401, 403):
                raise LLMError("Claude refused the key in LLM_KEY (HTTP %d%s)" % (e.code, ": " + detail if detail else "")) from None
            raise LLMError("%s answered HTTP %d%s" % (_who(c), e.code, ": " + detail if detail else "")) from None
        except (URLError, OSError) as e:
            why = getattr(e, "reason", e)
            if claude and attempt < RETRIES and not isinstance(why, TimeoutError) and not isinstance(e, TimeoutError):
                time.sleep(_wait(None, attempt))
                continue
            raise LLMError("%s at %s did not answer (%s)" % (_who(c), c["url"], why)) from None


def _body(messages, model, max_tokens, temperature, think, stream, extra):
    c = config()
    m = model or c["model"]
    if not c["kind"]:
        raise LLMError(NEED)
    if not m:
        raise LLMError("no model: set LLM_MODEL in the project .env (python3 forge/board/llm.py --models lists them)")
    if c["kind"] == "claude":
        return _claude_body(messages, m, max_tokens, think, stream, extra)
    b = {"model": m, "messages": messages, "stream": stream, "max_tokens": max_tokens, "temperature": temperature}
    if not think:
        b["reasoning_effort"] = "none"
    b.update(extra or {})
    return b


def _claude_body(messages, model, max_tokens, think, stream, extra):
    """The Messages API's request for the same conversation: its leading system messages as `system` (a later one
    stays where it is), effort and room for the thinking, fallbacks where the model takes them, and no temperature."""
    lead = 0
    while lead < len(messages) and messages[lead].get("role") == "system":
        lead += 1
    b = {"model": model, "max_tokens": max_tokens + THINK_ROOM[bool(think)], "messages": list(messages[lead:]),
         "output_config": {"effort": "high" if think else "low"}, "stream": stream}
    system = "\n\n".join(str(x.get("content") or "") for x in messages[:lead]).strip()
    if system:
        b["system"] = system
    if model in FALLBACK_MODELS:
        b["fallbacks"] = "default"
    b.update(extra or {})
    return b


def _declined(stop_details):
    category = (stop_details or {}).get("category") if isinstance(stop_details, dict) else None
    return LLMError("Claude declined to answer this%s" % (" (%s)" % category if category else ""))


def _events(r):
    """A server-sent event stream's events, each its `data:` JSON (None for `data: [DONE]`, the OpenAI API's last)."""
    for raw in r:   # `event: ...` and `data: {json}` lines, blank lines between
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            yield None
            return
        try:
            yield json.loads(data)
        except ValueError:
            continue


def stream(messages, model=None, max_tokens=800, temperature=0.7, think=False, timeout=TIMEOUT, **extra):
    """The answer, piece by piece as the model writes it (a generator of strings; reasoning is skipped)."""
    claude = config()["kind"] == "claude"
    r = _request("/messages" if claude else "/chat/completions", _body(messages, model, max_tokens, temperature, think, True, extra), timeout)
    with r:
        for ev in _events(r):
            if ev is None:
                return
            if claude:   # message_start, content_block_start/_delta/_stop (text, thinking, fallback), message_delta, message_stop
                kind, delta = ev.get("type"), ev.get("delta") or {}
                if kind == "content_block_delta" and delta.get("type") == "text_delta" and delta.get("text"):
                    yield delta["text"]
                elif kind == "message_delta" and delta.get("stop_reason") == "refusal":
                    raise _declined(delta.get("stop_details"))
                elif kind == "error":
                    err = ev.get("error") or {}
                    raise LLMError("Claude: %s" % (err.get("message") if isinstance(err, dict) else err))
                elif kind == "message_stop":
                    return
                continue
            if ev.get("error"):
                err = ev["error"]
                raise LLMError("the local model: %s" % (err.get("message") if isinstance(err, dict) else err))
            for ch in ev.get("choices") or []:
                piece = (ch.get("delta") or {}).get("content")
                if piece:
                    yield piece


def chat(messages, model=None, max_tokens=800, temperature=0.7, think=False, timeout=TIMEOUT, **extra):
    """The whole answer, once it is written."""
    claude = config()["kind"] == "claude"
    r = _request("/messages" if claude else "/chat/completions", _body(messages, model, max_tokens, temperature, think, False, extra), timeout)
    with r:
        try:
            j = json.load(r)
        except ValueError:
            raise LLMError("%s's answer was not JSON" % ("Claude" if claude else "the local model")) from None
    if claude:   # its content: thinking blocks (their words never given), text blocks, maybe a fallback's marker
        if j.get("stop_reason") == "refusal":
            raise _declined(j.get("stop_details"))
        if not isinstance(j.get("content"), list):
            raise LLMError("Claude's answer had no content: %s" % json.dumps(j)[:200])
        return "".join(b.get("text") or "" for b in j["content"] if isinstance(b, dict) and b.get("type") == "text")
    try:
        return j["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise LLMError("the local model's answer had no message: %s" % json.dumps(j)[:200]) from None


def models():
    """The model ids the API serves."""
    claude = config()["kind"] == "claude"
    with _request("/models?limit=100" if claude else "/models", timeout=15) as r:
        try:
            return [m["id"] for m in json.load(r).get("data") or [] if isinstance(m, dict) and "id" in m]
        except (ValueError, AttributeError):
            raise LLMError("%s's list was not JSON" % ("Claude" if claude else "the local model")) from None


if __name__ == "__main__":
    _load_env()
    args = sys.argv[1:]
    try:
        if "--models" in args:
            print("\n".join(models()))
        else:
            think = "--think" in args
            prompt = " ".join(a for a in args if a != "--think") or sys.stdin.read()
            for piece in stream([{"role": "user", "content": prompt}], think=think):
                sys.stdout.write(piece)
                sys.stdout.flush()
            print()
    except LLMError as e:
        sys.exit("llm: %s" % e)
    except KeyboardInterrupt:
        sys.exit(130)
