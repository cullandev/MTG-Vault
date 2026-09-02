package bridge;

import forge.LobbyPlayer;
import forge.game.GameEntityView;
import forge.game.card.CardView;
import forge.game.phase.PhaseType;
import forge.game.player.PlayerView;
import forge.game.spellability.SpellAbilityView;
import forge.player.PlayerZoneUpdate;
import forge.player.PlayerZoneUpdates;
import forge.game.zone.ZoneType;
import forge.gamemodes.match.AbstractGuiGame;
import forge.gui.interfaces.IGuiGame;
import forge.item.PaperCard;
import forge.localinstance.skin.FSkinProp;
import forge.trackable.TrackableCollection;
import forge.util.ITriggerEvent;
import forge.util.collect.FCollectionView;

import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.SynchronousQueue;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;

/**
 * Forge's rules engine, talking JSON.
 *
 * {@link AbstractGuiGame} is the seam: the engine drives any presentation
 * through {@link IGuiGame}, and already has one remote implementation for
 * online play. This is a second one whose "screen" is a stream of JSON events.
 *
 * Thirty-seven methods are abstract here. They divide cleanly:
 *
 *   ~20 are void notifications -- the engine telling us something changed.
 *       Each emits an event carrying a fresh snapshot of the board.
 *   ~15 are blocking prompts that return the human's answer. An AI-controlled
 *       seat never reaches them, so for a spectator they are unreachable; each
 *       records that it fired and answers harmlessly rather than throwing,
 *       because a crashed spectator must never take a running game down.
 *   2 are queries with obvious answers.
 *
 * Phase 3 replaces the prompt defaults with a real request/response over the
 * socket. Their being listed and counted here is what makes that job bounded.
 */
public final class BridgeGui extends AbstractGuiGame {

    /** How long a prompt waits for a person before it answers itself. */
    private static final long ANSWER_TIMEOUT_S = 300;

    private final Consumer<String> sink;
    private final Map<String, Integer> promptsSeen = new LinkedHashMap<>();
    private final Map<String, SynchronousQueue<String>> pending = new ConcurrentHashMap<>();
    private int sequence = 0;
    private int askId = 0;
    private volatile boolean interactive = false;
    //: Auto-pass priority through steps with nothing to decide. The owner can
    //: turn it off to get a stop at every window, the way Forge's own client
    //: does with all its phase toggles enabled.
    private volatile boolean autoPass = true;
    //: Which seat the person is in, by index into the game's player order.
    //: -1 means "ask Forge", which is only right when nobody is playing.
    private volatile int localSeat = -1;
    /**
     * Milliseconds the engine pauses after each of the AI's plays and combat
     * steps, so a person can see what happened before the next thing does.
     * Forge left to itself resolves a whole AI turn in well under a second,
     * which is faster than a board can be read. 0 is full speed.
     */
    private volatile int paceMs = 0;

    /**
     * Where the game stops for the person: on their own turn, and on the
     * opponent's. Forge's client has this as a row of per-phase toggles;
     * ours had one switch. These sets are what {@link #isUiSetToSkipPhase}
     * reads, so a toggle on the strip changes the actual stopping, not a
     * label. The defaults reproduce the previous fixed behaviour exactly.
     *
     * Held as holder classes for the same class-load reason as
     * {@link NeverADecision}: PhaseType must not load before FModel.
     */
    private static final class DefaultStops {
        static final java.util.Set<PhaseType> MINE = java.util.EnumSet.of(
                PhaseType.MAIN1, PhaseType.COMBAT_DECLARE_ATTACKERS, PhaseType.MAIN2);
        static final java.util.Set<PhaseType> THEIRS = java.util.EnumSet.of(
                PhaseType.COMBAT_DECLARE_BLOCKERS);
    }
    private java.util.Set<PhaseType> stopsMine = null;
    private java.util.Set<PhaseType> stopsTheirs = null;

    private synchronized java.util.Set<PhaseType> stopsMine() {
        if (stopsMine == null) stopsMine = java.util.EnumSet.copyOf(DefaultStops.MINE);
        return stopsMine;
    }

    private synchronized java.util.Set<PhaseType> stopsTheirs() {
        if (stopsTheirs == null) stopsTheirs = java.util.EnumSet.copyOf(DefaultStops.THEIRS);
        return stopsTheirs;
    }

    /** The person's own PlayerView, or null when nobody is seated. */
    private PlayerView localView() {
        forge.game.GameView game = getGameView();
        if (game == null || game.getPlayers() == null || localSeat < 0) return null;
        int index = 0;
        for (PlayerView candidate : game.getPlayers()) {
            if (index++ == localSeat) return candidate;
        }
        return null;
    }

    /**
     * Steps no player ever acts in, whoever's turn it is.
     *
     * Held behind a holder class, and not as a static field of BridgeGui, for
     * a reason that costs the whole feature when got wrong. BridgeMain builds
     * the BridgeGui before it calls FModel.initialize -- it has to, because
     * the gui is what Forge reports initialization progress to. Touching
     * PhaseType from BridgeGui's own static initializer therefore loads
     * PhaseType before Forge's Localizer exists, and PhaseType's constructor
     * asks the Localizer for its display name:
     *
     *     ExceptionInInitializerError at bridge.BridgeGui.<clinit>
     *     caused by NullPointerException: "this.resourceBundle" is null
     *         at forge.util.Localizer.getMessage(Localizer.java:87)
     *         at forge.game.phase.PhaseType.<init>(PhaseType.java:63)
     *
     * The bridge died before printing a line, so a game "started" and then had
     * no events at all. A holder defers the set to first use, which is during
     * play and therefore long after initialize.
     */
    private static final class NeverADecision {
        static final java.util.Set<PhaseType> SET = java.util.EnumSet.of(
                PhaseType.UNTAP, PhaseType.UPKEEP, PhaseType.DRAW,
                PhaseType.COMBAT_BEGIN, PhaseType.COMBAT_END,
                PhaseType.COMBAT_FIRST_STRIKE_DAMAGE, PhaseType.COMBAT_DAMAGE,
                PhaseType.END_OF_TURN, PhaseType.CLEANUP);
    }

    public BridgeGui(Consumer<String> sink) {
        this.sink = sink;
    }

    /** Turn blocking prompts into questions for a person rather than defaults. */
    public void setInteractive(boolean value) {
        this.interactive = value;
        applyTablePrefs();
    }

