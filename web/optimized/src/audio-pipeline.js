// Audio preparation has no musical timing or pitch correction.
export const SAMPLE_RATE = 22050;
export const AUDIO_POLICY = Object.freeze({
  version: '1.0.0', silencePeak: .003, silenceRms: .0006,
  quietRms: .01, quietPeak: .05, targetPeak: .35, maxGain: 4,
  clippingLevel: .999, clippingRatio: .001, clippingRunSamples: 3,
  trimWindowSec: .01, trimPaddingSec: .15, trimFloorRms: .0008,
  trimRelativeRms: .05, minimumRetainedSec: .5,
});

export function measureAudio(channels) {
  let peak = 0, sum = 0, count = 0, clipped = 0, longestRun = 0;
  for (const samples of channels) {
    let run = 0;
    for (const value of samples) {
      if (!Number.isFinite(value)) throw new Error('音频包含无效采样，无法处理。');
      peak = Math.max(peak, Math.abs(value)); sum += value * value; count++;
      if (Math.abs(value) >= AUDIO_POLICY.clippingLevel) { clipped++; run++; longestRun = Math.max(longestRun, run); }
      else run = 0;
    }
  }
  return {peak, rms: count ? Math.sqrt(sum / count) : 0,
    clippedSampleRatio: count ? clipped / count : 0, longestClippedRunSamples: longestRun};
}

export function prepareSamples(input, {normalize = true, trim = false, sourceMetrics = null} = {}) {
  const metrics = measureAudio([input]);
  const source = sourceMetrics || metrics;
  const silent = metrics.peak < AUDIO_POLICY.silencePeak || metrics.rms < AUDIO_POLICY.silenceRms;
  const warnings = [];
  if (silent) warnings.push({code:'near_silence', message:'录音几乎没有声音，请靠近麦克风重新录一段。'});
  else if (metrics.rms < AUDIO_POLICY.quietRms || metrics.peak < AUDIO_POLICY.quietPeak)
    warnings.push({code:'quiet', message:'录音偏小，适度放大会同时放大底噪；可以靠近麦克风再录。'});
  if (source.clippedSampleRatio >= AUDIO_POLICY.clippingRatio || source.longestClippedRunSamples >= AUDIO_POLICY.clippingRunSamples)
    warnings.push({code:'possible_clipping', message:'检测到接近满幅的采样，可能有失真；建议降低麦克风音量后重录。'});

  let first = 0, last = input.length;
  const windowSamples = Math.round(AUDIO_POLICY.trimWindowSec * SAMPLE_RATE);
  const threshold = Math.max(AUDIO_POLICY.trimFloorRms, metrics.rms * AUDIO_POLICY.trimRelativeRms);
  if (trim && !silent) {
    const active = [];
    for (let offset = 0; offset < input.length; offset += windowSamples) {
      const end = Math.min(input.length, offset + windowSamples);
      let energy = 0;
      for (let i = offset; i < end; i++) energy += input[i] ** 2;
      if (Math.sqrt(energy / (end - offset)) >= threshold) active.push([offset, end]);
    }
    if (active.length) {
      const padding = Math.round(AUDIO_POLICY.trimPaddingSec * SAMPLE_RATE);
      first = Math.max(0, active[0][0] - padding);
      last = Math.min(input.length, active.at(-1)[1] + padding);
      if (last - first < SAMPLE_RATE * AUDIO_POLICY.minimumRetainedSec) { first = 0; last = input.length; }
    }
  }
  const gain = normalize && !silent && metrics.peak < AUDIO_POLICY.targetPeak
    ? Math.min(AUDIO_POLICY.maxGain, AUDIO_POLICY.targetPeak / metrics.peak) : 1;
  const samples = Float32Array.from(input.subarray(first, last), value => value * gain);
  return {samples, metadata: {
    sampleRateHz:SAMPLE_RATE, channels:1, sampleFormat:'float32',
    processing:{version:AUDIO_POLICY.version, normalizeEnabled:normalize, gain,
      trimEnabled:trim, trimStartSec:first/SAMPLE_RATE,
      trimEndSec:(input.length-last)/SAMPLE_RATE, processedDurationSec:samples.length/SAMPLE_RATE,
      trimThresholdRms:threshold, policy:{...AUDIO_POLICY}, denoise:false, pitchCorrection:false, timeStretch:false},
    quality:{status:silent?'rejected':warnings.length?'warning':'ok', sourceMetrics:source,
      monoBeforeGain:metrics, processed:measureAudio([samples]), warnings}
  }};
}
