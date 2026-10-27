#!/usr/bin/env bash
# Vigilante: si el panel o el túnel se caen, lo levanta y lo deja anotado.
#
# Un sistema que se cae un domingo y nadie se entera es peor que uno lento: el lunes el cliente
# ya perdió un día de tareo. Esto corre cada 2 minutos y hace tres cosas:
#   1. Pregunta por la dirección PÚBLICA (no por localhost): si el túnel está caído, se entera.
#   2. Si no contesta, reinicia el servicio que corresponde.
#   3. Vuelve a preguntar y anota el resultado en el journal (y en un archivo, para el informe).

set -uo pipefail

URL="${RAUDA_URL_PANEL:-https://panel.example.com/entrar}"
REGISTRO="/var/log/rauda-vigilante.log"
INTENTOS=3

anotar() { echo "[$(date -Is)] $*" | tee -a "$REGISTRO"; }

responde() {
    # 200 o 303 son sanos: la puerta contesta (303 es el redirect de "/dia" a "/entrar").
    local codigo
    codigo=$(curl -s -o /dev/null -m 10 -w "%{http_code}" "$URL" || echo 000)
    [ "$codigo" = "200" ] || [ "$codigo" = "303" ]
}

for intento in $(seq 1 $INTENTOS); do
    if responde; then
        [ "$intento" -gt 1 ] && anotar "volvió a responder en el intento $intento"
        exit 0
    fi
    sleep 5
done

anotar "NO responde después de $INTENTOS intentos: reiniciando"
systemctl restart rauda-panel
sleep 5
systemctl restart cloudflared-hermes
sleep 8

if responde; then
    anotar "levantado: panel y túnel reiniciados, responde"
    exit 0
fi

anotar "FALLA: sigue sin responder después del reinicio. Revisar a mano."
exit 1