    /**
     * Forge hands the seat its controller here, during setup.
     *
     * This is the earliest point the auto-pass preference can be set: it lives
     * on the controller's YieldController, and before this call there is no
     * controller to set it on. setInteractive and setAutoPass both run before
     * the match is built, so without this override the preference would be
     * written to nothing and auto-pass would stay off for the whole game.
     */
    @Override
    public void setGameController(PlayerView player, forge.interfaces.IGameController controller) {
        super.setGameController(player, controller);
        //: The controller passed in, not getGameController(): the no-arg form
        //: resolves through the current player, and during setup there is not
        //: one yet.
        applyTablePrefs(controller);
    }

    /**
     * Which seat the person occupies.
     *
     * One BridgeGui serves both seats, so Forge's own isLocalPlayer can answer
     * true for BOTH -- and then every turn looks like yours and the stop rule
     * halts through the opponent's whole turn. BridgeMain seated the person; it
     * can simply say where.
     */
    public void setLocalSeat(int seat) {
        this.localSeat = seat;
    }

    /** How long to pause after each of the AI's plays; clamped, 0 for none. */
    public void setPace(int ms) {
        this.paceMs = Math.max(0, Math.min(ms, 5000));
    }

    /** Whether the match has actually started, as opposed to still being set up. */
    private boolean gameHasBegun() {
        forge.game.GameView game = getGameView();
        return game != null && game.getPhase() != null;
    }

    /** Whether this view is the person's own seat. */
    private boolean isMine(PlayerView player) {
        if (player == null) return false;
        if (localSeat >= 0) {
            forge.game.GameView game = getGameView();
            if (game != null && game.getPlayers() != null) {
                int index = 0;
                for (PlayerView candidate : game.getPlayers()) {
                    if (index++ == localSeat) return player.equals(candidate);
                }
            }
        }
        return isLocalPlayer(player);
    }

    /** Whether to pass priority automatically through steps with no decision. */
    public void setAutoPass(boolean value) {
        this.autoPass = value;
        applyTablePrefs();
        emit("autoPass", Json.object().put("on", value));
    }

    /**
     * Turn on the parts of Forge the table depends on.
     *
     * Three preferences, all off by default, all gating machinery the engine
     * already runs: auto-pass, actionable highlights, and the autotap
     * preview. Each was reimplemented badly here before it was found.
     *
     * isUiSetToSkipPhase reads like the auto-pass hook and is not one. Forge's
     * priority loop asks PlayerControllerHuman.mayAutoPass(), which is
     *
     *     yield.shouldAutoYield() || yield.isAutoPassingNoActions(localView)
     *
     * and isAutoPassingNoActions opens with
     *
     *     if (!getBoolPref(YIELD_AUTO_PASS_NO_ACTIONS)) return false;
     *
     * so with that preference off -- which is its default -- the GUI hook is
     * never reached and every phase stops for a person. Three attempts at the
     * hook did nothing because none of them were ever called.
     *
     * Flipping the preference buys two things at once. isAutoPassingNoActions
     * consults isUiSetToSkipPhase for the phase policy, and then falls through
     * to !player.hasAvailableActions() -- Forge's own rules-aware answer to
     * "could this person do anything here?", computed by AvailableActions and
     * therefore already aware of a held counterspell or an available block.
     * PlayerControllerHuman.needsAvailableActions() turns on that computation
     * from the same preference, so one flip arms the whole mechanism.
     *
     * The preference is set through the controller, which scopes it to this
     * match's YieldController rather than writing Forge's global preferences.
     */
    private void applyTablePrefs() {
        applyTablePrefs(null);
    }

    private void applyTablePrefs(forge.interfaces.IGameController seated) {
        try {
            //: These are Forge's own global preferences, and this is how Forge
            //: itself sets them -- YieldController.toggleAutoPassNoActions does
            //: exactly this. IGameController.setYieldPref is NOT a setter: its
            //: whole body is
            //:
            //:     if (pref == YIELD_AUTO_PASS_NO_ACTIONS) tryAutoPassNow();
            //:
            //: a nudge to re-evaluate, nothing more. Setting them through it
            //: quietly did nothing at all.
            //:
            //: Deliberately not saved to disk. The gauntlet's simulator is a
            //: separate JVM sharing this profile directory, and a table
            //: preference has no business changing how a batch simulation runs.
            forge.localinstance.properties.ForgePreferences prefs = forge.model.FModel.getPreferences();
            if (prefs == null) return;
            prefs.setPref(
                    forge.localinstance.properties.ForgePreferences.FPref.YIELD_AUTO_PASS_NO_ACTIONS,
                    interactive && autoPass);
            // The same switch, for the two halves of "what can I do right now".
            // Forge already works it out -- InputPassPriority and InputPayMana
            // call PlayerControllerHuman.pushActionableCards, which hands the
            // set to this gui through setWeaklySelectable -- but that method
            // opens by reading these preferences and, with both off, clears the
            // set and returns. The engine computed the answer and threw it away
            // before the table could see it.
            //
            // HIGHLIGHTS covers priority: what can I cast or activate now.
            // AUTOTAP_PREVIEW covers payment: which lands can pay the cost being
            // asked for -- the question that made paying mana feel broken when
            // nothing on the table answered it.
            prefs.setPref(
                    forge.localinstance.properties.ForgePreferences.FPref.UI_SHOW_ACTIONABLE_HIGHLIGHTS,
                    interactive);
            prefs.setPref(
                    forge.localinstance.properties.ForgePreferences.FPref.UI_SHOW_AUTOTAP_PREVIEW,
                    interactive);

            //: Having changed the preference, tell the seat to look again. This
            //: is what setYieldPref is actually for.
            forge.interfaces.IGameController controller =
                    seated != null ? seated : getGameController();
            if (controller != null) {
                controller.setYieldPref(
                        forge.localinstance.properties.ForgePreferences.FPref.YIELD_AUTO_PASS_NO_ACTIONS,
                        String.valueOf(interactive && autoPass));
            }
        } catch (RuntimeException e) {
            //: A preference is a convenience. If this Forge build has moved it,
            //: the table still plays -- it just stops more often.
            emit("warn", Json.object().put("at", "tablePrefs").put("error", String.valueOf(e)));
        }
    }

