#!/usr/bin/env node
// vigilar_panel.js - vigila bootFinish mientras se desenchufa/enchufa el panel.
//
// Uso:  node vigilar_panel.js [minutos]
// Requiere el modulo node-hid:  set COUGAR_NODE_HID=C:\ruta\a\nodehid.js
//
// Cada 3 s manda POST conn y muestra bootFinish. Detecta la desaparicion y
// reaparicion del USB y avisa en cuanto el panel arranque (bootFinish: 1).

const HID_MODULE = process.env.COUGAR_NODE_HID || 'node-hid';
const { HID, devices } = require(HID_MODULE);

const START = 0x5a, ESC = 0x5b, REPORT = 1025, VID = 0x1d6b, PID = 0x0126;
let seq = 0;

function appendEscaped(out, v) {
  if (v === START || v === ESC) out.push(ESC, v === START ? 1 : 2); else out.push(v);
}
function buildConn() {
  const n = seq++;
  const payload = Buffer.from(`POST conn 1\r\nSeqNumber=${n}\r\nDate=${Date.now()}\r\n\r\n`, 'ascii');
  const total = payload.length + 5;
  const alto = (total >> 8) & 0xff, bajo = total & 0xff;
  let chk = (alto + bajo) & 0xff;
  for (const b of payload) chk = (chk + b) & 0xff;
  const cable = [START];                    // delimitador CRUDO
  appendEscaped(cable, alto);
  appendEscaped(cable, bajo);
  for (const b of payload) appendEscaped(cable, b);
  appendEscaped(cable, chk);
  cable.push(START);                        // delimitador CRUDO
  const report = Buffer.alloc(REPORT, 0);
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
      frame.push(c === 1 ? START : ESC);
      i += 2;
    } else { frame.push(v); i += 1; }
  }
  if (!fin || frame.length < 5) return null;
  const declarado = (frame[1] << 8) | frame[2];
  let chk = 0;
  for (let k = 1; k + 2 < frame.length; k++) chk = (chk + frame[k]) & 0xff;
  return {
    ok: chk === frame[frame.length - 2] && declarado === frame.length,
    payload: Buffer.from(frame.slice(3, frame.length - 2)).toString('utf8'),
  };
}

const t0 = Date.now();
const ts = () => ((Date.now() - t0) / 1000).toFixed(0).padStart(4) + 's';
const dormir = ms => new Promise(x => setTimeout(x, ms));
let dev = null, respuesta = null, estadoPrevio = null, ausencia = false;
let vistoBoot1 = false;

function recibir(d) { const info = decode(Buffer.from(d)); if (info) respuesta = info; }
function abrir(info) {
  dev = new HID(info.path);
  dev.on('error', () => { });
  dev.on('data', recibir);
}
(async () => {
  const minutos = Number(process.argv[2] || 5);
  console.log(`=== vigilar_panel.js - ${new Date().toTimeString().slice(0, 8)} - ${minutos} min ===`);
  console.log('Desenchufa y vuelve a enchufar el USB del panel (o apaga/enciende la caja).');
  for (let t = 0; t < minutos * 20; t++) {
    let info = null;
    try { info = devices().filter(d => d.vendorId === VID && d.productId === PID)[0] || null; } catch (e) { }
    if (!info && dev) {
      console.log(`[${ts()}] *** USB DESCONECTADO ***`);
      try { dev.close(); } catch (e) { } dev = null; ausencia = true;
    }
    if (info && !dev) {
      abrir(info);
      seq = 0;
      console.log(`[${ts()}] *** USB CONECTADO ${ausencia ? '(tras el corte)' : ''} ***`);
    }
    if (dev) {
      respuesta = null;
      try { dev.write(buildConn()); } catch (e) { console.log(`[${ts()}] escritura fallo: ${e.message}`); }
      await dormir(2500);
      if (!respuesta) {
        console.log(`[${ts()}] conn -> sin respuesta`);
      } else {
        const cuerpo = respuesta.payload.split('\r\n\r\n')[1] || '';
        const m = /"bootFinish":(\d+)/.exec(cuerpo);
        const boot = m ? m[1] : '?';
        const linea = `[${ts()}] conn -> bootFinish=${boot}` +
          (/1 200/.test(respuesta.payload) ? '' : ' (code != 200)');
        if (linea.replace(/\[\s*\d+s\]/, '') !== estadoPrevio) { console.log(linea); estadoPrevio = linea.replace(/\[\s*\d+s\]/, ''); }
        if (boot === '1' && !vistoBoot1) {
          vistoBoot1 = true;
          console.log('\n>>>>>>>>>> EL PANEL HA ARRANCADO (bootFinish=1). Ya acepta ordenes. <<<<<<<<<<\n');
          console.log(cuerpo);
        }
        if (boot === '1') { try { dev.close(); } catch (e) { } process.exit(0); }
      }
    } else {
      await dormir(500);
    }
  }
  console.log(`[${ts()}] fin. ¿se corto la energia del panel? ${ausencia ? 'si' : 'NO se detecto ningun corte'}`);
  console.log('Si no hubo corte, el vigilante no pudo ver el reenchufe.');
  process.exit(0);
})();
