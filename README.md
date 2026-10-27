# Rauda — tareo y liquidación para operaciones de última milla

Sistema que reemplaza el cierre diario hecho en planillas para una operación de reparto: registra la
jornada de cada conductor, arma el tareo del día, calcula lo que hay que pagar y deja el cierre
firmado con autor y fecha.

Nació como un reemplazo de dos planillas de Excel y hoy corre en producción sobre una operación real
(decenas de registros por día, decenas de miles de filas de histórico). Este repositorio es el código
del sistema, sin datos de personas.

## El problema

En una operación de última milla, el día se cierra a mano: cada conductor manda por WhatsApp a qué
hora entró, a qué hora salió, cuántas vueltas hizo y cuántos pedidos entregó; alguien lo pasa a una
planilla; y de esa planilla sale el pago. En el camino aparecen los problemas de siempre:

- datos que se contradicen entre el chat y la planilla;
- documentos provisorios que impiden liquidar (y nadie sabe a quién pagarle);
- un día cargado dos veces, que paga el doble;
- correcciones hechas encima del dato original, sin rastro de quién las hizo.

El sistema hace tres cosas: **guarda cada mensaje**, **deriva el tareo con reglas explícitas** y
**cierra el día en un registro que no se puede reescribir en silencio**.

## Cómo está pensado

Decisiones que sostienen el resto y que están en el código, no en un documento aparte:

- **La regla de negocio vive en SQL, no en Python.** Tareo, tardanza, liquidación, resumen y los
  controles del día son vistas de PostgreSQL (`vistas_gestion.sql`). El panel solo consulta: la
  pantalla, el reporte y cualquier consulta a mano dan el mismo número.
- **El Excel no es el destino ni la salida: se elimina.** El día se cierra dentro del sistema, en
  `liquidaciones`, con autor y fecha. Si aparece la tentación de agregar "exportar a Excel", es señal
  de que el módulo todavía no resuelve el problema.
- **Nada se sobrescribe donde hay plata.** Tarifas, contratos y condiciones tienen período de
  vigencia: cada jornada liquida con la tarifa que regía ese día, y las correcciones retroactivas
  quedan registradas con el momento en que se supieron (bitemporalidad aplicada solo ahí, no como
  arquitectura total).
- **El sistema nunca completa una celda con un dato que no leyó.** Lo dudoso se marca; la corrección
  la hace una persona y queda visible en la fila, con autor y motivo. La regla se puede demostrar
  justamente porque la intervención humana se ve.
- **La identidad es una tabla, no una columna.** El identificador que llega por el canal no es
  confiable (documentos provisorios, claves repetidas, nombres cruzados): se resuelve contra una
  tabla de personas y, ante ambigüedad, **no se asigna: se manda a dudas**. El dato termina en la
  liquidación de una persona.
- **Dos puertas, dos públicos.** El panel de gestión y la aplicación del conductor se sirven en
  dominios distintos y se separan por la cabecera `Host`: en el dominio del conductor no existen las
  rutas del panel, y viceversa (404, no 403). El panel no comparte puerta con la gente de la calle.
- **El conductor no aprende un sistema nuevo.** La aplicación es un espejo del mensaje que ya mandaba:
  dos toques, sin texto libre y sin pasos nuevos. Funciona offline: cada marca se guarda en el
  teléfono antes de subir, y una clave generada por el propio dispositivo hace que el reintento no
  duplique.
- **El toque no define la plata.** Lo que se paga sale del cierre del día; las marcas sirven para el
  mapa y el control.

## Piezas

