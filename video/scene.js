import {Clock,clamp01,easeOut} from './vendor/clock.js';
const timeline=await (await fetch('assets/timeline.json')).json();
const data=await (await fetch('assets/snapshot.json')).json();
const checkpoint=await (await fetch('assets/native-checkpoint.json')).json();
document.getElementById('checkpoint').textContent=checkpoint.id;
const graph=data.graph, nodes=graph.nodes;
document.getElementById('graph-meta').innerHTML=`${nodes.length} observed states<br>${graph.occurrences.length} recorded transitions`;
const ns='http://www.w3.org/2000/svg';
function svgEl(tag,attrs,parent){const n=document.createElementNS(ns,tag);for(const[k,v]of Object.entries(attrs))n.setAttribute(k,v);parent.append(n);return n;}
const positions=new Map();
const travel=nodes.filter(n=>n.title.includes('travel'));
const equipment=nodes.filter(n=>n.title.includes('equipment'));
const stages=['start','prepare','review','saved'];
for(const group of [travel,equipment])group.sort((a,b)=>stages.findIndex(s=>a.title.includes(s))-stages.findIndex(s=>b.title.includes(s)));
for(const n of nodes){const row=travel.includes(n)?0:equipment.includes(n)?1:2;const col=(row===0?travel:equipment).indexOf(n);positions.set(n.id,row===2?{x:120,y:285}:{x:410+col*325,y:row===0?145:425});}
const paths=[];
const svg=document.getElementById('state-graph');
const seen=new Set();
for(const e of graph.occurrences){if(!positions.has(e.from)||!positions.has(e.to)||e.from===e.to)continue;const key=e.from+e.to;if(seen.has(key))continue;seen.add(key);const a=positions.get(e.from),b=positions.get(e.to);const p=svgEl('path',{d:`M${a.x},${a.y} C${(a.x+b.x)/2},${a.y} ${(a.x+b.x)/2},${b.y} ${b.x},${b.y}`,fill:'none',stroke:'#6e607e','stroke-width':3},svg);paths.push(p);}
for(const n of nodes){const p=positions.get(n.id);svgEl('circle',{cx:p.x,cy:p.y,r:31,fill:'#292335',stroke:'#baa3df','stroke-width':2},svg);svgEl('circle',{cx:p.x,cy:p.y,r:8,fill:n.title.includes('saved')?'#96d6ba':'#cab4ed'},svg);const title=n.title==='desktop'?'Desktop':n.title.split(' · ').at(-1);const text=svgEl('text',{x:p.x,y:p.y+61,fill:'#e8e0f2','font-family':'Geist','font-size':24,'text-anchor':'middle'},svg);text.textContent=title[0].toUpperCase()+title.slice(1);const label=svgEl('text',{x:p.x,y:p.y+88,fill:'#968ca8','font-family':'Mono','font-size':14,'text-anchor':'middle'},svg);label.textContent=n.title.includes('travel')?'TRAVEL':n.title.includes('equipment')?'EQUIPMENT':'SHARED ORIGIN';}
for(const id of ['hero-graph','end-graph']){const g=document.getElementById(id);for(let i=0;i<nodes.length;i++){const a=i*2.399;const r=120+i*28;const x=1380+Math.cos(a)*r,y=490+Math.sin(a)*r;svgEl('path',{d:`M1380 490Q${x+80} ${y-100} ${x} ${y}`,fill:'none',stroke:i%2?'#74998e':'#6f588a','stroke-width':2},g);svgEl('circle',{cx:x,cy:y,r:12+i%3*5,fill:i%2?'#75aa96':'#9775b8'},g);}}
const beats=timeline.map((_,i)=>document.getElementById('s'+i));
const labels=['INTRO','BRANCH','PARALLEL','STATE SPACE','REPLAY','REWIND','MULTIVERSE'];
const sweep=document.createElement('div');sweep.className='motion-sweep';document.getElementById('stage').prepend(sweep);
const replaySteps=[...document.querySelectorAll('.steps div')];
const forkLine=document.querySelector('.fork-lines path');
const graphPaths=[...document.querySelectorAll('#hero-graph path,#end-graph path')];
const allPaths=[...paths,forkLine,...graphPaths];
const lengths=new Map(allPaths.map(p=>[p,p.getTotalLength()]));
function draw(p,progress){const length=lengths.get(p);p.style.strokeDasharray=String(length);p.style.strokeDashoffset=String(length*(1-clamp01(progress)));}
function paint(t){
  let index=timeline.findIndex(s=>t>=s.start&&t<s.end);if(index<0)index=6;
  const s=timeline[index],local=t-s.start;
  for(let i=0;i<beats.length;i++){
    const b=beats[i];b.style.display=i===index?'block':'none';
    if(i!==index)continue;
    const enter=easeOut(clamp01(local/.65));
    b.style.opacity=Math.max(.1,enter);
    const head=b.querySelector('h1,h2');head.style.transform=`translateY(${42*(1-enter)}px) scale(${.97+.03*enter})`;head.style.transformOrigin='left center';
    for(const [j,p]of [...b.querySelectorAll('article')].entries()){
      const reveal=easeOut(clamp01((local-.3-j*.22)/1.05));
      const side=j===0?-1:1;
      p.style.opacity=String(reveal);
      p.style.transform=`translateX(${side*180*(1-reveal)}px) translateY(${5*Math.sin(local*.7+j)}px) rotateY(${-side*12*(1-reveal)}deg) scale(${.92+.08*reveal})`;
      const badge=p.querySelector('.ok,.bad');if(badge)badge.style.opacity=String(clamp01((local-2.6-j*.3)/.4));
      if(index===5&&j===1)p.style.clipPath=`inset(0 0 0 ${100*(1-easeOut(clamp01((local-1.8)/1.2)))}%)`;
    }
  }
  sweep.style.transform=`translateX(${(local*.12%1)*1920}px)`;sweep.style.opacity=String(.15+.2*Math.sin(local*.8)**2);
  document.querySelector('.dashboard-shot').style.opacity=index===3&&local>4?Math.min(1,(local-4)*2):0;
  document.getElementById('chapter').textContent=labels[index];
  document.getElementById('progress').style.width=`${t/60*100}%`;
  const caption=document.getElementById('caption');caption.textContent=t>=s.voice_start&&t<=s.voice_start+s.voice_duration+.25?s.line:'';caption.style.opacity=caption.textContent?1:0;
  paths.forEach((p,i)=>{draw(p,(local-.2-i*.18)/1.1);p.style.stroke='#b69ae2';});
  draw(forkLine,(local-.45)/1.2);
  graphPaths.forEach((p,i)=>draw(p,(local-.1-(i%9)*.10)/1.6));
  for(const id of ['hero-graph','end-graph']){
    const g=document.getElementById(id);g.style.transform=`rotate(${local*1.7-5}deg) scale(${.94+local*.009})`;
    [...g.querySelectorAll('circle')].forEach((p,i)=>{p.style.opacity=String(clamp01((local-.3-i*.1)/.6));p.style.filter=`drop-shadow(0 0 ${8+5*Math.sin(local*1.6+i)}px #bda2ff)`;});
  }
  replaySteps.forEach((p,i)=>{
    const active=local>.6+i*.9;p.style.borderColor=active?'#92d6bf':'#42404d';p.style.boxShadow=active?'0 0 30px #81cdb221':'none';
    p.style.transform=`translateY(${16*(1-easeOut(clamp01((local-i*.18)/.7)))}px)`;
  });
  document.querySelector('.graph-wrap').style.transform=`scale(${1+Math.max(0,local)*.002})`;
}
const clock=new Clock({duration:60,fps:30,onSeek:paint}).expose();
const fit=()=>document.getElementById('stage').style.transform=`scale(${Math.min(innerWidth/1920,innerHeight/1080)})`;addEventListener('resize',fit);fit();
await document.fonts.ready;await Promise.all([...document.images].map(i=>i.decode()));await clock.warmup();window.__ready=true;clock.play();
