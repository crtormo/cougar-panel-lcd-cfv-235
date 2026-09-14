#!/usr/bin/env node
// cougar_hid_node.js - cliente HID del panel COUGAR CFV235 para Windows (Node + node-hid).
//
// Por que existe: en Windows no hay /dev/hidraw, asi que cougar_panel.py (Linux) no puede
// abrir el panel. Este cliente usa node-hid y el MISMO modelo de trama verificado:
//   informe = 00 (report ID) + 5A | len(BE16) | payload escapado | checksum | 5A + ceros
//   escape  : 0x5A -> 5B 01, 0x5B -> 5B 02 (tambien en los bytes de longitud y el checksum)
//   len     : payload sin escapar + 5   (no cuenta los bytes de codigo insertados)
//   checksum: (byte_alto + byte_bajo + suma(payload)) & 0xFF
// Los delimitadores 0x5A van CRUDOS: si se escaparan, el panel no reconoceria la trama.
//
// Uso:
//   node cougar_hid_node.js estado
//   node cougar_hid_node.js power
//   node cougar_hid_node.js recovery [serial]
//   node cougar_hid_node.js transport <imagen.png>
//   node cougar_hid_node.js subir <imagen.png>
//   node cougar_hid_node.js raw POST <cmd> ['{"json":1}']
//   node cougar_hid_node.js listen [segundos]
//
// node-hid:  npm install node-hid   (o apunta la ruta con COUGAR_NODE_HID)

const fs = require('fs');
const path = require('path');
const HID_MODULE = process.env.COUGAR_NODE_HID || 'node-hid';
const { HID, devices } = require(HID_MODULE);

const START = 0x5a;
const ESC = 0x5b;
const REPORT_SIZE = 1025;
const VID = 0x1d6b;
const PID = 0x0126;
const ESPERA_MS = Number(process.env.COUGAR_ESPERA || 6000);

// Protocolo de medios (subida de imagenes)
const MEDIA_START = 0x5c;
const MEDIA_MSG = 0x13;
const MEDIA_TYPE = 0x02;
const MEDIA_CHUNK = 1000;
const PNG_FIRMA = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

let seq = 0;
let ultimaRespuesta = null;
// Respuestas indexadas por AckNumber: una respuesta rezagada de otra peticion NO debe
// pisar la que estamos esperando (fue exactamente el fallo que hizo que un transport
// aceptado se leyera como rechazado).
const respuestas = new Map();

function appendEscaped(out, value) {
  if (value === START || value === ESC) out.push(ESC, value === START ? 0x01 : 0x02);
  else out.push(value);
}

function buildReport(method, cmd, body) {
  const n = seq++;
  let cabecera = `${method} ${cmd} 1\r\nSeqNumber=${n}\r\nDate=${Date.now()}\r\n`;
  let payload;
  if (body === undefined) {
    payload = Buffer.from(cabecera + '\r\n', 'ascii');
  } else {
    const raw = Buffer.from(JSON.stringify(body), 'utf8');
    payload = Buffer.concat([
      Buffer.from(`${cabecera}ContentType=json\r\nContentLength=${raw.length}\r\n\r\n`, 'ascii'),
      raw,
    ]);
  }
  const total = payload.length + 5;
  const alto = (total >> 8) & 0xff;
  const bajo = total & 0xff;
  let checksum = (alto + bajo) & 0xff;
  for (const b of payload) checksum = (checksum + b) & 0xff;

  const cable = [START];                       // delimitador CRUDO
  appendEscaped(cable, alto);
  appendEscaped(cable, bajo);
  for (const b of payload) appendEscaped(cable, b);
  appendEscaped(cable, checksum);
  cable.push(START);                           // delimitador CRUDO

  const report = Buffer.alloc(REPORT_SIZE, 0);
  Buffer.from(cable).copy(report, 1);
  return { report, seq: n };
}

// Informe de medios: NO lleva trama 5A, ni escape, ni checksum.
// Formato COPIADO del editor, capturado instrumentando su node-hid (2026-09-13):
//   00 5c <len BE = 21+trozo> <[4]> <n bloques BE> <indice BE> <[9]> 00*15 <datos>
//   [4] = contador que incrementa en cada informe (el editor mando 0x16 para el fondo y
//         0x18 para la capa OSD; la referencia publica 0x13 fijo)
//   [9] = tipo: 0x02 imagen de fondo, 0x01 capa OSD
//   El ULTIMO bloque va SIN relleno (el editor mando 769 B para 744 de datos).
function buildMediaReport(index, totalBlocks, chunk) {
  const mensaje = Number(process.env.COUGAR_MSG ?? 0x16);
  const tipo = Number(process.env.COUGAR_TIPO ?? 0x02);
  const report = Buffer.alloc(25 + chunk.length, 0);
  const total = 21 + chunk.length;
  report[0] = 0x00;
  report[1] = MEDIA_START;
  report[2] = (total >> 8) & 0xff;
  report[3] = total & 0xff;
  report[4] = mensaje & 0xff;
  report[5] = (totalBlocks >> 8) & 0xff;
  report[6] = totalBlocks & 0xff;
  report[7] = (index >> 8) & 0xff;
  report[8] = index & 0xff;
  report[9] = tipo & 0xff;
  chunk.copy(report, 25);
  return report;
}

