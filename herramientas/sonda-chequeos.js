/* Sonda de comprobacion: manda peticiones concretas e imprime SOLO la respuesta que toca.
 *
 *   node sonda-chequeos.js conn
 *   node sonda-chequeos.js waterblock power-restart mode0 realtime-off
 *   node sonda-chequeos.js upload <fichero> [--osd] [--repeticiones N]
 *   node sonda-chequeos.js esperar <minutos>
 *
 * Necesita COUGAR_NODE_HID apuntando a un node-hid.
 */
'use strict';

const fs = require('fs');
const path = require('path');

const { HID, devices } = require(process.env.COUGAR_NODE_HID || 'node-hid');

const START = 0x5a, ESC = 0x5b, INFORME = 1025;
const VID = 0x1d6b, PID = 0x0126;
const WIRE = 0x5c, TIPO_FONDO = 0x02, TIPO_OSD = 0x01, TROZO = 1000, CAB = 24;

// ------------------------------------------------------------------ framing
function checksum(payload, total) {
  return ((total >> 8) + (total & 0xff) + payload.reduce((a, b) => a + b, 0)) & 0xff;
}
function escapar(v, out) {
  if (v === START || v === ESC) out.push(ESC, v === START ? 1 : 2); else out.push(v);
}
function trama(payload) {
  const total = payload.length + 5;
  const t = [START];
  escapar((total >> 8) & 0xff, t); escapar(total & 0xff, t);
  for (const b of payload) escapar(b, t);
  escapar(checksum(payload, total), t);
  t.push(START);
  return Buffer.from(t);
}
let seq = 0;
function peticion(cmd, cuerpo, metodo = 'POST') {
  seq += 1;
  let cab = `${metodo} ${cmd} 1\r\nSeqNumber=${seq}\r\nDate=${Date.now()}\r\n`;
  let payload = Buffer.from(cab, 'ascii');
  if (cuerpo !== undefined && cuerpo !== null) {
    const bruto = Buffer.from(JSON.stringify(cuerpo), 'utf8');
    payload = Buffer.concat([payload,
      Buffer.from(`ContentType=json\r\nContentLength=${bruto.length}\r\n\r\n`, 'ascii'), bruto]);
  } else {
    payload = Buffer.concat([payload, Buffer.from('\r\n', 'ascii')]);
  }
  return { t: trama(payload), seq };
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
function decodifica(t) {
  const decl = (t[1] << 8) | t[2];
  const payload = t.slice(3, t.length - 2);
  return { payload, decl, cable: t.length,
           checksumOk: checksum([...payload], decl) === t[t.length - 2] };
}
function analiza(payload) {
  const txt = payload.toString('ascii');
  const i = txt.indexOf('\r\n\r\n');
  const cab = i >= 0 ? txt.slice(0, i) : txt;
  const cuerpo = i >= 0 ? payload.slice(i + 4).toString('utf8') : '';
  const m = cab.match(/(\d{3})/);
  const a = cab.match(/AckNumber=(\d+)/);
  return { code: m ? +m[1] : null, ack: a ? +a[1] : null, cuerpo };
}

// ------------------------------------------------------------------ panel
const dev = devices().find((d) => d.vendorId === VID && d.productId === PID);
if (!dev) { console.error('no encuentro el panel'); process.exit(1); }
const hid = new HID(dev.path);
let buffer = Buffer.alloc(0);
const recibidas = [];
const avisos = [];                 // respuestas con AckNumber=0: acuses de bloque y avisos
hid.on('data', (d) => {
  let b = Buffer.from(d);
  if (b.length > 1 && b[0] === 0x00 && b[1] === START) b = b.slice(1);
  buffer = Buffer.concat([buffer, b]);
  const { out, resto } = extraer(buffer);
  buffer = resto;
  for (const t of out) {
    const info = decodifica(t);
    const r = analiza(info.payload);
    r.checksumOk = info.checksumOk;
    r.decl = info.decl;
    r.cable = info.cable;
    r.cuando = Date.now();
    if (r.ack === 0) { avisos.push(r); continue; }   // no se empareja con nada
    recibidas.push(r);
  }
});
const dormir = (ms) => new Promise((r) => setTimeout(r, ms));
// Un reinicio del panel corta el USB: el error se avisa y se sigue, no se cae el proceso.
hid.on('error', (e) => console.error('  !! error del dispositivo: '
  + (e && e.message ? e.message : e)));

function enviar(t) {
  // En Windows (hidapi) hay que anteponer el byte de report ID 0x00: el informe son 1025 B.
  const cuerpo = Buffer.concat([Buffer.from([0x00]), t]);
  hid.write([...Buffer.concat([cuerpo, Buffer.alloc(Math.max(0, INFORME - cuerpo.length))])]);
}
async function pedir(cmd, cuerpo, metodo, timeout = 3000) {
  const { t, seq: s } = peticion(cmd, cuerpo, metodo);
  recibidas.length = 0;                    // nada de respuestas viejas de antes de pedir
  const desde = Date.now();
  enviar(t);
  while (Date.now() - desde < timeout) {
    for (let i = 0; i < recibidas.length; i += 1) {
      const r = recibidas[i];
      // El panel acusa con AckNumber = seq + 1; tambien se ha medido el mismo seq.
      if (r.ack !== s + 1 && r.ack !== s) continue;
      if (r.cuando < desde) continue;
      recibidas.splice(i, 1);
      r.ms = Date.now() - desde;
      return r;
    }
    await dormir(10);
  }
  return { code: null, cuerpo: '', ms: timeout };
}
function muestra(etiqueta, r) {
  const cuerpo = r.cuerpo.trim().replace(/\s+/g, ' ').slice(0, 150);
  console.log(`  ${etiqueta.padEnd(26)} code=${String(r.code).padEnd(4)} ack=${String(r.ack).padEnd(5)} `
    + `${String(r.ms).padStart(5)} ms  ${cuerpo}`);
}
async function conn() {
  const r = await pedir('conn', null, 'POST', 4000);
  if (r.cuerpo) {
    try { r.props = JSON.parse(r.cuerpo); } catch (e) { r.props = null; }
  }
  return r;
}

// ------------------------------------------------------------------ subida
async function subir(ruta, tipo) {
  const datos = fs.readFileSync(ruta);
  const bloques = Math.ceil(datos.length / TROZO);
  const avisosAntes = avisos.length;
  const t0 = Date.now();
  const uno = await pedir('transport', { type: 'media', fileSize: datos.length,
                                        fileName: path.basename(ruta) }, 'POST', 6000);
  const t1 = Date.now();
  if (uno.code !== 200 || !uno.cuerpo.includes('blockMaxSize')) {
    muestra('transport', uno);
    return { ok: false, motivo: 'handshake rechazado' };
  }
  for (let i = 0; i < bloques; i += 1) {
    const trozo = datos.slice(i * TROZO, (i + 1) * TROZO);
    const cab = Buffer.alloc(CAB);
    cab[0] = WIRE;
    const tam = 21 + trozo.length;
    cab[1] = (tam >> 8) & 0xff; cab[2] = tam & 0xff;
    cab[3] = 0x16;
    cab[4] = (bloques >> 8) & 0xff; cab[5] = bloques & 0xff;
    cab[6] = (i >> 8) & 0xff; cab[7] = i & 0xff;
    cab[8] = tipo;
    hid.write([...Buffer.concat([Buffer.from([0x00]), cab, trozo])]);
  }
  const t2 = Date.now();
  await dormir(6);
  const tres = await pedir('transported', { md5: 'todo', fileName: path.basename(ruta) }, 'POST', 8000);
  const t3 = Date.now();
  const nuevos = avisos.slice(avisosAntes);
  const acuses = nuevos.filter((a) => a.code === 200).length;
  const rechazos = nuevos.filter((a) => a.code === 400).length;
  return { ok: tres.code === 200 && tres.cuerpo.includes('success'),
           aceptado: tres.cuerpo.includes('success'),
           bloques, bytes: datos.length, acuses, rechazos,
           msTransport: t1 - t0, msBloques: t2 - t1, msTransported: t3 - t2, msTotal: t3 - t0,
           cuerpo: tres.cuerpo.trim() };
}

// ------------------------------------------------------------------ ordenes de la linea
async function main() {
  const [orden, ...resto] = process.argv.slice(2);
  if (orden === 'conn') {
    const r = await conn();
    muestra('conn', r);
    if (r.props) {
      console.log('   ' + JSON.stringify({
        len: r.decl, space: r.props.space, brightness: r.props.brightness,
        displayInSleep: r.props.displayInSleep, osdState: r.props.osdState,
        mode: r.props.mode, degree: r.props.degree, background: r.props.background,
        bootFinish: r.props.bootFinish, timeout: r.props.timeout }));
    }
    return 0;
  }
  if (orden === 'waterblock') {
    muestra('GET waterBlockScreen', await pedir('waterBlockScreen', null, 'GET'));
    muestra('POST waterBlockScreen', await pedir('waterBlockScreen', null, 'POST'));
    return 0;
  }
  if (orden === 'power-restart') {
    for (const ev of ['restart', 'resume']) {
      muestra(`power ${ev}`, await pedir('power', { event: ev }));
    }
    return 0;
  }
  if (orden === 'mode0' || orden === 'mode1' || orden === 'mode2' || orden === 'mode3') {
    const v = Number(orden.slice(4));
    muestra(`mode value=${v}`, await pedir('mode', { value: v }));
    const r = await conn();
    console.log(`   mode despues = ${r.props ? r.props.mode : '?'}`);
    return 0;
  }
  if (orden === 'mode-malo') {
    muestra('mode {"mode":1}', await pedir('mode', { mode: 1 }));
    const r = await conn();
    console.log(`   mode despues = ${r.props ? r.props.mode : '?'}  (esperado 2147483647 si el bug es real)`);
    muestra('mode value=0 (arreglo)', await pedir('mode', { value: 0 }));
    const r2 = await conn();
    console.log(`   mode tras el arreglo = ${r2.props ? r2.props.mode : '?'}`);
    return 0;
  }
  if (orden === 'nodormir' || orden === 'dormir') {
    const enable = orden === 'dormir';
    muestra(`displayInSleep enable=${enable}`,
            await pedir('displayInSleep', { enable }));
    const r = await conn();
    console.log(`   displayInSleep = ${r.props ? r.props.displayInSleep : '?'}`);
    return 0;
  }
  if (orden === 'recovery') {
    // Mide lo que los documentos discuten: cuanto tarda, y si toca espacio, fondo u osdState.
    const antes = await conn();
    if (antes.props) {
      console.log(`  antes : space=${antes.props.space} KB  osdState=${antes.props.osdState}  `
        + `bootFinish=${antes.props.bootFinish}  background=${JSON.stringify(antes.props.background)}`);
    }
    const t0 = Date.now();
    let respuesta = await pedir('recovery', { enable: true }, 'POST', 8000);
    muestra('recovery', respuesta);
    console.log(`  enviado a las ${new Date().toLocaleTimeString()}; `
      + 'se sondea conn cada 10 s hasta 15 minutos...');
    let volvio = false;
    while (Date.now() - t0 < 15 * 60 * 1000) {
      await dormir(10000);
      const seg = Math.round((Date.now() - t0) / 1000);
      let ahora = null;
      try {
        ahora = await conn();
      } catch (e) { /* el dispositivo puede desaparecer un momento */ }
      if (!ahora || !ahora.props) {
        console.log(`  [${String(seg).padStart(4)} s] sin respuesta a conn`);
        continue;
      }
      console.log(`  [${String(seg).padStart(4)} s] bootFinish=${ahora.props.bootFinish}  `
        + `space=${ahora.props.space}  osdState=${ahora.props.osdState}  `
        + `brightness=${ahora.props.brightness}  `
        + `background=${JSON.stringify(ahora.props.background)}`);
      if (ahora.props.bootFinish === 1) {
        volvio = true;
        console.log(`  VOLVIO a responder a los ${seg} s`);
        const despues = ahora.props;
        console.log(`  despues: space=${despues.space} KB  osdState=${despues.osdState}  `
          + `background=${JSON.stringify(despues.background)}`);
        if (antes.props) {
          console.log(`  --- el espacio ${despues.space === antes.props.space ? 'NO cambio' : 'cambio'} `
            + `(${antes.props.space} -> ${despues.space} KB)`);
          console.log(`  --- el fondo ${JSON.stringify(despues.background) === JSON.stringify(antes.props.background) ? 'NO cambio' : 'cambio'}`);
          console.log(`  --- osdState ${despues.osdState} (antes ${antes.props.osdState})`);
        }
        break;
      }
    }
    if (!volvio) console.log('  se agoto el tiempo de espera sin volver a responder');
    return 0;
  }
  if (orden === 'realtime') {
    const enable = (resto[0] || 'off') !== 'off';
    const antes = (await conn()).props;
    muestra(`realtimeDisplay enable=${enable}`,
            await pedir('realtimeDisplay', { enable }));
    const despues = (await conn()).props;
    console.log(`   osdState: ${antes.osdState} -> ${despues.osdState}   `
      + `space: ${antes.space} -> ${despues.space}   brightness: ${antes.brightness} -> ${despues.brightness}`);
    return 0;
  }
  if (orden === 'esperar') {
    const minutos = Number(resto[0] || 4);
    const antes = await conn();
    console.log(`  inicio: brightness=${antes.props.brightness} `
      + `displayInSleep=${antes.props.displayInSleep} (${new Date().toLocaleTimeString()})`);
    console.log(`  esperando ${minutos} min SIN ningun trafico...`);
    await dormir(minutos * 60 * 1000);
    const despues = await conn();
    console.log(`  fin   : brightness=${despues.props.brightness} `
      + `displayInSleep=${despues.props.displayInSleep} (${new Date().toLocaleTimeString()})`);
    console.log(despues.props.brightness === 0
      ? '  RESULTADO: el panel SE APAGO solo (la pantalla se apago)'
      : '  RESULTADO: el panel SIGUE ENCENDIDO');
    return 0;
  }
  if (orden === 'upload') {
    const ruta = resto[0];
    const tipo = resto.includes('--osd') ? TIPO_OSD : TIPO_FONDO;
    const repeticiones = Number((resto[resto.indexOf('--repeticiones') + 1]) || 1) || 1;
    const datos = fs.readFileSync(ruta);
    console.log(`  fichero: ${path.basename(ruta)}  ${datos.length} B  `
      + `(${(datos.length / 1024).toFixed(0)} KB, ${Math.ceil(datos.length / TROZO)} bloques) `
      + `capa ${tipo === TIPO_OSD ? 'OSD' : 'FONDO'}`);
    const antes = await conn();
    for (let i = 1; i <= repeticiones; i += 1) {
      const r = await subir(ruta, tipo);
      console.log(`  [${i}] aceptado=${r.aceptado}  transport=${r.msTransport} ms  `
        + `bloques=${r.msBloques} ms  transported=${r.msTransported} ms  TOTAL=${r.msTotal} ms  `
        + `acuses(Ack=0): ${r.acuses} ok / ${r.rechazos} rechazos`);
    }
    const despues = await conn();
    console.log(`  space: ${antes.props.space} -> ${despues.props.space} KB   `
      + `background: ${JSON.stringify(despues.props.background)}`);
    return 0;
  }
  console.error('ordenes: conn | waterblock | power-restart | mode0..3 | mode-malo | nodormir | '
    + 'dormir | realtime on|off | recovery | esperar <min> | upload <fich> [--osd] '
    + '[--repeticiones N]');
  return 2;
}

main().then((c) => { hid.close(); process.exit(c); })
  .catch((e) => { console.error('!! ' + e.message); process.exit(1); });
