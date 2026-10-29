# The experiment image with the libxml2 headers Uintah's configure requires.
FROM stretto-exp:24.04
USER root
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends libxml2-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
