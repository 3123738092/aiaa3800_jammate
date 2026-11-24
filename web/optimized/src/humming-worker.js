import * as tf from '@tensorflow/tfjs';
import { BasicPitch, outputToNotesPoly, noteFramesToTime } from '@spotify/basic-pitch';
import { decodeHumming, frameRms } from './continuity.js';
import { SAMPLE_RATE } from './audio-pipeline.js';
import { DECODING, standardizeNotes, createMelodyDocument } from './melody.js';

let model = null;

async function loadModel() {
  if (model) return;
  await tf.setBackend('cpu');
  await tf.ready();
  const next = new BasicPitch(new URL('./model/model.json', self.location.href).href);
  await next.model;
  model = next;
}

async function transcribe({samples, source, metadata}, progress) {
  const started = performance.now();
  const frames = [], onsets = [];
  tf.engine().startScope();
  try {
    await model.evaluateModel(samples, (batch, attacks) => {
      frames.push(...batch);
      onsets.push(...attacks);
    }, progress);
  } finally {
    tf.engine().endScope();
  }
  if (!frames.length) throw new Error('这段录音没有产生可用结果，请再录一段。');

  const durationSec = source.durationSec;
  const offsetSec = metadata.processing.trimStartSec;
  const times = noteFramesToTime(Array.from({length: frames.length + 1}, (_, startFrame) =>
    ({startFrame, durationFrames: 1, pitchMidi: 60, amplitude: 1}))).map(note => note.startTimeSeconds);
  const rms = frameRms(samples, SAMPLE_RATE, times);
  const cleaned = decodeHumming(frames, onsets, times, {durationSec, offsetSec, rms});
  cleaned.acousticAttacks = Object.fromEntries(cleaned.notes.map(note => {
    const [start] = note.frameRange;
    const before = rms.slice(Math.max(0, start - 3), start);
    const after = rms.slice(start, start + 3);
    const attack = before.length > 0 && Math.max(...after) > 3 * Math.max(.0005, Math.min(...before));
    return [note.id, attack];
  }));
  const config = DECODING;
  const noteFrames = outputToNotesPoly(frames, onsets, config.onsetThreshold, config.frameThreshold,
    config.minimumNoteFrames, config.inferOnsets, config.maximumFrequencyHz, config.minimumFrequencyHz,
    config.melodiaTrick, config.energyToleranceFrames);
  const decoded = noteFramesToTime(noteFrames).map((note, index) => {
    const evidence = [-1, 0, 1].map(delta => onsets[noteFrames[index].startFrame + delta]?.[note.pitchMidi - 21])
      .filter(Number.isFinite);
    return {...note, onsetActivation: evidence.length ? Math.max(...evidence) : null};
  });
  const raw = standardizeNotes(decoded, Math.min(durationSec, offsetSec + samples.length / SAMPLE_RATE), offsetSec);
  for (const note of cleaned.notes) {
    note.sourceNoteIds = raw.filter(candidate => candidate.startSec < note.startSec + note.durationSec &&
      candidate.startSec + candidate.durationSec > note.startSec).map(candidate => candidate.id);
  }
  const document = createMelodyDocument(source, metadata, raw, cleaned, performance.now() - started);
  document.model.executionBackend = tf.getBackend();
  return {...document, selectedResult: 'cleaned', notes: cleaned.notes};
}

let queue = Promise.resolve();
self.onmessage = ({data}) => {
  queue = queue.then(async () => {
    const {id, type} = data;
    try {
      await loadModel();
      const document = type === 'transcribe'
        ? await transcribe(data, progress => self.postMessage({id, progress}))
        : undefined;
      self.postMessage({id, document});
    } catch (error) {
      self.postMessage({id, error: error.message || '哼唱识别失败。'});
    }
  });
};
