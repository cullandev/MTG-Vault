package bridge;

import java.util.Collection;
import java.util.Map;

/**
 * A JSON writer, because Forge bundles Netty but not Gson.
 *
 * Deliberately tiny: the bridge emits a fixed set of shapes and never parses,
 * so this needs to escape correctly and nothing else. The Python shim next
 * door hand-writes its JSON for the same reason.
 */
public final class Json {

    private final StringBuilder out = new StringBuilder(256);
    private boolean needsComma = false;

    public static Json object() {
        Json json = new Json();
        json.out.append('{');
        return json;
    }

    public Json put(String key, String value) {
        return raw(key, value == null ? "null" : quote(value));
    }

    public Json put(String key, long value) {
        return raw(key, Long.toString(value));
    }

    public Json put(String key, boolean value) {
        return raw(key, Boolean.toString(value));
    }

    public Json put(String key, Json nested) {
        return raw(key, nested == null ? "null" : nested.toString());
    }

    /** A list of already-rendered JSON values. */
    public Json putRawArray(String key, Collection<String> values) {
        StringBuilder array = new StringBuilder("[");
        boolean first = true;
        for (String value : values) {
            if (!first) array.append(',');
            array.append(value);
            first = false;
        }
        return raw(key, array.append(']').toString());
    }

    /** A list of strings. */
    public Json putStrings(String key, Collection<String> values) {
        StringBuilder array = new StringBuilder("[");
        boolean first = true;
        for (String value : values) {
            if (!first) array.append(',');
            array.append(quote(value));
            first = false;
        }
        return raw(key, array.append(']').toString());
    }

    public Json putCounts(String key, Map<String, Integer> counts) {
        Json nested = Json.object();
        for (Map.Entry<String, Integer> entry : counts.entrySet()) {
            nested.put(entry.getKey(), entry.getValue().longValue());
        }
        return put(key, nested);
    }

    private Json raw(String key, String rendered) {
        if (needsComma) out.append(',');
        out.append(quote(key)).append(':').append(rendered);
        needsComma = true;
        return this;
    }

    @Override
    public String toString() {
        return out + "}";
    }

    /**
     * Escape a JSON string.
     *
     * Card names carry apostrophes, accents and the occasional quote --
     * Kraum, Ludevic's Opus; Glóin the Mighty -- so this is not decoration.
     */
    public static String quote(String value) {
        StringBuilder buffer = new StringBuilder(value.length() + 2).append('"');
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            switch (c) {
                case '"':  buffer.append("\\\""); break;
                case '\\': buffer.append("\\\\"); break;
                case '\n': buffer.append("\\n"); break;
                case '\r': buffer.append("\\r"); break;
                case '\t': buffer.append("\\t"); break;
                default:
                    if (c < 0x20) buffer.append(String.format("\\u%04x", (int) c));
                    else buffer.append(c);
            }
        }
        return buffer.append('"').toString();
    }
}
