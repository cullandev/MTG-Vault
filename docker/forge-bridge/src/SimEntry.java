package bridge;

import forge.gui.GuiBase;
import forge.model.FModel;
import forge.view.SimulateMatch;

/**
 * Forge's batch simulator, without a display.
 *
 * `java -jar forge.jar sim ...` goes through forge.view.Main, which builds a
 * Swing UI before it reads its arguments -- so every gauntlet simulation has
 * run under xvfb-run since the sidecar was written. The engine itself never
 * needed a screen: forge.game (878 classes) and forge.ai (261) reference
 * java.awt and javax.swing exactly zero times.
 *
 * This calls the simulator directly behind the same headless IGuiBase the
 * bridge uses, so the batch path loses its display dependency and the output
 * the app parses is unchanged -- it is the same SimulateMatch writing it.
 */
public final class SimEntry {

    public static void main(String[] args) throws Exception {
        GuiBase.setInterface(Headless.gui(null));
        FModel.initialize(null, null);
        SimulateMatch.simulate(args);
        // The simulator leaves Forge's own threads running; the app is waiting
        // on this process, not on them.
        System.exit(0);
    }
}
