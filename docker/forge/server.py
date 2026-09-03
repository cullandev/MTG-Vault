"""HTTP shim around Forge's headless match simulator.

Deliberately dumb: write the .dck files, run the jar, return raw stdout. All
parsing and judgement lives app-side (clients/forge.py), where it is unit-tested;
this file has no dependencies beyond the standard library so the image stays a
JRE plus one script.

  GET  /health            -> {"status": "ok", "jar": "...", "version": "..."}
  POST /simulate          -> {"exit_code", "stdout", "duration_ms"}
      body: {"decks": [{"name": str, "dck": str}, ...],
             "games": int, "format": str, "verbose": bool}
  POST /practice/deck     -> writes one .dck into Forge's profile deck folder
      body: {"name": str, "dck": str, "format": str}
  GET  /practice/decks    -> {"decks": [relative path, ...]} -- what we filed
  POST /practice/decks/reset -> {"removed": int} -- delete only our own decks
  POST /bridge/start      -> {"running": true} -- run one AI-vs-AI game through
      the practice bridge (headless; no display), narrating it as JSON
      body: {"decks": [name, name], "format": "Constructed"|"Commander",
             "human": bool}  -- human seats a person in seat one
  POST /bridge/answer     -> {"delivered": true}; body {"id", "value"}
  GET  /bridge/events?since=N -> {"running", "next", "events": [json, ...]}
  POST /bridge/stop       -> {"running": false}

Deck files are written under the name Forge itself would choose; see
:func:`forge_safe_name` for the relocation bug that cost seven of every twelve
pushed decks.

Nothing here opens a display. Forge's desktop launcher wanted one; the engine
never did, and both the simulator and the bridge now call it directly behind a
headless IGuiBase.
"""

from __future__ import annotations

import collections
import glob
import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_STDOUT_BYTES = 400_000
MAX_BODY_BYTES = 4_000_000


def find_jar() -> str | None:
    """The desktop jar with dependencies, wherever the tarball put it."""
    root = os.environ.get("FORGE_DIR", "/opt/forge")
    patterns = [
        f"{root}/**/forge-gui-desktop-*jar-with-dependencies.jar",
        f"{root}/**/forge-gui-desktop-*.jar",
        f"{root}/**/forge*.jar",
    ]
    for pattern in patterns:
        matches = sorted(glob.glob(pattern, recursive=True))
        if matches:
            return matches[0]
    return None


def _decks_root() -> str:
    home = os.environ.get("HOME", "/data")
    path = os.path.join(home, ".forge", "decks")
    os.makedirs(path, exist_ok=True)
    return path


def _deck_dir(game_format: str) -> str:
    """Forge resolves -d names against its own profile deck directories."""
    sub = "commander" if game_format.lower() == "commander" else "constructed"
    path = os.path.join(_decks_root(), sub)
    os.makedirs(path, exist_ok=True)
    return path


#: Characters Forge (and Windows, where the volume is bind-mounted) refuse in a
#: deck file name. Forge maps them to "_" when it re-saves a deck.
_ILLEGAL_IN_FILENAME = '/\\<>:"|?*'


def forge_safe_name(name: str) -> str:
    """The name Forge itself would give this deck's file.

    Forge stores a deck at ``<name>.dck`` and, at startup, RELOCATES any deck
    whose file name disagrees with the ``Name=`` in its metadata -- it rewrites
    the file under the correct name in the decks ROOT, not the constructed/ or
    commander/ subfolder the New Game picker reads. The deck then vanishes from
    the picker while every API call still reports success.

    That is how seven of twelve pushed decks disappeared on every table open.
    The cure is to make the two agree before Forge ever sees them: this is the
    single name used for both the file and the ``Name=`` line.
    """
    cleaned = "".join("_" if ch in _ILLEGAL_IN_FILENAME else ch for ch in name)
    # Control characters and stray whitespace would survive the mapping above
    # and reappear as a mismatch after Forge normalises them.
    cleaned = " ".join(cleaned.split())
    cleaned = cleaned.strip(". ")
    return cleaned[:80].strip() or "deck"


