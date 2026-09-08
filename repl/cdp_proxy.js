// The public CDP endpoint is disconnected while the coordinator owns mutations.
const net = require('net');
const os = require('os');
let locked = false;
const sockets = new Set();
function lock(value) {
  locked = value;
  if (locked) for (const socket of sockets) socket.destroy();
}
function start() {
  const address = Object.values(os.networkInterfaces()).flat().find(a => a.family === 'IPv4' && !a.internal).address;
  return net.createServer(client => {
    if (locked) return client.destroy();
    const upstream = net.connect(9222, '127.0.0.1');
    for (const socket of [client, upstream]) {
      sockets.add(socket);
      socket.on('close', () => sockets.delete(socket));
      socket.on('error', () => { client.destroy(); upstream.destroy(); });
    }
    client.on('close', () => upstream.destroy());
    upstream.on('close', () => client.destroy());
    client.pipe(upstream).pipe(client);
  }).listen(9222, address);
}
module.exports = {start, lock};
