import {SAMPLE_RATE as rate} from './audio-pipeline.js';
import {quantile, median} from './humming-acoustics.js';

// Audio-only quarter/eighth-note estimator from the tempo benchmark.
const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));
const hop = Math.round(.008 * rate);
const width = 2 * Math.round(.018 * rate);
const window = Float64Array.from({length:width}, (_, i) => .5 - .5*Math.cos(2*Math.PI*i/(width-1)));
const firstBin = Math.ceil(65*width/rate), lastBin = Math.floor(4500*width/rate);
const coeff = Float64Array.from({length:lastBin-firstBin+1}, (_, i) => 2*Math.cos(2*Math.PI*(i+firstBin)/width));

function reflected(i, length) {
  if (length===1) return 0;
  const j = ((i % (2*length)) + 2*length) % (2*length);
  return j < length ? j : 2*length-1-j;
}

function attacks(samples) {
  if (samples.length < .15*rate) return [];
  const mean = samples.reduce((sum, x) => sum+x, 0)/samples.length;
  let peak=0;
  for (const x of samples) peak=Math.max(peak,Math.abs(x-mean));
  if (peak<1e-7) return [];
  const audio = new Float64Array(samples.length+width);
  samples.forEach((x,i) => {audio[i+width/2]=(x-mean)/peak;});
  const rms=[], flux=[], rise=[], frame=new Float64Array(width);
  const spectra=Array.from({length:4},()=>new Float64Array(coeff.length));
  for (let start=0,index=0; start<=samples.length; start+=hop,index++) {
    let power=0;
    for (let j=0;j<width;j++) {
      const x=audio[start+j]; power+=x*x; frame[j]=x*window[j];
    }
    rms.push(Math.sqrt(power/width));
    rise.push(Math.max(0,rms[index]-(rms[index-3]??0)));
    const spectrum=spectra[index%4], prior=spectra[(index+1)%4];
    let total=0;
    for (let k=0;k<coeff.length;k++) {
      // Goertzel evaluates the original non-power-of-two DFT on its exact bins.
      let a=0,b=0; const c=coeff[k];
      for (let j=0;j<width;j++) {const next=frame[j]+c*a-b; b=a; a=next;}
      spectrum[k]=Math.pow(Math.max(0,a*a+b*b-c*a*b),.25);
      const neighbor=Math.max(prior[k],prior[k-1]??0,prior[k+1]??0);
      total+=Math.max(0,spectrum[k]-neighbor);
    }
    flux.push(total);
  }
  const scale=values=>Math.max(quantile(values,.94),.12*Math.max(...values),1e-8);
  const r=scale(rise),f=scale(flux);
  const novelty=rise.map((v,i)=>.53*Math.min(v/r,4)+.47*Math.min(flux[i]/f,4));
  const weights=Array.from({length:9},(_,j)=>Math.exp(-.5*((j-4)/1.05)**2));
  const sum=weights.reduce((a,b)=>a+b,0);
  const smooth=novelty.map((_,i)=>weights.reduce((v,w,j)=>v+w*novelty[reflected(i+j-4,novelty.length)],0)/sum);
  const peaks=[];
  for (let i=0;i<smooth.length;i++) {
    if (smooth[i]<=(smooth[i-1]??0)) continue;
    let end=i;
    while (end+1<smooth.length && smooth[end+1]===smooth[i]) end++;
    if (smooth[i]>(smooth[end+1]??0)) {
      const mid=(i+end)>>1;
      const background=median(Array.from({length:31},(_,j)=>smooth[reflected(mid+j-15,smooth.length)]));
      if (smooth[mid]>=.26+.95*background) peaks.push(mid);
    }
    i=end;
  }
  // scipy.find_peaks applies distance before prominence.
  const separated=[], distance=Math.round(.085*rate/hop);
  for (const i of peaks.sort((a,b)=>smooth[b]-smooth[a]||b-a)) {
    if (separated.every(j=>Math.abs(i-j)>=distance)) separated.push(i);
  }
  const floor=Math.max(.035*quantile(rms,.95),1e-5), events=[];
  for (const i of separated.sort((a,b)=>a-b)) {
    const side=step=>{
      let lowest=smooth[i];
      for (let j=i+step;j>=-1 && j<=smooth.length;j+=step) {
        const value=smooth[j]??0;
        if (value>smooth[i]) break;
        lowest=Math.min(lowest,value);
      }
      return lowest;
    };
    if (smooth[i]-Math.max(side(-1),side(1))<.19) continue;
    const loud=Math.max(...rms.slice(Math.max(0,i-5),i+6));
    if (loud>floor) events.push({t:i*hop/rate,loud,novelty:smooth[i]});
  }
  if (events.length) {
    const loud=Math.max(1e-7,quantile(events.map(e=>e.loud),.9));
    const nov=Math.max(1e-7,quantile(events.map(e=>e.novelty),.9));
    for (const e of events) e.strength=clamp(Math.sqrt(e.loud/loud*e.novelty/nov),.06,1.6);
  }
  return events;
}