def _retitle(dck: str, name: str) -> str:
    """Force the deck text's ``Name=`` to the name we are filing it under."""
    lines = dck.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("Name="):
            lines[index] = f"Name={name}"
            break
    else:
        lines = ["[metadata]", f"Name={name}"] + lines
    return "\n".join(lines) + "\n"


#: Decks this shim has written, so a reset removes ours and never a deck the
#: owner built in Forge's own editor.
_MANIFEST = ".mtgvault-managed.json"


def _manifest_path() -> str:
    return os.path.join(_decks_root(), _MANIFEST)


def _read_manifest() -> list[str]:
    try:
        with open(_manifest_path(), encoding="utf-8") as handle:
            data = json.load(handle)
        return [str(entry) for entry in data] if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _write_manifest(entries: list[str]) -> None:
    try:
        with open(_manifest_path(), "w", encoding="utf-8") as handle:
            json.dump(sorted(set(entries)), handle)
    except OSError:
        pass


def practice_decks_reset() -> dict:
    """Remove every deck this shim wrote, so the picker shows only current ones.

    Without this the picker accumulates a copy of every deck from every push:
    the meta job renumbers its decks each week, so "[Meta] Kinnan [#38]" and
    "[Meta] Kinnan [#80]" sit side by side, indistinguishable and mostly dead.
    Only manifest entries are removed -- decks built in Forge's editor stay.
    """
    removed = 0
    root = _decks_root()
    for relative in _read_manifest():
        # Defend the profile against a manifest that has been tampered with:
        # only ever unlink inside the decks tree.
        candidate = os.path.normpath(os.path.join(root, relative))
        if not candidate.startswith(root + os.sep) or not candidate.endswith(".dck"):
            continue
        # Forge may have relocated it to the decks root; take both.
        for path in (candidate, os.path.join(root, os.path.basename(candidate))):
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass
    _write_manifest([])
    return {"removed": removed}


_sim_lock = threading.Lock()
_sim_running = False


def _as_int(value: str, fallback: int = 0) -> int:
    try:
        return int(value)
    except ValueError:
        return fallback


#: Deck sources that are Forge's own, not ours. A remembered selection from one
#: of these is a deck the owner never chose.
_FOREIGN_DECK_TYPES = (
    "PRECONSTRUCTED_DECK",
    "PRECON_COMMANDER_DECK",
    "COLOR_DECK",
    "STANDARD_COLOR_DECK",
    "MODERN_COLOR_DECK",
    "PAUPER_COLOR_DECK",
    "THEME_DECK",
    "RANDOM_DECK",
    "RANDOM_COMMANDER_DECK",
    "QUEST_OPPONENT_DECK",
)


# -- the practice bridge (phase 2: watch a game) ------------------------------
#
# The bridge is Forge's engine driven through our own IGuiGame, narrating the
# board as one JSON object per line. It runs headless -- no Xvfb, no VNC -- so
# it is a plain subprocess whose stdout we buffer and hand out by sequence.

#: The answer wire format: '<askId><TAB><value><NEWLINE>' written to the
#: bridge's stdin. Named rather than escaped: these characters pass through
#: a shell, a heredoc and a JSON body before they reach the pipe, and an
#: escape survives none of that reliably.
TAB = chr(9)
CR = chr(13)
NL = chr(10)

BRIDGE_JAR = "/opt/forge-sim/bridge.jar"
#: Events kept in memory. A whole game was 541; ten games of headroom is
#: cheaper than any eviction policy worth arguing about.
BRIDGE_BUFFER = 6000

_bridge_lock = threading.Lock()
_bridge_proc: subprocess.Popen | None = None
_bridge_events: list[str] = []
#: How many events have been dropped off the FRONT of the buffer. The
#: client's ``since`` is a count of events it has seen since the game began,
#: and stays meaningful only if the index it maps to keeps moving with the
#: trim. Without this, the first trim pinned every client at BRIDGE_BUFFER
#: -- the slice past it was always empty, ``next`` never grew, and the page
#: froze while the game went on. Arithmetic, not a race.
_bridge_base = 0
_bridge_error: str | None = None
#: The bridge's last words. A JVM that dies at class-load prints nothing to
#: stdout, so with stderr discarded a crash looked exactly like a game that
#: had started and gone quiet -- and cost a round of work to read by hand.
_bridge_stderr: collections.deque[str] = collections.deque(maxlen=40)


