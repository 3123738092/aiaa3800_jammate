// Waveform measurements used by the frozen humming rules. All clocks start at PCM sample zero.
import { SAMPLE_RATE as rate } from './audio-pipeline.js';

export const hzFor = pitch => 440 * 2 ** ((pitch - 69) / 12);
export const end = note => note.startSec + note.durationSec;
export const roundTime = seconds => Math.round(seconds * 1e6) / 1e6;
export function quantile(values, fraction) {
  if (!values.length) return NaN;
  const sorted = Array.from(values).sort((a,b) => a-b);
  const pos = (sorted.length-1)*fraction, i = Math.floor(pos);
  return sorted[i] + (sorted[Math.ceil(pos)]-sorted[i])*(pos-i);
}
export const median = values => quantile(values,.5);
const TAU = Math.PI*2;
const hann = (size,periodic=false) => Float64Array.from({length:size},(_,i)=>.5-.5*Math.cos(TAU*i/(periodic?size:size-1)));

// Precompute twiddles and bit reversal once for all STFT windows of the same size.
function spectrumAnalyzer(size) {
  const rev = new Uint32Array(size);
  for (let i=1;i<size;i++) rev[i]=(rev[i>>1]>>1)|((i&1)*(size>>1));
  const cos=Float64Array.from({length:size/2},(_,i)=>Math.cos(-TAU*i/size));
  const sin=Float64Array.from({length:size/2},(_,i)=>Math.sin(-TAU*i/size));
  const re=new Float64Array(size), im=new Float64Array(size);
  return (x,start,window)=>{
    re.fill(0); im.fill(0);
    for(let i=0;i<window.length;i++) re[rev[i]]=(x[start+i]??0)*window[i];
    for(let width=2;width<=size;width*=2){
      const half=width/2, step=size/width;
      for(let base=0;base<size;base+=width){
        for(let j=0;j<half;j++){
          const a=base+j,b=a+half,k=j*step;
          const real=re[b]*cos[k]-im[b]*sin[k], imag=re[b]*sin[k]+im[b]*cos[k];
          re[b]=re[a]-real;im[b]=im[a]-imag;re[a]+=real;im[a]+=imag;
        }
      }
    }
    return Float64Array.from({length:size/2+1},(_,i)=>Math.hypot(re[i],im[i]));
  };
}

function carrier(x,start,window,hz) {
  const angle=-TAU*hz/rate, cs=Math.cos(angle), sn=Math.sin(angle);
  let re=0,im=0,c=Math.cos(angle*start),s=Math.sin(angle*start);
  for(let j=0;j<window.length;j++){
    const v=x[start+j]*window[j];
    re+=v*c; im+=v*s;
    const next=c*cs-s*sn;
    s=s*cs+c*sn;c=next;
  }
  return [re,im];
}

export function stationaryComponent(x) {
  const size=Math.min(Math.floor(.21*rate),Math.floor(x.length/4)),tail=x.length-size;
  const window=hann(size), weight=window.reduce((a,b)=>a+b,0), fft=spectrumAnalyzer(32768);
  function peak(start){
    const spectrum=fft(x,start,window);
    let best=Math.ceil(64*32768/rate), power=0;
    for(let i=best+1;i<Math.ceil(400*32768/rate);i++) if(spectrum[i]>spectrum[best]) best=i;
    for(let i=start;i<start+size;i++) power+=x[i]**2;
    const amp=2*spectrum[best]/weight;
    return {hz:best*rate/32768,amp,purity:amp/(Math.SQRT2*Math.max(1e-7,Math.sqrt(power/size)))};
  }
  const head=peak(0),last=peak(tail),tones=[];
  if(Math.min(head.amp,last.amp)<.002 || Math.min(head.purity,last.purity)<.68 ||
     Math.abs(head.hz-last.hz)>1.5 || Math.min(head.amp,last.amp)<.64*Math.max(head.amp,last.amp)) return {pcm:x,tones};
  const strength=f=>Math.hypot(...carrier(x,0,window,f))+Math.hypot(...carrier(x,tail,window,f));
  let lo=head.hz-1.5,hi=head.hz+1.5;
  for(let i=0;i<34;i++){
    const third=(hi-lo)/3;
    if(strength(lo+third)<strength(hi-third)) lo+=third; else hi-=third;
  }
  const hz=(lo+hi)/2, residual=Float64Array.from(x);
  for(const harmonic of [1,2,3]){
    const f=hz*harmonic;
    const a=carrier(x,0,window,f).map(v=>v*2/weight), b=carrier(x,tail,window,f).map(v=>v*2/weight);
    if(Math.min(Math.hypot(...a),Math.hypot(...b))<.001 ||
       Math.hypot(a[0]-b[0],a[1]-b[1])>.38*Math.max(Math.hypot(...a),Math.hypot(...b))) continue;
    const levels=[];
    for(let i=0;i<=tail;i+=Math.floor(size/2)) levels.push(2*Math.hypot(...carrier(x,i,window,f))/weight);
    if(quantile(levels,.13)<.60*Math.hypot(...a)) continue;
    const re=(a[0]+b[0])/2,im=(a[1]+b[1])/2;
    for(let i=0;i<x.length;i++) residual[i]-=re*Math.cos(TAU*f*i/rate)-im*Math.sin(TAU*f*i/rate);
    tones.push({midi:69+12*Math.log2(f/440),amplitude:Math.hypot(re,im)});
  }
  return {pcm:tones.length?residual:x,tones};
}

