#!/usr/bin/env node
// generar_png.js - genera un PNG solido de 1920x462 (sin dependencias) para pruebas.
// Uso: node generar_png.js <salida.png> [r g b]      (0-255)
const fs = require('fs');
const zlib = require('zlib');

const salida = process.argv[2] || 'prueba.png';
const r = Number(process.argv[3] ?? 255);
const g = Number(process.argv[4] ?? 0);
const b = Number(process.argv[5] ?? 255);
const ANCHO = 1920, ALTO = 462;

const tabla = (() => {
  const t = new Int32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c;
  }
  return t;
})();
function crc32(buf) {
  let c = 0xffffffff;
  for (const byte of buf) c = tabla[(c ^ byte) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}
function trozo(tipo, datos) {
  const largo = Buffer.alloc(4);
  largo.writeUInt32BE(datos.length);
  const cuerpo = Buffer.concat([Buffer.from(tipo, 'ascii'), datos]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(cuerpo));
  return Buffer.concat([largo, cuerpo, crc]);
}

const ihdr = Buffer.alloc(13);
ihdr.writeUInt32BE(ANCHO, 0);
ihdr.writeUInt32BE(ALTO, 4);
ihdr[8] = 8;      // 8 bits por canal
ihdr[9] = 2;      // color RGB
ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;

// scanlines con filtro 0, y una banda de color distinto cada 60 filas para contar filas
const filas = [];
for (let y = 0; y < ALTO; y++) {
  const fila = Buffer.alloc(1 + ANCHO * 3);
  const franja = Math.floor(y / 60) % 2 === 0;
  for (let x = 0; x < ANCHO; x++) {
    fila[1 + x * 3] = franja ? r : 0;
    fila[2 + x * 3] = franja ? g : 0;
    fila[3 + x * 3] = franja ? b : 255;
  }
  filas.push(fila);
}
const idat = zlib.deflateSync(Buffer.concat(filas), { level: 9 });
const png = Buffer.concat([
  Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
  trozo('IHDR', ihdr), trozo('IDAT', idat), trozo('IEND', Buffer.alloc(0)),
]);
fs.writeFileSync(salida, png);
console.log(`${salida}: ${png.length} B (${ANCHO}x${ALTO}, franjas rgb(${r},${g},${b}) / azul)`);
