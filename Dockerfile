FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# Install dependencies
RUN apt-get update && apt-get install -y \
    locales git python3 python3-pip curl wget sudo zstd file libtinfo5 \
    gcc-aarch64-linux-gnu build-essential flex bison libssl-dev bc \
    device-tree-compiler cpio rsync gosu kmod chrpath diffstat gawk \
    universal-ctags \
    && rm -rf /var/lib/apt/lists/*

# Set locale
RUN locale-gen en_US.UTF-8
ENV LANG='en_US.UTF-8' LANGUAGE='en_US:en' LC_ALL='en_US.UTF-8'

# Allow pip to install globally
ENV PIP_BREAK_SYSTEM_PACKAGES=1

# Install Python dependencies + QGenie SDK
RUN pip3 install kas flask flask-socketio pyyaml eventlet \
    && pip3 install "qgenie-sdk[all]" -i https://devpi.qualcomm.com/qcom/dev/+simple --trusted-host devpi.qualcomm.com

# Expose the web port
EXPOSE 5000

# Create builder user
RUN useradd -m -s /bin/bash builder

WORKDIR /work
COPY entrypoint.sh /entrypoint.sh
COPY web_manager.py /web_manager.py
COPY editor_manager.py /work/editor_manager.py
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
