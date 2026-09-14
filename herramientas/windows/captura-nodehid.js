// captura-nodehid.js
// ---------------------------------------------------------------------------
// Intercepta node-hid y vuelca a un fichero TODO lo que el COUGAR LCD Editor
// lee y escribe por USB HID. Sirve para capturar el protocolo completo,
// incluidos los BLOQUES DE FICHERO de la subida (lo unico que falta).
//
// No hay que modificar el app.asar: se carga con NODE_OPTIONS=--require ...
//
// Uso (en una consola de ADMINISTRADOR):
//   $env:NODE_OPTIONS = '--require C:\Users\Maximo\Desktop\CFV235-Linux\instrumentar\captura-nodehid.js'
//   & 'C:\Program Files\COUGAR LCD Editor\COUGAR LCD Editor.exe'
//
// El volcado va a: %USERPROFILE%\Desktop\CFV235-Linux\hid-captura.log
// (o a la ruta de la variable de entorno COUGAR_HID_LOG)
//
// Cada linea del log es:  W <ms> <hex>   -> escrito por la app (hacia el panel)
//                         R <ms> <hex>   -> leido de la app (del panel)
// ---------------------------------------------------------------------------
'use strict';

const fs = require('fs');
const path = require('path');

const LOG = process.env.COUGAR_HID_LOG ||
  path.join(process.env.USERPROFILE || '.', 'Desktop', 'CFV235-Linux', 'hid-captura.log');

function log(linea) {
  try {
    fs.appendFileSync(LOG, linea + '\n');
  } catch (e) {
    /* si no se puede escribir, no rompemos la app */
  }
}

function hex(dato) {
  try {
    return Buffer.from(dato).toString('hex');
  } catch (e) {
    return '(no convertible)';
  }
}

log('# ===== captura iniciada ' + new Date().toISOString() + ' pid=' + process.pid + ' =====');

let parcheado = false;

try {
  const Module = require('module');
  const requireOriginal = Module.prototype.require;

  Module.prototype.require = function (id) {
    const modulo = requireOriginal.apply(this, arguments);

    if (!parcheado && modulo && modulo.HID && modulo.HID.prototype && /node-hid/.test(id)) {
      const proto = modulo.HID.prototype;
      parcheado = true;

      // --- escrituras ---
      for (const nombre of ['write', 'sendFeatureReport', 'getFeatureReport']) {
        const original = proto[nombre];
        if (typeof original !== 'function') continue;
        proto[nombre] = function (...args) {
          const dato = args.find((a) => a && (Buffer.isBuffer(a) || Array.isArray(a) || a instanceof Uint8Array));
          if (dato) log('W ' + Date.now() + ' ' + hex(dato));
          return original.apply(this, args);
        };
      }

      // --- lecturas (read acepta callback) ---
      const readOriginal = proto.read;
      if (typeof readOriginal === 'function') {
        proto.read = function (cb) {
          if (typeof cb !== 'function') return readOriginal.apply(this, arguments);
          return readOriginal.call(this, function (err, data) {
            if (data) log('R ' + Date.now() + ' ' + hex(data));
            return cb(err, data);
          });
        };
      }

      // --- tambien los eventos 'data' del stream, por si se usa ese camino ---
      const onOriginal = proto.on || proto.addListener;
      if (typeof onOriginal === 'function') {
        const envolver = function (evento, manejador) {
          if (evento === 'data' && typeof manejador === 'function') {
            const envuelto = function (data) {
              if (data) log('R ' + Date.now() + ' ' + hex(data));
              return manejador.apply(this, arguments);
            };
            return onOriginal.call(this, evento, envuelto);
          }
          return onOriginal.apply(this, arguments);
        };
        if (proto.on) proto.on = envolver;
        else proto.addListener = envolver;
      }

      log('# node-hid interceptado correctamente');
    }

    return modulo;
  };

  log('# enganche de require instalado');
} catch (e) {
  log('# ERROR instalando el enganche: ' + e.message);
}

// Si el proceso se cierra, deja constancia
process.on('exit', () => log('# ===== captura terminada ' + new Date().toISOString() + ' ====='));
