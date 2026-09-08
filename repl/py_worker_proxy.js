const { spawn } = require('child_process');
const readline = require('readline');
class PyWorker {
  constructor() { this.pending = null; this.seq = 0; this.spawn(); }
  get alive() { return this.child && this.child.exitCode === null && !this.child.killed; }
  spawn() {
    const child = this.child = spawn('/opt/venv/bin/python3', ['-u', '/opt/fork/repl/py_worker.py'], {stdio:['pipe','pipe','pipe']});
    child.stderr.on('data', data => process.stderr.write(data));
    readline.createInterface({input:child.stdout}).on('line', line => {
      if (!this.pending || this.pending.child !== child) return;
      let result;
      try { result = JSON.parse(line); } catch { return; }
      const p = this.pending;
      if (result.id !== p.id) return;
      if (Object.hasOwn(result, "stream")) {p.stdout += result.stream; return;}
      clearTimeout(p.timer); this.pending = null;
      if (result.error) p.reject(Object.assign(new Error(result.error.message), result.error));
      else p.resolve(result);
    });
    child.on('exit', () => {
      if (this.pending?.child === child) {
        const p = this.pending; this.pending = null; clearTimeout(p.timer);
        p.reject(Object.assign(new Error('Python worker exited'), {data:{state_lost:true}}));
      }
    });
  }
  request(payload, timeout = 30000) {
    if (this.pending) return Promise.reject(new Error('Python worker busy'));
    if (!this.alive) this.spawn();
    return new Promise((resolve, reject) => {
      const id = ++this.seq, child = this.child;
      const timer = setTimeout(() => {
        const stdout = this.pending?.stdout || '';
        this.pending = null; child.kill('SIGKILL'); this.spawn();
        reject(Object.assign(new Error('Python execution timed out; state lost'), {code:-32002, data:{state_lost:true}, partial_stdout:stdout}));
      }, timeout);
      this.pending = {id, child, resolve, reject, timer, stdout:''};
      child.stdin.write(JSON.stringify({id, ...payload})+'\n');
    });
  }
  async exec(code, timeout) {
    const start = Date.now();
    const r = await this.request({code}, timeout);
    return {stdout:r.stdout, images:r.images, duration_ms:Date.now()-start, state_lost:false};
  }
  async call(method, params = {}) { return (await this.request({method,params})).value; }
}
module.exports = {PyWorker};
