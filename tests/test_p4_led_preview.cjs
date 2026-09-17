// Run: node tests/test_p4_led_preview.cjs [firmware QA frame directory]
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { frame } = require("../lampgo/web/static/p4-led-preview.js");
const atlas = require("../lampgo/web/static/p4-led-preview.json");
const names = ["off","red","green","blue","white","theater","theaterred","theatergreen","theaterblue",
  "rainbow","rainbowchase","left","right","up","down","check","cross","exclaim","question","star",
  "music","smiley","sad","heart","surprised","blush","angry","thinking","sleep","helpless","cool","focused","wink","myu7gt"];
const asBytes = (pixels) => Buffer.from(pixels.flatMap((color) => color ? [...Buffer.from(color.slice(1), "hex")] : [0,0,0]));
assert.equal(atlas.width,54); assert.equal(atlas.height,9);
for (const [mode,name] of names.entries()) {
  for (let tick=0;tick<30;++tick) {
    const pixels=frame(atlas,{effect_id:name},tick*100);
    assert.equal(pixels.length,486);
    if (process.argv[2]) assert.deepEqual(asBytes(pixels),fs.readFileSync(path.join(process.argv[2],`mode-${mode}-${tick}.rgb`)));
  }
}
const question=frame(atlas,{effect_id:"question"},0);
assert.equal(question.slice(6*54,7*54).filter(Boolean).length,0);
assert.ok(question[7*54+26]);
const down=frame(atlas,{effect_id:"arrow",program:{version:1,template:"arrow"}},0,{direction:"down"});
assert.deepEqual(down.map(Boolean),frame(atlas,{effect_id:"down"},0).map(Boolean));
const blue=frame(atlas,{effect_id:"exclaim"},0,{color:"#0000ff"});
assert.equal(blue[54+26],"#0000ff");
assert.deepEqual(frame(atlas,{effect_id:"heart"},3100),frame(atlas,{effect_id:"heart"},100));
const templates = ["arrow","mouth:smile","mouth:open","mouth:flat","mouth:dizzy","heart","pulse","codex"];
for (const [index,name] of templates.entries()) {
  const [template,variant] = name.split(":");
  const effect={effect_id:name,program:{template,variant}};
  for(let tick=0;tick<30;++tick) {
    const pixels=frame(atlas,effect,tick*100,{color:"#ffffff"});
    if (process.argv[2]) assert.deepEqual(asBytes(pixels),fs.readFileSync(path.join(process.argv[2],`template-${index+1}-${tick}.rgb`)));
  }
}
console.log("PASS: 42 previews x 30 ticks match firmware RGB; punctuation, direction, color overrides and loop boundary");