    /**
     * Deliver an answer from the client. Called off the game thread.
     *
     * @return whether a prompt was actually waiting under that id.
     */
    public boolean answer(String id, String value) {
        SynchronousQueue<String> slot = pending.get(id);
        if (slot == null) return false;
        return slot.offer(value);
    }

    /**
     * An action the player took while no prompt was outstanding.
     *
     * Mulligans, "press OK to continue" and passing priority are not prompts:
     * the engine advertises them through updateButtons and expects the answer
     * through IGameController. Without this the game stops at the first
     * mulligan with nothing to press.
     *
     * @return whether the action was recognised and a controller was there.
     */
    public boolean action(String what) {
        forge.interfaces.IGameController controller = getGameController();
        if (controller == null) return false;
        if (what != null && what.startsWith("stop:")) return setStop(what);
        if (what != null && what.startsWith("pace:")) {
            try {
                setPace(Integer.parseInt(what.substring(5).trim()));
                return true;
            } catch (NumberFormatException malformed) {
                return false;
            }
        }
        switch (what == null ? "" : what.trim().toLowerCase()) {
            case "ok": controller.selectButtonOk(); return true;
            case "cancel": controller.selectButtonCancel(); return true;
            case "pass": controller.passPriority(); return true;
            default:
                // "player:<seat>" -- pressed a player, not a permanent.
                // Lightning Bolt to the face is a legal play and there was no
                // way to express it.
                if (what != null && what.startsWith("player:")) {
                    try {
                        int seat = Integer.parseInt(what.substring(7).trim());
                        forge.game.GameView game = getGameView();
                        if (game == null || game.getPlayers() == null) return false;
                        int index = 0;
                        for (PlayerView candidate : game.getPlayers()) {
                            if (index++ == seat) {
                                controller.selectPlayer(candidate, null);
                                return true;
                            }
                        }
                        return false;
                    } catch (NumberFormatException malformed) {
                        return false;
                    }
                }
                // "card:<id>" -- the player pressed something on the board.
                if (what != null && what.startsWith("card:")) {
                    try {
                        CardView card = findCard(Integer.parseInt(what.substring(5).trim()));
                        if (card == null) return false;
                        return controller.selectCard(card, new ArrayList<>(), null);
                    } catch (NumberFormatException malformed) {
                        return false;
                    }
                }
                return false;
            case "concede": controller.concede(); return true;
            case "autopass:on": setAutoPass(true); return true;
            case "autopass:off": setAutoPass(false); return true;
            // The controls Forge's own client has. Each is one call that was
            // already there; none of them had a way to be pressed.
            case "endturn":
                // Forge's End Turn: yield until the turn is over, respecting
                // its own interrupts (a spell aimed at you still stops it).
                forge.gamemodes.match.YieldController.endTurn(controller, localView());
                return true;
            case "undo": controller.undoLastAction(); return true;
            case "alpha": controller.alphaStrike(); return true;
            case "resync": controller.requestResync(); return true;
        }
    }

    /**
     * "stop:mine:MAIN2:off" -- change where the game stops for the person.
     *
     * Kept out of {@link #action} so the parse has room to fail politely: a
     * phase name this Forge build does not have is ignored, not thrown.
     */
    private boolean setStop(String spec) {
        String[] parts = spec.split(":");
        if (parts.length != 4) return false;
        PhaseType phase;
        try {
            phase = PhaseType.valueOf(parts[2].trim().toUpperCase());
        } catch (IllegalArgumentException unknown) {
            return false;
        }
        boolean on = "on".equalsIgnoreCase(parts[3].trim());
        java.util.Set<PhaseType> set = "theirs".equalsIgnoreCase(parts[1]) ? stopsTheirs() : stopsMine();
        synchronized (this) {
            if (on) set.add(phase); else set.remove(phase);
        }
        emit("stops");
        return true;
    }

    /** Prompts that fired, so phase 3 knows which ones a real game actually uses. */
    public Map<String, Integer> promptsSeen() {
        return promptsSeen;
    }

    // -- the wire ------------------------------------------------------------

    private void emit(String kind, Json extra) {
        Json event = Json.object().put("seq", ++sequence).put("kind", kind);
        if (extra != null) event.put("detail", extra);
        Json state = snapshot();
        if (state != null) event.put("state", state);
        sink.accept(event.toString());
    }

    private void emit(String kind) {
        emit(kind, null);
    }

    /** Record an unreachable-for-a-spectator prompt instead of throwing. */
    private <T> T prompt(String name, T fallback) {
        promptsSeen.merge(name, 1, Integer::sum);
        emit("prompt", Json.object().put("method", name).put("answered", "default"));
        return fallback;
    }

    /**
     * Put a question to the person and BLOCK the game thread until they answer.
     *
     * This is the shape Forge's own online play uses: an IGuiGame prompt is
     * synchronous, so a remote seat has to hold the engine's thread while the
     * answer travels. The timeout exists because a browser tab can close --
     * lapsing to the safe default is the only alternative to a game that hangs
     * forever holding the sidecar's heap.
     *
     * @param options what the client may answer with, rendered as buttons.
     * @return the raw answer, or null if nobody answered in time.
     */
    private String ask(String method, String text, List<String> options, int min, int max) {
        promptsSeen.merge(method, 1, Integer::sum);
        String id = "ask" + (++askId);
        SynchronousQueue<String> slot = new SynchronousQueue<>();
        pending.put(id, slot);
        try {
            // Forge passes -1 for "unbounded". Normalise here so the client
            // never has to know that, and never mistakes it for single-select.
            int lo = Math.max(0, min);
            int hi = max < 0 ? options.size() : Math.min(max, options.size());
            emit("ask", Json.object()
                    .put("id", id)
                    .put("method", method)
                    .put("text", text)
                    .put("min", lo)
                    .put("max", Math.max(lo, hi))
                    .putStrings("options", options));
            return slot.poll(ANSWER_TIMEOUT_S, TimeUnit.SECONDS);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            return null;
        } finally {
            pending.remove(id);
            emit("askDone", Json.object().put("id", id));
        }
    }

    /** An answer read as an index into the options, or -1. */
    private int askIndex(String method, String text, List<String> options, int fallback) {
        if (!interactive) return prompt(method, fallback);
        String answer = ask(method, text, options, 1, 1);
        if (answer == null) return fallback;
        try {
            int index = Integer.parseInt(answer.trim());
            return index >= 0 && index < options.size() ? index : fallback;
        } catch (NumberFormatException malformed) {
            return fallback;
        }
    }

