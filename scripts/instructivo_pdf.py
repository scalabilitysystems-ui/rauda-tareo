"""Genera el PDF del instructivo con la identidad de los documentos de Scalability.

Toma `INSTRUCTIVO.md` y produce un PDF A4 con el mismo lenguaje visual que la propuesta de Casa
Macio: fondo crema, barra naranja arriba en cada página, títulos en negrita condensada, tarjetas
arena y pie con el nombre del documento y el número de página.

Por qué un script y no un HTML a mano: el instructivo va a cambiar (precios, reglas, pantallas), y
regenerar tiene que ser un comando, no volver a maquetar.

Uso:
    ./.venv/bin/python scripts/instructivo_pdf.py
    ./.venv/bin/python scripts/instructivo_pdf.py --salida ~/Rauda-Instructivo.pdf
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

import markdown
import pymupdf
from pypdf import PdfReader, PdfWriter
from playwright.sync_api import sync_playwright

RAIZ = Path(__file__).resolve().parent.parent
MD = RAIZ / "INSTRUCTIVO.md"
LOGO = Path("/tmp/pdf/logo.png")

# Paleta tomada del PDF de Casa Macio (muestreada de la portada y de una página interior).
CREMA = "#F9F7E8"
ARENA = "#F0EDD3"
NARANJA = "#FB8201"
TINTA = "#141414"
GRIS = "#6F6A5A"
LINEA = "#D9D3BC"

TIPOGRAFIA = "'DejaVu Sans Condensed', 'DejaVu Sans', sans-serif"

CSS_BASE = f"""
  /* Chromium no pinta el fondo de @page: la hoja va con margen 0 y el fondo se pinta en el
     documento. La barra y el pie son `fixed`, así se repiten en cada página. */
  @page {{ size: A4; margin: 0; }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; background: {CREMA}; color: {TINTA}; }}
  body {{ font: 10.2pt/1.55 {TIPOGRAFIA}; -webkit-print-color-adjust: exact;
          print-color-adjust: exact; }}
  .barra {{ position: fixed; top: 0; left: 0; width: 210mm; height: 9mm;
            background: {NARANJA}; }}

  .contenido {{ padding: 0; }}
  .pagina {{ width: 210mm; height: 297mm; padding: 22mm 20mm 26mm; position: relative;
             overflow: hidden; break-after: page; box-sizing: border-box; }}
  .pagina:last-child {{ break-after: auto; }}
  h1, h2, h3 {{ letter-spacing: -.015em; margin: 0; break-after: avoid; }}
  p, li {{ orphans: 2; widows: 2; }}
  h2 {{ font-size: 19pt; font-weight: 700; margin: 0 0 7pt; }}
  h3 {{ font-size: 11.6pt; font-weight: 700; margin: 14pt 0 4pt; }}
  p {{ margin: 0 0 6.5pt; }}
  strong {{ font-weight: 700; }}
  em {{ color: {GRIS}; }}
  a {{ color: {TINTA}; text-decoration: none; border-bottom: .6pt solid {NARANJA}; }}
  ul, ol {{ margin: 0 0 7pt 0; padding-left: 13pt; }}
  li {{ margin: 0 0 3.4pt; }}
  hr {{ border: 0; border-top: .6pt solid {LINEA}; margin: 12pt 0; }}
  .eyebrow {{ font-size: 8.2pt; font-weight: 700; letter-spacing: .13em;
              text-transform: uppercase; color: {NARANJA}; margin: 0 0 3pt; }}
  .seccion {{ margin: 0 0 14pt; padding-top: 4pt; break-inside: auto; }}
  .seccion + .seccion {{ border-top: .6pt solid {LINEA}; padding-top: 12pt; }}
  blockquote {{ margin: 8pt 0 9pt; padding: 7pt 10pt; background: {ARENA};
                border-left: 2.6pt solid {NARANJA}; break-inside: avoid; }}
  blockquote p {{ margin: 0; font-size: 9.5pt; }}
  table {{ width: 100%; border-collapse: collapse; margin: 6pt 0 10pt; font-size: 9pt; }}
  thead {{ display: table-header-group; }}
  tr {{ break-inside: avoid; }}
  th {{ text-align: left; padding: 5pt 7pt; font-size: 7.8pt; font-weight: 700;
        letter-spacing: .09em; text-transform: uppercase; color: {GRIS};
        border-bottom: .9pt solid {TINTA}; }}
  td {{ padding: 5pt 7pt; border-bottom: .5pt solid {LINEA}; vertical-align: top; }}
  code {{ font-family: 'DejaVu Sans Mono', monospace; font-size: 8.6pt; background: {ARENA};
          padding: .6pt 3pt; border-radius: 2pt; }}
  .anexo {{ background: {ARENA}; padding: 12pt 14pt; margin-top: 12pt; break-inside: avoid; }}
  .anexo-bloque {{ break-inside: avoid; }}
  .anexo h2 {{ font-size: 13pt; }}
