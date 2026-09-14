#!/usr/bin/env node
// cdp_parche.js - parchea EN CALIENTE el modulo node-hid dentro del editor en marcha para
// grabar todos los informes que escribe al panel (incluidos los bloques de medios, que el
// log del editor NO registra).
//
// Por que asi: en node-hid 3.x, `write` no esta en el prototipo: el constructor copia los
// metodos del binding nativo a cada instancia. Parchear HID.prototype.write no sirve; hay
// que modificar el codigo cargado, y el inspector lo permite con Debugger.setScriptSource.
//
// Uso:  node cdp_parche.js [puerto] [fichero de salida]
// Despues: hay que hacer que el editor abra el dispositivo DE NUEVO (desenchufar y volver a
// enchufar el USB del panel) para que el constructor parcheado se aplique.

const puerto = Number(process.argv[2] || 9229);
const salida = (process.argv[3] || 'C:/Users/Maximo/Desktop/CFV235-Linux/captura_informes.txt')
  .replace(/\\/g, '/');

const ORIGINAL = 'for (var i in binding.HID.prototype) this[i] = binding.HID.prototype[i].bind(this._raw);';

const PARCHE = `
  /* --- PARCHE DE INGENIERIA INVERSA (no altera el comportamiento, solo registra) --- */
  for (var i in binding.HID.prototype) {
    if (i === 'write' || i === 'writeSync') {
      this[i] = (function (nombre, original) {
        return function () {
          try {
            var b = arguments[0];
            require('fs').appendFileSync(${JSON.stringify(salida)},
              new Date().toISOString() + ' ' + nombre + ' len=' + (b && b.length ? b.length : 0) +
              ' ' + Buffer.from(b || []).toString('hex') + '\\n');
          } catch (e) { /* si falla el registro, la app sigue igual */ }
          return original.apply(this, arguments);
        };
      })(i, binding.HID.prototype[i].bind(this._raw));
    } else {
      this[i] = binding.HID.prototype[i].bind(this._raw);
    }
  }`;

(async () => {
  const lista = await (await fetch(`http://127.0.0.1:${puerto}/json/list`)).json();
  if (!lista.length) throw new Error('sin objetivos en el inspector');
  const ws = new WebSocket(lista[0].webSocketDebuggerUrl);
  let id = 0;
  const pendientes = new Map();
  const scripts = [];
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
      if (msg.error) p.rej(new Error(JSON.stringify(msg.error))); else p.res(msg.result);
      return;
    }
    if (msg.method === 'Debugger.scriptParsed' && /node-hid\/nodehid\.js$/.test(msg.params.url || '')) {
      scripts.push(msg.params);
    }
  });
  await new Promise((res, rej) => { ws.addEventListener('open', res); ws.addEventListener('error', rej); });
  await enviar('Debugger.enable');
  await enviar('Runtime.enable');
  await new Promise(x => setTimeout(x, 2500));

  if (!scripts.length) throw new Error('no encuentro el script node-hid/nodehid.js cargado');
  const script = scripts[0];
  console.log(`script: ${script.url}\n   scriptId=${script.scriptId}  ${script.length} bytes`);

  const fuente = await enviar('Debugger.getScriptSource', { scriptId: script.scriptId });
  const original = fuente.scriptSource;
  if (!original.includes(ORIGINAL)) {
    console.error('!! la linea a parchear no aparece tal cual. Fragmento encontrado:');
    const linea = original.split('\n').find(l => l.includes('binding.HID.prototype'));
    console.error('   ' + (linea || '(ninguna linea con binding.HID.prototype)'));
    process.exit(1);
  }
  const parcheado = original.replace(ORIGINAL, PARCHE.trim());
  const r = await enviar('Debugger.setScriptSource', {
    scriptId: script.scriptId, scriptSource: parcheado, dryRun: false,
  });
  console.log(`resultado del parche: ${JSON.stringify(r)}`);
  if (r.status && r.status !== 'Ok') {
    console.error('!! V8 rechazo el parche: ' + JSON.stringify(r.exceptionDetails || r));
    process.exit(1);
  }
  console.log(`\nPARCHE APLICADO. Se grabara en:\n   ${salida}`);
  console.log('Ahora hay que forzar que el editor abra el dispositivo de nuevo:');
  console.log('   desenchufa y vuelve a enchufar el USB del panel.');
  ws.close();
})().catch(e => { console.error('ERROR: ' + e.message); process.exit(1); });