function decodeReport(buf) {
  let i = buf.indexOf(START);
  if (i < 0) return null;
  const frame = [START];
  const escapes = [];
  i += 1;
  let terminada = false;
  while (i < buf.length) {
    const value = buf[i];
    if (value === START) { frame.push(value); i += 1; terminada = true; break; }
    if (value === ESC) {
      if (i + 1 >= buf.length) return null;
      const code = buf[i + 1];
      if (code !== 0x01 && code !== 0x02) return null;
      frame.push(code === 0x01 ? START : ESC);
      escapes.push(code === 0x01 ? START : ESC);
      i += 2;
    } else { frame.push(value); i += 1; }
  }
  if (!terminada || frame.length < 5) return null;
  const declarado = (frame[1] << 8) | frame[2];
  let checksum = 0;
  for (let k = 1; k + 2 < frame.length; k++) checksum = (checksum + frame[k]) & 0xff;
  const cabecera = Buffer.from(frame.slice(3, frame.length - 2)).toString('utf8');
  return {
    payload: cabecera,
    len: declarado,
    lenOk: declarado === frame.length,
    checksumOk: checksum === frame[frame.length - 2],
    escapes: escapes.length,
    consumidos: i,
  };
}

function abrir() {
  const info = devices().filter(d => d.vendorId === VID && d.productId === PID)[0];
  if (!info) {
    console.error(`No encuentro el panel (${VID.toString(16)}:${PID.toString(16)}).`);
    process.exit(1);
  }
  console.log(`dispositivo: ${info.path}`);
  const dev = new HID(info.path);
  dev.on('error', err => console.error(`!! error HID: ${err && err.message ? err.message : err}`));
  const pendientes = new Map();
  dev.on('data', d => {
    const info2 = decodeReport(Buffer.from(d));
    if (!info2) {
      console.error(`   [rx] informe de ${d.length} B que no decodifica como trama`);
      return;
    }
    ultimaRespuesta = info2.payload;
    const partes = info2.payload.split('\r\n\r\n');
    const lineas = partes[0].split('\r\n');
    const m = /^(\d+)\s+(\d{3})$/.exec((lineas[0] || '').trim());
    let ack = null;
    for (const l of lineas) { const a = /^AckNumber=(\d+)/.exec(l.trim()); if (a) ack = Number(a[1]); }
    if (ack !== null) respuestas.set(ack, info2.payload);
    const etq = pendientes.has(ack) ? pendientes.get(ack) : `(Ack=${ack})`;
    console.log(`  <-- ${etq}: code=${m ? m[2] : '?'}` +
      `  [len=${info2.len} trama=${info2.consumidos} escapes=${info2.escapes}` +
      ` checksum=${info2.checksumOk ? 'ok' : 'MAL'}]`);
    const cuerpo = partes[1] || '';
    if (cuerpo) console.log(`      ${cuerpo.replace(/\s+/g, ' ')}`);
    if (process.env.COUGAR_DEBUG) {
      console.error(`      [crudo] ${JSON.stringify(info2.payload)}`);
    }
  });
  return { dev, pendientes };
}

const dormir = ms => new Promise(x => setTimeout(x, ms));

async function pedir(dev, pendientes, etq, method, cmd, body, espera = ESPERA_MS) {
  const { report, seq: n } = buildReport(method, cmd, body);
  // El panel NO usa siempre AckNumber = SeqNumber + 1: medido +1 en conn (seq 12 -> Ack 13)
  // pero +0 en transport (seq 755 -> Ack 755) y recovery (seq 754 -> Ack 754). Se registran
  // las dos claves para poder atribuir la respuesta en ambos casos.
  pendientes.set(n, etq);
  pendientes.set(n + 1, etq);
  respuestas.delete(n);
  respuestas.delete(n + 1);
  ultimaRespuesta = null;
  console.log(`--> ${etq}  (${method} ${cmd}, seq=${n})`);
  try {
    dev.write(report);
  } catch (err) {
    console.error(`!! la escritura lanzo: ${err.message}`);
  }
  await dormir(espera);
  return respuestas.get(n) || respuestas.get(n + 1) || null;
}

function comprobarPng(ruta) {
  const datos = fs.readFileSync(ruta);
  if (datos.length <= 8) throw new Error('fichero demasiado pequeno');
  // El editor acepta .png, .jpg, .mp4 y .gif (su selector filtra por image/*, asi que el gif
  // entra como imagen). Aqui se admiten PNG, JPEG y GIF: lo que el panel sabe mostrar.
  const cabecera = datos.subarray(0, 8);
  const esPng = cabecera.subarray(0, 8).equals(PNG_FIRMA);
  const esJpeg = datos[0] === 0xff && datos[1] === 0xd8;
  const esGif = cabecera.subarray(0, 6).toString('latin1') === 'GIF87a' ||
                cabecera.subarray(0, 6).toString('latin1') === 'GIF89a';
  if (!esPng && !esJpeg && !esGif) {
    throw new Error('no es PNG, JPEG ni GIF (empieza por ' +
      datos.subarray(0, 8).toString('hex') + ')');
  }
  const nombre = path.basename(ruta);
  if (!/^[A-Za-z0-9._-]{1,127}$/.test(nombre)) {
    throw new Error(`nombre no valido para el firmware: ${nombre}`);
  }
  return { datos, nombre, formato: esPng ? 'png' : (esJpeg ? 'jpeg' : 'gif') };
}