    /** An answer read as a comma-separated list of indices into `options`. */
    private List<Integer> askIndices(String method, String text, List<String> options,
                                     int min, int max) {
        List<Integer> picked = new ArrayList<>();
        if (!interactive) return picked;
        String answer = ask(method, text, options, min, max);
        if (answer == null || answer.isBlank()) return picked;
        for (String part : answer.split(",")) {
            try {
                int index = Integer.parseInt(part.trim());
                if (index >= 0 && index < options.size() && !picked.contains(index)) picked.add(index);
            } catch (NumberFormatException ignored) {
                // A malformed index is a dropped click, not a reason to stall.
            }
        }
        return picked;
    }

    private static List<String> labels(List<?> items) {
        List<String> out = new ArrayList<>();
        if (items == null) return out;
        for (Object item : items) out.add(String.valueOf(item));
        return out;
    }

    // -- the board -----------------------------------------------------------

    /**
     * The whole visible game state, every event.
     *
     * Deltas would be less traffic, but a snapshot cannot drift out of sync,
     * and IGameController.requestResync exists precisely because Forge expects
     * a client to give up on deltas sometimes. Correct first.
     */
    //: How much of Forge's game log has already gone out. The log only grows,
    //: so everything past this index is new. Guarded because snapshots are
    //: built on Forge's game thread and on whichever thread answers an action.
    private int logSent = 0;

    /**
     * Whatever Forge has written to its game log since the last snapshot.
     *
     * The "log" on the page was never a game log. It was the prompt banner --
     * "Priority: X / Turn: N / Phase: ... / Stack: Empty" -- appended once per
     * update, so it said where you were over and over and never once said what
     * happened.
     *
     * Forge keeps a real one. GameView.getGameLog() holds GameLogEntry records
     * of (type, message, sourceCard), typed by a GameLogEntryType enum with
     * nineteen values: TURN, PHASE, LAND, DAMAGE, LIFE, COMBAT, ZONE_CHANGE,
     * STACK_ADD, STACK_RESOLVE, MULLIGAN, DISCARD, MANA and the rest. Nothing
     * in IGuiGame ever offers it to a client -- the desktop screen reaches in
     * and reads it -- so a bridge has to reach in too.
     *
     * Sent as a delta rather than a whole log: every event already carries a
     * full board snapshot, and the log is the one thing that only ever grows.
     */
    private List<String> newLogEntries(forge.game.GameView game) {
        List<String> fresh = new ArrayList<>();
        forge.game.GameLog log;
        try {
            log = game.getGameLog();
        } catch (RuntimeException notThere) {
            return fresh;
        }
        if (log == null) return fresh;
        List<forge.game.GameLogEntry> all = log.getAllEntries();
        if (all == null) return fresh;
        synchronized (this) {
            //: GameLog.add appends, so the list is oldest-first and everything
            //: past logSent is what has happened since the last snapshot.
            int size = all.size();
            if (size <= logSent) return fresh;
            for (int i = logSent; i < size; i++) {
                forge.game.GameLogEntry entry = all.get(i);
                if (entry == null) continue;
                String message = entry.message();
                if (message == null || message.isEmpty()) continue;
                fresh.add(Json.object()
                        .put("type", entry.type() == null ? "INFORMATION" : entry.type().name())
                        .put("text", message)
                        .toString());
            }
            logSent = size;
        }
        return fresh;
    }

    private Json snapshot() {
        forge.game.GameView game = getGameView();
        if (game == null) return null;

        List<String> players = new ArrayList<>();
        FCollectionView<PlayerView> seats = game.getPlayers();
        if (seats != null) {
            int seatIndex = 0;
            for (PlayerView player : seats) {
                players.add(describe(player, seatIndex++).toString());
            }
        }

        List<String> stack = new ArrayList<>();
        List<String> stackItems = new ArrayList<>();
        if (game.getStack() != null) {
            int index = 0;
            for (forge.game.spellability.StackItemView item : game.getStack()) {
                stack.add(Json.quote(String.valueOf(item)));
                stackItems.add(describeStackItem(item, index++).toString());
            }
        }

        List<String> mine = new ArrayList<>();
        List<String> theirs = new ArrayList<>();
        synchronized (this) {
            for (PhaseType p : stopsMine()) mine.add(p.name());
            for (PhaseType p : stopsTheirs()) theirs.add(p.name());
        }

        Json state = Json.object()
                .putRawArray("log", newLogEntries(game))
                .putRawArray("stackItems", stackItems)
                .putRawArray("combat", describeCombat(game))
                .putStrings("stopsMine", mine)
                .putStrings("stopsTheirs", theirs)
                .put("selecting", isSelecting())
                .put("selectMin", getSelectionMin())
                .put("selectMax", getSelectionMax())
                .put("turn", game.getTurn())
                .put("phase", game.getPhase() == null ? null : game.getPhase().name())
                .put("active", game.getPlayerTurn() == null ? null : game.getPlayerTurn().getName())
                .put("gameOver", game.isGameOver())
                .putRawArray("players", players)
                .putRawArray("stack", stack);
        if (game.isGameOver() && game.getWinningPlayerName() != null) {
            state.put("winner", game.getWinningPlayerName());
        }
        return state;
    }

    private Json describe(PlayerView player, int seatIndex) {
        Json seat = Json.object()
                .put("seat", seatIndex)
                // Forge highlights the entities that answer the current
                // question. For a player that is the only signal there is:
                // isSelectable takes a CardView and a player is not one.
                .put("targetable", isHighlighted(player))
                .put("name", player.getName())
                .put("life", player.getLife())
                .put("hand", count(player.getHand()))
                .put("library", count(player.getLibrary()))
                .put("you", isMine(player))
                // The engine's own answers to "is it me?" and "could I do
                // anything?" -- the page inferred both from whose turn it was,
                // which is how it once said "Your move" during the opponent's.
                .put("hasPriority", player.getHasPriority())
                .put("canAct", player.hasAvailableActions())
                .put("landsPlayed", player.getNumLandThisTurn())
                .put("landsAllowed", player.hasUnlimitedLandPlay() ? -1 : player.getMaxLandPlay())
                .put("poison", poison(player))
                .putRawArray("battlefieldCards", identified(player.getBattlefield()))
                .putRawArray("commanderCards", identified(player.getCommanders()))
                // As cards, not names: Forge highlights a graveyard card with the
                // same isSelectable flag the hand uses -- flashback, escape,
                // "return target creature card" -- and findCard already looked
                // here for a click. Nothing was drawn to click on.
                .putRawArray("graveyardCards", identified(player.getGraveyard()))
                .putRawArray("exileCards", identified(player.getExile()))
                .putStrings("battlefield", names(player.getBattlefield()))
                .putStrings("graveyard", names(player.getGraveyard()))
                .putStrings("commanders", names(player.getCommanders()));
        // Your own hand is yours to see; an opponent's stays a number.
        if (isMine(player)) {
            seat.putRawArray("handCards", identified(player.getHand()));
        }
        return seat;
    }

