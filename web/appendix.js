const names={sustain:'持续音',repeated:'重复音',steps:'换音',rest:'休止',short:'短音'};
const cases=window.JAMMATE_BENCHMARK.cases;
const $=id=>document.getElementById(id);
for(const [key,name]of Object.entries(names)){
 const a=cases[`baseline-${key}`],b=cases[`optimized-${key}`],tr=document.createElement('tr');
 for(const value of [name,b.expected.length,a.count,b.count,b.pitchSequenceCorrect?'符合预期':'不符合']){const td=document.createElement('td');td.textContent=value;tr.append(td);}
 $('scores').append(tr);
}
function draw(){
 const key=$('case').value,a=cases[`baseline-${key}`],b=cases[`optimized-${key}`];
 $('sample').src=`fixtures/${key}.wav`;
 $('comparison').textContent=`${names[key]}：基线 ${a.count} 音 → 优化 ${b.count} 音；预期 ${b.expected.length} 音。`;
 const rows=[['已知音符',b.expected,'#828b83'],['整合基线',a.actual,'#c86840'],['最终优化',b.actual,'#427451']];
 const all=rows.flatMap(r=>r[1]),duration=Math.max(...all.map(n=>n.start+n.duration))+.25;
 const lo=Math.min(...all.map(n=>n.pitch))-1,hi=Math.max(...all.map(n=>n.pitch))+1;
 $('rolls').replaceChildren();
 for(const [label,notes,color]of rows){
  const box=document.createElement('div');box.className='timeline';const title=document.createElement('p');title.textContent=label;box.append(title);
  const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 900 135');svg.setAttribute('role','img');svg.setAttribute('aria-label',`${label}，${notes.length} 个音符`);
  function shape(kind,attrs,text){const el=document.createElementNS(ns,kind);for(const[k,v]of Object.entries(attrs))el.setAttribute(k,v);if(text!==undefined)el.textContent=text;svg.append(el);}
  for(let t=0;t<=duration;t++){const x=35+t/duration*840;shape('line',{x1:x,x2:x,y1:8,y2:110,stroke:'#dfe5d9'});shape('text',{x,y:130,fill:'#657261','font-size':13},`${t}s`);}
  notes.forEach(n=>{shape('rect',{x:35+n.start/duration*840,y:8+(hi-n.pitch)/(hi-lo)*85,width:Math.max(1,n.duration/duration*840),height:9,rx:2,fill:color});});
  box.append(svg);$('rolls').append(box);
 }
}
$('case').addEventListener('change',draw);draw();
