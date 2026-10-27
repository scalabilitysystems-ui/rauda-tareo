"""
Genera los iconos del panel del conductor (PNG) sin depender de Pillow.

Se dibuja un cuadrado redondeado azul oscuro con una "R" blanca hecha a mano con
rectángulos: alcanza para que el teléfono muestre algo decente al instalarlo.

Uso: ~/rauda/.venv/bin/python ~/rauda/gestion/conductor_web/iconos.py
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

FONDO = (17, 24, 39)      # #111827
TINTA = (255, 255, 255)
SALIDA = Path(__file__).resolve().parent


def _png(ancho: int, alto: int, pixeles: bytes) -> bytes:
    def chunk(tipo: bytes, datos: bytes) -> bytes:
        return (struct.pack(">I", len(datos)) + tipo + datos
                + struct.pack(">I", zlib.crc32(tipo + datos) & 0xFFFFFFFF))

    filas = b"".join(b"\x00" + pixeles[y * ancho * 3:(y + 1) * ancho * 3] for y in range(alto))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", ancho, alto, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(filas, 9))
            + chunk(b"IEND", b""))


def dibujar(lado: int) -> bytes:
    px = bytearray()
    radio = lado * 0.22
    # geometría de la R, en fracciones del lado
    trazos = [
        (0.34, 0.26, 0.44, 0.74),   # palo vertical
        (0.44, 0.26, 0.62, 0.33),   # lomo arriba
        (0.56, 0.33, 0.62, 0.46),   # lomo derecho
        (0.44, 0.46, 0.62, 0.53),   # lomo abajo
        (0.50, 0.53, 0.62, 0.74),   # pierna
    ]

    for y in range(lado):
        for x in range(lado):
            # esquinas redondeadas
            cx = min(max(x, radio), lado - radio)
            cy = min(max(y, radio), lado - radio)
            dentro = (x - cx) ** 2 + (y - cy) ** 2 <= radio ** 2
            color = FONDO
            if dentro:
                fx, fy = x / lado, y / lado
                for x0, y0, x1, y1 in trazos:
                    if x0 <= fx <= x1 and y0 <= fy <= y1:
                        color = TINTA
                        break
            else:
                color = (0, 0, 0)
            px += bytes(color)
    return _png(lado, lado, bytes(px))


if __name__ == "__main__":
    for lado in (192, 512):
        (SALIDA / f"icono-{lado}.png").write_bytes(dibujar(lado))
        print("icono", lado, "ok")
