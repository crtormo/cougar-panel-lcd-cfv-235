#!/usr/bin/env node
// buscar_bloque.js - busqueda guiada del formato del informe de bloque de medios.
//
// El panel acusa cada subida de bloques con un frame "1 200 AckNumber=0" (aceptado) o
// "1 400 AckNumber=0" (rechazado), y despues 'transported' devuelve {"state":"success"}
// o un cuerpo vacio. Con esas dos senales se puede probar variantes y encontrar la buena.
//
// Uso:  node buscar_bloque.js <imagen.png>     (con el COUGAR LCD Editor CERRADO)
//       COUGAR_NODE_HID=<ruta a nodehid.js> si hace falta.

const fs = require('fs');
const path = require('path');
const { HID, devices } = require(process.env.COUGAR_NODE_HID || 'node-hid');

const START = 0x5a, ESC = 0x5b, REPORT_SIZE = 1025;
const VID = 0x1d6b, PID = 0x0126;

let seq = 0;
const recibidos = [];
let dev = null;

function appendEscaped(out, v) {
  if (v === START || v === ESC) out.push(ESC, v === START ? 1 : 2); else out.push(v);
}
function frameDe(method, cmd, body) {
  const n = seq++;
  let cabecera = `${method} ${cmd} 1\r\nSeqNumber=${n}\r\nDate=${Date.now()}\r\n`;
  let payload;
  if (body === undefined) payload = Buffer.from(cabecera + '\r\n', 'ascii');
  else {
    const raw = Buffer.from(JSON.stringify(body), 'utf8');
    payload = Buffer.concat([Buffer.from(`${cabecera}ContentType=json\r\nContentLength=${raw.length}\r\n\r\n`, 'ascii'), raw]);
  }
  const total = payload.length + 5;
  const alto = (total >> 8) & 0xff, bajo = total & 0xff;
  let chk = (alto + bajo) & 0xff;
  for (const b of payload) chk = (chk + b) & 0xff;
  const cable = [START];
  appendEscaped(cable, alto); appendEscaped(cable, bajo);
  for (const b of payload) appendEscaped(cable, b);
  appendEscaped(cable, chk); cable.push(START);
  const out = [START].slice(0, 0);           // el informe empieza por el report ID 0x00
  const report = Buffer.alloc(REPORT_SIZE, 0);
  Buffer.from(cable).copy(report, 1);
  return report;
}
function decode(buf) {
  let i = buf.indexOf(START);
  if (i < 0) return null;
  const frame = [START];
  i += 1;
  let fin = false;
  while (i < buf.length) {
    const v = buf[i];
    if (v === START) { frame.push(v); i += 1; fin = true; break; }
    if (v === ESC) {
      if (i + 1 >= buf.length) return null;
      const c = buf[i + 1];
      if (c !== 1 && c !== 2) return null;
      frame.push(c === 1 ? START : ESC); i += 2;
    } else { frame.push(v); i += 1; }
  }
  if (!fin || frame.length < 5) return null;
  let chk = 0;
  for (let k = 1; k + 2 < frame.length; k++) chk = (chk + frame[k]) & 0xff;
  if (chk !== frame[frame.length - 2]) return null;
  const texto = Buffer.from(frame.slice(3, frame.length - 2)).toString('utf8');
  const m = /^(\d+)\s+(\d{3})$/.exec((texto.split('\r\n')[0] || '').trim());
  let ack = null;
  for (const l of texto.split('\r\n')) { const a = /^AckNumber=(\d+)/.exec(l.trim()); if (a) ack = Number(a[1]); }
  const idx = texto.indexOf('\r\n\r\n');
  return { code: m ? Number(m[2]) : null, ack, body: idx >= 0 ? texto.slice(idx + 4) : '' };
}

const dormir = ms => new Promise(x => setTimeout(x, ms));

// --- Variantes del informe de bloque -------------------------------------------------
// Cada variante recibe (indice, total, trozo) y devuelve el informe de 1025 bytes.
const variantes = [
  {
    nombre: 'A base (referencia)',
    nota: 'hdr 24 B, [4]=0x13, BE, indice desde 0, tipo 0x02, len=21+trozo, 1000 B',
    construir: (i, n, d) => informeEstandar(i, n, d, { base: 0, lenBase: 21, tipo: 0x02, prefijo: true, datos: 25 }),
  },
  {
    nombre: 'B indice desde 1',
    construir: (i, n, d) => informeEstandar(i + 1, n, d, { base: 0, lenBase: 21, tipo: 0x02, prefijo: true, datos: 25 }),
  },
  {
    nombre: 'C len = 24 + trozo',
    construir: (i, n, d) => informeEstandar(i, n, d, { base: 0, lenBase: 24, tipo: 0x02, prefijo: true, datos: 25 }),
  },
  {
    nombre: 'D tipo de medio 0x01',
    construir: (i, n, d) => informeEstandar(i, n, d, { base: 0, lenBase: 21, tipo: 0x01, prefijo: true, datos: 25 }),
  },
  {
    nombre: 'E sin byte de report ID',
    construir: (i, n, d) => informeEstandar(i, n, d, { base: 0, lenBase: 21, tipo: 0x02, prefijo: false, datos: 25 }),
  },
  {
    nombre: 'F datos en el 24 (hdr 23 B)',
    construir: (i, n, d) => informeEstandar(i, n, d, { base: 0, lenBase: 21, tipo: 0x02, prefijo: true, datos: 24 }),
  },
  {
    nombre: 'G contador e indice en LE',
    construir: (i, n, d) => informeEstandar(i, n, d, { base: 0, lenBase: 21, tipo: 0x02, prefijo: true, datos: 25, little: true }),
  },
  {
    nombre: 'H len = 21 + trozo, tipo de mensaje 0x14',
    construir: (i, n, d) => informeEstandar(i, n, d, { base: 0, lenBase: 21, tipo: 0x02, prefijo: true, datos: 25, msg: 0x14 }),
  },
];