```
esquema*.sql          esquemas por migración (login, gestión, tarifas, conductor, cierre, paradas)
vistas_*.sql          vistas de negocio: tareo, liquidación, resumen, controles, mapa, piloto
gestion/              panel de gestión y API del conductor (FastAPI)
  app.py              rutas del panel: día, liquidación, mapa, revisión, usuarios, configuración
  conductor.py        API y puerta de la app del conductor
  auth.py             cuentas, sesiones, roles, intentos, auditoría
  configuracion.py    alta de clientes, tiendas con geocerca, conductores, tarifas con vigencia
  db.py               consultas: solo SELECT sobre las vistas
  exportar.py         reporte del período en Excel (tareo, resumen, excepciones)
  plantillas/         Jinja2, sin framework de front
  conductor_web/      PWA: css, js, service worker, manifest, iconos
lib/                  núcleo previo: cuaderno de mensajes, QA del día, informe
pruebas/              suite de pruebas + seeds de demostración
scripts/              respaldo, vigilante, limpieza, alta de cuentas y de operación
```

## Roles

- **Administración**: todo, incluidos usuarios y configuración.
- **Operaciones**: día a día y configuración (tiendas, conductores, tarifas), pero no crea cuentas.
- **Conductor**: solo su aplicación; el panel lo rebota. Entra con su documento y una contraseña que
  elige él la primera vez — la cuenta nace en el primer ingreso, no se precarga.

Nadie se cambia el rol a sí mismo, y un conductor no se "convierte" en administración: si pasa a la
oficina, se le crea una cuenta de gestión. El cierre del día y las correcciones se firman con la
cuenta de la sesión, no con un campo del formulario.

## Puesta en marcha

Requisitos: **Python 3.11+** y **PostgreSQL 14+**.

```bash
git clone https://github.com/scalabilitysystems-ui/rauda-tareo.git
cd rauda-tareo
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
cp .env.example .env      # completar host, base, usuario y clave
```

Base de datos, en este orden:

```bash
createdb rauda_dev
for f in esquema.sql esquema_gestion.sql esquema_login.sql esquema_tarifas.sql \
         esquema_conductor.sql esquema_cierre.sql esquema_cierre_tipos.sql \
         esquema_paradas.sql esquema_tolerancia.sql esquema_2026_10_19.sql \
         vistas_gestion.sql vistas_mapa.sql vistas_piloto.sql; do
  psql -d rauda_dev -f "$f"
done
```

El orden importa: `esquema.sql` crea el núcleo de captura, `esquema_gestion.sql` el cliente y la
operación, y las vistas van al final porque dependen de todo lo anterior.

Datos de demostración (gente inventada, con documento que empieza en `99` para no chocar con nadie
real) y arranque del panel:

```bash
./.venv/bin/python pruebas/seed_demo_gestion.py
./.venv/bin/uvicorn gestion.app:app --host 127.0.0.1 --port 8095
```

`/entrar` es la puerta del panel; `/conductor` la del conductor. En `/revision` hay un índice con el
día, la ficha, la liquidación, el mapa y la app del conductor en el orden en que conviene mirarlo.

## Pruebas

122 pruebas con `unittest`, contra PostgreSQL. Cada corrida **arma su propia fixture y la borra al
terminar**: las pruebas nunca escriben sobre la operación real ni dejan datos inventados al lado de
los del cliente.

```bash
./.venv/bin/python -m unittest discover -s pruebas -v
```

Cubren: puertas separadas por dominio, login y auditoría, la API del conductor, las vistas del día y
la liquidación, la captura de mensajes y el QA sobre el camino de la plata.

La base se puede levantar de cero solo con los esquemas de este repositorio: sobre una base vacía,
aplicando los `esquema*.sql` en el orden de arriba y corriendo el seed, las 122 pruebas pasan.

## Estado y alcance

- Corriendo en producción con una operación real, con respaldo diario cuya restauración se prueba y
  un vigilante que verifica la URL pública (no `localhost`: un túnel caído parece sano).
- **No incluye** app nativa (la ubicación se registra por marca, no con seguimiento continuo), ni
  cierre de quincena automático, ni aislamiento multi-cliente por RLS: están en el camino, no adentro.
- Los datos de este repositorio son de demostración. No hay nombres, documentos, teléfonos ni tarifas
  de personas reales.

---

Python · FastAPI · PostgreSQL · Jinja2 · SQL · PWA (service worker, offline) · Leaflet