// Envia y devuelve la respuesta EN CUANTO LLEGA (sondeando cada 10 ms), en vez de esperar
// un tiempo fijo. Es imprescindible para la subida: el editor manda los bloques 87 ms
// despues del transport y 'transported' 6 ms despues; si se tarda segundos, la sesion de
// transferencia del panel caduca y los bloques se rechazan (acuse 400).
async function pedirRapido(dev, pendientes, etq, method, cmd, body, topeMs = 3000) {
  const { report, seq: n } = buildReport(method, cmd, body);
  pendientes.set(n, etq);
  pendientes.set(n + 1, etq);
  respuestas.delete(n);
  respuestas.delete(n + 1);
  console.log(`--> ${etq}  (${method} ${cmd}, seq=${n})`);
  try {
    dev.write(report);
  } catch (err) {
    console.error(`!! la escritura lanzo: ${err.message}`);
    return null;
  }
  const limite = Date.now() + topeMs;
  while (Date.now() < limite) {
    const r = respuestas.get(n) || respuestas.get(n + 1);
    if (r) return r;
    await dormir(10);
  }
  return null;
}

// Envia un fichero al panel SIN preludio: las tres fases del protocolo, con la
// sincronizacion que exige el firmware (bloques inmediatos, cierre inmediato).
// Devuelve 0 si el panel lo acepto, 1 si no. Con silencio=true no imprime nada.
async function enviarArchivo(dev, pendientes, ruta, silencio) {
  const di = (...args) => { if (!silencio) console.log(...args); };
  const { datos, nombre } = comprobarPng(ruta);
  const bloques = Math.ceil(datos.length / MEDIA_CHUNK);
  if (bloques > 0xffff) throw new Error('demasiados bloques');
  di(`fichero: ${nombre}  ${datos.length} B  ->  ${bloques} bloques de ${MEDIA_CHUNK} B`);

  const respuesta = await pedirRapido(dev, pendientes, silencio ? 'transport' : 'transport',
    'POST', process.env.COUGAR_HANDSHAKE || 'transport',
    { type: 'media', fileSize: datos.length, fileName: nombre }, 4000);
  const cuerpo = respuesta ? respuesta.split('\r\n\r\n')[1] || '' : '';
  if (!/1 200/.test(respuesta || '') || !/blockMaxSize/.test(cuerpo)) {
    console.log('!! el panel RECHAZO el handshake transport (se espera 200 con blockMaxSize).');
    return 1;
  }
  di(`handshake OK: ${cuerpo.replace(/\s+/g, ' ')}`);

  for (let index = 0; index < bloques; index++) {
    const trozo = datos.subarray(index * MEDIA_CHUNK, (index + 1) * MEDIA_CHUNK);
    try {
      dev.write(buildMediaReport(index, bloques, trozo));
    } catch (err) {
      console.error(`!! fallo al escribir el bloque ${index + 1}/${bloques}: ${err.message}`);
      return 1;
    }
  }
  di(`  ${bloques} bloques enviados`);

  // Cierre inmediato: el editor tarda 6 ms desde el ultimo bloque.
  const fin = await pedirRapido(dev, pendientes, 'transported', 'POST', 'transported',
    { md5: 'todo', fileName: nombre }, 5000);
  const finCuerpo = fin ? (fin.split('\r\n\r\n')[1] || '') : '';
  const finCode = fin ? (/1 (\d{3})/.exec(fin) || [])[1] : null;
  if (finCode !== '200') {
    console.log(`!! 'transported' devolvio code=${finCode}`);
    return 1;
  }
  // 200 SIN cuerpo = NO aceptado (la sesion caduco). Con exito devuelve {"state":"success"}.
  if (!/"state"\s*:\s*"success"/.test(finCuerpo)) {
    console.log("!! 'transported' 200 pero sin cuerpo: el panel no acepto la subida.");
    return 1;
  }
  di(`SUBIDA ACEPTADA: ${finCuerpo.replace(/\s+/g, ' ')}`);
  return 0;
}

