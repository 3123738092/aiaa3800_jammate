// The browser hands off the source material. The model adapter owns encoding.
export function prepareMrt2Input(source, {prompt = null, midi = null, duration = null}) {
  if (source === 'text') {
    if (typeof prompt !== 'string' || !prompt.trim()) throw new Error('请输入文字。');
  } else if (!midi?.length || !Number.isFinite(duration) || duration <= 0) {
    throw new Error('请先准备有效的 MIDI。');
  }

  let midiBase64 = midi;
  if (midi instanceof Uint8Array) {
    let binary = '';
    for (let offset = 0; offset < midi.length; offset += 8192) {
      binary += String.fromCharCode(...midi.subarray(offset, offset + 8192));
    }
    midiBase64 = btoa(binary);
  }
  return {schema_version: 1, source, prompt, midi_base64: midiBase64, duration_s: duration};
}

function download(data, filename, type) {
  const link = document.createElement('a');
  link.href = URL.createObjectURL(new Blob([data], {type}));
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  const url = link.href;
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function downloadMrt2Input(input) {
  download(JSON.stringify(input, null, 2), `mrt2-${input.source}.json`, 'application/json');
}

export function downloadMrt2Midi(input) {
  const bytes = Uint8Array.from(atob(input.midi_base64), character => character.charCodeAt(0));
  download(bytes, `mrt2-${input.source}.mid`, 'audio/midi');
}
