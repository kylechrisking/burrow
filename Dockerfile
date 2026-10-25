FROM python:3.13-alpine

LABEL org.opencontainers.image.title="lab-dashboard" \
      org.opencontainers.image.description="A homelab dashboard that discovers your Proxmox + Docker lab" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LABDASH_PORT=8099
WORKDIR /app
COPY labdash ./labdash

# Runs as root by default so it can read a mounted /var/run/docker.sock.
# Without the socket, run it as any user: `user: "1000:1000"` in compose.
EXPOSE 8099
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://127.0.0.1:${LABDASH_PORT}/healthz || exit 1
CMD ["python", "-m", "labdash"]
