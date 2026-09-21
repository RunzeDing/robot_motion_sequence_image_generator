const C = require("./compose_core.js");

function eq(name, actual, expected) {
  const a = JSON.stringify(Array.from(actual));
  const e = JSON.stringify(Array.from(expected));
  if (a !== e) { console.error("FAIL", name, "got", a, "want", e); process.exitCode = 1; }
  else console.log("ok  ", name);
}

// ---- medianBackground (2x1, 3 samples; pixel1 has object in one frame) ----
let w = 2, h = 1;
const s0 = new Uint8ClampedArray([100,100,100,255, 100,100,100,255]);
const s1 = new Uint8ClampedArray([100,100,100,255, 200,200,200,255]);
const s2 = new Uint8ClampedArray([100,100,100,255, 100,100,100,255]);
let bg = C.medianBackground([s0,s1,s2], w, h);
eq("median bg", bg, [100,100,100,255, 100,100,100,255]);

// ---- diffMask ----
let frame = new Uint8ClampedArray([100,100,100,255, 200,200,200,255]);
let mask = C.diffMask(frame, bg, w, h, 30);
eq("diff mask", mask, new Uint8Array([0, 255]));

// ---- morphClose fills a 1-px hole ----
mask = C.morphClose(new Uint8Array([255,0,255]), 3, 1, 1);
eq("morphClose", mask, new Uint8Array([255,255,255]));

// ---- dilate ----
mask = C.dilate(new Uint8Array([0,255,0]), 3, 1, 1);
eq("dilate", mask, new Uint8Array([255,255,255]));

// ---- erode ----
mask = C.erode(new Uint8Array([255,255,0]), 3, 1, 1);
eq("erode", mask, new Uint8Array([255,0,0]));

// ---- filterSmallComponents: remove isolated 1-px, keep 2-px blob ----
// 4x1 grid: [255,255,0,255] -> blob {0,1}, isolated {3}
let m = new Uint8Array([255,255,0,255]);
C.filterSmallComponents(m, 4, 1, 2);
eq("filterSmall", m, new Uint8Array([255,255,0,0]));

// ---- composite overlay ----
let out = new Uint8ClampedArray(bg);
C.composite(out, frame, new Uint8Array([0,255]), 2, 1);
eq("composite", out, new Uint8ClampedArray([100,100,100,255, 200,200,200,255]));

console.log("core tests done");