    private static int count(Iterable<?> cards) {
        if (cards == null) return 0;
        int n = 0;
        for (Object ignored : cards) n++;
        return n;
    }

    private static List<String> names(Iterable<CardView> cards) {
        List<String> out = new ArrayList<>();
        if (cards == null) return out;
        for (CardView card : cards) {
            if (card == null) continue;
            String name = card.getName();
            out.add(name == null || name.isEmpty() ? "(hidden)" : name);
        }
        return out;
    }

    /**
     * Cards as {id, name} so a click can name one back.
     *
     * A name is not an identity: two Plains in hand are different cards, and
     * the engine wants the one you pressed.
     */
    private List<String> identified(Iterable<CardView> cards) {
        List<String> out = new ArrayList<>();
        if (cards == null) return out;
        for (CardView card : cards) {
            if (card == null) continue;
            String name = card.getName();
            boolean visible = mayView(card) && name != null && !name.isEmpty();
            Json entry = Json.object()
                    .put("id", card.getId())
                    .put("name", visible ? name : "(hidden)")
                    .put("tapped", card.isTapped());
            // The engine knows precisely what it will accept a click on. Not
            // surfacing it left the player guessing, which is the difference
            // between "combat is unimplemented" and "combat is unlabelled".
            if (isSelectable(card)) entry.put("selectable", true);
            else if (isWeaklySelectable(card)) entry.put("weak", true);
            if (card.isAttacking()) entry.put("attacking", true);
            if (card.isBlocking()) entry.put("blocking", true);
            if (card.isSick()) entry.put("sick", true);
            if (card.isToken()) entry.put("token", true);
            if (card.isFaceDown()) entry.put("faceDown", true);
            if (card.isCommander()) entry.put("commander", true);
            if (card.getDamage() > 0) entry.put("damage", card.getDamage());
            // Counters, and what is attached to what. Three +1/+1 counters made
            // a creature look identical to one with none; an aura sat in the
            // row as if it were a permanent of its own.
            Map<String, Integer> counters = counters(card);
            if (!counters.isEmpty()) entry.putCounts("counters", counters);
            List<CardView> attached = card.getAttachedCards();
            if (attached != null && !attached.isEmpty()) {
                List<String> ids = new ArrayList<>();
                for (CardView a : attached) if (a != null) ids.add(String.valueOf(a.getId()));
                entry.putRawArray("attached", ids);
            }
            if (card.getAttachedTo() != null) entry.put("attachedTo", card.getAttachedTo().getId());
            // What the card IS decides where it sits on the table: lands in
            // their own row, creatures in the combat row.
            if (visible) {
                CardView.CardStateView view = card.getCurrentState();
                if (view != null) {
                    forge.card.CardTypeView type = view.getType();
                    if (type != null) {
                        if (type.isLand()) entry.put("kind", "land");
                        else if (type.isCreature()) entry.put("kind", "creature");
                        else if (type.isPlaneswalker()) entry.put("kind", "planeswalker");
                        else if (type.isEnchantment()) entry.put("kind", "enchantment");
                        else if (type.isArtifact()) entry.put("kind", "artifact");
                        else entry.put("kind", "spell");
                        entry.put("types", type.toString());
                    }
                    if (type != null && type.isCreature()) {
                        entry.put("power", view.getPower());
                        entry.put("toughness", view.getToughness());
                    }
                    if (type != null && type.isPlaneswalker() && view.getLoyalty() != null
                            && !view.getLoyalty().isEmpty()) {
                        entry.put("loyalty", view.getLoyalty());
                    }
                    if (view.getManaCost() != null) {
                        entry.put("cost", view.getManaCost().toString());
                    }
                    List<String> keywords = keywords(view);
                    if (!keywords.isEmpty()) entry.putStrings("keywords", keywords);
                }
            }
            out.add(entry.toString());
        }
        return out;
    }

    /** A card's counters by name -- "P1P1": 3, "LOYALTY": 4, "LORE": 2. */
    private static Map<String, Integer> counters(forge.game.GameEntityView entity) {
        Map<String, Integer> out = new LinkedHashMap<>();
        try {
            com.google.common.collect.Multiset<forge.game.card.CounterType> set = entity.getCounters();
            if (set == null) return out;
            for (com.google.common.collect.Multiset.Entry<forge.game.card.CounterType> e : set.entrySet()) {
                if (e.getElement() == null || e.getCount() <= 0) continue;
                String name = e.getElement().getName();
                if (name != null && !name.isEmpty()) out.put(name, e.getCount());
            }
        } catch (RuntimeException notTracked) {
            // A view that has not synced its counters yet is a view with none.
        }
        return out;
    }

    private static int poison(PlayerView player) {
        try {
            forge.game.card.CounterType poison = forge.game.card.CounterType.getType("POISON");
            return poison == null ? 0 : player.getCounters(poison);
        } catch (RuntimeException notTracked) {
            return 0;
        }
    }

    /**
     * The keywords on a card, as Forge titles them -- "Flying", "Ward 2",
     * "Protection from red". Capped, because a card with eleven is a card
     * whose text you read, not whose badges you count.
     */
    private static List<String> keywords(CardView.CardStateView view) {
        List<String> out = new ArrayList<>();
        try {
            forge.game.keyword.KeywordCollectionView all = view.getKeywords();
            if (all == null) return out;
            for (forge.game.keyword.KeywordView k : all) {
                if (k == null) continue;
                String title = k.title();
                if (title == null || title.isEmpty()) title = k.original();
                if (title == null || title.isEmpty()) continue;
                if (!out.contains(title)) out.add(title);
                if (out.size() >= 8) break;
            }
        } catch (RuntimeException notTracked) {
            // Same rule as counters: no keywords is a valid answer.
        }
        return out;
    }