function grid(events,bpm) {
  const period=60/bpm, tol=clamp(period*.065,.020,.036), first=events[0].t;
  let bestPhase=0, timing=0, beatEnergy=0, bestScore=-Infinity;
  for (let phase=0;phase<64;phase++) {
    let sum=0,beatSum=0,weighted=0;
    for (const e of events) {
      const pos=(e.t-first)/period-phase/64, half=Math.round(2*pos);
      const fit=Math.exp(-.5*(((pos-half/2)*period)/tol)**2);
      sum+=fit; weighted+=fit*e.strength;
      if (!(half%2)) beatSum+=fit*e.strength;
    }
    const align=sum/events.length, energy=beatSum/(weighted+1e-8), score=.68*align+.32*energy;
    if (score>bestScore) {bestScore=score; bestPhase=phase; timing=align; beatEnergy=energy;}
  }
  const positions=[new Set(),new Set()], strengths=[[],[]];
  for (const e of events) {
    const pos=(e.t-first)/period-bestPhase/64, half=Math.round(2*pos);
    if (Math.abs(pos-half/2)*period>=1.65*tol) continue;
    const parity=Math.abs(half%2);
    positions[parity].add(half); strengths[parity].push(e.strength);
  }
  const span=(events.at(-1).t-first)/period, possible=Math.max(span+.5,1);
  const coverage=Math.min(1,positions[0].size/possible), offCoverage=Math.min(1,positions[1].size/possible);
  const accent=median(strengths[0])/median(strengths[1]);
  const hierarchy=positions[1].size>=2 && (accent>=1.22 || (coverage-offCoverage>=.30 && accent>=1.02));
  const support=(strengths[0].length+strengths[1].length)/events.length;
  return {bpm, score:.46*timing+.21*coverage+.18*beatEnergy+.09*support+.06*hierarchy,
    supported:hierarchy && positions[0].size>=4 && span>=4 && coverage>=.49 && timing>=.69 && support>=.77};
}

export function estimateTempo(samples) {
  const events=attacks(samples);
  const fallback={bpm:null,candidate:null,thresholdMs:110};
  if (events.length<7) return fallback;
  const all=Array.from({length:151},(_,i)=>grid(events,i+55));
  const eligible=all.filter(g=>g.supported);
  const best=items=>items.reduce((a,b)=>a.score>=b.score?a:b);
  const chosen=best(eligible.length?eligible:all);
  fallback.candidate=chosen.bpm;
  if (!eligible.length) return fallback;
  for (const factor of [.5,2]) {
    const alias=best([.99,.995,1,1.005,1.01].map(r=>grid(events,chosen.bpm*factor*r)));
    if (alias.supported && chosen.score-alias.score<.055) return fallback;
  }
  if (eligible.some(g=>Math.abs(Math.log2(g.bpm/chosen.bpm))>.075 && chosen.score-g.score<.045)) return fallback;
  return {bpm:chosen.bpm,candidate:chosen.bpm,thresholdMs:Math.round(clamp(.22*60000/chosen.bpm,80,130)*1000)/1000};
}
