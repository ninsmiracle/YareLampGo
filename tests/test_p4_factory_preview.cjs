const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {frame} = require('../lampgo/web/static/p4-led-preview.js');
const atlas = require('../lampgo/web/static/factory-faces/led.json');
const root = process.argv[2];
let count = 0;
for (const id of Object.keys(atlas.effects)) {
  const raw = fs.readFileSync(path.join(root, `${id}.rgb`));
  for (let t=0;t<30;t++) {
    const rgb = frame(atlas, {effect_id:id}, t*100).flatMap(c => {
      const n = parseInt((c||'#000000').slice(1),16);
      return [n>>16,(n>>8)&255,n&255];
    });
    assert.deepEqual(Buffer.from(rgb),raw.subarray(t*1458,(t+1)*1458),`${id} tick ${t}`);
    count++;
  }
}
console.log(`PASS: ${count} factory mouth frames match C++ pixel for pixel`);