export function fineEnvelope(x){
  const hop=Math.round(.003*rate), half=Math.floor(Math.round(.006*rate)/2);
  const power=new Float64Array(x.length+1);
  for(let i=0;i<x.length;i++) power[i+1]=power[i]+x[i]**2;
  const rms=[];
  for(let i=0;i<x.length;i+=hop){
    const lo=Math.max(0,i-half),hi=Math.min(x.length,i+half);
    rms.push(Math.round(1e7*Math.sqrt(Math.max(0,power[hi]-power[lo])/(hi-lo)))/1e7);
  }
  return {rms,hop:hop/rate};
}

export function smoothEnvelope(x){
  const sigma=.0055*rate, radius=Math.round(4*sigma), hop=Math.round(.003*rate);
  const kernel=Float64Array.from({length:2*radius+1},(_,i)=>Math.exp(-.5*((i-radius)/sigma)**2));
  const sum=kernel.reduce((a,b)=>a+b,0),values=[],times=[];
  for(let center=0;center<x.length;center+=hop){
    let power=0;
    for(let j=-radius;j<=radius;j++){
      let k=center+j;
      if(k<0) k=-k-1;
      if(k>=x.length) k=2*x.length-k-1;
      power+=kernel[j+radius]*x[k]**2;
    }
    values.push(Math.sqrt(power/sum));times.push(center/rate);
  }
  return {values,times,step:hop/rate};
}

// Closed time ranges on sorted clocks. Keep the original indices for contiguous spans.
export function lower(times,value,after=false){
  let a=0,b=times.length;
  while(a<b){const m=(a+b)>>1;if(after?times[m]<=value:times[m]<value) a=m+1; else b=m;}
  return a;
}
export function interval(times,a,b){
  return [lower(times,a),lower(times,b,true)];
}

export function pitchBands(x,pitches){
  const size=2048, hop=176, nfft=4096, win=hann(size,true), fft=spectrumAnalyzer(nfft);
  const times=Array.from({length:Math.ceil(x.length/hop)+1},(_,i)=>i*hop/rate);
  const bands=new Map();
  for(const pitch of new Set(pitches)){
    const hz=hzFor(pitch), nearest=Math.round(hz*nfft/rate);
    let lo=Math.ceil(hz*2**(-1.23/12)*nfft/rate), hi=Math.floor(hz*2**(1.23/12)*nfft/rate);
    if(hi<lo) lo=hi=nearest;
    bands.set(pitch,{lo,hi,values:new Float64Array(times.length)});
  }
  for(let i=0;i<times.length;i++){
    const spec=fft(x,i*hop-size/2,win);
    for(const {lo,hi,values} of bands.values()){
      let peak=0;
      for(let j=lo;j<=hi;j++) peak=Math.max(peak,spec[j]);
      values[i]=peak/(size/2);
    }
  }
  return {times,band:p=>bands.get(p).values};
}

export function steadyCarrier(x,pitch){
  const half=Math.floor(.048*rate),window=hann(half*2),scale=2/window.reduce((a,b)=>a+b,0);
  const times=[],levels=[],phase=[];
  for(let i=0;;i++){
    const time=.048+i*.024;
    if(time>=x.length/rate-.048) break;
    const center=Math.round(time*rate);
    const [re,im]=carrier(x,center-half,window,hzFor(pitch));
    times.push(time);levels.push(Math.hypot(re,im)*scale);
    let angle=Math.atan2(im,re);
    if(i) angle-=TAU*Math.round((angle-phase[i-1])/TAU);
    phase.push(angle);
  }
  return {times,levels,phase};
}
