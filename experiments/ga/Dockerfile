# The experiment image with what an autotools build of Global Arrays from its git tree needs
# (autoreconf: autoconf, automake, libtool) and gdb for hangs.
FROM stretto-exp:24.04
USER root
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      autoconf automake libtool m4 file gdb \
    && rm -rf /var/lib/apt/lists/*
