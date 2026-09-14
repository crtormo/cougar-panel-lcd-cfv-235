#!/usr/bin/env node
// cdp_scripts.js - lista los scripts que tiene cargados el proceso principal del editor.
// Si alguno es JavaScript normal (no cache de bytecode), se le puede leer el codigo y
// poner un punto de interrupcion justo en la escritura HID.
//
// Uso:  node cdp_scripts.js [puerto] [filtro]

const puerto = Number(process.argv[2] || 9229);
const filtro = process.argv[3] || '';

(async () => {
  const lista = await (await fetch(`http://127.0.0.1:${puerto}/json/list`)).json();
  const ws = new WebSocket(lista[0].webSocketDebuggerUrl);
  let id = 0;
  const pendientes = new Map();
  const enviar = (metodo, params) => new Promise((res, rej) => {
    const mio = ++id;
    pendientes.set(mio, { res, rej });
    ws.send(JSON.stringify({ id: mio, method: metodo, params: params || {} }));
  });
  const scripts = [];
  ws.addEventListener('message', evento => {
    let msg;
    try { msg = JSON.parse(evento.data); } catch (e) { return; }
    if (msg.id && pendientes.has(msg.id)) {
      const p = pendientes.get(msg.id);
      pendientes.delete(msg.id);
      if (msg.error) p.rej(new Error(JSON.stringify(msg.error))); else p.res(msg.result);
      return;
    }
    if (msg.method === 'Debugger.scriptParsed') scripts.push(msg.params);
  });
  await new Promise((res, rej) => { ws.addEventListener('open', res); ws.addEventListener('error', rej); });
  await enviar('Debugger.enable');
  await new Promise(x => setTimeout(x, 4000));   // deja que lleguen los scriptParsed

  const conUrl = scripts.filter(s => s.url && (!filtro || s.url.includes(filtro)));
  console.log(`scripts cargados: ${scripts.length}   con URL: ${conUrl.length}\n`);
  const porOrigen = new Map();
  for (const s of conUrl) {
    const origen = s.url.split('/').slice(0, -1).join('/') || s.url;
    porOrigen.set(origen, (porOrigen.get(origen) || 0) + 1);
  }
  console.log('--- origenes (recuento) ---');
  for (const [origen, n] of [...porOrigen].sort((a, b) => b[1] - a[1]).slice(0, 25)) {
    console.log(`   ${String(n).padStart(4)}  ${origen}`);
  }
  console.log('\n--- scripts que parecen de la app (asar / main / hid) ---');
  for (const s of conUrl.filter(s => /asar|main|hid|cougar|preload/i.test(s.url)).slice(0, 30)) {
    console.log(`   ${s.url}   (${s.length || 0} bytes)  ${s.hash || ''}`);
  }
  console.log('\n--- los 10 primeros con URL, sea cual sea ---');
  for (const s of conUrl.slice(0, 10)) console.log(`   ${s.url}`);
  ws.close();
})().catch(e => { console.error('ERROR: ' + e.message); process.exit(1); });