async function subir(dev, pendientes, ruta, soloHandshake) {
  const { datos, nombre } = comprobarPng(ruta);

  // Preludio: la implementacion de referencia manda brillo y "power resume" ANTES de
  // transport. Se replica el orden exacto (es lo unico que diferencia su flujo del nuestro).
  const estado = await pedir(dev, pendientes, 'conn (leer estado)', 'POST', 'conn', undefined, 4000);
  const cuerpoEstado = estado ? estado.split('\r\n\r\n')[1] || '' : '';
  const mBrillo = /"brightness":(\d+)/.exec(cuerpoEstado);
  const brillo = mBrillo ? Number(mBrillo[1]) : 100;
  const mBoot = /"bootFinish":(\d+)/.exec(cuerpoEstado);
  const mOsd = /"osdState":(\d+)/.exec(cuerpoEstado);
  console.log(`   estado: bootFinish=${mBoot ? mBoot[1] : '?'} brightness=${brillo}` +
    ` space=${(/"space":(\d+)/.exec(cuerpoEstado) || [])[1]}` +
    ` osdState=${mOsd ? mOsd[1] : '?'}`);
  if (mOsd && mOsd[1] === '1') {
    console.log('   AVISO: osdState=1 -> hay una capa OSD activa y se dibujara ENCIMA de la');
    console.log('          imagen que subas (veras restos de lo anterior). Solucion: mandar');
    console.log('          `recovery` (Reset) antes, que deja el panel con osdState=0.');
  }
  await pedir(dev, pendientes, `brightness ${brillo} (mismo valor, sin cambio visible)`,
    'POST', 'brightness', { value: brillo }, 3000);
  await pedir(dev, pendientes, 'power resume', 'POST', 'power', { event: 'resume' }, 3000);

  // Fases 1-3 en la funcion compartida (la usa tambien el modo bucle del dashboard).
  if (soloHandshake) {
    const r = await pedirRapido(dev, pendientes, 'transport (solo handshake)', 'POST', 'transport',
      { type: 'media', fileSize: datos.length, fileName: nombre }, 4000);
    const c = r ? r.split('\r\n\r\n')[1] || '' : '';
    console.log(`handshake: ${c.replace(/\s+/g, ' ') || '(sin respuesta)'}`);
    return /blockMaxSize/.test(c) ? 0 : 1;
  }
  const resultado = await enviarArchivo(dev, pendientes, ruta, false);
  if (resultado !== 0) return resultado;

  if (process.env.COUGAR_SIN_APLICAR) {
    console.log('   (COUGAR_SIN_APLICAR=1: no se envia recovery/rotate)');
    return 0;
  }

  // Aplicar: es la secuencia que usa el editor despues de subir el fondo del tema.
  // OJO: recovery REINICIA el panel (desaparece del USB unos segundos) y limpia su estado
  // de visualizacion, asi que conviene comprobar despues si el fondo sigue ahi.
  await pedir(dev, pendientes, 'recovery {"enable":true} (aplicar)', 'POST', 'recovery',
    { enable: true }, 3000);
  await pedir(dev, pendientes, 'rotate 270', 'POST', 'rotate', { degree: 270 }, 3000);
  const estadoFinal = await pedir(dev, pendientes, 'conn (estado final)', 'POST', 'conn', undefined, 5000);
  if (estadoFinal) {
    const cuerpoFinal = estadoFinal.split('\r\n\r\n')[1] || '';
    console.log(`   estado final: ${cuerpoFinal.replace(/\s+/g, ' ')}`);
  }
  return 0;
}

// --- Dashboard: renderizar en el PC y subir el PNG en bucle -------------------------
const { spawnSync } = require('child_process');

function telemetriaDemo(n) {
  return {
    network: { upload: 0, download: 1 },
    memory: { total: 32675, used: 11000 + (n % 500), load: 33, temperature: 0, speed: 1065 },
    cpu: { load: 8 + (n % 40), temperature: 45 + (n % 5), speedAverage: 3900,
           power: 45, voltage: 1.05, usage: 5 },
    gpu: { load: 3, temperature: 32, fan: 0, speed: 80, power: 0, voltage: 0.6 },
    disk: { total: 465, used: 50, load: 10, activity: 0, temperature: 0,
            readSpeed: 0, writeSpeed: 0 },
    fans: [{ onBoard: true, type: 'Pump', name: 'Fan AIO Pump', value: 2500 },
           { onBoard: true, type: 'Fan', name: 'Fan CPU', value: 800 },
           { onBoard: true, type: 'Chassis', name: 'Fan Chassis3', value: 600 }],
    motherboard: { temperature: 25 },
    timestamp: Date.now(),
  };
}

function renderizar(guion, png) {
  // stdio 'ignore' a proposito: no se captura la salida del proceso hijo.
  const r = spawnSync('powershell',
    ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', guion, '-Salida', png],
    { stdio: 'ignore' });
  if (r.error) { console.error(`!! no pude lanzar el render: ${r.error.message}`); return 1; }
  return r.status === null ? 1 : r.status;
}

// Captura la pantalla y la deja ajustada a 1920x462 en `png` (Windows).
function capturarPantalla(png, recortar) {
  const guion = path.join(__dirname, '..', 'widgets', 'capturar-pantalla.ps1');
  const args = ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', guion,
    '-Salida', png, '-Ancho', '1920', '-Alto', '462'];
  if (recortar) args.push('-Recortar');
  const r = spawnSync('powershell', args, { stdio: 'ignore' });
  if (r.error) { console.error(`!! no pude capturar la pantalla: ${r.error.message}`); return 1; }
  return r.status === null ? 1 : r.status;
}

