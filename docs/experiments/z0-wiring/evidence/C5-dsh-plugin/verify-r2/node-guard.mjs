// verifier guard: refuse 11501 and non-loopback TCP connects; log violations
import net from 'node:net'
import {appendFileSync} from 'node:fs'
const log = process.env.VERIFY_GUARD_LOG
const orig = net.Socket.prototype.connect
net.Socket.prototype.connect = function (...args) {
  let o = args[0]; if (Array.isArray(o)) o = o[0]
  let port, host
  if (typeof o === 'object' && o) { port = o.port; host = o.host } else { port = o; host = typeof args[1] === 'string' ? args[1] : undefined }
  if (port !== undefined && o?.path === undefined) {
    const h = host ?? 'localhost'
    const bad = String(port) === '11501' || !(h === '127.0.0.1' || h === 'localhost' || h === '::1')
    if (bad) { try { appendFileSync(log, `node ${h}:${port}\n`) } catch {} ; throw new Error('verify-guard: blocked ' + h + ':' + port) }
  }
  return orig.apply(this, args)
}
