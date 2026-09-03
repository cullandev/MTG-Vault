package bridge;

import forge.LobbyPlayer;
import forge.deck.Deck;
import forge.deck.DeckGroup;
import forge.game.GameRules;
import forge.game.GameType;
import forge.game.player.RegisteredPlayer;
import forge.gamemodes.match.HostedMatch;
import forge.gui.GuiBase;
import forge.gui.interfaces.IGuiBase;
import forge.gui.interfaces.IGuiGame;
import forge.model.FModel;

import java.io.BufferedReader;
import java.io.File;
import java.io.InputStreamReader;
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.EnumSet;
import java.util.List;
import java.util.Map;
import java.util.function.Consumer;

/**
 * Phase 1 of the practice bridge: host a real game and narrate it as JSON.
 *
 * Phase 0 proved the engine runs with no display. This drives it through our
 * own {@link BridgeGui} rather than Forge's simulator, which is the step that
 * makes a browser client possible: every board change arrives as an event we
 * chose the shape of.
 *
 *   java -cp forge.jar:bridge BridgeMain <deck.dck> <deck.dck> [out.jsonl]
 *
 * Writes one JSON object per line -- the transcript a spectator playmat
 * replays. Phase 2 renders it; phase 3 answers back.
 */
public final class BridgeMain {

    /** Where Forge keeps decks written into its profile. */
    private static Path deckDir(String home) {
        return Path.of(home, ".forge", "decks");
    }

