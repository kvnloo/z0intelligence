// C5 node socket guard (preload with --import): refuse and log any TCP connect to port 11501 or a non-loopback
// address, mirroring the Python sitecustomize guard. Unix sockets and loopback private ports pass.
import net from 'node:net'
import {appendFileSync} from 'node:fs'
const LOG = process.env.Z0INT_SOCKET_GUARD_LOG
const orig = net.Socket.prototype.connect
function blocked(port, host) {
  if (Number(port) === 11501) return true
  const h = String(host ?? 'localhost')
  return !(h === 'localhost' || h === '::1' || h.startsWith('127.'))
}
net.Socket.prototype.connect = function (...args) {
  let opts = args[0]
  if (Array.isArray(opts)) opts = opts[0]
  let port, host
  if (opts && typeof opts === 'object') { port = opts.port; host = opts.host; if (opts.path) return orig.apply(this, args) }
  else { port = args[0]; host = typeof args[1] === 'string' ? args[1] : undefined }
  if (port !== undefined && blocked(port, host)) {
    if (LOG) appendFileSync(LOG, `${process.pid} node ${host}:${port}\n`)
    const err = Object.assign(new Error('socket guard: blocked connect'), {code: 'ECONNREFUSED'})
    process.nextTick(() => this.destroy(err))
    return this
  }
  return orig.apply(this, args)
}