// Modo "streaming": refleja la pantalla del PC en el panel, fotograma a fotograma.
async function stream(dev, pendientes, periodoMs, repeticiones) {
  // Los envios continuos van como CAPA OSD (tipo 0x01): medido, el panel reutiliza ese hueco
  // (-16 KB en 6 subidas) mientras que como fondo (0x02) acumula (~1 MB en 6 subidas).
  // Asi se puede emitir durante horas sin llenar su memoria.
  if (!process.env.COUGAR_TIPO) process.env.COUGAR_TIPO = '1';
  const png = process.env.COUGAR_PNG
    || 'C:/Users/Maximo/AppData/Local/Temp/cougar-stream.png';
  const recortar = process.env.COUGAR_RECORTAR === '1';
  console.log(`stream: reflejando la pantalla cada ${periodoMs / 1000}s` +
    (recortar ? ' (banda central)' : ' (ajustada con bandas negras)') +
    (repeticiones ? `, ${repeticiones} fotogramas` : ', sin limite (Ctrl+C para parar)'));
  const estadoInicial = await pedirRapido(dev, pendientes, 'conn (comprobacion)',
    'POST', 'conn', undefined, 3000);
  const cuerpoInicial = estadoInicial ? estadoInicial.split('\r\n\r\n')[1] || '' : '';
  if (/"osdState":1/.test(cuerpoInicial)) {
    console.log('!! osdState=1: la capa OSD se vera ENCIMA. Manda antes: recovery');
  }
  await pedir(dev, pendientes, 'power resume', 'POST', 'power', { event: 'resume' }, 800);
  await pedir(dev, pendientes, 'brightness 100', 'POST', 'brightness', { value: 100 }, 800);
  let n = 0;
  while (!repeticiones || n < repeticiones) {
    n++;
    const t0 = Date.now();
    const codigo = capturarPantalla(png, recortar);
    if (codigo !== 0) console.error(`!! la captura devolvio codigo ${codigo}`);
    let kb = 0;
    try { kb = Math.round(require('fs').statSync(png).size / 1024); } catch (e) { /* nada */ }
    const ok = await enviarArchivo(dev, pendientes, png, true);
    const ms = Date.now() - t0;
    console.log(`fotograma ${n}: ${ok === 0 ? 'subido' : 'FALLO'}  ${kb} KB  (${ms} ms)`);
    const fin = Date.now() + Math.max(0, periodoMs - ms);
    let k = 0;
    while (Date.now() < fin) {
      try {
        dev.write(buildReport('STATE', 'all', telemetriaDemo(k++)).report);
      } catch (err) { /* el panel puede estar reiniciando */ }
      await dormir(Math.min(1000, Math.max(50, fin - Date.now())));
    }
  }
  console.log('stream terminado');
}

async function bucle(dev, pendientes, periodoMs, repeticiones) {
  // Igual que en el stream: los fotogramas continuos van como capa OSD (0x01) para que el
  // panel reutilice el hueco y no se llene su memoria.
  if (!process.env.COUGAR_TIPO) process.env.COUGAR_TIPO = '1';
  const png = process.env.COUGAR_PNG || 'C:/Users/Maximo/AppData/Local/Temp/panel_cfv235.png';
  const guion = process.env.COUGAR_RENDER
    || 'C:/Users/Maximo/Desktop/CFV235-Linux/dashboard/panel-dashboard.ps1';
  console.log(`bucle: render + subir cada ${periodoMs / 1000}s` +
    (repeticiones ? `, ${repeticiones} fotogramas` : ', sin limite (Ctrl+C para parar)'));
  // Comprobacion previa: con osdState=1 hay una capa OSD activa (la pone el editor al
  // aplicar un tema) y se dibujara ENCIMA de nuestro dashboard.
  const estadoInicial = await pedirRapido(dev, pendientes, 'conn (comprobacion)',
    'POST', 'conn', undefined, 3000);
  const cuerpoInicial = estadoInicial ? estadoInicial.split('\r\n\r\n')[1] || '' : '';
  const mOsd = /"osdState":(\d+)/.exec(cuerpoInicial);
  if (mOsd && mOsd[1] === '1') {
    console.log('!! osdState=1: hay una capa OSD activa que se vera ENCIMA del dashboard.');
    console.log('   Para dejarlo limpio: node cougar_hid_node.js recovery   (Reset: borra los');
    console.log('   medios y pone osdState=0) y despues arranca el bucle otra vez.');
  } else {
    console.log(`   estado inicial: osdState=${mOsd ? mOsd[1] : '?'}` +
      ` background=${(/"background":\[([^\]]*)\]/.exec(cuerpoInicial) || [])[1] || '?'}`);
  }
  await pedir(dev, pendientes, 'power resume', 'POST', 'power', { event: 'resume' }, 800);
  await pedir(dev, pendientes, 'brightness 100', 'POST', 'brightness', { value: 100 }, 800);
  let n = 0;
  while (!repeticiones || n < repeticiones) {
    n++;
    const t0 = Date.now();
    const codigo = renderizar(guion, png);
    if (codigo !== 0) console.error(`!! el render devolvio codigo ${codigo}`);
    const ok = await enviarArchivo(dev, pendientes, png, true);
    const ms = Date.now() - t0;
    console.log(`fotograma ${n}: ${ok === 0 ? 'subido y aceptado' : 'FALLO'}  (${ms} ms)`);
    // Mantener el panel despierto (se apaga sin flujo) hasta el siguiente fotograma.
    const fin = Date.now() + Math.max(0, periodoMs - ms);
    let k = 0;
    while (Date.now() < fin) {
      try {
        dev.write(buildReport('STATE', 'all', telemetriaDemo(k++)).report);
      } catch (err) { /* el panel puede estar reiniciando */ }
      await dormir(Math.min(1000, Math.max(50, fin - Date.now())));
    }
  }
  console.log('bucle terminado');
}

