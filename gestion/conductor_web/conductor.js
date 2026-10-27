/* Panel del conductor.
 *
 * Dos ideas nomás:
 *   1. Cada toque se guarda PRIMERO en el celular (localStorage) y después se intenta subir.
 *      Así el conductor puede marcar sin señal y nada se pierde.
 *   2. La clave que se manda al servidor la genera el celular una sola vez por toque
 *      (`clave_cliente`). Si el envío se reintenta, la base lo ignora: un toque, una fila.
 *
 * El cierre es aparte: ahí el conductor DECLARA vueltas, pedidos, SAC y rechazados, y ese
 * número es el que se paga. Los toques no entran en esa cuenta.
 */
(() => {
  const API = '/api/conductor';
  const K = { token: 'rauda_token', nombre: 'rauda_nombre', cola: 'rauda_cola',
              estado: 'rauda_estado', vencida: 'rauda_vence' };

  const leer = (clave, porDefecto = null) => {
    try { return JSON.parse(localStorage.getItem(clave)) ?? porDefecto; } catch { return porDefecto; }
  };
  const guardar = (clave, valor) => localStorage.setItem(clave, JSON.stringify(valor));
  const borrar = (clave) => localStorage.removeItem(clave);

  let cola = leer(K.cola, []);
  // Si la app murió antes de que llegara la ubicación, ese ítem quedaba con `esperandoGeo` en
  // true para siempre y FRENABA toda la cola: el conductor marcaba y nada subía. Se destraba al
  // arrancar (se pierde el punto, no el toque; la hora queda).
  cola = cola.map((item) => ({ ...item, esperandoGeo: false }));
  guardar(K.cola, cola);
  let estado = leer(K.estado, null);
  let vista = 'dia';                       // dia | cierre
  const guardarCola = () => guardar(K.cola, cola);

  const token = () => leer(K.token);
  const salir = (borrarCola = false) => {
    [K.token, K.nombre, K.estado, K.vencida].forEach(borrar);
    if (borrarCola) borrar(K.cola);       // solo cuando la persona sale a propósito
    location.href = '/conductor';
  };

  async function pedir(ruta, opciones = {}) {
    const t = token();
    if (!t) { salir(); throw new Error('sin sesión'); }
    const r = await fetch(`${API}${ruta}`, {
      ...opciones,
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${t}`, ...(opciones.headers || {}) },
    });
    if (r.status === 401) {
      // La sesión venció. Se borra SOLO el token: los toques de la calle siguen guardados y
      // suben solos cuando vuelva a entrar. Antes se borraba la cola entera — se perdía el día.
      borrar(K.token);
      borrar(K.nombre);
      guardar(K.vencida, true);
      location.replace('/conductor?vencida=1');
      throw new Error('sesión vencida');
    }
    return r;
  }

  /* ---------- ubicación ---------- */
  function ubicacion() {
    return new Promise((resolver) => {
      if (!navigator.geolocation) return resolver(null);
      navigator.geolocation.getCurrentPosition(
        (p) => resolver({
          lat: Number(p.coords.latitude.toFixed(6)),
          lon: Number(p.coords.longitude.toFixed(6)),
          precision: Math.round(p.coords.accuracy),
        }),
        () => resolver(null),
        { enableHighAccuracy: true, timeout: 6000, maximumAge: 60000 }
      );
    });
  }

  /* ---------- cola ---------- */
  function encolar(tipo, datos, conUbicacion = true) {
    const clave = (crypto.randomUUID ? crypto.randomUUID() : `c${Date.now()}${Math.random()}`);
    const item = {
      clave,
      tipo,                        // llegada | carga | marca | cerrar
      datos: { ...datos, clave_cliente: clave },
      cuando: new Date().toISOString(),
      esperandoGeo: conUbicacion,
    };
    cola.push(item);
    guardarCola();
    return item;
  }

  async function enviar(item) {
    const cuerpo = { ...item.datos, hora_telefono: item.cuando };
    return pedir(`/${item.tipo}`, { method: 'POST', body: JSON.stringify(cuerpo) });
  }

  let enviando = false;
  async function vaciarCola() {
    if (enviando) return;
    enviando = true;
    try {
      while (cola.length) {
        const item = cola[0];
        if (item.esperandoGeo) break;          // respeta el orden: primero la ubicación
        let r;
        try {
          r = await enviar(item);
        } catch (e) {
          break;                                // sin red: se queda en el celular
        }
        if (r.status === 409) {                 // no abrió el día (o ya lo cerró la admin)
          cola.shift(); guardarCola();
          continue;
        }
        if (!r.ok) break;
        try { estado = await r.json(); guardar(K.estado, estado); } catch (e) { /* sin cuerpo */ }
        cola.shift();
        guardarCola();
      }
    } finally {
      enviando = false;
      pintar();
    }
  }

  function accion(tipo, datos = {}) {
    const item = encolar(tipo, datos);
    pintar();
    ubicacion().then((u) => {
      if (u) item.datos = { ...item.datos, ...u };
      item.esperandoGeo = false;
      guardarCola();
      vaciarCola();
    });
  }

  function cerrarDia(datos) {
    encolar('cerrar', datos, false);
    vista = 'dia';
    pintar();
    vaciarCola();
  }

  /* ---------- números para la pantalla ---------- */
  function cuentas() {
    const base = (estado && estado.toques) || {};
    const cuenta = {
      entregados: base.entregados || 0,
      rechazados: base.rechazados || 0,
      vueltas_caidas: base.vueltas_caidas || 0,
      pendientes: 0,
      llegadaPendiente: false,
      cierrePendiente: null,
    };
    cola.forEach((i) => {
      if (i.tipo === 'llegada') cuenta.llegadaPendiente = true;
      if (i.tipo === 'cerrar') { cuenta.cierrePendiente = i.datos; return; }
      if (i.tipo === 'marca') {
        cuenta[i.datos.tipo] = (cuenta[i.datos.tipo] || 0) + 1;
        cuenta.pendientes += 1;
      }
      if (i.tipo === 'carga') cuenta.pendientes += 1;
    });
    return cuenta;
  }

  const hhmm = (iso) => {
    if (!iso) return '';
    const d = new Date(iso);
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  };

  function cargados() {
    const pendiente = cola.find((i) => i.tipo === 'llegada');
    if (pendiente) return pendiente.datos.pedidos;
    return (estado && estado.pedidos_cargados) || 0;
  }

  const numero = (valor) => (valor === null || valor === undefined ? 0 : Number(valor));

  /* ---------- pantalla: cerrar el día ---------- */
  function pantallaCierre() {
    const c = cuentas();
    const d = (estado && estado.declarado) || {};
    const vueltas = Math.max(1, (estado && estado.cargas && estado.cargas.length) || 1);
    const pedidos = numero(d.pedidos_regulares) || c.entregados;
    const sac = numero(d.sac);
    const rechazados = numero(d.rechazados) || c.rechazados;
    const caidas = numero(d.vueltas_caidas) || c.vueltas_caidas;

    return `
      <div class="tarjeta">
        <h2>Cerrar el día</h2>
        <p>Esto es lo que se cobra. Revisá los números antes de confirmar.</p>

        <div class="etiqueta" style="margin-top:16px">Vueltas</div>
        <div class="campo-pedidos">
          <button class="paso" type="button" data-paso="vueltas" data-delta="-1">−</button>
          <input id="f-vueltas" type="number" inputmode="numeric" min="1" max="50" value="${vueltas}">
          <button class="paso" type="button" data-paso="vueltas" data-delta="1">+</button>
        </div>

        <div class="etiqueta" style="margin-top:16px">Pedidos entregados (sin contar SAC)</div>
        <div class="campo-pedidos">
          <button class="paso" type="button" data-paso="pedidos" data-delta="-1">−</button>
          <input id="f-pedidos" type="number" inputmode="numeric" min="0" max="1000" value="${pedidos}">
          <button class="paso" type="button" data-paso="pedidos" data-delta="1">+</button>
        </div>

        <div class="fila-botones" style="margin-top:16px">
          <label style="flex:1">
            <div class="etiqueta">SAC</div>
            <input id="f-sac" type="number" inputmode="numeric" min="0" value="${sac}" class="campo-suelto">
          </label>
          <label style="flex:1">
            <div class="etiqueta">Rechazados</div>
            <input id="f-rechazados" type="number" inputmode="numeric" min="0" value="${rechazados}" class="campo-suelto">
          </label>
        </div>

        <div class="fila-botones" style="margin-top:12px">
          <label style="flex:1">
            <div class="etiqueta">Vueltas caídas</div>
            <input id="f-caidas" type="number" inputmode="numeric" min="0" value="${caidas}" class="campo-suelto">
          </label>
          <div style="flex:1"></div>
        </div>

        <div class="aviso ok" id="resumen" style="margin-top:16px"></div>

        <button class="boton-principal" id="confirmar">CONFIRMAR CIERRE</button>
        <button class="boton-secundario" id="volver" style="width:100%">Volver sin cerrar</button>
      </div>`;
  }

  function engancharCierre(raiz) {
    const valor = (id) => Math.max(0, Number(document.getElementById(id).value || 0));
    const resumen = document.getElementById('resumen');
    const pintarResumen = () => {
      const sac = valor('f-sac');
      resumen.innerHTML = `<strong>${valor('f-vueltas')} vueltas · ${valor('f-pedidos')} pedidos${
        sac ? ` + ${sac} SAC = ${valor('f-pedidos') + sac}` : ''}</strong>${
        valor('f-rechazados') ? ` · ${valor('f-rechazados')} rechazados` : ''}${
        valor('f-caidas') ? ` · ${valor('f-caidas')} vuelta(s) caída(s)` : ''}`;
    };
    pintarResumen();

    raiz.querySelectorAll('.paso').forEach((b) => b.addEventListener('click', () => {
      const input = document.getElementById(`f-${b.dataset.paso}`);
      input.value = Math.max(0, Number(input.value || 0) + Number(b.dataset.delta));
      pintarResumen();
    }));
    ['f-vueltas', 'f-pedidos', 'f-sac', 'f-rechazados', 'f-caidas']
      .forEach((id) => document.getElementById(id).addEventListener('input', pintarResumen));

    document.getElementById('volver').addEventListener('click', () => { vista = 'dia'; pintar(); });
    document.getElementById('confirmar').addEventListener('click', () => {
      cerrarDia({
        vueltas: Math.max(1, valor('f-vueltas')),
        pedidos: valor('f-pedidos'),
        sac: valor('f-sac'),
        rechazados: valor('f-rechazados'),
        vueltas_caidas: valor('f-caidas'),
      });
    });
  }

  /* ---------- pintar ---------- */
  function pintar() {
    const raiz = document.getElementById('app');
    if (!raiz) return;

    document.getElementById('nombre').textContent = leer(K.nombre, '') || '';
    document.getElementById('dia').textContent =
      new Date().toLocaleDateString('es-PE', { weekday: 'long', day: 'numeric', month: 'long' });

    const red = document.getElementById('red');
    const enLinea = navigator.onLine;
    red.className = `estado-red ${enLinea ? 'online' : 'offline'}`;
    red.innerHTML = `<span class="punto"></span>${enLinea ? 'en línea' : 'sin conexión'}`;

    const c = cuentas();
    const jornada = estado && estado.jornada;
    const abierto = Boolean(jornada) || c.llegadaPendiente;
    const cerrado = Boolean((estado && estado.cerrada) || c.cierrePendiente);

    if (vista === 'cierre' && abierto) {
      raiz.innerHTML = pantallaCierre();
      engancharCierre(raiz);
      return;
    }

    if (!abierto) {
      raiz.innerHTML = `
        <div class="tarjeta">
          <h2>Empezar el día</h2>
          <p>Cuando llegues a la tienda, toca el botón. Pedimos la ubicación para saber que llegaste.</p>
          <div class="etiqueta" style="margin-top:16px">¿Cuántos pedidos llevás?</div>
          <div class="campo-pedidos">
            <button class="paso" type="button" data-paso="-5">−</button>
            <input id="pedidos" type="number" inputmode="numeric" min="0" max="500" value="20">
            <button class="paso" type="button" data-paso="5">+</button>
          </div>
          <button class="boton-principal" id="llegue">YA LLEGUÉ</button>
          <p style="font-size:12.5px;color:#9ca3af;text-align:center">
            Si no hay señal, el toque queda guardado y sube solo después.
          </p>
        </div>`;
      raiz.querySelectorAll('.paso').forEach((b) => b.addEventListener('click', () => {
        const input = document.getElementById('pedidos');
        input.value = Math.max(0, Number(input.value || 0) + Number(b.dataset.paso));
      }));
      document.getElementById('llegue').addEventListener('click', () => {
        const pedidos = Math.max(0, Number(document.getElementById('pedidos').value || 0));
        accion('llegada', { pedidos });
      });
      return;
    }

    const ubi = jornada && jornada.estado_ubicacion;
    const avisoUbi = c.llegadaPendiente
      ? `<div class="aviso ojo"><strong>Llegada guardada en el celular</strong>Sube sola cuando vuelva la señal.</div>`
      : ubi === 'lejos'
        ? `<div class="aviso ojo"><strong>Marcaste lejos de la tienda</strong>Queda registrado así; si fue un error, avisale a la oficina.</div>`
        : ubi === 'sin_ubicacion'
          ? `<div class="aviso ojo"><strong>Sin ubicación</strong>Quedó la hora, pero no el punto. Si puedes, activa el GPS del celular.</div>`
          : `<div class="aviso ok"><strong>Llegaste a las ${hhmm(jornada && jornada.abierta_en)}</strong>${
              jornada && jornada.distancia_tienda_m !== null && jornada.distancia_tienda_m !== undefined
                ? `A ${Math.round(jornada.distancia_tienda_m)} m de la tienda.` : ''}</div>`;

    const declarado = (estado && estado.declarado) || {};

    if (cerrado) {
      const fuente = c.cierrePendiente || declarado;
      const pedidos = numero(c.cierrePendiente ? fuente.pedidos : fuente.pedidos_regulares);
      const sac = numero(fuente.sac);
      const vueltas = numero(fuente.vueltas);
      const rechazados = numero(fuente.rechazados);
      raiz.innerHTML = `
        ${c.cierrePendiente ? `<div class="aviso ojo"><strong>Cierre guardado en el celular</strong>Sube cuando vuelva la señal.</div>` : ''}
        <div class="tarjeta">
          <h2>Día cerrado</h2>
          <div class="dato-grande" style="margin:6px 0 2px">${pedidos + sac} pedidos</div>
          <p>${vueltas} vuelta(s)${sac ? ` · ${pedidos} regulares + ${sac} SAC` : ''}${
            rechazados ? ` · ${rechazados} rechazados` : ''}</p>
          <p style="margin-top:10px">Eso es lo que queda declarado para la liquidación. Si algo está mal, corregilo ahora.</p>
        </div>
        <div class="fila-datos" style="margin-bottom:14px">
          <div><div class="etiqueta">Tocaste</div><div class="dato-medio">${c.entregados}</div></div>
          <div><div class="etiqueta">Entrada</div><div class="dato-medio">${hhmm(jornada && jornada.abierta_en)}</div></div>
          <div><div class="etiqueta">Salida</div><div class="dato-medio">${hhmm(jornada && jornada.cerrada_en)}</div></div>
        </div>
        <button class="boton-secundario" id="corregir" style="width:100%">Corregir el cierre</button>
        <p class="nota">Después de que la administración cierre el día, ya no se puede cambiar.</p>`;
      document.getElementById('corregir').addEventListener('click', () => { vista = 'cierre'; pintar(); });
      return;
    }

    raiz.innerHTML = `
      ${avisoUbi}
      <div class="fila-datos" style="margin-bottom:14px">
        <div>
          <div class="etiqueta">Cargados</div>
          <div class="dato-medio">${cargados()}</div>
        </div>
        <div>
          <div class="etiqueta">Entregados</div>
          <div class="dato-medio">${c.entregados}</div>
        </div>
        <div>
          <div class="etiqueta">Te quedan</div>
          <div class="dato-medio">${Math.max(0, cargados() - c.entregados)}</div>
        </div>
      </div>

      <button class="boton-principal" id="entregue">ENTREGUÉ UN PEDIDO</button>

      <div class="fila-botones">
        <button class="boton-secundario rojo" id="rechazado">Rechazado</button>
        <button class="boton-secundario ambar" id="caida">Vuelta caída</button>
      </div>

      <div class="tarjeta" id="motivos" hidden>
        <h2>¿Por qué no se entregó?</h2>
        <p>Con esto la oficina sabe qué pasa en la calle. Si no sabes, toca "Otro".</p>
        <div class="fila-botones" style="flex-wrap:wrap;margin-top:12px">
          <button class="boton-chico" data-motivo="no_habia_quien_reciba">No había quién reciba</button>
          <button class="boton-chico" data-motivo="direccion_erronea">Dirección errónea</button>
          <button class="boton-chico" data-motivo="rechazado_por_cliente">Lo rechazó el cliente</button>
          <button class="boton-chico" data-motivo="fuera_de_horario">Fuera de horario</button>
          <button class="boton-chico" data-motivo="otro">Otro</button>
        </div>
      </div>

      <div class="tarjeta">
        <h2>¿Volviste a la tienda?</h2>
        <p>Si cargaste más pedidos, súmalos aquí.</p>
        <div class="campo-pedidos">
          <input id="mas" type="number" inputmode="numeric" min="0" max="500" value="10">
          <button class="boton-chico" id="cargar">Cargo más</button>
        </div>
      </div>

      ${c.pendientes ? `<div class="aviso ojo"><strong>${c.pendientes} toque(s) esperando subir</strong>Se mandan solos cuando vuelva la señal.</div>` : ''}

      <button class="boton-secundario" id="alcierre" style="width:100%;padding:18px">
        TERMINÉ EL DÍA · CERRAR
      </button>
      <p class="nota">
        Los toques son para el seguimiento del día. Lo que se cobra es lo que declares en el cierre.
      </p>`;

    document.getElementById('entregue').addEventListener('click', () => {
      accion('marca', { tipo: 'entregado' });
    });
    document.getElementById('rechazado').addEventListener('click', () => {
      const panel = document.getElementById('motivos');
      panel.hidden = !panel.hidden;
      if (!panel.hidden) panel.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
    raiz.querySelectorAll('[data-motivo]').forEach((b) => b.addEventListener('click', () => {
      accion('marca', { tipo: 'rechazado', motivo: b.dataset.motivo });
      document.getElementById('motivos').hidden = true;
    }));
    document.getElementById('caida').addEventListener('click', () => {
      accion('marca', { tipo: 'vuelta_caida' });
    });
    document.getElementById('cargar').addEventListener('click', () => {
      const pedidos = Math.max(0, Number(document.getElementById('mas').value || 0));
      if (pedidos > 0) accion('carga', { pedidos });
    });
    document.getElementById('alcierre').addEventListener('click', () => { vista = 'cierre'; pintar(); });
  }

  /* ---------- arranque ---------- */
  async function sincronizar() {
    if (!navigator.onLine || !token()) return;
    try {
      const r = await pedir('/hoy');
      if (r.ok) { estado = await r.json(); guardar(K.estado, estado); }
    } catch (e) { /* sin red: se muestra lo último guardado */ }
    pintar();
    vaciarCola();
  }

  window.addEventListener('online', () => { pintar(); sincronizar(); });
  window.addEventListener('offline', pintar);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') sincronizar();
  });

  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/conductor/sw.js', { scope: '/conductor/' }).catch(() => {});
  }

  pintar();
  sincronizar();
})();