def _bridge_reader(proc: subprocess.Popen) -> None:
    """Drain the bridge's stdout into the buffer, one JSON object per line."""
    global _bridge_error, _bridge_base
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.strip()
        if not line.startswith("{"):
            continue
        with _bridge_lock:
            _bridge_events.append(line)
            excess = len(_bridge_events) - BRIDGE_BUFFER
            if excess > 0:
                del _bridge_events[:excess]
                _bridge_base += excess
    code = proc.wait()
    if code != 0:
        with _bridge_lock:
            tail = "\n".join(_bridge_stderr).strip()
            _bridge_error = f"the bridge exited with code {code}" + (
                f"\n{tail}" if tail else ""
            )


def _bridge_stderr_reader(stream) -> None:
    """Keep the bridge's last lines of stderr, for the moment it dies."""
    for line in stream:
        line = line.rstrip()
        if line:
            with _bridge_lock:
                _bridge_stderr.append(line)


#: Forge ships these under res/ai/*.ai; anything else falls back to Default.
AI_PROFILES = ("Default", "Cautious", "Reckless", "Experimental")


def _player_name(value: object) -> str:
    """A name Forge can wear: printable, one line, at most 24 characters."""
    text = "".join(
        ch for ch in str(value or "") if ch.isprintable() and ch not in "\t\r\n"
    )
    return text.strip()[:24]


def bridge_start(payload: dict) -> dict:
    """Run one AI-vs-AI game through the bridge and stream it.

    Deliberately not mutually exclusive with the practice table: this is a
    separate short-lived JVM at a smaller heap, and the point of watching is
    that you can do it while thinking about something else. It IS exclusive
    with itself -- two at once would interleave into one buffer.
    """
    global _bridge_proc, _bridge_error, _bridge_base
    decks = payload.get("decks") or []
    if len(decks) != 2:
        return {"error": "exactly two decks"}
    if not os.path.exists(BRIDGE_JAR):
        return {"error": "this image has no bridge.jar; rebuild the forge image"}
    jar = find_jar()
    if jar is None:
        return {"error": "no Forge jar found in the image"}

    with _bridge_lock:
        if _bridge_proc is not None and _bridge_proc.poll() is None:
            return {"error": "a game is already being watched"}
        _bridge_events.clear()
        _bridge_base = 0
        _bridge_stderr.clear()
        _bridge_error = None

    command = [
        "java",
        f"-Xmx{os.environ.get('BRIDGE_HEAP', '2g')}",
        "-Djava.awt.headless=true",
        "-Dfile.encoding=UTF-8",
        f"-Duser.home={os.environ.get('HOME', '/data')}",
        "-cp",
        f"{jar}:{BRIDGE_JAR}",
        "bridge.BridgeMain",
        str(decks[0]),
        str(decks[1]),
        "-",
        str(payload.get("format") or "Constructed"),
        "human" if payload.get("human") else "ai",
        # Milliseconds the engine pauses after each AI play; 0 is full speed.
        str(_as_int(str(payload.get("pace", 0)), 0)),
        # The person's name at the table; one line, printable, short.
        _player_name(payload.get("name")),
        # The AI's personality (one of Forge's profile files) and whether it
        # simulates its plays before choosing.
        str(payload.get("ai_profile")) if payload.get("ai_profile") in AI_PROFILES else "Default",
        "sim" if payload.get("ai_simulation") else "nosim",
    ]
    proc = subprocess.Popen(
        command,
        cwd=os.path.dirname(jar),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        bufsize=1,
        env=dict(os.environ, DISPLAY=""),
    )
    with _bridge_lock:
        _bridge_proc = proc
    threading.Thread(target=_bridge_reader, args=(proc,), daemon=True).start()
    threading.Thread(
        target=_bridge_stderr_reader, args=(proc.stderr,), daemon=True
    ).start()
    return {"running": True}


