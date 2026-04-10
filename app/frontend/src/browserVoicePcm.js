/**
 * Resample mono float32 [-1, 1] PCM to 16 kHz s16le for Gemini Live uplink.
 * @param {Float32Array} inputFloat32
 * @param {number} inputRate
 * @returns {Int16Array}
 */
export function floatTo16kPcmS16le(inputFloat32, inputRate) {
  const outRate = 16000;
  if (!inputFloat32?.length) {
    return new Int16Array(0);
  }
  if (inputRate === outRate) {
    const out = new Int16Array(inputFloat32.length);
    for (let i = 0; i < inputFloat32.length; i++) {
      const s = Math.max(-1, Math.min(1, inputFloat32[i]));
      out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    return out;
  }
  const outLen = Math.max(1, Math.floor((inputFloat32.length * outRate) / inputRate));
  const out = new Int16Array(outLen);
  for (let i = 0; i < outLen; i++) {
    const srcPos = (i * inputRate) / outRate;
    const j = Math.floor(srcPos);
    const f = srcPos - j;
    const a = inputFloat32[j] ?? 0;
    const b = inputFloat32[j + 1] ?? a;
    const s = a * (1 - f) + b * f;
    const clamped = Math.max(-1, Math.min(1, s));
    out[i] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
  }
  return out;
}

/** @param {Int16Array} i16 */
export function int16ToCopyBuffer(i16) {
  const copy = new Int16Array(i16.length);
  copy.set(i16);
  return copy.buffer;
}
