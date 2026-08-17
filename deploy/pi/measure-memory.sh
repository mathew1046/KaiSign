#!/usr/bin/env sh
set -eu

printf 'System memory:\n'
free -h
printf '\nBackend process RSS:\n'
ps -C python -C uvicorn -o pid,comm,rss,vsz,args --sort=-rss
printf '\nChromium process RSS:\n'
ps -C chromium-browser -C chromium -o pid,comm,rss,vsz,args --sort=-rss
