FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# Install dependencies (Yocto + Upstream Kernel)
RUN apt-get update && apt-get install -y \
    locales git python3 python3-pip curl wget sudo zstd file libtinfo5 \
    gcc-aarch64-linux-gnu build-essential flex bison libssl-dev bc \
    device-tree-compiler cpio rsync gosu kmod chrpath diffstat gawk \
    && rm -rf /var/lib/apt/lists/*

# Set locale
RUN locale-gen en_US.UTF-8
ENV LANG='en_US.UTF-8' LANGUAGE='en_US:en' LC_ALL='en_US.UTF-8'

# Allow pip to install globally in newer Python versions
ENV PIP_BREAK_SYSTEM_PACKAGES=1

# Python dependencies
RUN pip3 install kas flask flask-socketio pyyaml eventlet

# Create builder user (standard uid, will be modified by entrypoint)
RUN useradd -m -s /bin/bash builder

# CRITICAL: We stay as ROOT here so entrypoint.sh can change UIDs.
# The entrypoint will switch to 'builder' user before running the app.
WORKDIR /work
COPY entrypoint.sh /entrypoint.sh
COPY web_manager.py /web_manager.py
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]