// Spike runner: live frame pacing + deterministic 30 fps capture to MP4 + stills.
// usage: node capture.mjs <outDir>
import { chromium } from 'playwright';
import { execFileSync } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const out = process.argv[2] || path.join(here, 'out');
mkdirSync(path.join(out, 'frames'), { recursive: true });
const url = pathToFileURL(path.join(here, 'index.html')).href;

const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome' });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });

await page.goto(url + '#live');
const fps = await page.evaluate(() => window.fpsReport);
console.log('live pacing', JSON.stringify(fps));

await page.goto(url);
const dur = await page.evaluate(() => window.SEGMENT.duration);
const t0 = Date.now();
for (let i = 0, n = dur * 30; i < n; i++) {
  await page.evaluate(t => window.renderAt(t), i / 30);
  await page.locator('canvas').screenshot({ path: path.join(out, 'frames', `f${String(i).padStart(4, '0')}.png`) });
}
console.log('captured', dur * 30, 'frames in', ((Date.now() - t0) / 1000).toFixed(1), 's');
for (const s of [2, 4.2, 7, 9.6, 13]) {
  await page.evaluate(t => window.renderAt(t), s);
  await page.locator('canvas').screenshot({ path: path.join(out, `still-${s}.png`) });
}
await browser.close();
execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-framerate', '30', '-i', path.join(out, 'frames', 'f%04d.png'),
  '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '20', path.join(out, 'segment.mp4')]);
console.log('wrote', path.join(out, 'segment.mp4'));
