FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update -qq && apt-get install -y -qq git python3 python3-venv ca-certificates curl procps >/dev/null && rm -rf /var/lib/apt/lists/* \
 && useradd -m -s /bin/bash dev && git config --system --add safe.directory '*'