async function main() {
  const [orden, ...resto] = process.argv.slice(2);
  const { dev, pendientes } = abrir();
  try {
    if (orden === 'estado' || !orden) {
      await pedir(dev, pendientes, 'conn', 'POST', 'conn');
    } else if (orden === 'comprobar') {
      // Una linea, para usarlo en bucle desde PowerShell (abre y cierra el dispositivo en
      // cada pasada, igual que hace el editor cada ~6 s).
      const resp = await pedir(dev, pendientes, 'conn', 'POST', 'conn', undefined, 5000);
      const cuerpo = resp ? resp.split('\r\n\r\n')[1] || '' : '';
      const dato = clave => (new RegExp(`"${clave}":("?[^,}]*"?)`).exec(cuerpo) || [])[1];
      const marca = new Date().toTimeString().slice(0, 8);
      console.log(`${marca}  bootFinish=${dato('bootFinish')}  space=${dato('space')}  ` +
        `degree=${dato('degree')}  osdState=${dato('osdState')}  background=${dato('background')}  ` +
        (resp ? `code=${/1 (\d{3})/.exec(resp)[1]}` : 'SIN RESPUESTA'));
    } else if (orden === 'power') {
      await pedir(dev, pendientes, 'power resume', 'POST', 'power', { event: 'resume' });
      await pedir(dev, pendientes, 'conn (comprobar)', 'POST', 'conn');
    } else if (orden === 'recovery') {
      await pedir(dev, pendientes, 'conn ANTES', 'POST', 'conn');
      // Formato EXACTO del editor (log 20:59:07):
      //   POST recovery 1 / SeqNumber=754 / Date=... / ContentType=json
      //   ContentLength=15 / {"enable":true}
      await pedir(dev, pendientes, 'recovery {"enable":true}', 'POST', 'recovery',
        { enable: true }, 4000);
      const serial = resto[0];
      if (serial) {
        await pedir(dev, pendientes, `recovery sn=${serial}`, 'POST', 'recovery',
          { sn: serial }, 4000);
      }
      console.log('   ...esperando 10 s a que el panel reaccione...');
      await dormir(10000);
      await pedir(dev, pendientes, 'conn DESPUES', 'POST', 'conn');
    } else if (orden === 'medios') {
      // Sondeo del listado de medios del panel.
      await pedir(dev, pendientes, 'GET waterBlockScreen', 'GET', 'waterBlockScreen', undefined, 5000);
      await pedir(dev, pendientes, 'POST waterBlockScreen', 'POST', 'waterBlockScreen', undefined, 5000);
      await pedir(dev, pendientes, 'POST waterBlockScreenId', 'POST', 'waterBlockScreenId', undefined, 5000);
    } else if (orden === 'borrar') {
      // Prueba formas de borrado. Con un nombre INEXISTENTE se descubre el formato sin
      // borrar nada de verdad.
      const nombre = resto[0];
      const pruebas = [
        ['DELETE', 'mediaDelete', { path: nombre }],
        ['POST', 'mediaDelete', { path: nombre }],
        ['DELETE', 'mediaDelete', { fileName: nombre }],
        ['POST', 'mediaDelete', { fileName: nombre }],
      ];
      for (const [m, c, b] of pruebas) {
        const r = await pedir(dev, pendientes, `${m} ${c} ${JSON.stringify(b)}`, m, c, b, 4000);
        const code = r ? (/1 (\d{3})/.exec(r) || [])[1] : 'sin respuesta';
        console.log(`   -> ${code}`);
        if (code === '200') { console.log('   >>> FORMATO DE BORRADO ACEPTADO'); break; }
      }
    } else if (orden === 'eventos') {
      // El editor solo uso power {"event":"resume"}. Se prueban otros eventos plausibles:
      // si el panel acepta uno de reinicio, se recupera sin cortar la corriente.
      const eventos = resto.length ? resto : ['restart', 'reboot', 'reset', 'reload'];
      await pedir(dev, pendientes, 'conn (base)', 'POST', 'conn', undefined, 4000);
      for (const evento of eventos) {
        const resp = await pedir(dev, pendientes, `power {"event":"${evento}"}`, 'POST', 'power',
          { event: evento }, 4000);
        const code = resp ? (/1 (\d{3})/.exec(resp) || [])[1] : 'sin respuesta';
        console.log(`   -> power/${evento}: ${code}`);
        if (code === '200') {
          console.log(`   ...200 con "${evento}": esperando 20 s y comprobando...`);
          await dormir(20000);
          await pedir(dev, pendientes, 'conn (tras el evento)', 'POST', 'conn', undefined, 5000);
        }
      }
    } else if (orden === 'medios') {
      // Sondeo del listado de medios del panel.
      await pedir(dev, pendientes, 'GET waterBlockScreen', 'GET', 'waterBlockScreen', undefined, 5000);
      await pedir(dev, pendientes, 'POST waterBlockScreen', 'POST', 'waterBlockScreen', undefined, 5000);
      await pedir(dev, pendientes, 'POST waterBlockScreenId', 'POST', 'waterBlockScreenId', undefined, 5000);
    } else if (orden === 'borrar') {
      // Prueba formas de borrado. Con un nombre INEXISTENTE se descubre el formato sin
      // borrar nada de verdad.
      const nombre = resto[0];
      const pruebas = [
        ['DELETE', 'mediaDelete', { path: nombre }],
        ['POST', 'mediaDelete', { path: nombre }],
        ['DELETE', 'mediaDelete', { fileName: nombre }],
        ['POST', 'mediaDelete', { fileName: nombre }],
      ];
      for (const [m, c, b] of pruebas) {
        const r = await pedir(dev, pendientes, `${m} ${c} ${JSON.stringify(b)}`, m, c, b, 4000);
        const code = r ? (/1 (\d{3})/.exec(r) || [])[1] : 'sin respuesta';
        console.log(`   -> ${code}`);
        if (code === '200') { console.log('   >>> FORMATO DE BORRADO ACEPTADO'); break; }
      }
    } else if (orden === 'transport') {
      process.exitCode = await subir(dev, pendientes, resto[0], true);
    } else if (orden === 'subir') {
      process.exitCode = await subir(dev, pendientes, resto[0], false);
    } else if (orden === 'raw') {
      const [method, cmd, json] = resto;
      await pedir(dev, pendientes, `${method} ${cmd}`, method, cmd, json ? JSON.parse(json) : undefined);
    } else if (orden === 'vigilar') {
      // Repite conn cada ~6 s, como hace el editor. El log demuestra que el panel pasa a
      // bootFinish=1 por si solo tras varias rondas: hay que insistir, no basta con una.
      const segundos = Number(resto[0] || 120);
      const limite = Date.now() + segundos * 1000;
      let ultimo = null;
      let ronda = 0;
      while (Date.now() < limite) {
        ronda += 1;
        const resp = await pedir(dev, pendientes, `conn #${ronda}`, 'POST', 'conn', undefined, 6000);
        const cuerpo = resp ? resp.split('\r\n\r\n')[1] || '' : '';
        const m = /"bootFinish":(\d+)/.exec(cuerpo);
        const boot = m ? m[1] : '?';
        const marca = new Date().toTimeString().slice(0, 8);
        if (boot !== ultimo) {
          console.log(`   [${marca}] bootFinish = ${boot}` +
            (resp ? '' : '  (sin respuesta)'));
          ultimo = boot;
        }
        if (boot === '1') {
          console.log(`\n>>>> bootFinish = 1: el panel ha arrancado. Ya acepta ordenes.\n`);
          console.log(cuerpo);
          return;
        }
      }
      console.log(`   terminado sin ver bootFinish=1 (ultimo: ${ultimo})`);
    } else if (orden === 'mantener') {
      // Emula al editor: STATE all cada segundo. El panel se APAGA si no recibe trafico
      // (por eso el editor no para de mandar telemetria). Este modo sirve para comprobar
      // si una imagen subida se ve mientras hay flujo.
      const segundos = Number(resto[0] || 60);
      const imagen = resto[1];
      console.log(`manteniendo el panel despierto ${segundos} s` + (imagen ? ` y reenviando ${imagen}` : ''));
      await pedir(dev, pendientes, 'power resume', 'POST', 'power', { event: 'resume' }, 1500);
      await pedir(dev, pendientes, 'brightness 100', 'POST', 'brightness', { value: 100 }, 1500);
      const fin = Date.now() + segundos * 1000;
      let n = 0;
      while (Date.now() < fin) {
        n++;
        const telemetria = {
          network: { upload: 0, download: 1 },
          memory: { total: 32675, used: 11000 + (n % 500), load: 33, temperature: 0, speed: 1065 },
          cpu: { load: 8 + (n % 40), temperature: 45 + (n % 5), speedAverage: 3900,
                 power: 45, voltage: 1.05, usage: 5 },
          gpu: { load: 3, temperature: 32, fan: 0, speed: 80, power: 0, voltage: 0.6 },
          disk: { total: 465, used: 50, load: 10, activity: 0, temperature: 0,
                  readSpeed: 0, writeSpeed: 0 },
          fans: [{ onBoard: true, type: 'Pump', name: 'Fan AIO Pump', value: 2500 },
                 { onBoard: true, type: 'Fan', name: 'Fan CPU', value: 800 },
                 { onBoard: true, type: 'Chassis', name: 'Fan Chassis3', value: 600 }],
          motherboard: { temperature: 25 },
          timestamp: Date.now(),
        };
        try {
          dev.write(buildReport('STATE', 'all', telemetria).report);
        } catch (err) {
          console.error(`!! ${err.message}`);
          break;
        }
        await dormir(1000);
        if (n % 15 === 0) console.log(`   ${n} tramas STATE all enviadas`);
      }
      console.log(`fin: ${n} tramas enviadas`);
    } else if (orden === 'bucle') {
      const periodoMs = Number(resto[0] || 5) * 1000;
      const repeticiones = Number(resto[1] || 0);
      await bucle(dev, pendientes, periodoMs, repeticiones);
    } else if (orden === 'stream') {
      const periodoMs = Number(resto[0] || 2) * 1000;
      const repeticiones = Number(resto[1] || 0);
      await stream(dev, pendientes, periodoMs, repeticiones);
    } else if (orden === 'nodormir') {
      // enable = permitir que la pantalla se apague por espera.
      //   nodormir          -> {"enable":false}  = se queda encendido sin flujo
      //   nodormir dormir   -> {"enable":true}   = vuelve a permitir el apagado
      const dormir = resto[0] === 'dormir';
      const r = await pedirRapido(dev, pendientes, `displayInSleep enable=${dormir}`,
        'POST', 'displayInSleep', { enable: dormir }, 3000);
      const code = r ? (/1 (\d{3})/.exec(r) || [])[1] : null;
      console.log(code === '200'
        ? `apagado por espera: ${dormir ? 'PERMITIDO (se apagara sin flujo)' : 'DESACTIVADO (se queda encendido sin flujo)'}`
        : `!! no aceptado (code=${code})`);
    } else if (orden === 'barrido') {
      // Barrido de comandos pendientes CON CUERPO. Los que probamos sin cuerpo daban 400,
      // pero en este protocolo casi todo exige cuerpo: sin el, el 400 no informa de nada.
      const pruebas = [
        ['POST', 'config', {}],
        ['POST', 'config', { enable: true }],
        ['POST', 'displayInSleep', { enable: true }],
        ['POST', 'displayInSleep', { value: 1 }],
        ['POST', 'fanLCDSet', { enable: true }],
        ['POST', 'fanLCDSet', { value: 100 }],
        ['POST', 'sysinfoDisplay', { enable: true }],
        ['POST', 'realtimeDisplay', { enable: true }],
        ['POST', 'waterBlockScreen', {}],
        ['POST', 'waterBlockScreen', { type: 'media' }],
        ['POST', 'waterBlockScreenId', { id: 0 }],
        ['GET', 'waterBlockScreen', undefined],
        ['STATE', 'waterBlockScreen', undefined],
        ['POST', 'mediaDelete', { path: 'no_existe.png' }],
        ['POST', 'media', { type: 'media' }],
        ['GET', 'media', undefined],
        ['POST', 'conn', { enable: true }],
        ['DELETE', 'conn', undefined],
        ['POST', 'brightness', { value: 100 }],   // control: este SI debe dar 200
      ];
      console.log('=== barrido de comandos pendientes (con cuerpo) ===');
      for (const [metodo, cmd, cuerpo] of pruebas) {
        const etq = `${metodo} ${cmd} ${cuerpo ? JSON.stringify(cuerpo) : ''}`.trim();
        const r = await pedirRapido(dev, pendientes, etq, metodo, cmd, cuerpo, 3000);
        const code = r ? (/1 (\d{3})/.exec(r) || [])[1] : null;
        const body = r ? (r.split('\r\n\r\n')[1] || '') : '';
        const marca = code === '200' ? 'ACEPTA' : (code ? code : 'sin respuesta');
        console.log(`   ${marca.padEnd(14)} ${etq}` + (body ? `   -> ${body.slice(0, 80)}` : ''));
        await dormir(400);
      }
    } else if (orden === 'efectos') {
      // Que CAMBIA cada comando recien descubierto. Se lee el estado antes y despues.
      const campos = ['bootFinish', 'space', 'brightness', 'degree', 'osdState', 'mode',
                      'logo', 'timeout', 'displayInSleep', 'background'];
      const leerEstado = async () => {
        const r = await pedirRapido(dev, pendientes, 'conn (leer)', 'POST', 'conn', undefined, 3000);
        const cuerpo = r ? (r.split('\r\n\r\n')[1] || '') : '';
        const salida = {};
        for (const c of campos) {
          const m = new RegExp(`"${c}":("?[^,}]*"?)`).exec(cuerpo);
          if (m) salida[c] = m[1];
        }
        return salida;
      };
      const pruebas = [
        ['displayInSleep enable=true', 'POST', 'displayInSleep', { enable: true }],
        ['displayInSleep enable=false', 'POST', 'displayInSleep', { enable: false }],
        ['realtimeDisplay enable=true', 'POST', 'realtimeDisplay', { enable: true }],
        ['realtimeDisplay enable=false', 'POST', 'realtimeDisplay', { enable: false }],
        ['realtimeDisplay sin cuerpo', 'POST', 'realtimeDisplay', undefined],
        ['fanLCDSet enable=true', 'POST', 'fanLCDSet', { enable: true }],
      ];
      console.log('=== que cambia cada comando ===');
      let anterior = await leerEstado();
      console.log(`   estado inicial: ${JSON.stringify(anterior)}`);
      for (const [etq, metodo, cmd, cuerpo] of pruebas) {
        const r = await pedirRapido(dev, pendientes, etq, metodo, cmd, cuerpo, 3000);
        const code = r ? (/1 (\d{3})/.exec(r) || [])[1] : null;
        await dormir(1200);
        const ahora = await leerEstado();
        const cambios = Object.keys(ahora).filter(c => ahora[c] !== anterior[c])
          .map(c => `${c}: ${anterior[c]} -> ${ahora[c]}`);
        console.log(`   ${String(code || 'sin respuesta').padEnd(14)} ${etq}` +
          (cambios.length ? `\n        CAMBIA: ${cambios.join(' | ')}` : '   (sin cambios)'));
        anterior = ahora;
        await dormir(500);
      }
    } else if (orden === 'listen') {
      const segundos = Number(resto[0] || 20);
      console.log(`escuchando ${segundos} s...`);
      await dormir(segundos * 1000);
    } else {
      console.error('orden desconocida: ' + orden);
      process.exitCode = 2;
    }
  } catch (err) {
    console.error(`!! ${err.message}`);
    process.exitCode = 1;
  } finally {
    try { dev.close(); } catch (e) { /* nada */ }
  }
}

main();