def bridge_events(since: int) -> dict:
    """Events after ``since``, plus whether the game is still going."""
    with _bridge_lock:
        proc = _bridge_proc
        # ``since`` and ``next`` count events since the game began; the list
        # holds only the most recent BRIDGE_BUFFER of them, starting at base.
        total = _bridge_base + len(_bridge_events)
        start = max(0, min(since - _bridge_base, len(_bridge_events)))
        events = _bridge_events[start:]
        error = _bridge_error
    running = proc is not None and proc.poll() is None
    payload: dict = {"running": running, "next": total, "events": events}
    if error:
        payload["error"] = error
    return payload


def bridge_answer(payload: dict) -> dict:
    """Answer a blocking prompt.

    The bridge holds Forge's game thread inside an IGuiGame call until this
    arrives -- that is what a synchronous prompt across a network means. One
    line on its stdin is the whole channel in.
    """
    ask_id = str(payload.get("id") or "").strip()
    value = str(payload.get("value") if payload.get("value") is not None else "")
    if not ask_id or any(ch in ask_id for ch in (TAB, CR, NL)):
        return {"error": "bad prompt id"}
    with _bridge_lock:
        proc = _bridge_proc
    if proc is None or proc.poll() is not None or proc.stdin is None:
        return {"error": "no game is being played"}
    try:
        first_line = value.splitlines()[0] if value else ""
        proc.stdin.write(ask_id + TAB + first_line + NL)
        proc.stdin.flush()
    except OSError as error:
        return {"error": f"could not deliver the answer: {error}"}
    return {"delivered": True}


def bridge_action(payload: dict) -> dict:
    """Press a button, pass priority, or concede.

    Rides the same stdin channel as an answer, under the reserved id
    "action" -- mulligans and priority are not prompts, but they arrive at the
    engine the same way.
    """
    return bridge_answer({"id": "action", "value": payload.get("value")})


def bridge_stop() -> dict:
    """Kill a watched game."""
    global _bridge_proc
    with _bridge_lock:
        proc = _bridge_proc
        _bridge_proc = None
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    return {"running": False}


def practice_deck(payload: dict) -> dict:
    """Drop one .dck into Forge's profile folder for the New Game picker.

    The file name and the deck's ``Name=`` are the same string by construction
    -- see :func:`forge_safe_name` for why any disagreement makes the deck
    disappear from the picker at the next table open.
    """
    game_format = str(payload.get("format") or "Commander")
    safe = forge_safe_name(str(payload.get("name") or "deck"))
    directory = _deck_dir(game_format)
    path = os.path.join(directory, f"{safe}.dck")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(_retitle(str(payload.get("dck") or ""), safe))
    relative = os.path.relpath(path, _decks_root())
    _write_manifest([*_read_manifest(), relative])
    return {"written": os.path.basename(path), "name": safe}


