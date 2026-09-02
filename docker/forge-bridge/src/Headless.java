package bridge;

import forge.gamemodes.match.HostedMatch;
import forge.gui.interfaces.IGuiBase;
import forge.gui.interfaces.IGuiGame;

import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Proxy;

/**
 * A platform Forge can run on with no display.
 *
 * IGuiBase has ~45 methods and a headless game reaches almost none of them:
 * skins, card art, audio and dialogs are all unreachable. Answering them
 * reflectively keeps this to the six that must behave, rather than two hundred
 * lines of stubs that would each need reading.
 */
final class Headless {

    static IGuiBase gui(IGuiGame game) {
        InvocationHandler handler = (proxy, method, args) -> {
            switch (method.getName()) {
                case "isRunningOnDesktop": return Boolean.TRUE;
                case "isLibgdxPort":
                case "isGuiThread":
                case "hasNetGame":
                case "isSupportedAudioFormat":
                case "showBoxedProduct": return Boolean.FALSE;
                case "getCurrentVersion": return "2.0.14-bridge";
                // Relative to the working directory, where Forge's res/ tree
                // with the 33,587 card scripts lives.
                case "getAssetsDir": return "";
                case "getScreenScale": return 1.0f;
                // There is no event dispatch thread: run the work where we are.
                case "invokeInEdtNow":
                case "invokeInEdtLater":
                case "invokeInEdtAndWait": ((Runnable) args[0]).run(); return null;
                case "runBackgroundTask": ((Runnable) args[1]).run(); return null;
                case "getNewGuiGame": return game;
                case "hostMatch": return new HostedMatch();
                default: return defaultFor(method.getReturnType());
            }
        };
        return (IGuiBase) Proxy.newProxyInstance(
                Headless.class.getClassLoader(), new Class<?>[] { IGuiBase.class }, handler);
    }

    /** A proxy may not return null where a primitive is declared. */
    private static Object defaultFor(Class<?> type) {
        if (!type.isPrimitive()) return null;
        if (type == boolean.class) return Boolean.FALSE;
        if (type == int.class) return 0;
        if (type == long.class) return 0L;
        if (type == float.class) return 0f;
        if (type == double.class) return 0d;
        return null;
    }

    private Headless() {}
}
