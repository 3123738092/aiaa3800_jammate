import { build } from 'esbuild';
import { mkdir, copyFile, cp, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import path from 'node:path';
const require = createRequire(import.meta.url);
const root = path.resolve(import.meta.dirname, '..');
const dist = path.join(root, 'dist');
await mkdir(dist, { recursive: true });
await Promise.all(['index.html', 'style.css'].map(name => copyFile(path.join(root, 'src', name), path.join(dist, name))));
await build({ entryPoints: [path.join(root, 'src/app.js')], bundle: true, minify: true, sourcemap: false, outfile: path.join(dist, 'app.js'), format: 'esm', platform: 'browser', target: ['es2020'], logLevel: 'info' });
const basicPitchRoot = path.dirname(path.dirname(require.resolve('@spotify/basic-pitch')));
await cp(path.join(basicPitchRoot, 'model'), path.join(dist, 'model'), { recursive: true });
await copyFile(path.join(basicPitchRoot, 'LICENSE'), path.join(dist, 'BASIC-PITCH-LICENSE.txt'));
const sampleRate = 22050, duration = 4.4;
const samples = new Float32Array(Math.ceil(sampleRate * duration));
for (const [index, midi] of [60, 64, 67, 64, 62].entries()) {
  const start = .3 + index * .75, length = .57, hz = 440 * 2 ** ((midi - 69) / 12);
  for (let i = 0; i < length * sampleRate; i++) {
    const t = i / sampleRate, envelope = Math.min(1, t / .035, (length - t) / .08);
    samples[Math.floor(start * sampleRate) + i] = .22 * envelope * (Math.sin(2 * Math.PI * hz * t) + .24 * Math.sin(4 * Math.PI * hz * t));
  }
}
const wav = Buffer.alloc(44 + samples.length * 2);
wav.write('RIFF'); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ', 8); wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22); wav.writeUInt32LE(sampleRate, 24); wav.writeUInt32LE(sampleRate * 2, 28); wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34); wav.write('data', 36); wav.writeUInt32LE(samples.length * 2, 40);
samples.forEach((v, i) => wav.writeInt16LE(Math.round(Math.max(-1, Math.min(1, v)) * 32767), 44 + i * 2));
await writeFile(path.join(dist, 'example.wav'), wav);
console.log('Built JamMate demo, bundled Basic Pitch model, and synthetic audio example.');
