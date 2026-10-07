// Deterministic code-native development icon; no external asset or image dependency.
import { deflateSync } from "node:zlib";
import { mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let i = 0; i < 8; i++) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}
function chunk(type, data) {
  const body = Buffer.concat([Buffer.from(type), data]);
  const size = Buffer.alloc(4); size.writeUInt32BE(data.length);
  const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(body));
  return Buffer.concat([size, body, crc]);
}
const pixels = Buffer.alloc(32 * (1 + 32 * 4));
for (let y = 0; y < 32; y++) for (let x = 0; x < 32; x++) {
  const ink = x >= 13 && x < 19 && y >= 6 && y < 26;
  pixels.set(ink ? [136, 215, 193, 255] : [24, 36, 46, 255], y * 129 + 1 + x * 4);
}
const header = Buffer.alloc(13); header.writeUInt32BE(32); header.writeUInt32BE(32, 4); header[8] = 8; header[9] = 6;
const png = Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk("IHDR", header), chunk("IDAT", deflateSync(pixels)), chunk("IEND", Buffer.alloc(0))]);
const ico = Buffer.alloc(22); ico.writeUInt16LE(1, 2); ico.writeUInt16LE(1, 4); ico[6] = 32; ico[7] = 32;
ico.writeUInt16LE(1, 10); ico.writeUInt16LE(32, 12); ico.writeUInt32LE(png.length, 14); ico.writeUInt32LE(22, 18);
const directory = fileURLToPath(new URL("../src-tauri/icons/", import.meta.url));
mkdirSync(directory, { recursive: true });
writeFileSync(`${directory}/icon.png`, png); writeFileSync(`${directory}/icon.ico`, Buffer.concat([ico, png]));
