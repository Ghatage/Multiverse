// Astra's independent canvas composition. All animation derives from seconds.
const canvas=document.querySelector('canvas'),ctx=canvas.getContext('2d',{alpha:false});
const names=['hero.mp4','fantasy.mp4','jupiter.mp4','vector.mp4','mars.mp4','browser.mp4','desert.mp4','forest.mp4','city.mp4'];
const videos=names.map(name=>{const v=document.createElement('video');v.src=`assets/${name}`;v.muted=true;v.preload='auto';v.playsInline=true;return v;});
await Promise.all(videos.map(v=>new Promise((resolve,reject)=>{v.onloadeddata=resolve;v.onerror=()=>reject(new Error(`Missing footage: ${v.src}`));})));
await document.fonts.load('700 100px Geist');
const clamp=x=>Math.max(0,Math.min(1,x)),smooth=x=>{x=clamp(x);return x*x*(3-2*x);};
const mix=(a,b,x)=>a+(b-a)*x;
const tiles=[];
for(let r=-8;r<=8;r++)for(let c=-13;c<=13;c++){
 const mark=Math.abs(c)<=8&&Math.abs(r)<=6&&(Math.abs(c)>=6||Math.abs(r-(1-Math.abs(c)*1.15))<1.6);
 let source=Math.abs(c*13+r*7)%names.length;
 if(r===0&&c===0)source=0;
 if(r===0&&c===1)source=1;
 if(r===0&&c===-1)source=2;
 if(r===-1&&c===0)source=3;
 if(r===1&&c===0)source=4;
 if(r===-1&&c===1)source=5;
 tiles.push({c,r,source,mark,depth:Math.sin(c*17+r*11)*18});
}
function rounded(x,y,w,h,r){ctx.beginPath();ctx.roundRect(x,y,w,h,r);}
function draw(t){
 ctx.fillStyle='#03060b';ctx.fillRect(0,0,1920,1080);
 const glow=ctx.createRadialGradient(960,420,80,960,480,1000);glow.addColorStop(0,'#17253c');glow.addColorStop(.6,'#0a101e');glow.addColorStop(1,'#020409');ctx.fillStyle=glow;ctx.fillRect(0,0,1920,1080);
 const pull=smooth((t-.65)/7.85),scale=Math.exp(mix(Math.log(2.56),Math.log(.094),pull));
 const reveal=smooth((t-6.1)/2.4),centerY=mix(530,426,smooth((t-5)/3.5));
 // Distant dust establishes a second depth plane without competing with the screens.
 for(let i=0;i<110;i++){const x=(i*751.13)%1920,y=(i*317.71+t*(2+i%3))%1080;ctx.globalAlpha=.08+(i%5)*.025;ctx.fillStyle=i%2?'#74e8ff':'#ae99ff';ctx.fillRect(x,y,1.5,1.5);}ctx.globalAlpha=1;
 ctx.save();ctx.translate(960,centerY);ctx.rotate(mix(-.012,0,smooth(t/8.5)));ctx.scale(scale,scale);
 for(const tile of tiles){
  const {c,r,source,mark,depth}=tile;
  const opacity=mark?1:1-reveal;
  if(opacity<.003)continue;
  const x=c*676,y=r*394;
  if(Math.abs(x*scale)>1250+400*scale||Math.abs(y*scale)>850+240*scale)continue;
  ctx.save();ctx.translate(x,y);ctx.transform(1,(c*.0018)*(1-reveal),0,1,0,depth*(1-reveal));ctx.globalAlpha=opacity;
  // Thick chassis, lower lip, brushed edge and an individual status LED.
  ctx.shadowColor=mark&&reveal>.2?'#548dbb99':'#000b';ctx.shadowBlur=mark?25:18;ctx.shadowOffsetY=10;
  rounded(-327,-188,654,379,10);ctx.fillStyle='#05070b';ctx.fill();ctx.shadowBlur=0;ctx.shadowOffsetY=0;
  ctx.strokeStyle='#536071';ctx.lineWidth=2;ctx.stroke();
  rounded(-320,-181,640,360,4);ctx.save();ctx.clip();
  const v=videos[source];ctx.drawImage(v,-320,-181,640,360);
  if(source===0){
   // Recognizable player furniture over the film, no fabricated product state.
   const shade=ctx.createLinearGradient(0,102,0,180);shade.addColorStop(0,'#0000');shade.addColorStop(1,'#000d');ctx.fillStyle=shade;ctx.fillRect(-320,95,640,85);
   ctx.fillStyle='#ffffff66';ctx.fillRect(-301,145,602,2);ctx.fillStyle='#ff3347';ctx.fillRect(-301,145,140+t*8,2);
   ctx.fillStyle='white';ctx.fillRect(-297,158,3,10);ctx.fillRect(-290,158,3,10);ctx.font='10px Geist';ctx.fillText('8:10 / 12:14',-274,167);ctx.fillText('⚙',270,167);ctx.strokeStyle='white';ctx.strokeRect(293,158,10,8);
   ctx.fillStyle='#ff243d';rounded(-300,-165,26,18,5);ctx.fill();ctx.fillStyle='white';ctx.beginPath();ctx.moveTo(-290,-161);ctx.lineTo(-280,-156);ctx.lineTo(-290,-151);ctx.fill();ctx.font='600 12px Geist';ctx.fillText('Tears of Steel',-266,-152);
  }
  const glass=ctx.createLinearGradient(-320,-180,300,180);glass.addColorStop(0,'#a9deff12');glass.addColorStop(.4,'#fff0');glass.addColorStop(1,'#05091224');ctx.fillStyle=glass;ctx.fillRect(-320,-181,640,360);
  ctx.restore();ctx.fillStyle='#79e2eb';ctx.fillRect(299,183,5,2);ctx.fillStyle='#252b36';ctx.fillRect(-24,184,48,2);
  ctx.restore();
 }
 ctx.restore();
 // Optical falloff, then the title on an uncluttered final plane.
 const vignette=ctx.createRadialGradient(960,510,350,960,510,1180);vignette.addColorStop(0,'#0000');vignette.addColorStop(1,'#0009');ctx.fillStyle=vignette;ctx.fillRect(0,0,1920,1080);
 ctx.globalAlpha=smooth((t-7.65)/.85);ctx.textAlign='center';ctx.font='650 108px Geist';ctx.fillStyle='#f3f7ff';ctx.shadowColor='#92c8ff55';ctx.shadowBlur=32;ctx.fillText('Multiverse:',960,826);ctx.shadowBlur=0;
 ctx.font='400 42px Geist';ctx.letterSpacing='0px';ctx.fillStyle='#a3b2cc';ctx.fillText('Your Desktop. Forkable',960,889);ctx.letterSpacing='0px';ctx.textAlign='left';ctx.globalAlpha=1;
}
async function seekAsync(t){
 await Promise.all(videos.map(v=>new Promise((resolve,reject)=>{const target=Math.min(t,Math.max(0,v.duration-.06));if(Math.abs(v.currentTime-target)<.002&&v.readyState>=2)return resolve();const timeout=setTimeout(()=>reject(new Error(`Seek timed out: ${v.src}`)),10000);v.addEventListener('seeked',()=>{clearTimeout(timeout);resolve();},{once:true});v.currentTime=target;})));
 await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
 draw(t);
}
window.hf={fps:30,totalFrames:300,duration:10,seekAsync,seek:draw,frame:n=>draw(n/30)};
window.addEventListener('hf-seek',e=>{window.__seek=seekAsync(e.detail?.time??0);});
window.__ready=true;draw(0);
if(!location.search.includes('capture')){let start,last=0;for(const v of videos){v.loop=true;await v.play();}function tick(now){start??=now;const time=((now-start)/1000)%10;if(time<last)for(const v of videos)v.currentTime=0;last=time;draw(time);requestAnimationFrame(tick);}requestAnimationFrame(tick);}