"""


def cuerpo_html() -> str:
    """Markdown → HTML con las secciones armadas como bloques con su número."""
    texto = MD.read_text()

    # Cada `## N. Título` se convierte en un bloque con el número en naranja arriba.
    partes = re.split(r"^## ", texto, flags=re.MULTILINE)
    intro, resto = partes[0], partes[1:]

    html = ['<section class="seccion">' + markdown.markdown(
        intro.replace("---", ""), extensions=["tables", "sane_lists"]) + "</section>"]

    for bloque in resto:
        titulo, _, cuerpo = bloque.partition("\n")
        numero = ""
        m = re.match(r"^(\d+)\.\s+(.*)$", titulo.strip())
        if m:
            numero, titulo = m.group(1).zfill(2), m.group(2)
        clase = ' class="anexo"' if numero == "" else ""
        encabezado = (f'<div class="eyebrow">{numero}</div>' if numero else "")
        interior = markdown.markdown(cuerpo, extensions=["tables", "sane_lists"])
        if clase:      # el anexo se mantiene entero
            interior = f'<div class="anexo-bloque">{interior}</div>'
        html.append(
            f'<section class="seccion"{clase}>{encabezado}<h2>{titulo}</h2>'
            + interior + "</section>")
    return "\n".join(html)


def portada_html() -> str:
    hoy = date.today()
    mes = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
           "septiembre", "octubre", "noviembre", "diciembre"][hoy.month - 1]
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><style>
      @page {{ size: A4; margin: 0; }}
      html, body {{ margin: 0; padding: 0; background: {CREMA};
                    font-family: {TIPOGRAFIA}; color: {TINTA};
                    -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
      .hoja {{ width: 210mm; height: 297mm; position: relative; overflow: hidden; }}
      .logo {{ position: absolute; top: 34mm; right: 16mm; width: 96mm; }}
      .texto {{ position: absolute; left: 22mm; right: 22mm; bottom: 46mm; }}
      .eyebrow {{ font-size: 9pt; font-weight: 700; letter-spacing: .13em;
                  text-transform: uppercase; }}
      .regla {{ width: 20mm; height: 2.2mm; background: {NARANJA}; margin: 3mm 0 7mm; }}
      h1 {{ font-size: 46pt; font-weight: 700; margin: 0 0 4mm; letter-spacing: -.03em;
            line-height: 1; }}
      .bajada {{ font-size: 14pt; margin: 0 0 5mm; }}
      .meta {{ font-size: 10pt; color: {GRIS}; }}
      .pie {{ position: absolute; left: 22mm; bottom: 18mm; font-size: 9.4pt; }}
      .pie strong {{ display: block; }}
      .pie span {{ color: {GRIS}; }}
    </style></head><body><div class="hoja">
      <img class="logo" src="file://{LOGO}" alt="">
      <div class="texto">
        <div class="eyebrow">Instructivo de uso</div>
        <div class="regla"></div>
        <h1>Rauda</h1>
        <p class="bajada">Control de la operación de última milla</p>
        <p class="meta">Panel de gestión y app del conductor · Lima, Perú</p>
      </div>
      <div class="pie"><strong>Scalability Digital</strong>
        <span>@scalability.digital · {mes.capitalize()} de {hoy.year}</span></div>
    </div></body></html>"""


