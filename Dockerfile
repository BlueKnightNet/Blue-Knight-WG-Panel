FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    golang \
    curl \
    ca-certificates \
    tar \
    build-essential \
    pkg-config \
    libmnl-dev \
    iproute2 \
    iputils-ping \
    iptables \
    qrencode \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .

# Build pinned AmneziaWG binaries
RUN mkdir -p /opt/diana-awg/{go,tools,bin} && \
    curl -fsSL https://codeload.github.com/amnezia-vpn/amneziawg-go/tar.gz/b5928efb6ca19f0153958460c3d141f04abc5c2e | tar -xz --strip-components=1 -C /opt/diana-awg/go && \
    curl -fsSL https://codeload.github.com/amnezia-vpn/amneziawg-tools/tar.gz/ee0f0a9aa34ff0a0da4b3433b9512781cfe02843 | tar -xz --strip-components=1 -C /opt/diana-awg/tools && \
    (cd /opt/diana-awg/go && go build -trimpath -o /opt/diana-awg/bin/amneziawg-go .) && \
    make -C /opt/diana-awg/tools/src -j2 && \
    cp /opt/diana-awg/tools/src/wg /opt/diana-awg/bin/awg

# Run unit tests and bash syntax validation
RUN python3 -m unittest -v test_panel.py test_local_app.py && \
    bash -n install.sh update.sh awg-install.sh test_awg_linux.sh

# Run the real AmneziaWG tunnel smoke test
CMD ["bash", "test_awg_linux.sh", "/opt/diana-awg/bin"]
