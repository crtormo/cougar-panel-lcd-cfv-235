#!/usr/bin/env node
// cdp_eval.js - ejecuta una expresion JavaScript DENTRO del proceso principal del editor
// y muestra el resultado. Es la herramienta de ingenieria inversa en vivo.
//
// Uso:  node cdp_eval.js "<expresion>"  [puerto]
//       node cdp_eval.js --fichero consulta.js
//
// El editor debe estar abierto con --inspect=9229 (consola de administrador).

const fs = require('fs');
const puerto = Number(process.env.CDP_PUERTO || 9229);

let expresion;
if (process.argv[2] === '--fichero') expresion = fs.readFileSync(process.argv[3], 'utf8');
else expresion = process.argv[2];

if (!expresion) {
  console.error('Uso: node cdp_eval.js "<expresion>" [puerto]');
  process.exit(2);
}

(async () => {
  const lista = await (await fetch(`http://127.0.0.1:${puerto}/json/list`)).json();
  if (!lista.length) throw new Error('sin objetivos en el inspector');
  const ws = new WebSocket(lista[0].webSocketDebuggerUrl);
  let id = 0;
  const pendientes = new Map();
  const enviar = (metodo, params) => new Promise((res, rej) => {
    const mio = ++id;
    pendientes.set(mio, { res, rej });
    ws.send(JSON.stringify({ id: mio, method: metodo, params: params || {} }));
  });

  ws.addEventListener('message', evento => {
    let msg;
    try { msg = JSON.parse(evento.data); } catch (e) { return; }
    if (msg.id && pendientes.has(msg.id)) {
      const p = pendientes.get(msg.id);
      pendientes.delete(msg.id);
      if (msg.error) p.rej(new Error(JSON.stringify(msg.error)));
      else p.res(msg.result);
    }
  });

  await new Promise((res, rej) => {
    ws.addEventListener('open', res);
    ws.addEventListener('error', rej);
  });
  await enviar('Runtime.enable');
  const r = await enviar('Runtime.evaluate', {
    expression: expresion,
    returnByValue: true,
    awaitPromise: true,
    includeCommandLineAPI: true,
  });
  if (r.exceptionDetails) {
    console.error('EXCEPCION: ' + (r.exceptionDetails.exception?.description || r.exceptionDetails.text));
    process.exitCode = 1;
  } else {
    const v = r.result?.value;
    console.log(typeof v === 'string' ? v : JSON.stringify(v, null, 2));
  }
  ws.close();
})().catch(e => { console.error('ERROR: ' + e.message); process.exit(1); });