    /**
     * One thing on the stack: what it says, what cast it, who cast it, and
     * what it is aimed at. It was a string; a counterspell window is the
     * moment auto-pass hands the person, and it read "stack: 1".
     */
    private Json describeStackItem(forge.game.spellability.StackItemView item, int index) {
        Json out = Json.object()
                .put("index", index)
                .put("text", item.getText() == null ? "" : item.getText())
                .put("trigger", item.isTrigger());
        CardView source = item.getSourceCard();
        if (source != null) {
            out.put("sourceId", source.getId());
            out.put("source", source.getName() == null ? "" : source.getName());
        }
        if (item.getActivatingPlayer() != null) {
            out.put("by", item.getActivatingPlayer().getName());
            out.put("mine", isMine(item.getActivatingPlayer()));
        }
        List<String> cards = new ArrayList<>();
        if (item.getTargetCards() != null) {
            for (CardView t : item.getTargetCards()) if (t != null) cards.add(String.valueOf(t.getId()));
        }
        out.putRawArray("targetCards", cards);
        List<String> players = new ArrayList<>();
        if (item.getTargetPlayers() != null) {
            for (PlayerView t : item.getTargetPlayers()) if (t != null) players.add(t.getName());
        }
        out.putStrings("targetPlayers", players);
        return out;
    }

    /**
     * Combat as pairings: each attacker, what it is attacking, and what is
     * blocking it. Everything Forge's own client draws its combat lines from,
     * and the first time this bridge has said which creature is hitting whom.
     */
    private List<String> describeCombat(forge.game.GameView game) {
        List<String> out = new ArrayList<>();
        forge.game.combat.CombatView combat;
        try {
            combat = game.getCombat();
        } catch (RuntimeException none) {
            return out;
        }
        if (combat == null || combat.getAttackers() == null) return out;
        for (CardView attacker : combat.getAttackers()) {
            if (attacker == null) continue;
            Json pair = Json.object().put("attacker", attacker.getId());
            GameEntityView defender = combat.getDefender(attacker);
            if (defender instanceof CardView) {
                pair.put("defenderCard", ((CardView) defender).getId());
            } else if (defender instanceof PlayerView) {
                pair.put("defenderPlayer", defender.getName());
            }
            List<String> blockers = new ArrayList<>();
            FCollectionView<CardView> blocking = combat.getBlockers(attacker);
            if (blocking != null) {
                for (CardView b : blocking) if (b != null) blockers.add(String.valueOf(b.getId()));
            }
            pair.putRawArray("blockers", blockers);
            out.add(pair.toString());
        }
        return out;
    }

    /** Find a card anywhere on the board by the id the client sent back. */
    private CardView findCard(int id) {
        forge.game.GameView game = getGameView();
        if (game == null || game.getPlayers() == null) return null;
        for (PlayerView player : game.getPlayers()) {
            for (Iterable<CardView> zone : List.of(
                    player.getHand(), player.getBattlefield(), player.getGraveyard(),
                    player.getCommand(), player.getExile())) {
                if (zone == null) continue;
                for (CardView card : zone) {
                    if (card != null && card.getId() == id) return card;
                }
            }
        }
        return null;
    }

    // -- pacing: a beat after each of the AI's plays -------------------------
    //
    // The headless shim runs the engine's UI callbacks inline on the game
    // thread, so sleeping in one of them holds the game itself -- which is the
    // point. Nothing here pauses for the person's own plays: they know what
    // they just did, and a laggy hand is worse than a fast opponent.

    /** Sleep a fraction of the pace, if pacing is on and a game is in progress. */
    private void beat(double fraction) {
        if (paceMs <= 0 || !gameHasBegun()) return;
        forge.game.GameView game = getGameView();
        if (game == null || game.isGameOver()) return;
        try {
            Thread.sleep(Math.round(paceMs * fraction));
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
        }
    }

    /** Whether it is the AI's turn -- or nobody's in particular, when watching. */
    private boolean theirTurn() {
        forge.game.GameView game = getGameView();
        if (game == null) return false;
        return !isMine(game.getPlayerTurn());
    }

    @Override
    public void updateStack() {
        super.updateStack();
        forge.game.GameView game = getGameView();
        forge.game.spellability.StackItemView top = game == null ? null : game.peekStack();
        // Something the person cast or activated: no pause.
        if (top != null && isMine(top.getActivatingPlayer())) return;
        beat(1.0);
    }

    @Override
    public void updatePhase(boolean saveState) {
        super.updatePhase(saveState);
        forge.game.GameView game = getGameView();
        PhaseType phase = game == null ? null : game.getPhase();
        String name = phase == null ? "" : phase.name();
        // On the person's own turn the one step worth a pause is the AI's
        // blocks; everything else there is theirs to take at their own speed.
        if (!theirTurn()) {
            if (name.equals("COMBAT_DECLARE_BLOCKERS")) beat(0.9);
            return;
        }
        beat(name.startsWith("COMBAT_DECLARE") || name.equals("COMBAT_DAMAGE") ? 0.9 : 0.25);
    }

    @Override
    public void updateTurn(PlayerView player) {
        super.updateTurn(player);
        if (!isMine(player)) beat(0.6);
    }

    @Override
    public void updateZones(Iterable<PlayerZoneUpdate> zones) {
        super.updateZones(zones);
        // A land played, a creature entering, a card drawn: the AI's board
        // changing without the stack being involved.
        if (theirTurn()) beat(0.4);
    }

    // -- notifications: the engine telling us something happened -------------

    @Override
    protected void updateCurrentPlayer(PlayerView player) {
        emit("currentPlayer", Json.object().put("player", player == null ? null : player.getName()));
    }

    @Override
    public void setGameView(forge.game.GameView view) {
        super.setGameView(view);
        emit("gameView");
    }