def cuerpo_completo() -> str:
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
    <style>{CSS_BASE}</style></head><body>
    <div class="barra"></div>
    <div class="contenido" id="flujo">{cuerpo_html()}</div>
    </body></html>"""


# Reparte el contenido en hojas de 297 mm midiendo en el navegador. Sin esto, Chromium corta
# donde quiere y el texto se mete abajo del pie de página.
PAGINADOR = r"""
() => {
  const PX = 96 / 25.4;                       // px por mm a 96 dpi
  const ALTO = (297 - 22 - 26 - 6) * PX;      // alto útil, con 6 mm de holgura
  const flujo = document.getElementById('flujo');

  // 1. Agrupar en "átomos": un encabezado nunca se separa de lo que viene después.
  const grupos = [];
  let titulo = null;
  for (const seccion of [...flujo.children]) {
    for (const hijo of [...seccion.children]) {
      const esTitulo = /^H[23]$/.test(hijo.tagName) || hijo.classList.contains('eyebrow');
      if (esTitulo) { titulo = titulo || []; titulo.push(hijo); continue; }
      if (titulo) { titulo.push(hijo); grupos.push(titulo); titulo = null; }
      else grupos.push([hijo]);
    }
    // aire entre secciones
    const aire = document.createElement('div');
    aire.style.height = '9px';
    seccion.appendChild(aire);
    grupos.push([aire]);
  }
  if (titulo) grupos.push(titulo);

  // 2. Medir ANTES de mover nada (un nodo desprendido mide cero).
  // getBoundingClientRect NO incluye márgenes: sin sumarlos, el contenido se desborda de a poco
  // y en la última hoja termina pisando el pie.
  const altoDe = (n) => {
    const caja = n.getBoundingClientRect().height;
    const cs = getComputedStyle(n);
    return caja + parseFloat(cs.marginTop || 0) + parseFloat(cs.marginBottom || 0);
  };
  const alturas = grupos.map(g => g.reduce((suma, n) => suma + altoDe(n), 0) + 4);

  // 3. Repartir en hojas.
  let hoja = null, usado = 0, hojas = 0;
  const nuevaHoja = () => {
    hoja = document.createElement('div');
    hoja.className = 'pagina';
    document.body.appendChild(hoja);
    usado = 0;
    hojas += 1;
  };
  nuevaHoja();

  grupos.forEach((grupo, i) => {
    const alto = alturas[i];
    if (usado + alto > ALTO && usado > 0) nuevaHoja();
    for (const nodo of grupo) hoja.appendChild(nodo);
    usado += alto;
  });

  flujo.remove();
  return hojas;
}
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--salida", default=str(RAIZ / "Rauda-Instructivo.pdf"))
    args = parser.parse_args()

    if not MD.exists():
        print("falta INSTRUCTIVO.md", file=sys.stderr)
        return 1
    if not LOGO.exists():
        print(f"falta el logo en {LOGO}", file=sys.stderr)
        return 1

    tmp = Path("/tmp/pdf")
    tmp.mkdir(exist_ok=True)
    (tmp / "portada.html").write_text(portada_html())
    (tmp / "cuerpo.html").write_text(cuerpo_completo())

    with sync_playwright() as p:
        navegador = p.chromium.launch()
        pagina = navegador.new_page()

        pagina.goto((tmp / "portada.html").as_uri())
        pagina.pdf(path=str(tmp / "portada.pdf"), format="A4", print_background=True)
        print("portada ok")

        pagina.goto((tmp / "cuerpo.html").as_uri())
        paginas = pagina.evaluate(PAGINADOR)
        print(f"paginado: {paginas} páginas")
        pagina.pdf(path=str(tmp / "cuerpo.pdf"), format="A4", print_background=True)
        print("cuerpo ok")
        navegador.close()

    # La portada no lleva número; el cuerpo empieza en 1, como en los documentos de Scalability.
    documento = pymupdf.open(str(tmp / "cuerpo.pdf"))
    mm = 72 / 25.4
    gris = (0.44, 0.42, 0.35)
    for i, hoja in enumerate(documento, start=1):
        # línea fina + pie: izquierda el sello, centro el documento, derecha la página
        hoja.draw_line((20 * mm, (297 - 18) * mm), (190 * mm, (297 - 18) * mm),
                       color=(0.85, 0.83, 0.74), width=0.5)
        hoja.insert_text((20 * mm, (297 - 13) * mm), "Scalability Digital · @scalability.digital",
                         fontsize=7.4, fontname="helv", color=gris)
        hoja.insert_text((84 * mm, (297 - 13) * mm), "Instructivo para Rauda",
                         fontsize=7.4, fontname="helv", color=gris)
        hoja.insert_text((185 * mm, (297 - 13) * mm), str(i),
                         fontsize=7.4, fontname="helv", color=gris)
    documento.save(str(tmp / "cuerpo_numerado.pdf"))

    escritor = PdfWriter()
    for archivo in (tmp / "portada.pdf", tmp / "cuerpo_numerado.pdf"):
        for hoja in PdfReader(str(archivo)).pages:
            escritor.add_page(hoja)
    salida = Path(args.salida).expanduser()
    with open(salida, "wb") as f:
        escritor.write(f)
    print(f"listo: {salida} · {len(escritor.pages)} páginas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
