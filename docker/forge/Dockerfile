# Forge battle sidecar (ADR-031, tier 2).
#
# Forge is the mature open-source MTG rules engine. This image wraps it behind a
# tiny HTTP shim so the app can request real games without a Java toolchain or a
# docker socket. Opt-in via the "battles" compose profile -- the default stack
# never starts it.
#
# There is no display in this image, and no desktop. Forge's DESKTOP LAUNCHER
# built a Swing UI before it parsed its arguments, which is the only reason a
# simulation ever needed one; the engine itself never did -- forge.game (878
# classes) and forge.ai (261) reference java.awt and javax.swing exactly zero
# times. Everything goes through the bridge now (docker/forge-bridge), which
# calls the engine directly behind a headless IGuiBase.

FROM eclipse-temurin:17-jre-jammy AS runtime

ARG FORGE_VERSION=2.0.14

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 bzip2 curl \
    && rm -rf /var/lib/apt/lists/*

# The installer tarball carries the desktop jar and every card script.
RUN curl -fL -o /tmp/forge.tar.bz2 \
        "https://github.com/Card-Forge/forge/releases/download/forge-${FORGE_VERSION}/forge-installer-${FORGE_VERSION}.tar.bz2" \
    && mkdir -p /opt/forge \
    && tar -xjf /tmp/forge.tar.bz2 -C /opt/forge \
    && rm /tmp/forge.tar.bz2

# Kept as a separate layer so changing it never re-downloads the 300 MB tarball.
# fontconfig and the freetype/X client libraries stay even though nothing draws:
# the JVM links them for font metrics under java.awt.headless=true. Xvfb,
# x11vnc, websockify and noVNC are gone with the desktop they carried.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fontconfig libfreetype6 libxrender1 libxtst6 libxi6 libxext6 \
    && rm -rf /var/lib/apt/lists/*

COPY docker/forge/server.py /opt/forge-sim/server.py


# -- the practice bridge -----------------------------------------------------
#
# Forge's engine drives any presentation through IGuiGame; the bridge is an
# implementation whose screen is a stream of JSON (docker/forge-bridge). It
# needs a compiler, which the runtime image deliberately lacks, so it is built
# in a throwaway JDK stage and only the jar is kept.

FROM eclipse-temurin:17-jdk-jammy AS bridge-build
COPY --from=runtime /opt/forge /opt/forge
COPY docker/forge-bridge/src /src
RUN set -eu; \
    JAR="$(ls /opt/forge/forge-gui-desktop-*jar-with-dependencies.jar | head -1)"; \
    mkdir -p /out; \
    javac -nowarn -cp "$JAR" -d /out /src/*.java; \
    jar cf /bridge.jar -C /out .

FROM runtime AS final
COPY --from=bridge-build /bridge.jar /opt/forge-sim/bridge.jar

# Forge writes caches and profiles under HOME; keep them on the data volume.
ENV HOME=/data \
    FORGE_DIR=/opt/forge \
    FORGE_HEAP=4g \
    FORGE_TIMEOUT_S=900 \
    FORGE_VERSION=${FORGE_VERSION}

# A JVM chewing on deck text the app composed has no business being root; same
# uid as the app image so files on the shared volume stay mutually readable.
RUN useradd --uid 10001 --no-create-home forge \
    && chown -R 10001 /opt/forge
USER 10001

EXPOSE 8080
CMD ["python3", "/opt/forge-sim/server.py"]