function informeEstandar(indice, total, trozo, o) {
  const report = Buffer.alloc(REPORT_SIZE, 0);
  const desplazamiento = o.prefijo ? 1 : 0;         // 0x00 de report ID
  const h = report.subarray ? report : report;      // (Buffer)
  const total_len = o.lenBase + trozo.length;
  let p = desplazamiento;
  report[p++] = 0x5c;
  report[p++] = (total_len >> 8) & 0xff;
  report[p++] = total_len & 0xff;
  report[p++] = o.msg === undefined ? 0x13 : o.msg;
  if (o.little) {
    report[p++] = total & 0xff; report[p++] = (total >> 8) & 0xff;
    report[p++] = indice & 0xff; report[p++] = (indice >> 8) & 0xff;
  } else {
    report[p++] = (total >> 8) & 0xff; report[p++] = total & 0xff;
    report[p++] = (indice >> 8) & 0xff; report[p++] = indice & 0xff;
  }
  report[p++] = o.tipo;
  // huecos en cero hasta el desplazamiento de los datos
  trozo.copy(report, o.datos);
  return report;
}

function abrir() {
  const info = devices().filter(d => d.vendorId === VID && d.productId === PID)[0];
  if (!info) { console.error('No encuentro el panel.'); process.exit(1); }
  dev = new HID(info.path);
  dev.on('error', err => console.error(`   !! ${err.message}`));
  dev.on('data', d => {
    const f = decode(Buffer.from(d));
    if (f) recibidos.push(f);
  });
}

async function enviar(etq, method, cmd, body, espera) {
  dev.write(frameDe(method, cmd, body));
  await dormir(espera);
}

(async () => {
  const ruta = process.argv[2];
  if (!ruta) { console.error('Uso: node buscar_bloque.js <imagen.png>'); process.exit(2); }
  const datos = fs.readFileSync(ruta);
  if (!datos.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))) {
    console.error('No es un PNG.'); process.exit(2);
  }
  const solo = Number(process.env.COUGAR_TROZO || 1000);   // tamano de trozo por bloque
  const bloques = Math.ceil(datos.length / solo);
  console.log(`imagen: ${path.basename(ruta)}  ${datos.length} B  ->  ${bloques} bloques de ${solo} B\n`);

  abrir();
  // La EXTENSION del nombre decide que espera el panel: con .osd trata la subida como capa
  // OSD (formato ya conocido) y con .png como imagen de fondo (formato por descubrir).
  const ext = process.env.COUGAR_EXT || '.osd';
  console.log(`extension de los nombres de prueba: ${ext}\n`);
  const resumen = [];
  for (const v of variantes) {
    const nombre = `b${v.nombre[0].toLowerCase()}_${Date.now() % 100000}${ext}`;
    recibidos.length = 0;
    console.log(`--- variante ${v.nombre}${v.nota ? '  (' + v.nota + ')' : ''}  fichero: ${nombre}`);

    await enviar('transport', 'POST', 'transport',
      { type: 'media', fileSize: datos.length, fileName: nombre }, 2000);
    const ackTransport = recibidos.slice();

    for (let i = 0; i < bloques; i++) {
      const trozo = datos.subarray(i * solo, (i + 1) * solo);
      dev.write(v.construir(i, bloques, trozo));
    }
    recibidos.length = 0;
    await dormir(1500);
    const ackBloques = recibidos.slice();

    recibidos.length = 0;
    await enviar('transported', 'POST', 'transported', { md5: 'todo', fileName: nombre }, 2500);
    const ackFinal = recibidos.slice();

    const codes = lista => lista.map(x => `${x.code}(Ack=${x.ack})${x.body ? ' body=' + x.body.slice(0, 40) : ''}`).join(' | ') || 'nada';
    console.log(`    transport  -> ${codes(ackTransport)}`);
    console.log(`    bloques    -> ${codes(ackBloques)}    <-- 200 = ACEPTADOS`);
    console.log(`    transported-> ${codes(ackFinal)}`);

    const aceptado = ackBloques.some(f => f.code === 200) &&
      ackFinal.some(f => f.code === 200 && /state"\s*:\s*"success/.test(f.body));
    resumen.push({ variante: v.nombre, aceptado });
    if (aceptado) {
      console.log(`\n>>> VARIANTE VALIDA: ${v.nombre}\n`);
      break;
    }
    console.log('');
  }

  recibidos.length = 0;
  await enviar('conn', 'POST', 'conn', undefined, 2000);
  const estado = recibidos.find(f => f.body && f.body.includes('bootFinish'));
  if (estado) console.log(`estado final: ${estado.body.replace(/\s+/g, ' ')}`);

  console.log('\n=== resumen ===');
  for (const r of resumen) console.log(`   ${r.aceptado ? 'OK ' : '   '} ${r.variante}`);
  try { dev.close(); } catch (e) { /* nada */ }
  process.exit(resumen.some(r => r.aceptado) ? 0 : 1);
})();
