#!/usr/bin/env node
// vigilar_boot.js — vigila el arranque del panel distinguiendo TRES estados:
//
//   USB ausente                 el dispositivo no esta en el bus
//   presente, sin respuesta     el dispositivo esta pero conn no contesta
//   bootFinish = 0 / 1          el panel responde y este es su estado
//
// La distincion importa porque en el arranque real el panel enciende la pantalla y muestra el
// medio guardado RAPIDO, pero el subsistema de control (el que responde a `conn`) puede tardar
// mas. Sin distinguir "USB ausente" de "presente pero mudo", una medicion de bootFinish no vale.
//
// Uso:  node vigilar_boot.js [segundos_entre_intentos] [minutos_totales]
//       COUGAR_NODE_HID=<ruta a node-hid>   (en Windows)
//
// Al final imprime: cuando reaparecio el USB y cuanto tardo bootFinish en pasar a 1.

'use strict';

const { HID, devices } = require(process.env.COUGAR_NODE_HID || 'node-hid');

const VID = 0x1d6b, PID = 0x0126;
const START = 0x5a, ESC = 0x5b;
const INTERVALO = Number(process.argv[2] || 2) * 1000;
const MINUTOS = Number(process.argv[3] || 15);

// ------------------------------------------------------------------ framing
function checksum(payload, total) {
  return ((total >> 8) + (total & 0xff) + payload.reduce((a, b) => a + b, 0)) & 0xff;
}
function escapar(v, out) {
  if (v === START || v === ESC) out.push(ESC, v === START ? 1 : 2); else out.push(v);
}
let seq = 0;
function tramaConn() {
  seq += 1;
  const payload = Buffer.from(
    `POST conn 1\r\nSeqNumber=${seq}\r\nDate=${Date.now()}\r\n\r\n`, 'ascii');
  const total = payload.length + 5;
  const t = [START];
  escapar((total >> 8) & 0xff, t); escapar(total & 0xff, t);
  for (const b of payload) escapar(b, t);
  escapar(checksum(payload, total), t);
  t.push(START);
  return { t: Buffer.from(t), seq };
}
function extraer(buf) {
  const out = [];
  let i = 0;
  while (i < buf.length) {
    if (buf[i] !== START) { i += 1; continue; }
    const bytes = [START]; let j = i + 1, ok = false, roto = false;
    while (j < buf.length) {
      const v = buf[j];
      if (v === START) { bytes.push(v); j += 1; ok = true; break; }
      if (v === ESC) {
        if (j + 1 >= buf.length) { roto = true; break; }
        const c = buf[j + 1];
        if (c !== 1 && c !== 2) { roto = true; break; }
        bytes.push(c === 1 ? START : ESC); j += 2;
      } else { bytes.push(v); j += 1; }
    }
    if (ok && bytes.length >= 5) { out.push(Buffer.from(bytes)); i = j; }
    else if (roto) { i += 1; }
    else break;
  }
  return { out, resto: buf.slice(i) };
}
function analizar(payload) {
  const texto = payload.toString('ascii');
  const i = texto.indexOf('\r\n\r\n');
  const cab = i >= 0 ? texto.slice(0, i) : texto;
  const cuerpo = i >= 0 ? payload.slice(i + 4).toString('utf8') : '';
  const m = cab.match(/(\d{3})/);
  const a = cab.match(/AckNumber=(\d+)/);
  const b = cuerpo.match(/"bootFinish":(\d+)/);
  return { code: m ? +m[1] : null, ack: a ? +a[1] : null, bootFinish: b ? +b[1] : null };
}

// ------------------------------------------------------------------ estado
let hid = null;
let buffer = Buffer.alloc(0);
let respuestas = [];

function abrir() {
  const d = devices().find((x) => x.vendorId === VID && x.productId === PID);
  if (!d) return false;
  try { if (hid) hid.close(); } catch (e) { /* nada */ }
  try {
    hid = new HID(d.path);
  } catch (e) {
    hid = null;              // el dispositivo desaparecio justo en el corte
    return false;
  }
  buffer = Buffer.alloc(0);
  respuestas = [];
  hid.on('data', (datos) => {
    let b = Buffer.from(datos);
    if (b.length > 1 && b[0] === 0x00 && b[1] === START) b = b.slice(1);
    buffer = Buffer.concat([buffer, b]);
    const { out, resto } = extraer(buffer);
    buffer = resto;
    for (const t of out) {
      const info = analizar(t.slice(3, t.length - 2));
      if (info.code !== null) respuestas.push({ ...info, cuando: Date.now() });
    }
  });
  hid.on('error', () => { hid = null; });   // el handle muere con la desconexion: se descarta
  return true;
}

async function pedirConn(timeoutMs) {
  if (!hid) return null;
  const { t, seq: s } = tramaConn();
  respuestas = [];
  const cuerpo = Buffer.concat([Buffer.from([0x00]), t,
    Buffer.alloc(1025 - 1 - t.length)]);
  try { hid.write([...cuerpo]); } catch (e) { return null; }
  const fin = Date.now() + timeoutMs;
  // await, no spin: hay que ceder al event loop para que lleguen los eventos 'data'
  while (Date.now() < fin) {
    const r = respuestas.find((x) => x.ack === s + 1 || x.ack === s);
    if (r) return r;
    await new Promise((res) => setTimeout(res, 10));
  }
  return respuestas.find((x) => x.ack === s + 1 || x.ack === s) || null;
}

// ------------------------------------------------------------------ bucle
const t0 = Date.now();
let reaparecio = null;
let listo = null;
let huboCorte = false;
const fin = t0 + MINUTOS * 60000;

(async () => {
  while (Date.now() < fin) {
    const seg = Math.round((Date.now() - t0) / 1000);
    if (!abrir()) {
      console.log(`t=${seg}s  USB ausente${huboCorte ? '' : '  (corte detectado)'}`);
      huboCorte = true;
      reaparecio = null;
      listo = null;
    } else {
      const r = await pedirConn(3000);
      if (r === null || r.code === null) {
        console.log(`t=${seg}s  presente, conn sin respuesta`);
      } else if (reaparecio === null) {
        reaparecio = seg;
        console.log(`t=${seg}s  USB reaparecido (conn responde code=${r.code}, bootFinish=${r.bootFinish})`);
      } else {
        console.log(`t=${seg}s  bootFinish=${r.bootFinish}`);
      }
      // Solo se cierra la medida si hubo corte y el panel ya quedo listo.
      if (r && r.bootFinish === 1 && huboCorte) {
        listo = seg;
        console.log(`\nLISTO: bootFinish=1 a los ${seg}s de empezar a vigilar`);
        if (reaparecio !== null) {
          console.log(`  (${seg - reaparecio}s desde que reaparecio el USB y respondio conn)`);
        }
        break;
      }
    }
    await new Promise((r) => setTimeout(r, INTERVALO));
  }
  if (!huboCorte) console.log('no se detecto ningun corte durante la vigilancia');
  if (hid) hid.close();
  console.log('fin de la vigilancia');
})();
