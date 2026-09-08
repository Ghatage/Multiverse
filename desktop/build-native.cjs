const { execFileSync } = require('node:child_process');
const path = require('node:path');
if (process.platform === 'darwin') {
  execFileSync('xcrun', ['swiftc', '-O', '-target', `${process.arch === 'arm64' ? 'arm64' : 'x86_64'}-apple-macosx13.0`, path.join(__dirname, 'native/option-trigger.swift'), '-o', path.join(__dirname, 'native/option-trigger')], {stdio:'inherit'});
  execFileSync(path.join(__dirname, 'native/option-trigger'), ['--self-test'], {stdio:'inherit'});
}