    @Override public void openView(TrackableCollection<PlayerView> myPlayers) { emit("openView"); }
    @Override public void finishGame() { emit("finishGame"); }
    @Override public void showCombat() { emit("combat"); }
    @Override public void setCard(CardView card) {
        emit("card", Json.object().put("name", card == null ? null : card.getName()));
    }
    @Override public void setPanelSelection(CardView card) { emit("panelSelection"); }
    @Override public void updateShards(Iterable<PlayerView> players) { emit("shards"); }
    @Override public void updateButtons(PlayerView owner, String label1, String label2,
                                        boolean enable1, boolean enable2, boolean focus) {
        emit("buttons", Json.object()
                .put("ok", label1)
                .put("cancel", label2)
                .put("okEnabled", enable1)
                .put("cancelEnabled", enable2));
    }
    @Override public void showPromptMessage(PlayerView player, String message, CardView card) {
        emit("message", Json.object()
                .put("player", player == null ? null : player.getName())
                .put("text", message));
    }
    @Override public void message(String message, String title) {
        emit("message", Json.object().put("title", title).put("text", message));
    }
    @Override public void showErrorDialog(String message, String title) {
        emit("error", Json.object().put("title", title).put("text", message));
    }
    @Override public void showManaPool(PlayerView player) { emit("manaPool", Json.object().put("show", true)); }
    @Override public void hideManaPool(PlayerView player) { emit("manaPool", Json.object().put("show", false)); }
    @Override public void enableOverlay() { emit("overlay", Json.object().put("on", true)); }
    @Override public void disableOverlay() { emit("overlay", Json.object().put("on", false)); }
    @Override public void alertUser() { emit("alert"); }
    @Override public void flashIncorrectAction() { emit("incorrectAction"); }
    @Override public void setPlayerAvatar(LobbyPlayer player, forge.game.player.IHasIcon icon) { }
    @Override public void hideZones(PlayerView player, Iterable<PlayerZoneUpdate> zones) { }
    @Override public void restoreOldZones(PlayerView player, PlayerZoneUpdates zones) { }

    // -- queries -------------------------------------------------------------

    /**
     * Whether to pass priority here without asking.
     *
     * Forge offers a human priority at every step of every turn -- their untap,
     * their upkeep, their draw, each of their combat steps. Answering false
     * everywhere meant pressing OK a dozen times a turn to watch the AI take
     * its, which is not a game, it is a metronome.
     *
     * On YOUR turn: your two main phases and declare-attackers.
     *
     * On THEIR turn, only what you can actually act on -- being attacked, or a
     * spell on the stack you have untapped mana to answer. Stopping for a
     * spell you cannot pay to respond to offers a button and nothing else.
     */
    @Override
    public boolean isUiSetToSkipPhase(PlayerView player, PhaseType phase) {
        if (!interactive || !autoPass || phase == null) return false;
        forge.game.GameView game = getGameView();
        // Forge asks this at phase boundaries, and the view can be absent for
        // a moment. Answering "stop" then produced a halt at the opponent's
        // untap and upkeep -- steps that are never a decision for anybody.
        // With no view to reason from, skip the steps that are never yours.
        if (game == null) return NeverADecision.SET.contains(phase);

        PlayerView active = game.getPlayerTurn();
        boolean mine = isMine(active);
        boolean stacked = game.getStack() != null && game.getStack().iterator().hasNext();

        if (mine) {
            if (stacked) return false;
            synchronized (this) { return !stopsMine().contains(phase); }
        }
        synchronized (this) { if (stopsTheirs().contains(phase)) return false; }
        return !(stacked && hasUntappedMana());
    }

    /**
     * Whether the local seat could pay for anything right now.
     *
     * An approximation on purpose: an untapped land is the cheap, honest proxy
     * for "you might be able to respond". Asking Forge what is actually
     * castable means walking every card in hand against a mana pool, which is
     * a lot of work to decide whether to show a button.
     */
    private boolean hasUntappedMana() {
        forge.game.GameView game = getGameView();
        if (game == null || game.getPlayers() == null) return false;
        for (PlayerView seat : game.getPlayers()) {
            if (!isMine(seat) || seat.getBattlefield() == null) continue;
            for (CardView card : seat.getBattlefield()) {
                if (card == null || card.isTapped()) continue;
                CardView.CardStateView view = card.getCurrentState();
                if (view != null && view.getType() != null && view.getType().isLand()) return true;
            }
        }
        return false;
    }
    @Override public forge.game.GameState getGamestate() { return null; }

    // -- prompts: a human seat's decisions, unreachable for a spectator ------

    @Override
    public PlayerZoneUpdates openZones(PlayerView player, Collection<ZoneType> zones,
                                       Map<PlayerView, Object> playerZones, boolean backupLastZones) {
        return prompt("openZones", null);
    }

    @Override
    public Iterable<PlayerZoneUpdate> tempShowZones(PlayerView player, Iterable<PlayerZoneUpdate> zones) {
        return prompt("tempShowZones", zones);
    }

    @Override
    public GameEntityView chooseSingleEntityForEffect(String title, List<? extends GameEntityView> options,
                                                      forge.game.player.DelayedReveal reveal, boolean isOptional) {
        if (options == null || options.isEmpty()) return null;
        int index = askIndex("chooseSingleEntityForEffect", title, labels(options), 0);
        return options.get(Math.max(0, Math.min(index, options.size() - 1)));
    }

    @Override
    public List<GameEntityView> chooseEntitiesForEffect(String title, List<? extends GameEntityView> options,
                                                        int min, int max, forge.game.player.DelayedReveal reveal) {
        List<GameEntityView> chosen = new ArrayList<>();
        if (options == null || options.isEmpty()) return chosen;
        for (int index : askIndices("chooseEntitiesForEffect", title, labels(options), min, max)) {
            chosen.add(options.get(index));
        }
        // The engine will not accept fewer than it asked for; fill from the top
        // rather than hand back something it must reject.
        for (int i = 0; chosen.size() < min && i < options.size(); i++) {
            if (!chosen.contains(options.get(i))) chosen.add(options.get(i));
        }
        return chosen;
    }

    @Override
    public <T> List<T> getChoices(String message, int min, int max, List<T> choices, List<T> selected,
                                  forge.util.FSerializableFunction<T, String> display) {
        if (choices == null || choices.isEmpty()) return new ArrayList<>();
        // Before the first phase exists there is no game to make decisions in,
        // and an optional multi-select at that point is setup, not play. Forge
        // opens every match by asking the seat to trim cards the AI handles
        // badly -- "AI can't play these cards well from <deck>" -- which is a
        // notice its own client shows and dismisses. Held up as a question it
        // stopped the table on turn zero, before a card was ever dealt.
        //
        // Only the optional form is answered this way. A required choice is a
        // real decision even during setup, and mulligans do not come through
        // here at all -- they arrive as buttons, through action("ok").
        if (min <= 0 && !gameHasBegun()) return new ArrayList<>();
        List<String> shown = new ArrayList<>();
        for (T choice : choices) {
            shown.add(display == null ? String.valueOf(choice) : display.apply(choice));
        }
        List<T> chosen = new ArrayList<>();
        for (int index : askIndices("getChoices", message, shown, min, max)) chosen.add(choices.get(index));
        for (int i = 0; chosen.size() < min && i < choices.size(); i++) {
            if (!chosen.contains(choices.get(i))) chosen.add(choices.get(i));
        }
        return chosen;
    }

