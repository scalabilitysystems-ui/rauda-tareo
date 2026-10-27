/* Mapa del día.
 *
 * Qué dibuja, y nada más: la tienda con su radio, el último punto de cada conductor y el
 * recorrido que se arma uniendo sus puntos en orden de hora.
 *
 * Qué NO dibuja: un vehículo moviéndose. La PWA no recibe ubicación con la pantalla apagada,
 * así que entre dos toques no se sabe dónde está. La línea es lo que marcó, no el camino.
 *
 * El mosaico del mapa es de OpenStreetMap y se pide de su servidor: quien abre el panel manda
 * su IP a OSM, como en cualquier mapa web.
 */
(() => {
  const datos = JSON.parse(document.getElementById('datos-mapa').textContent || '[]');
  const COLORES = ['#1f3a5f', '#0a7b48', '#b54708', '#7c3aed', '#b42318', '#0e7490', '#a16207'];

  const mapa = L.map('mapa', { zoomControl: true, attributionControl: true })
    .setView([-12.05, -77.04], 12);

  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; colaboradores de OpenStreetMap',
  }).addTo(mapa);

  const limites = [];

  const icono = (color, texto, activo) => L.divIcon({
    className: 'pin-conductor',
    html: `<span class="pin-circulo${activo ? ' activo' : ''}" style="--pin:${color}">${texto}</span>`,
    iconSize: [30, 30],
    iconAnchor: [15, 15],
  });

  const capasPorConductor = {};

  datos.forEach((c, i) => {
    const color = COLORES[i % COLORES.length];
    const capa = L.layerGroup().addTo(mapa);
    capasPorConductor[c.conductor_id] = capa;

    // --- la tienda y su radio ---
    if (c.tienda_lat && c.tienda_lon) {
      L.circle([c.tienda_lat, c.tienda_lon], {
        radius: c.radio_m || 150,
        color: '#6b7280', weight: 1, dashArray: '4 4',
        fillColor: '#6b7280', fillOpacity: 0.06,
      }).addTo(capa);
      L.marker([c.tienda_lat, c.tienda_lon], {
        icon: L.divIcon({
          className: 'pin-tienda',
          html: '<span class="pin-tienda-cuadro">' + (c.codigo_tienda || 'T') + '</span>',
          iconSize: [26, 26], iconAnchor: [13, 13],
        }),
      }).bindTooltip(`Tienda ${c.codigo_tienda || ''}`).addTo(capa);
      limites.push([c.tienda_lat, c.tienda_lon]);
    }

    // --- el recorrido ---
    const puntos = (c.puntos || []).filter((p) => p.lat !== null && p.lon !== null);
    if (puntos.length > 1) {
      L.polyline(puntos.map((p) => [p.lat, p.lon]), {
        color, weight: 3, opacity: 0.75,
      }).addTo(capa);
    }

    // --- cada punto, con su hora ---
    puntos.forEach((p, n) => {
      const esLlegada = p.tipo === 'llegada';
      if (esLlegada) {
        L.circleMarker([p.lat, p.lon], {
          radius: 8, color, weight: 2, fillColor: '#fff', fillOpacity: 1,
        }).bindPopup(`<strong>Llegó</strong><br>${hora(p.hora)}<br>${
          c.distancia_tienda_m !== null && c.distancia_tienda_m !== undefined
            ? Math.round(c.distancia_tienda_m) + ' m de la tienda' : ''}`).addTo(capa);
      } else {
        L.circleMarker([p.lat, p.lon], {
          radius: 5, color, weight: 2, fillColor: color, fillOpacity: 0.9,
        }).bindPopup(`<strong>${etiqueta(p.tipo)}</strong><br>${hora(p.hora)}<br>` +
                     `${n + 1}° punto del día`).addTo(capa);
      }
      limites.push([p.lat, p.lon]);
    });

    // --- el último punto, bien visible ---
    if (c.ultima_lat && c.ultima_lon) {
      L.marker([c.ultima_lat, c.ultima_lon], { icon: icono(color, iniciales(c.nombre), true) })
        .bindPopup(`<strong>${c.nombre}</strong><br>último punto: ${hora(c.ultima_hora)}<br>` +
                   `${c.entregas_tocadas} entregas marcadas`)
        .addTo(capa);
      limites.push([c.ultima_lat, c.ultima_lon]);
    }
  });

  if (limites.length) mapa.fitBounds(limites, { padding: [60, 60], maxZoom: 16 });
  else mapa.setView([-12.05, -77.04], 11);

  // Si todo lo del día cabe en menos de una cuadra (varios conductores en la misma tienda),
  // el encuadre automático deja los pines encimados: se fuerza una escala útil.
  const estirado = (puntos) => {
    const lats = puntos.map((p) => p[0]);
    const lons = puntos.map((p) => p[1]);
    return Math.max(Math.max(...lats) - Math.min(...lats),
                    Math.max(...lons) - Math.min(...lons));
  };
  if (limites.length > 1 && estirado(limites) < 0.004) {
    mapa.setView(limites[0], 17);
  }

  // --- la lista de la derecha apunta al conductor ---
  document.querySelectorAll('.conductor-mapa').forEach((li) => {
    li.addEventListener('click', () => {
      document.querySelectorAll('.conductor-mapa').forEach((o) => o.classList.remove('activo'));
      li.classList.add('activo');
      Object.entries(capasPorConductor).forEach(([id, capa]) => {
        const esEste = String(id) === li.dataset.conductor;
        if (esEste) capa.addTo(mapa); else mapa.removeLayer(capa);
      });
      if (li.dataset.lat) mapa.setView([Number(li.dataset.lat), Number(li.dataset.lon)], 15);
    });
  });

  function hora(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  }

  function etiqueta(tipo) {
    return { llegada: 'Llegó', entregado: 'Entregó', rechazado: 'Rechazado',
             vuelta_caida: 'Vuelta caída' }[tipo] || tipo;
  }

  function iniciales(nombre) {
    return (nombre || '?').split(' ').filter(Boolean).slice(0, 2)
      .map((p) => p[0]).join('');
  }
})();
