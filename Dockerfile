# Immutable official Python image, resolved for linux/amd64 on 2026-11-20.
FROM python:3.13.9-slim-bookworm@sha256:8cdd496996889eef0a8a7921c152c0a4a9f18a26c7a5e7257d3c90b431891894
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /bot
COPY --chmod=444 bot.py arena_map.py combat.py mechfight.py /bot/
USER 65534:65534
ENTRYPOINT ["python3", "-B", "-u", "/bot/bot.py"]
