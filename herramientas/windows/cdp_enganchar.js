#!/usr/bin/env node
// cdp_enganchar.js - se conecta al inspector del proceso principal del editor y engancha
// su escritura HID para grabar los bytes EXACTOS que manda al panel.
//
// Por que: el log del editor registra las peticiones pero NO los informes de medios (los
// bloques de subida), que son justo lo que nos falta. Enganchando la escritura HID los
// capturamos todos.
//
// Uso:
//   1) en una consola de ADMINISTRADOR:
//        cd 'C:\Program Files\COUGAR LCD Editor'
//        & '.\COUGAR LCD Editor.exe' --inspect=9229
//   2) node cdp_enganchar.js [puerto] [fichero de salida]

const fs = require('fs');
const puerto = Number(process.argv[2] || 9229);
const salida = (process.argv[3] || 'C:/Users/Maximo/Desktop/CFV235-Linux/captura_informes.txt')
  .replace(/\\/g, '/');

async function objetivo() {
  const r = await fetch(`http://127.0.0.1:${puerto}/json/list`);
  const lista = await r.json();
  if (!lista.length) throw new Error('el inspector no ofrece ningun objetivo');
  console.log('objetivos del inspector:');
  for (const t of lista) console.log(`   - ${t.type}  ${t.title || ''}  ${t.url || ''}`);
  return lista[0].webSocketDebuggerUrl;
}

function conectar(url) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url);
    let id = 0;
    const pendientes = new Map();
    ws.addEventListener('open', () => resolve({
      enviar(metodo, params) {
        return new Promise((res, rej) => {
          const mio = ++id;
          pendientes.set(mio, { res, rej });
          ws.send(JSON.stringify({ id: mio, method: metodo, params: params || {} }));
        });
      },
      cerrar() { ws.close(); },
    }));
    ws.addEventListener('message', evento => {
      let msg;
      try { msg = JSON.parse(evento.data); } catch (e) { return; }
      if (msg.id && pendientes.has(msg.id)) {
        const p = pendientes.get(msg.id);
        pendientes.delete(msg.id);
        if (msg.error) p.rej(new Error(JSON.stringify(msg.error)));
        else p.res(msg.result);
      } else if (msg.method) {
        console.log(`   [evento] ${msg.method}`);
      }
    });
    ws.addEventListener('error', reject);
  });
}

const expresion = `
(() => {
  const req = (process.mainModule && process.mainModule.require)
    ? process.mainModule.require.bind(process.mainModule)
    : null;
  if (!req) return 'sin require en el proceso principal';
  const fichero = ${JSON.stringify(salida)};
  const fs = req('fs');
  let hid = null;
  for (const clave of Object.keys(req.cache || {})) {
    if (/node-hid/i.test(clave)) { hid = req.cache[clave].exports; break; }
  }
  if (!hid) { try { hid = req('node-hid'); } catch (e) { return 'node-hid no cargado: ' + e.message; } }
  const HID = hid.HID || (hid.default && hid.default.HID);
  if (!HID) return 'el modulo no expone HID: ' + Object.keys(hid).join(',');
  if (!HID.prototype.__enganchado) {
    const original = HID.prototype.write;
    HID.prototype.write = function (buffer) {
      try {
        const b = Buffer.from(buffer);
        fs.appendFileSync(fichero,
          new Date().toISOString() + ' len=' + b.length + ' ' + b.toString('hex') + '\\n');
      } catch (e) { /* nada */ }
      return original.apply(this, arguments);
    };
    HID.prototype.__enganchado = true;
  }
  return 'enganchado (HID.prototype.write). Modulos en cache con node-hid: '
    + Object.keys(req.cache || {}).filter(k => /node-hid/i.test(k)).length;
})()
`;

(async () => {
  try {
    const url = await objetivo();
    console.log(`\nconectando a ${url}`);
    const cdp = await conectar(url);
    await cdp.enviar('Runtime.enable');
    const res = await cdp.enviar('Runtime.evaluate', {
      expression: expresion, returnByValue: true, awaitPromise: false,
    });
    console.log('\nresultado del enganche:');
    console.log('   ' + JSON.stringify(res.result && res.result.value));
    if (res.exceptionDetails) console.log('   excepcion: ' + JSON.stringify(res.exceptionDetails.text));
    console.log(`\nA partir de ahora TODO lo que escriba el editor al panel se graba en:\n   ${salida}`);
    console.log('Haz la accion en la interfaz (p. ej. cambiar el fondo) y luego mira ese fichero.');
    // Mantiene la conexion un rato para que el enganche siga vivo y no se cierre el socket.
    const minutos = Number(process.env.CDP_MINUTOS || 20);
    console.log(`\n(la conexion se mantiene ${minutos} minutos; Ctrl+C para salir)`);
    await new Promise(x => setTimeout(x, minutos * 60000));
    cdp.cerrar();
  } catch (error) {
    console.error('ERROR: ' + error.message);
    console.error('Comprueba que el editor esta abierto con --inspect=9229 y en una consola de administrador.');
    process.exit(1);
  }
})();