    public static void main(String[] args) throws Exception {
        if (args.length < 2) {
            System.err.println("usage: BridgeMain <deck1.dck> <deck2.dck> [out.jsonl]");
            System.exit(2);
        }
        String home = System.getenv().getOrDefault("HOME", "/data");

        // Same headless shim as the phase 0 probe, plus getNewGuiGame so that
        // anything asking the platform for a screen gets ours.
        // Stream as we go rather than collecting: a human seat is waiting on
        // the other side of this pipe for the prompt it has to answer.
        List<String> transcript = new ArrayList<>();
        BridgeGui gui = new BridgeGui(line -> {
            synchronized (transcript) { transcript.add(line); }
            System.out.println(line);
            System.out.flush();
        });
        GuiBase.setInterface(Headless.gui(gui));

        long started = System.currentTimeMillis();
        FModel.initialize(null, null);
        System.err.printf("[bridge] card database loaded in %d ms%n",
                System.currentTimeMillis() - started);

        // args: deck1 deck2 [out|-] [type] [seat1] [paceMs] [name] [aiProfile] [sim|nosim]
        //   seat1 = "human" puts a person in the first seat; anything else is
        //   the spectator case, both sides played by the AI.
        boolean human = args.length > 4 && "human".equalsIgnoreCase(args[4]);
        gui.setInteractive(human);
        // Seat one is the person when there is one. Said plainly here so the
        // stop rule never has to infer it.
        if (human) gui.setLocalSeat(0);

        // args[6] = the person's name. Forge's GUI seat is one static
        // LobbyPlayer, named from the PLAYER_NAME preference during
        // FModel.initialize -- and, the first time that preference was empty,
        // from a rolled fantasy name it then saved: "Migorn". The name is a
        // plain field on the seat by the time main() runs, so it is set on the
        // seat itself; the preference is set too, in memory only, so anything
        // that reads it agrees. Nothing is saved: the file keeps what it had.
        if (human && args.length > 6 && !args[6].trim().isEmpty()) {
            String name = args[6].trim();
            FModel.getPreferences().setPref(
                    forge.localinstance.properties.ForgePreferences.FPref.PLAYER_NAME, name);
            forge.player.GamePlayerUtil.getGuiPlayer().setName(name);
        }

        // args[5] = milliseconds the engine pauses after each of the AI's plays
        // and combat steps, so a person can see what happened before the next
        // thing does. 0, or absent, is full speed.
        int pace = 0;
        if (args.length > 5) {
            try { pace = Integer.parseInt(args[5].trim()); } catch (NumberFormatException ignored) { }
        }
        gui.setPace(pace);
        System.err.printf("[bridge] pace %d ms%n", pace);

        // args[7] = the AI's profile -- Default, Cautious, Reckless or
        // Experimental, Forge's own res/ai/*.ai files; Reckless attacks into
        // trades where Default holds back. args[8] = "sim" to give the AI
        // Forge's simulation picker, which plays each candidate spell forward
        // in a copied game before choosing: slower, and marked experimental
        // upstream, so it is opt-in.
        String aiProfile = args.length > 7 ? args[7].trim() : "";
        boolean aiSimulation = args.length > 8 && "sim".equalsIgnoreCase(args[8].trim());
        java.util.Set<forge.ai.AIOption> aiOptions = aiSimulation
                ? java.util.EnumSet.of(forge.ai.AIOption.USE_FULL_SIMULATION)
                : java.util.EnumSet.noneOf(forge.ai.AIOption.class);
        System.err.printf("[bridge] ai profile %s, simulation %s%n",
                aiProfile.isEmpty() ? "(preference)" : aiProfile, aiSimulation);

        // args: deck1 deck2 [out|-] [Constructed|Commander]
        GameType type = args.length > 3 && "Commander".equalsIgnoreCase(args[3])
                ? GameType.Commander : GameType.Constructed;
        System.err.println("[bridge] game type " + type);

        List<RegisteredPlayer> seats = new ArrayList<>();
        for (int i = 0; i < 2; i++) {
            Deck deck = loadDeck(deckDir(home), args[i]);
            LobbyPlayer player = human && i == 0
                    ? forge.player.GamePlayerUtil.getGuiPlayer()
                    : forge.player.GamePlayerUtil.createAiPlayer("AI " + (i + 1), i, 0, aiOptions, aiProfile);
            // Only forCommander moves the deck's [Commander] section into the
            // command zone and sets 40 life. The plain constructor shuffles the
            // commanders into a 100-card library and starts at 20 -- which is
            // how every commander practice game had been played until now.
            RegisteredPlayer seat = (type == GameType.Commander
                    ? RegisteredPlayer.forCommander(deck)
                    : new RegisteredPlayer(deck)).setPlayer(player);
            seat.setTeamNumber(i);
            seats.add(seat);
            System.err.printf("[bridge] seat %d (%s): %s (%d cards, %d commanders)%n",
                    i + 1, human && i == 0 ? "human" : "ai", deck.getName(),
                    deck.getMain().countAll(),
                    deck.has(forge.deck.DeckSection.Commander) ? deck.get(forge.deck.DeckSection.Commander).countAll() : 0);
        }

        // Answers arrive on stdin, one per line: "<askId>	<value>". stdin is
        // the whole channel INTO this process -- no socket, no port, and it
        // closes when the sidecar reaps us.
        Thread answers = new Thread(() -> {
            try (BufferedReader in = new BufferedReader(new InputStreamReader(System.in))) {
                String line;
                while ((line = in.readLine()) != null) {
                    int tab = line.indexOf('	');
                    if (tab <= 0) continue;
                    String id = line.substring(0, tab);
                    String value = line.substring(tab + 1);
                    boolean handled = "action".equals(id)
                            ? gui.action(value)
                            : gui.answer(id, value);
                    if (!handled) {
                        System.err.println("[bridge] nothing accepted " + id + " = " + value);
                    }
                }
            } catch (Exception closed) {
                // stdin ending means the sidecar is done with us.
            }
        }, "bridge-answers");
        answers.setDaemon(true);
        answers.start();

        GameRules rules = new GameRules(type);
        rules.setGamesPerMatch(1);
        HostedMatch match = new HostedMatch();
        match.setEndGameHook(() -> System.err.println("[bridge] game over"));

        System.err.println("[bridge] starting match");
        match.startMatch(rules, EnumSet.noneOf(GameType.class), seats, seats.get(0), (IGuiGame) gui);

        // startMatch runs the game on Forge's own thread; wait for it to settle.
        // A human seat can sit thinking, so the wait is generous.
        long deadline = System.currentTimeMillis() + (human ? 7_200_000L : 600_000L);
        while (System.currentTimeMillis() < deadline) {
            forge.game.GameView view = gui.getGameView();
            if (view != null && view.isGameOver()) break;
            Thread.sleep(250);
        }

        String out = args.length > 2 && !"-".equals(args[2]) ? args[2] : null;
        if (out != null) {
            synchronized (transcript) { Files.write(Path.of(out), transcript); }
        }
        System.err.printf("[bridge] %d events%n", transcript.size());
        System.err.println("[bridge] prompts reached: " + gui.promptsSeen());
        System.exit(0);
    }

    /** Read a .dck from Forge's own profile folder. */
    private static Deck loadDeck(Path dir, String name) throws Exception {
        File file = dir.resolve(name).toFile();
        if (!file.exists()) {
            for (String sub : new String[] { "constructed", "commander" }) {
                File candidate = dir.resolve(sub).resolve(name).toFile();
                if (candidate.exists()) { file = candidate; break; }
            }
        }
        if (!file.exists()) throw new IllegalArgumentException("no such deck: " + name);
        Deck deck = forge.deck.io.DeckSerializer.fromFile(file);
        if (deck == null) throw new IllegalArgumentException("could not parse: " + file);
        return deck;
    }

}
