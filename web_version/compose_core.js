/*
 * compose_core.js — 纯算法核心（浏览器 & Node 通用，UMD）
 *
 * 所有函数都不依赖 DOM，便于在 Node 中单元测试。
 * 数据约定：
 *   - 帧图像：Uint8ClampedArray，RGBA，长度 = w*h*4
 *   - 二值遮罩：Uint8Array，长度 = w*h，值 0 或 255
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.ComposeCore = factory();
  }
}(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // 对一组采样帧逐像素求中值，得到静态背景（RGBA）
  function medianBackground(samples, w, h) {
    const n = samples.length;
    const total = w * h;
    const out = new Uint8ClampedArray(total * 4);
    const buf = new Float64Array(n);
    for (let p = 0; p < total; p++) {
      const base = p * 4;
      for (let c = 0; c < 3; c++) {
        for (let k = 0; k < n; k++) buf[k] = samples[k][base + c];
        for (let i = 1; i < n; i++) {
          const v = buf[i];
          let j = i - 1;
          while (j >= 0 && buf[j] > v) { buf[j + 1] = buf[j]; j--; }
          buf[j + 1] = v;
        }
        out[base + c] = buf[n >> 1];
      }
      out[base + 3] = 255;
    }
    return out;
  }

  // 前景检测：|frame - bg| 各通道最大差 > threshold
  function diffMask(frame, bg, w, h, threshold) {
    const total = w * h;
    const mask = new Uint8Array(total);
    for (let p = 0; p < total; p++) {
      const b = p * 4;
      const dr = frame[b] - bg[b];
      const dg = frame[b + 1] - bg[b + 1];
      const db = frame[b + 2] - bg[b + 2];
      const d = Math.max(Math.abs(dr), Math.abs(dg), Math.abs(db));
      mask[p] = d > threshold ? 255 : 0;
    }
    return mask;
  }

  // 二值膨胀（方形结构元素，可分离实现）
  function dilate(mask, w, h, radius) {
    if (radius <= 0) return mask.slice();
    const tmp = new Uint8Array(w * h);
    const out = new Uint8Array(w * h);
    for (let y = 0; y < h; y++) {
      const row = y * w;
      for (let x = 0; x < w; x++) {
        const x0 = x - radius, x1 = x + radius;
        let m = 0;
        for (let xx = x0; xx <= x1; xx++) {
          if (xx >= 0 && xx < w && mask[row + xx]) { m = 255; break; }
        }
        tmp[row + x] = m;
      }
    }
    for (let x = 0; x < w; x++) {
      for (let y = 0; y < h; y++) {
        const y0 = y - radius, y1 = y + radius;
        let m = 0;
        for (let yy = y0; yy <= y1; yy++) {
          if (yy >= 0 && yy < h && tmp[yy * w + x]) { m = 255; break; }
        }
        out[y * w + x] = m;
      }
    }
    return out;
  }

  // 二值腐蚀（方形结构元素，可分离实现）
  function erode(mask, w, h, radius) {
    if (radius <= 0) return mask.slice();
    const tmp = new Uint8Array(w * h);
    const out = new Uint8Array(w * h);
    for (let y = 0; y < h; y++) {
      const row = y * w;
      for (let x = 0; x < w; x++) {
        const x0 = x - radius, x1 = x + radius;
        let m = 255;
        for (let xx = x0; xx <= x1; xx++) {
          if (xx >= 0 && xx < w && !mask[row + xx]) { m = 0; break; }
        }
        tmp[row + x] = m;
      }
    }
    for (let x = 0; x < w; x++) {
      for (let y = 0; y < h; y++) {
        const y0 = y - radius, y1 = y + radius;
        let m = 255;
        for (let yy = y0; yy <= y1; yy++) {
          if (yy >= 0 && yy < h && !tmp[yy * w + x]) { m = 0; break; }
        }
        out[y * w + x] = m;
      }
    }
    return out;
  }

  // 形态学闭运算：先膨胀后腐蚀
  function morphClose(mask, w, h, radius) {
    return erode(dilate(mask, w, h, radius), w, h, radius);
  }

  // 连通域面积过滤：面积 < minArea 的连通块置 0
  function filterSmallComponents(mask, w, h, minArea) {
    const total = w * h;
    const visited = new Uint8Array(total);
    const stack = new Int32Array(total);
    for (let p = 0; p < total; p++) {
      if (mask[p] && !visited[p]) {
        let top = 0;
        stack[top++] = p;
        visited[p] = 1;
        let i = 0;
        while (i < top) {
          const q = stack[i++];
          const x = q % w;
          const y = (q / w) | 0;
          if (x > 0) { const n = q - 1; if (mask[n] && !visited[n]) { visited[n] = 1; stack[top++] = n; } }
          if (x < w - 1) { const n = q + 1; if (mask[n] && !visited[n]) { visited[n] = 1; stack[top++] = n; } }
          if (y > 0) { const n = q - w; if (mask[n] && !visited[n]) { visited[n] = 1; stack[top++] = n; } }
          if (y < h - 1) { const n = q + w; if (mask[n] && !visited[n]) { visited[n] = 1; stack[top++] = n; } }
        }
        if (top < minArea) {
          for (let j = 0; j < top; j++) mask[stack[j]] = 0;
        }
      }
    }
    return mask;
  }

  // 把目标帧的前景像素叠加到 output 上
  function composite(output, frame, mask, w, h) {
    const total = w * h;
    for (let p = 0; p < total; p++) {
      if (mask[p]) {
        const b = p * 4;
        output[b] = frame[b];
        output[b + 1] = frame[b + 1];
        output[b + 2] = frame[b + 2];
        output[b + 3] = 255;
      }
    }
    return output;
  }

  return {
    medianBackground: medianBackground,
    diffMask: diffMask,
    dilate: dilate,
    erode: erode,
    morphClose: morphClose,
    filterSmallComponents: filterSmallComponents,
    composite: composite
  };
}));
