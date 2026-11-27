import {VoiceSegmentation} from './jam-voice-segmentation.js';

class JamVoiceProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.voice = new VoiceSegmentation(sampleRate, options?.processorOptions?.speechOnly === true,
      (event, transfer = []) => this.port.postMessage(event, transfer));
    this.port.onmessage = ({data}) => this.voice.setTempo(data.bpm);
  }

  process(inputs) {
    const channel = inputs[0]?.[0];
    if (channel) this.voice.push(channel);
    return true;
  }
}

registerProcessor('jam-voice', JamVoiceProcessor);
