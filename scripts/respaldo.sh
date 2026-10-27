#!/usr/bin/env bash
# Respaldo de la base de Rauda, con verificación y retención.
#
# Por qué un script y no un `pg_dump` a mano: un respaldo que nadie probó no es un respaldo, es
# un archivo. Este hace tres cosas y falla ruidosamente si alguna no sale:
#   1. Vuelca la base comprimida.
#   2. Verifica que el archivo se puede LEER y contiene las tablas del circuito.
#   3. Borra los respaldos de más de 30 días (los últimos 7 diarios quedan siempre).
#
# Uso:  ./scripts/respaldo.sh          (lo llama el timer todos los días)
#       ./scripts/respaldo.sh --probar (además restaura en una base descartable)

set -euo pipefail

BASE="rauda_dev"
DESTINO="${RAUDA_RESPALDO_DIR:-/var/backups/rauda}"
RETENER_DIAS=30
RETENER_MINIMOS=7
FECHA="$(date +%Y-%m-%d_%H%M)"
ARCHIVO="$DESTINO/${BASE}_${FECHA}.dump"

mkdir -p "$DESTINO"
chmod 750 "$DESTINO"

echo "[$(date -Is)] respaldando $BASE -> $ARCHIVO"
# La salida se redirige desde ESTE shell (root): postgres no puede escribir en /var/backups.
sudo -u postgres pg_dump --format=custom --compress=9 "$BASE" > "$ARCHIVO"
chown postgres:postgres "$ARCHIVO"     # para que el propio postgres pueda restaurarlo

# --- verificación: el archivo tiene que listar sus tablas ---------------------
TABLAS=$(pg_restore --list "$ARCHIVO" | grep -c "TABLE DATA" || true)
if [ "$TABLAS" -lt 10 ]; then
    echo "FALLA: el respaldo solo tiene $TABLAS tablas con datos (se esperaban 20+)" >&2
    exit 1
fi
TAMANO=$(du -h "$ARCHIVO" | cut -f1)
echo "[$(date -Is)] ok: $TABLAS tablas con datos, $TAMANO"

# --- prueba de restauración opcional ----------------------------------------
if [ "${1:-}" = "--probar" ]; then
    PRUEBA="rauda_prueba_restauracion"
    echo "[$(date -Is)] restaurando en $PRUEBA"
    sudo -u postgres psql -q -c "DROP DATABASE IF EXISTS $PRUEBA" postgres
    sudo -u postgres createdb "$PRUEBA"
    sudo -u postgres pg_restore --no-owner --no-privileges --dbname="$PRUEBA" "$ARCHIVO" 2>/dev/null || true
    # `--no-privileges` deja la base sin permisos: sin este paso la app entra pero no puede leer
    # ninguna tabla. Es el detalle que hace que una restauración "exitosa" no arranque.
    sudo -u postgres psql -q -d "$PRUEBA" -c "GRANT USAGE ON SCHEMA public TO rauda_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO rauda_app;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO rauda_app;"
    FILAS=$(sudo -u postgres psql -tAq -d "$PRUEBA" -c "select count(*) from jornadas")
    CONDUCTORES=$(sudo -u postgres psql -tAq -d "$PRUEBA" -c "select count(*) from conductores")
    echo "[$(date -Is)] restaurado: $FILAS jornadas, $CONDUCTORES conductores"
    if [ "$FILAS" -lt 1 ] && [ "$CONDUCTORES" -lt 1 ]; then
        echo "FALLA: la restauración quedó vacía" >&2
        exit 1
    fi
    # Contar filas no alcanza: una base restaurada sin una columna que el código usa deja el
    # panel en 500. La prueba es que la app CONSULTE la base restaurada y responda.
    PRUEBA_LOG=$(mktemp)
    if ! (cd /home/ubuntu/rauda && RAUDA_DB_NAME="$PRUEBA" \
          ./.venv/bin/python - "$PRUEBA" <<'PYFIN' >"$PRUEBA_LOG" 2>&1
import sys
sys.path.insert(0, ".")
from gestion import db, auth
db.clientes()                 # la consulta que abre el panel
auth.cuentas()                # la que abre /usuarios
print("app ok")
PYFIN
    ); then
        echo "FALLA: la base restaurada no sirve para la aplicación:" >&2
        tail -5 "$PRUEBA_LOG" >&2
        rm -f "$PRUEBA_LOG"
        exit 1
    fi
    rm -f "$PRUEBA_LOG"
    echo "[$(date -Is)] la aplicación consulta la base restaurada sin errores"
    sudo -u postgres psql -q -c "DROP DATABASE $PRUEBA" postgres
    echo "[$(date -Is)] restauración probada y base de prueba eliminada"
fi

# --- retención --------------------------------------------------------------
# Los últimos N quedan siempre, aunque sean viejos.
ls -1t "$DESTINO"/${BASE}_*.dump 2>/dev/null | tail -n +$((RETENER_MINIMOS + 1)) | while read -r viejo; do
    if [ -n "$(find "$viejo" -mtime +$RETENER_DIAS 2>/dev/null)" ]; then
        rm -f "$viejo"
        echo "[$(date -Is)] borrado por antigüedad: $(basename "$viejo")"
    fi
done

echo "[$(date -Is)] listo. Respaldos en $DESTINO:"
ls -1t "$DESTINO"/${BASE}_*.dump | head -5
