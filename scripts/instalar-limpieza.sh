#!/usr/bin/env bash
# Instala la limpieza de GPS como servicio semanal. Se copia el SQL a /etc/rauda porque el
# usuario `postgres` no puede leer dentro del home (750).
set -euo pipefail
install -d -m 755 /etc/rauda
install -m 644 /home/ubuntu/rauda/scripts/limpieza_gps.sql /etc/rauda/limpieza_gps.sql
install -m 644 /home/ubuntu/rauda/scripts/rauda-limpieza.service /etc/systemd/system/
install -m 644 /home/ubuntu/rauda/scripts/rauda-limpieza.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now rauda-limpieza.timer
echo "limpieza instalada. Prueba: sudo systemctl start rauda-limpieza.service"