    @Override
    public List<CardView> manipulateCardList(String title, Iterable<CardView> cards, Iterable<CardView> manipulable,
                                             boolean toTop, boolean toBottom, boolean toAnywhere) {
        List<CardView> all = new ArrayList<>();
        if (cards != null) for (CardView card : cards) all.add(card);
        if (!interactive || all.size() < 2) return prompt("manipulateCardList", all);

        List<CardView> ordered = new ArrayList<>();
        for (int index : askIndices("manipulateCardList", title, names(all), 0, all.size())) {
            ordered.add(all.get(index));
        }
        for (CardView card : all) {
            if (!ordered.contains(card)) ordered.add(card);
        }
        return ordered;
    }

    @Override
    public List<PaperCard> sideboard(forge.deck.CardPool sideboard, forge.deck.CardPool main, String message) {
        return prompt("sideboard", null);
    }

    @Override
    public Map<CardView, Integer> assignCombatDamage(CardView attacker, List<CardView> blockers, int damage,
                                                     GameEntityView defender, boolean overrideOrder, boolean maySkip) {
        Map<CardView, Integer> assignment = new LinkedHashMap<>();
        if (blockers == null || blockers.isEmpty() || damage <= 0) return assignment;

        // One blocker is not a decision.
        List<CardView> order = new ArrayList<>(blockers);
        if (blockers.size() > 1 && interactive) {
            List<String> shown = new ArrayList<>();
            for (CardView blocker : blockers) {
                shown.add(blocker.getName() + " (lethal " + blocker.getLethalDamage() + ")");
            }
            String who = attacker == null ? "Assign damage" : attacker.getName() + " assigns " + damage;
            List<Integer> picked = askIndices("assignCombatDamage", who, shown, 1, blockers.size());
            if (!picked.isEmpty()) {
                order = new ArrayList<>();
                for (int index : picked) order.add(blockers.get(index));
                for (CardView blocker : blockers) {
                    if (!order.contains(blocker)) order.add(blocker);
                }
            }
        }

        // Lethal down the chosen order, remainder to the last -- the ordering
        // is the real decision; the arithmetic after it is not.
        int left = damage;
        for (int i = 0; i < order.size() && left > 0; i++) {
            CardView blocker = order.get(i);
            int lethal = Math.max(1, blocker.getLethalDamage());
            int give = (i == order.size() - 1) ? left : Math.min(lethal, left);
            assignment.put(blocker, give);
            left -= give;
        }
        if (left > 0 && !order.isEmpty()) {
            CardView last = order.get(order.size() - 1);
            assignment.merge(last, left, Integer::sum);
        }
        return assignment;
    }

    @Override
    public Map<Object, Integer> assignGenericAmount(CardView effect, Map<Object, Integer> targets, int amount,
                                                    boolean atLeastOne, String amountLabel) {
        return prompt("assignGenericAmount", new LinkedHashMap<>());
    }

    @Override
    public <T> IGuiGame.OrderResult<T> order(String title, String top, int remainingMin, int remainingMax,
                                            List<T> sourceChoices, List<T> destChoices,
                                            CardView referenceCard, boolean sideboardingMode,
                                            boolean ordered) {
        List<T> source = sourceChoices == null ? new ArrayList<>() : sourceChoices;
        List<T> destination = destChoices == null ? new ArrayList<>() : destChoices;
        if (!interactive || source.isEmpty()) {
            // Handing back the destination unchanged is the "no reordering"
            // answer, not an empty one.
            return prompt("order", new IGuiGame.OrderResult<>(destination, false));
        }
        // askIndices preserves the order they were pressed in, which is exactly
        // the answer this prompt wants.
        // Not `ordered`: that is this method's own boolean parameter.
        List<T> result = new ArrayList<>(destination);
        for (int index : askIndices("order", top == null ? title : title + " — " + top,
                labels(source), Math.max(0, remainingMin), source.size())) {
            result.add(source.get(index));
        }
        for (T item : source) {
            if (!result.contains(item)) result.add(item);
        }
        return new IGuiGame.OrderResult<>(result, false);
    }

    @Override
    public SpellAbilityView getAbilityToPlay(CardView host, List<SpellAbilityView> abilities, ITriggerEvent event) {
        if (abilities == null || abilities.isEmpty()) return null;
        // One option is not a decision: playing the only thing a card does
        // should not cost a round trip to the browser.
        if (abilities.size() == 1) return abilities.get(0);
        int index = askIndex("getAbilityToPlay",
                host == null ? "Choose an ability" : host.getName(), labels(abilities), 0);
        return abilities.get(Math.max(0, Math.min(index, abilities.size() - 1)));
    }

    @Override
    public boolean confirm(CardView host, String message, boolean defaultYes, List<String> options) {
        List<String> buttons = options == null || options.isEmpty() ? List.of("Yes", "No") : options;
        return askIndex("confirm", message, buttons, defaultYes ? 0 : 1) == 0;
    }

    @Override
    public boolean showConfirmDialog(String message, String title, String yes, String no, boolean defaultYes) {
        return askIndex("showConfirmDialog", title == null ? message : title + " — " + message,
                List.of(yes == null ? "Yes" : yes, no == null ? "No" : no),
                defaultYes ? 0 : 1) == 0;
    }

    @Override
    public int showOptionDialog(String message, String title, FSkinProp icon, List<String> options, int defaultOption) {
        if (options == null || options.isEmpty()) return defaultOption;
        return askIndex("showOptionDialog", title == null ? message : title + " — " + message,
                options, defaultOption);
    }

    @Override
    public String showInputDialog(String message, String title, FSkinProp icon, String initial,
                                  List<String> options, boolean isNumeric) {
        return prompt("showInputDialog", initial);
    }
}