def simulate(payload: dict) -> dict:
    decks = payload.get("decks") or []
    games = int(payload.get("games") or 3)
    game_format = str(payload.get("format") or "Commander")
    verbose = bool(payload.get("verbose"))
    # Refusals (-2) are contract answers, not failures: they reply 200 so the
    # app's circuit breaker never counts a deliberate "not now" as an outage.
    if not 2 <= len(decks) <= 4:
        return {"error": "between 2 and 4 decks", "exit_code": -1}

    jar = find_jar()
    if jar is None:
        return {"error": "no Forge jar found in the image", "exit_code": -3}

    global _sim_running
    with _sim_lock:
        if _sim_running:
            return {"error": "another simulation is already running", "exit_code": -2}
        _sim_running = True
    deck_dir = _deck_dir(game_format)
    stamp = str(int(time.time() * 1000))
    written = []
    try:
        names = []
        for index, deck in enumerate(decks):
            filename = f"sim-{stamp}-{index}.dck"
            path = os.path.join(deck_dir, filename)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(str(deck.get("dck") or ""))
            written.append(path)
            names.append(filename)

        # No xvfb-run. Forge's DESKTOP launcher builds a Swing UI before it
        # parses arguments, which is the only reason a simulation ever needed a
        # display; forge.game and forge.ai reference java.awt exactly zero
        # times. bridge.SimEntry skips that launcher and calls the simulator
        # directly behind a headless IGuiBase.
        command = [
            "java",
            "-Djava.awt.headless=true",
            f"-Xmx{os.environ.get('FORGE_HEAP', '4g')}",
            "-Dfile.encoding=UTF-8",
            # Java takes user.home from passwd (/root), not $HOME; pin it so
            # Forge's profile -- and the decks we just wrote -- share /data.
            f"-Duser.home={os.environ.get('HOME', '/data')}",
            "-cp",
            f"{jar}:{BRIDGE_JAR}",
            "bridge.SimEntry",
            "sim",
            "-f",
            game_format,
            "-d",
            *names,
            "-n",
            str(games),
        ]
        if not verbose:
            # Quiet keeps 27-game gauntlet runs cheap; verbose emits the full
            # per-turn game log the battle playback renders.
            command.append("-q")
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                check=False,
                cwd=os.path.dirname(jar),
                capture_output=True,
                text=True,
                errors="replace",
                timeout=float(os.environ.get("FORGE_TIMEOUT_S", "900")),
            )
            exit_code = completed.returncode
            stdout = completed.stdout + "\n--- stderr ---\n" + completed.stderr
        except subprocess.TimeoutExpired as error:
            exit_code = -9
            stdout = str(error.stdout or "") + "\n--- timeout ---"
        duration_ms = round((time.monotonic() - started) * 1000, 1)
    finally:
        with _sim_lock:
            _sim_running = False
        for path in written:
            try:
                os.remove(path)
            except OSError:
                pass

    return {
        "exit_code": exit_code,
        "stdout": stdout[-MAX_STDOUT_BYTES:],
        "duration_ms": duration_ms,
    }


class Handler(BaseHTTPRequestHandler):
    def _reply(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/practice/decks":
            self._reply(200, {"decks": sorted(_read_manifest())})
            return
        if self.path.startswith("/bridge/events"):
            query = self.path.partition("?")[2]
            since = 0
            for part in query.split("&"):
                key, _, value = part.partition("=")
                if key == "since":
                    since = _as_int(value)
            self._reply(200, bridge_events(since))
            return
        if self.path != "/health":
            self._reply(404, {"error": "not found"})
            return
        jar = find_jar()
        self._reply(
            200 if jar else 500,
            {
                "status": "ok" if jar else "no jar",
                "jar": jar,
                "version": os.environ.get("FORGE_VERSION", "unknown"),
            },
        )

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            self._reply(413, {"error": "body too large"})
            return
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._reply(400, {"error": "invalid JSON"})
            return
        if self.path == "/practice/deck":
            self._reply(200, practice_deck(payload))
            return
        if self.path == "/practice/decks/reset":
            self._reply(200, practice_decks_reset())
            return
        if self.path == "/bridge/start":
            self._reply(200, bridge_start(payload))
            return
        if self.path == "/bridge/answer":
            self._reply(200, bridge_answer(payload))
            return
        if self.path == "/bridge/action":
            self._reply(200, bridge_action(payload))
            return
        if self.path == "/bridge/stop":
            self._reply(200, bridge_stop())
            return
        if self.path != "/simulate":
            self._reply(404, {"error": "not found"})
            return
        result = simulate(payload)
        # Only genuine sidecar trouble (no jar, hung engine) reads as 500 --
        # those are the states the app's circuit breaker should trip on.
        broken = result.get("exit_code", -1) in (-3, -9)
        self._reply(500 if broken else 200, result)

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[forge-sim] {fmt % args}", flush=True)


if __name__ == "__main__":
    # Threaded so status and practice endpoints answer while a simulation
    # blocks in subprocess.run -- a single-threaded server made every guard
    # unreachable and queued table-open requests behind 15-minute sims.
    server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    server.daemon_threads = True
    print(f"[forge-sim] listening on 8080, jar={find_jar()}", flush=True)
    server.serve_forever()
