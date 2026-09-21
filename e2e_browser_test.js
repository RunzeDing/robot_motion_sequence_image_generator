// 浏览器版端到端自动化测试（Node + Chrome DevTools Protocol）
// 用法: node e2e_browser_test.js
// 依赖: 本机安装 Chrome，Node >= 22（内置 WebSocket/fetch）。路径按需修改。
const { spawn } = require("child_process");

const CHROME = "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe";
const PAGE_URL = "file:///D:/Action_Sequence_Image_corridor/action_sequence_web.html";
const VIDEO = "D:/Action_Sequence_Image_corridor/IMG_3617.MOV";
const PORT = 9229;

const log = (...a) => console.log("[e2e]", ...a);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const chrome = spawn(CHROME, [
    "--headless", "--disable-gpu", "--no-sandbox", "--disable-crashpad",
    `--remote-debugging-port=${PORT}`,
    "--user-data-dir=D:\\Action_Sequence_Image_corridor\\.chrome-e2e",
    "about:blank",
  ], { stdio: "ignore" });

  // 等待调试端口
  let wsUrl = null;
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json/list`);
      const list = await r.json();
      const page = list.find((p) => p.type === "page");
      if (page) { wsUrl = page.webSocketDebuggerUrl; break; }
    } catch (e) { /* retry */ }
    await sleep(500);
  }
  if (!wsUrl) { log("FAIL: chrome debug port not reachable"); chrome.kill(); return; }
  log("connected:", wsUrl);

  const ws = new WebSocket(wsUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

  let mid = 0;
  const pending = new Map();
  const consoleErrors = [];
  const exceptions = [];
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) {
      const p = pending.get(m.id); pending.delete(m.id);
      m.error ? p.reject(new Error(m.error.message)) : p.resolve(m.result);
    } else if (m.method === "Runtime.exceptionThrown") {
      exceptions.push(m.params.exceptionDetails.text || JSON.stringify(m.params.exceptionDetails));
    } else if (m.method === "Log.entryAdded" && m.params.entry.level === "error") {
      consoleErrors.push(m.params.entry.text);
    } else if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error") {
      consoleErrors.push(m.params.args.map((a) => a.value || a.description).join(" "));
    }
  };
  const send = (method, params = {}) =>
    new Promise((resolve, reject) => {
      const id = ++mid;
      pending.set(id, { resolve, reject });
      ws.send(JSON.stringify({ id, method, params }));
    });
  const evalJs = async (expression, awaitPromise = false) => {
    const r = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise });
    if (r.exceptionDetails) throw new Error("eval failed: " + (r.exceptionDetails.text || ""));
    return r.result.value;
  };

  await send("Runtime.enable");
  await send("Log.enable");
  await send("Page.enable");
  await send("DOM.enable");

  // 1) 导航
  log("navigate...");
  await send("Page.navigate", { url: PAGE_URL });
  await sleep(2500);

  const coreLoaded = await evalJs("typeof ComposeCore !== 'undefined'");
  log("ComposeCore loaded:", coreLoaded);

  // 2) 设置文件输入
  const obj = await send("Runtime.evaluate", { expression: 'document.getElementById("fileInput")' });
  const objectId = obj.result.objectId;
  await send("DOM.setFileInputFiles", { files: [VIDEO], objectId });
  log("file input set to", VIDEO);

  // 3) 等待元数据加载
  let meta = null;
  for (let i = 0; i < 40; i++) {
    await sleep(500);
    meta = await evalJs(`({fc: state.frameCount, w: state.srcW, h: state.srcH, fps: state.fps, dur: state.duration, status: document.getElementById('status').textContent})`);
    if (meta.fc > 0) break;
  }
  log("metadata:", JSON.stringify(meta));

  if (!meta || meta.fc <= 0) {
    log("FAIL: video metadata never loaded (frameCount=0). console errors:", JSON.stringify(consoleErrors), "exceptions:", JSON.stringify(exceptions));
  }

  // 4) 测试帧捕获是否黑屏：seek 到帧 300，读取中心像素
  await evalJs("showFrame(300)", true);
  const px = await evalJs(`(() => { const d = captureFrameData(); const i = ((Math.floor(state.srcH/2) * state.srcW) + Math.floor(state.srcW/2)) * 4; return [d.data[i], d.data[i+1], d.data[i+2]]; })()`);
  log("center pixel @frame300 (RGB):", JSON.stringify(px));

  // 5) 配置小范围 + 与桌面一致的参数，然后生成
  await evalJs(`(() => {
    document.getElementById('startSec').value = 0;
    document.getElementById('endSec').value = 1;
    document.getElementById('intervalVal').value = 10;
    document.getElementById('history').value = 100;
    document.getElementById('varThreshold').value = 40;
    document.getElementById('kernel').value = 10;
    document.getElementById('area').value = 200;
    document.getElementById('epsilon').value = 0.01;
    document.getElementById('bgSamples').value = 3;
    document.getElementById('seed').value = 42;
    document.querySelector('input[name="mode"][value="fixed"]').checked = true;
  })()`);
  log("generating (MOG2, 3 bg samples, ~4 target frames)...");
  const genErr = await evalJs("generate().then(() => 'ok').catch(e => 'ERR:' + (e && e.message ? e.message : e))", true);
  log("generate result:", genErr);
  const res = await evalJs(`({result: !!state.result, status: document.getElementById('status').textContent, dl: !document.getElementById('downloadBtn').disabled})`);
  log("post-generate:", JSON.stringify(res));

  // 统计合成图里非背景像素（与角落背景色对比）
  let changedPx = null;
  if (res.result) {
    changedPx = await evalJs(`(() => {
      const o = state.result; const w = state.srcW, h = state.srcH;
      const i0 = 0; const r0 = o[0], g0 = o[1], b0 = o[2];
      let n = 0;
      for (let p = 0; p < w * h; p += 37) {
        const i = p * 4;
        if (Math.abs(o[i]-r0) + Math.abs(o[i+1]-g0) + Math.abs(o[i+2]-b0) > 30) n++;
      }
      return n;
    })()`);
    log("sampled changed pixels (sampling step 37):", changedPx);
  }

  log("console errors:", JSON.stringify(consoleErrors));
  log("exceptions:", JSON.stringify(exceptions));

  ws.close();
  chrome.kill();
  log("done");
}

main().catch((e) => { console.error("FATAL", e); process.exit(1); });
