// E6 part 2 — the sandbox ENFORCES the manifest. Run: node enforce.mjs
// Modules are emitted by wasm_ingot.py; this proves the import section is not
// advisory documentation but a hard precondition for instantiation.
import { readFileSync } from 'node:fs';
import { execSync } from 'node:child_process';

execSync('python3 -c "import wasm_ingot as w, pathlib;' +
  'pathlib.Path(\'_pure.wasm\').write_bytes(w.pure_module());' +
  'pathlib.Path(\'_pert.wasm\').write_bytes(w.perturbing_module())"');

const pure = readFileSync('_pure.wasm'), pert = readFileSync('_pert.wasm');
const a = new WebAssembly.Instance(new WebAssembly.Module(pure), {});
console.log('  pure module, instantiated with NO imports at all -> add(17,25) =', a.exports.add(17, 25));

try {
  new WebAssembly.Instance(new WebAssembly.Module(pert), {});
  console.log('  perturbing module instantiated without imports   <-- UNEXPECTED, claim is false');
} catch (e) {
  console.log('  perturbing module REFUSES to instantiate without its declared import:');
  console.log('    ' + String(e).split('\n')[0]);
}
let calls = 0;
const b = new WebAssembly.Instance(new WebAssembly.Module(pert), { env: { now: () => (calls++, 1234) } });
console.log('  perturbing module, host clock supplied -> stamp() =', b.exports.stamp(), `(host reached ${calls}x)`);
console.log('\n  => the import section is a hard precondition, not documentation.');
