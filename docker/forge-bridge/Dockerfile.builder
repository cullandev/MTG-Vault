# Compiler image for the practice bridge.
#
# Built FROM the running forge image so the 300 MB Forge distribution is not
# downloaded a second time; adds a JDK, which the runtime image deliberately
# lacks (it ships a JRE and one Python script).
#
#   docker build -f docker/forge-bridge/Dockerfile.builder -t mtg-forge-builder .
#
# Used for two things: reading Forge's real method signatures with javap, and
# compiling the bridge against forge-gui-desktop's jar.

FROM mtg-forge:latest

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends openjdk-17-jdk-headless \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /work
