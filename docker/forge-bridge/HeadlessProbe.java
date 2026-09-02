// Phase 0 of the practice bridge: does Forge's rules engine run a match with
// NO display at all?
//
// forge.game (878 classes) and forge.ai (261) reference java.awt and
// javax.swing exactly zero times, so in principle the engine never needed a
// screen -- only Forge's desktop LAUNCHER does, which is why the gauntlet
// wraps its simulations in xvfb-run to this day. This skips the launcher and
// calls the engine's own entry point behind a headless IGuiBase.
//
// A throwaway. If it prints a game result under -Djava.awt.headless=true with
// no X server anywhere, the bridge is buildable and Xvfb, x11vnc, websockify
// and noVNC can all eventually go. If it does not, the plan stops here.

import forge.gui.GuiBase;
import forge.gui.interfaces.IGuiBase;
import forge.model.FModel;
import forge.view.SimulateMatch;

import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;

public final class HeadlessProbe {

    /**
     * A do-nothing IGuiBase.
     *
     * IGuiBase has ~45 methods, nearly all of them about skins, card art,
     * audio and dialogs that a headless simulation never reaches. Rather than
     * stub each one by hand, answer them reflectively: the handful that must
     * behave get a real answer, every other call returns a harmless default.
     */
    private static IGuiBase headlessGui() {
        InvocationHandler handler = (proxy, method, args) -> {
            switch (method.getName()) {
                case "isRunningOnDesktop":
                    return Boolean.TRUE;
                case "isLibgdxPort":
                case "isGuiThread":
                case "hasNetGame":
                case "isSupportedAudioFormat":
                case "showBoxedProduct":
                    return Boolean.FALSE;
                case "getCurrentVersion":
                    return "2.0.14-bridge-probe";
                // Relative to the working directory, which is where Forge's
                // res/ tree with the 33,587 card scripts lives.
                case "getAssetsDir":
                    return "";
                case "getScreenScale":
                    return 1.0f;
                // No event dispatch thread exists: run the work where we are.
                case "invokeInEdtNow":
                case "invokeInEdtLater":
                case "invokeInEdtAndWait":
                    ((Runnable) args[0]).run();
                    return null;
                case "runBackgroundTask":
                    ((Runnable) args[1]).run();
                    return null;
                case "hostMatch":
                    return new forge.gamemodes.match.HostedMatch();
                default:
                    return defaultFor(method.getReturnType());
            }
        };
        return (IGuiBase) Proxy.newProxyInstance(
                HeadlessProbe.class.getClassLoader(),
                new Class<?>[] { IGuiBase.class },
                handler);
    }

    /** A proxy may not return null where a primitive is declared. */
    private static Object defaultFor(Class<?> type) {
        if (!type.isPrimitive()) return null;
        if (type == boolean.class) return Boolean.FALSE;
        if (type == int.class) return 0;
        if (type == long.class) return 0L;
        if (type == float.class) return 0f;
        if (type == double.class) return 0d;
        if (type == void.class) return null;
        return null;
    }

    public static void main(String[] args) throws Exception {
        System.out.println("[probe] java.awt.headless=" + System.getProperty("java.awt.headless"));
        System.out.println("[probe] DISPLAY=" + System.getenv("DISPLAY"));

        GuiBase.setInterface(headlessGui());
        System.out.println("[probe] gui interface set");

        long started = System.currentTimeMillis();
        FModel.initialize(null, null);
        System.out.printf("[probe] card database loaded in %d ms%n",
                System.currentTimeMillis() - started);

        System.out.println("[probe] simulating: " + String.join(" ", args));
        SimulateMatch.simulate(args);
        System.out.println("[probe] simulate() returned without throwing");
    }
}
