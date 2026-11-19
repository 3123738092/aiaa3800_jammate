// Independent pYIN evidence for short notes, using the benchmark's librosa
// settings (70–650 Hz, 1024 samples, 110-sample hop, 0.1-semitone bins).
import {SAMPLE_RATE as rate} from './audio-pipeline.js';

export const PITCH_HOP=110;
const size=1024, fmin=70, fmax=650;
const minLag=Math.floor(rate/fmax), maxLag=Math.ceil(rate/fmin);
const bins=1+Math.floor(120*Math.log2(fmax/fmin)), states=2*bins;
const lowestPitch=69+12*Math.log2(fmin/440);
const tiny=2.2250738585072014e-308;

// Beta(2,18) assigns probability to 100 YIN trough thresholds.
const tail=x=>(1-x)**18*(1+18*x);
const mass=Float64Array.from({length:100},(_,i)=>tail(i/100)-tail((i+1)/100));

function differenceFrames(samples,count) {
  const x=new Float64Array(samples.length+size);
  x.set(samples,size/2);
  const energy=new Float64Array(x.length+1), prefix=new Float64Array(x.length+1);
  for (let i=0;i<x.length;i++) energy[i+1]=energy[i]+x[i]*x[i];
  const diff=new Float64Array(count*(maxLag+1)), sums=new Float64Array(count);
  for (let lag=1;lag<=maxLag;lag++) {
    let sum=0;
    for (let j=0;j<x.length-lag;j++) {sum+=x[j]*x[j+lag];prefix[j+1]=sum;}
    for (let frame=0;frame<count;frame++) {
      const start=frame*PITCH_HOP, stop=start+size;
      const corr=prefix[stop-lag]-prefix[start];
      const head=lag===1?0:energy[start+lag]-energy[start];
      const d=2*(energy[stop]-energy[start]-corr)-head;
      sums[frame]+=d;
      if (lag>=minLag) diff[frame*(maxLag+1)+lag]=d/(sums[frame]/lag+tiny);
    }
  }
  return diff;
}

function observations(frame,obs) {
  const troughs=[];
  for (let lag=minLag;lag<=maxLag;lag++) {
    const x=frame[lag];
    const left=frame[lag-1], right=frame[lag+1];
    if (lag===minLag ? !(x<right) : !(x<left && (lag===maxLag || x<=right))) continue;
    const curvature=right+left-2*x, slope=(right-left)/2;
    const shift=lag>minLag && lag<maxLag && Math.abs(slope)<Math.abs(curvature)?-slope/curvature:0;
    const bin=Math.round(120*Math.log2(rate/(lag+shift)/fmin));
    troughs.push({height:x,bin:Math.max(0,Math.min(bins,bin)),prob:0});
  }
  obs.fill(0);
  if (troughs.length) {
    const minimum=troughs.reduce((a,b)=>a.height<=b.height?a:b);
    const decay=Math.exp(-2);
    for (let i=0;i<mass.length;i++) {
      const threshold=(i+1)/100;
      let count=0;
      for (const t of troughs) if(t.height<threshold) count++;
      if (!count) {minimum.prob+=.01*mass[i];continue;}
      let p=mass[i]*(1-decay)/(1-decay**count);
      for (const t of troughs) if(t.height<threshold) {t.prob+=p;p*=decay;}
    }
    for (const t of troughs) if (t.prob>0) obs[t.bin]=t.prob;
  }
  let voiced=0;
  for(let i=0;i<bins;i++) voiced+=obs[i];
  obs.fill((1-Math.min(1,voiced))/bins,bins);
}

// Each pitch bin has at most 21 local pitch predecessors per voicing state.
function predecessors() {
  const radius=Math.round(35.92*12*PITCH_HOP/rate)*5;
  const incoming=Array.from({length:states},()=>[]);
  for(let state=0;state<states;state++) {
    const bin=state%bins, lo=Math.max(0,bin-radius), hi=Math.min(bins-1,bin+radius);
    let total=0;
    for(let j=lo;j<=hi;j++) total+=radius+1-Math.abs(j-bin);
    for(let j=lo;j<=hi;j++) for(const voice of [0,1]) {
      const p=(radius+1-Math.abs(j-bin))/total*(voice===Math.floor(state/bins)?.99:.01);
      if(p>=1e-4) incoming[j+voice*bins].push([state,Math.log(p)]);
    }
  }
  return incoming.map(row=>({from:Uint16Array.from(row,x=>x[0]),log:Float64Array.from(row,x=>x[1])}));
}

const transitions=predecessors();
export function independentPitch(samples) {
  const count=1+Math.floor(samples.length/PITCH_HOP), width=maxLag+1;
  const diff=differenceFrames(samples,count), back=new Uint16Array(count*states);
  const obs=new Float64Array(states);
  let prev=new Float64Array(states), next=new Float64Array(states);
  for(let f=0;f<count;f++) {
    observations(diff.subarray(f*width,(f+1)*width),obs);
    for(let s=0;s<states;s++) {
      let cost=0;
      if(f) {
        const {from,log}=transitions[s];
        let index=0; cost=-Infinity;
        for(let k=0;k<from.length;k++) {
          const v=prev[from[k]]+log[k];
          if(v>cost) {cost=v;index=from[k];}
        }
        back[f*states+s]=index;
      }
      next[s]=cost+Math.log(obs[s]+tiny);
    }
    [prev,next]=[next,prev];
  }
  let state=0;
  for(let s=1;s<states;s++) if(prev[s]>prev[state]) state=s;
  const pitch=new Float64Array(count);
  for(let f=count-1;f>=0;f--) {
    pitch[f]=state<bins?lowestPitch+state/10:NaN;
    state=back[f*states+state];
  }
  return pitch;
}
