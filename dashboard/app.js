/* Playback shows evidence; action recovery creates a separate desktop. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const palette = ['#b6a0e2', '#80bbb5', '#dea381', '#8eaddb', '#cb92b8', '#bdc787', '#c39cdf', '#7fb8d0'];
  const statusColors = {succeeded:'#87c9ad', failed:'#ef8f91', pending:'#d9b77c', unverified:'#8dadd5'};
  const appColors = new Map();
  function appName(node) { return node.app || 'Unknown app'; }
  function appColor(node) {
    const name=appName(node);
    if(name==='Unknown app') return '#9298a8';
    if(!appColors.has(name)) {
      let hash=0; for(const char of name) hash=(Math.imul(hash,31)+char.charCodeAt(0))>>>0;
      appColors.set(name,`hsl(${hash%360}, 62%, 70%)`);
    }
    return appColors.get(name);
  }
  function edgeColor(link) { return statusColors[link.status] || '#9298a8'; }
  function renderAppLegend(nodes) {
    const legend=clear($('app-legend'));
    legend.append(el('b','','APPS'));
    const apps=new Map(nodes.map(n=>[appName(n),n]));
    for(const [name,node] of [...apps].sort(([a],[b])=>a.localeCompare(b))) {
      const item=el('span','',name), dot=el('i');dot.style.background=appColor(node);item.prepend(dot);legend.append(item);
    }
    if(!apps.size) legend.append(el('span','','No recorded apps'));
  }
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const state = {payload:null, branch:'', runFilter:'', selectedRun:null, selected:null, timeline:[], position:0, playing:false, grid:false, tab:'inspect', revision:null};
  const nodeCache = new Map(), linkCache = new Map(), desktopCache = new Map(), labelCache = new Map();
  let bloomPass;
  let graph, initialFit = false, graphSignature = '', pollBusy = false, refreshQueued = false, inspectorRequest = 0;
  let pauseTimer, playbackTimer, toastTimer, source, pollTimer, resumeQueued=false;
  let runColors = new Map();

  function el(tag, className, text) { const node=document.createElement(tag); if(className) node.className=className; if(text!==undefined) node.textContent=String(text); return node; }
  function clear(node) { node.replaceChildren(); return node; }
  function append(parent,...children) { for(const child of children) if(child) parent.append(child); return parent; }
  function button(text, className, action) { const node=el('button',className,text); node.type='button'; node.addEventListener('click',action); return node; }
  function pill(text, kind='') { return el('span',`pill ${kind}`,text); }
  function shorten(value, n=40) { const text=String(value || ''); return text.length>n ? text.slice(0,n-1)+'…' : text; }
  function numeric(value, fallback='—') { return typeof value==='number' ? value.toLocaleString(undefined,{maximumFractionDigits:2}) : fallback; }
  function clock(ts) { if(!ts) return 'Time unavailable'; const date=new Date(ts); return Number.isNaN(date.getTime()) ? 'Time unavailable' : date.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'}); }
  function runColor(id) { if(!runColors.has(id)) runColors.set(id,palette[runColors.size%palette.length]); return runColors.get(id); }
  function runById(id) { return state.payload?.runs.find(r=>r.id===id); }
  function statusName(status) { return ({succeeded:'Verified',failed:'Failed',pending:'Pending',unverified:'Unverified'})[status] || status; }
  function toast(text) { $('toast').textContent=text; $('toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('toast').hidden=true,4200); }
  function tooltip(text) { return el('div','graph-tooltip',text); }
  function selectedPath(id) { return !state.selectedRun || id===state.selectedRun; }
  function visibleRuns() { return (state.payload?.runs || []).filter(r=>(!state.branch || r.branch===state.branch)&&(!state.runFilter || r.id===state.runFilter)); }
  function visibleSteps() { const ids=new Set(visibleRuns().map(r=>r.id)); return (state.payload?.graph.occurrences || []).filter(s=>ids.has(s.run_id)); }
  function visibleEvents() { const ids=new Set(visibleRuns().filter(r=>!state.selectedRun || r.id===state.selectedRun).map(r=>r.id)); return (state.payload?.events || []).filter(e=>ids.has(e.run_id)); }

  function wake(milliseconds=500) {
    if(!graph || document.hidden) return;
    // A controls callback can run inside the library's first animation cycle.
    // Defer resume to avoid re-entering that cycle before its RAF id is installed.
    if(!resumeQueued){resumeQueued=true;queueMicrotask(()=>{resumeQueued=false;if(!document.hidden)graph.resumeAnimation();});}
    clearTimeout(pauseTimer);
    pauseTimer=setTimeout(()=>{ if(!state.playing) graph.pauseAnimation(); },milliseconds);
  }
  function labels() {
    if(!graph) return;
    const data=graph.graphData();
    const chosen=data.nodes.filter(n=>!state.selectedRun || n.run_ids.includes(state.selectedRun)).sort((a,b)=>b.occurrence_count-a.occurrence_count).slice(0,6);
    if(state.selected?.type==='node') { const n=data.nodes.find(n=>n.id===state.selected.id); if(n && !chosen.includes(n)) chosen.unshift(n); }
    const ids=new Set(chosen.map(n=>n.id));
    for(const [id,node] of labelCache) if(!ids.has(id)) { node.remove(); labelCache.delete(id); }
    const width=$('graph').clientWidth, height=$('graph').clientHeight;
    for(const n of chosen) {
      if(!Number.isFinite(n.x)||!Number.isFinite(n.y)||!Number.isFinite(n.z)) continue;
      let label=labelCache.get(n.id);
      if(!label) { label=button('','node-label',()=>selectNode(n.id)); $('node-labels').append(label); labelCache.set(n.id,label); }
      label.textContent=shorten(n.title || n.url_pattern || n.id,25);
      label.style.borderLeft=`3px solid ${appColor(n)}`;
      label.title=`${appName(n)} · ${n.title || n.id}`;
      label.setAttribute('aria-label',`View screen: ${n.title || n.id}`);
      const point=graph.graph2ScreenCoords(n.x,n.y,n.z);
      label.hidden=point.x<15 || point.x>width-150 || point.y<130 || point.y>height-45 || point.z>1;
      label.style.left=`${point.x}px`; label.style.top=`${point.y}px`;
    }
  }
  function fitCamera(duration=0, onlySelected=false) {
    if(!graph) return;
    const nodes=graph.graphData().nodes.filter(n=>Number.isFinite(n.x)&&(!onlySelected||!state.selectedRun||n.run_ids.includes(state.selectedRun)));
    if(!nodes.length)return;
    const camera=graph.camera();
    const center=camera.position.clone().set(0,0,0);
    for(const axis of ['x','y','z'])center[axis]=(Math.min(...nodes.map(n=>n[axis]))+Math.max(...nodes.map(n=>n[axis])))/2;
    const inverse=camera.quaternion.clone().invert();
    const tangent=Math.tan(camera.fov*Math.PI/360);
    let distance=60;
    for(const n of nodes){const local=camera.position.clone().set(n.x,n.y,n.z).sub(center).applyQuaternion(inverse);distance=Math.max(distance,Math.max(Math.abs(local.x)/(tangent*camera.aspect*.68),Math.abs(local.y)/(tangent*.59))+local.z+18);}
    const direction=camera.position.clone().sub(graph.controls().target).normalize();
    const position=center.clone().add(direction.multiplyScalar(distance));
    graph.cameraPosition(position,center,duration);wake(duration+600);
  }
  function initializeGraph() {
    try {
      graph = new ForceGraph3D($('graph'), {controlType:'orbit', rendererConfig:{antialias:true,alpha:true,powerPreference:'low-power'}})
        .backgroundColor('#00000000').showNavInfo(false).nodeRelSize(4.4).nodeResolution(12).nodeOpacity(.96)
        .nodeVal(n=>Math.min(4,1+n.occurrence_count*.1)).nodeLabel(n=>tooltip(`${n.title}\n${appName(n)} · ${n.occurrence_count} recorded occurrences`))
        .linkLabel(l=>tooltip(`${runById(l.run_id)?.branch || l.run_id} · tool call ${l.index}\n${l.tool} · ${statusName(l.status)}`))
        .linkOpacity(.78).linkDirectionalArrowLength(3.3).linkDirectionalArrowRelPos(.77)
        .linkDirectionalArrowColor(l=>statusColors[l.status]).linkDirectionalParticles(0)
        .linkDirectionalParticleWidth(2.5).linkDirectionalParticleSpeed(.014).linkDirectionalParticleColor(edgeColor)
        .linkCurvature(l=>l.curve).linkCurveRotation(l=>l.rotation).linkHoverPrecision(4)
        .cooldownTicks(0).warmupTicks(80).d3VelocityDecay(.5)
        .onNodeClick(n=>selectNode(n.id)).onLinkClick(l=>selectStep(l.id))
        .onNodeDrag(()=>wake(1500)).onNodeDragEnd(n=>{n.fx=n.x;n.fy=n.y;n.fz=n.z;labels();wake(500);})
        .onEngineTick(labels).onEngineStop(()=>{
          for(const n of graph.graphData().nodes) {n.fx=n.x;n.fy=n.y;n.fz=n.z;}
          if(!initialFit && graph.graphData().nodes.length) {fitCamera(0); initialFit=true;}
          labels(); wake(600);
        });
      graph.renderer().setPixelRatio(Math.min(window.devicePixelRatio || 1,1.5));
      if(window.DashboardBloom) {
        bloomPass=DashboardBloom.createBloom();
        graph.postProcessingComposer().addPass(bloomPass);
        bloomPass.enabled=!reduced;
      }
      const bloomButton=$('bloom-toggle');
      const updateBloomButton=()=>{const enabled=Boolean(bloomPass?.enabled);bloomButton.setAttribute('aria-pressed',String(enabled));bloomButton.querySelector('span').textContent=enabled?'On':'Off';};
      updateBloomButton();
      bloomButton.disabled=!bloomPass;
      bloomButton.addEventListener('click',()=>{if(!bloomPass)return;bloomPass.enabled=!bloomPass.enabled;updateBloomButton();wake(500);});

      graph.d3Force('charge').strength(-155);
      graph.d3Force('link').distance(75);
      graph.controls().addEventListener('change',()=>{labels();wake(350);});
      $('graph').addEventListener('pointerdown',()=>wake(2500));
      $('graph').addEventListener('pointermove',()=>wake(350));
      $('graph').addEventListener('wheel',()=>wake(1000),{passive:true});
      $('graph').addEventListener('keydown',()=>wake(600));
      const resize=new ResizeObserver(()=>{graph.width($('graph').clientWidth).height($('graph').clientHeight);labels();wake(500);});
      resize.observe($('graph'));
    } catch(error) {
      $('graph-empty').hidden=false;
      $('graph-empty').querySelector('h2').textContent='3D rendering unavailable';
      $('graph-empty').querySelector('p').textContent='WebGL could not start. Recorded paths remain available in the run list and timeline.';
      $('fit').disabled=$('reset-camera').disabled=true;
      console.error('Graph initialization failed',error);
    }
  }
  function refreshStyles() {
    if(!graph) return;
    graph.nodeColor(n=>{
      if(state.selectedRun && !n.run_ids.includes(state.selectedRun)) return '#333747';
      return appColor(n);
    }).nodeVal(n=>Math.min(4,1+n.occurrence_count*.1)*(state.selected?.type==='node'&&state.selected.id===n.id?1.5:1))
      .linkColor(l=>selectedPath(l.run_id)?edgeColor(l):'#292b37')
      .linkWidth(l=>state.selected?.id===l.id?2.8:!selectedPath(l.run_id)?.3:l.status==='failed'?1.6:l.status==='succeeded'?1:.55)
      .linkDirectionalArrowColor(l=>selectedPath(l.run_id)?edgeColor(l):'#373744');
    labels();wake(400);
  }
  function renderGraph(newIds=[]) {
    const steps=visibleSteps(), ids=new Set(steps.flatMap(s=>[s.from,s.to]).filter(Boolean));
    const rawNodes=state.payload.graph.nodes.filter(n=>ids.has(n.id));
    const rawLinks=steps.filter(s=>s.from&&s.to);
    renderAppLegend(rawNodes);
    $('graph-summary').textContent=`${rawNodes.length} states · ${steps.length} tool calls · ${steps.filter(s=>s.gap_before||s.gap_after).length} gaps`;
    if(graph) {
      $('graph-empty').hidden=rawNodes.length>0;
      $('empty-detail').textContent=steps.length ? `${steps.length} calls have missing observations. Inspect them in the timeline.` : 'Existing desktops are available below. No runs are started by this dashboard.';
      const nodes=rawNodes.map(n=>{const old=nodeCache.get(n.id);if(old){Object.assign(old,n);return old;}const item={...n};nodeCache.set(n.id,item);return item;});
      const groups=new Map();
      const links=rawLinks.map(l=>{
        const key=[l.from,l.to].sort().join('|'); const rank=groups.get(key)||0;groups.set(key,rank+1);
        let cached=linkCache.get(l.id);
        if(!cached) { cached={...l,curve:l.from===l.to?.85+rank*.22:rank===0?0:.13*Math.ceil(rank/2),rotation:rank*Math.PI*.72};linkCache.set(l.id,cached); }
        else {const source=cached.from===l.from?cached.source:l.source,target=cached.to===l.to?cached.target:l.target;Object.assign(cached,l,{source,target});}
        return cached;
      });
      const signature=JSON.stringify([nodes.map(n=>n.id),links.map(l=>l.id)]);
      if(signature!==graphSignature) {
        graphSignature=signature;
        // Reuse node objects (including fixed positions) and never reset the camera during updates.
        graph.graphData({nodes,links}); wake(1800);
      }
      refreshStyles();
      if(!reduced && newIds.length) for(const l of links) if(newIds.includes(l.id)&&l.status!=='pending') graph.emitParticle(l);
      if(newIds.length) wake(1200);
    }
  }
  function metric(label,value,caption) {
    const box=el('div','metric'), n=el('div','metric-value',value);
    if(caption) n.append(el('small','',caption));append(box,el('label','',label),n);return box;
  }
  function renderMetrics() {
    const ids=new Set(visibleRuns().map(r=>r.id)), steps=visibleSteps(), runs=state.payload.runs.filter(r=>ids.has(r.id));
    const api=runs.filter(r=>r.usage_source==='api');
    const calls=api.length&&api.every(r=>r.model_calls!==null)?api.reduce((sum,r)=>sum+r.model_calls,0):null;
    const cost=api.length&&api.every(r=>r.estimated_cost_usd!==null)?api.reduce((sum,r)=>sum+r.estimated_cost_usd,0):null;
    const scripted=runs.filter(r=>r.usage_source==='scripted').length;
    clear($('metrics')).append(metric('RECORDED TOOL CALLS',numeric(steps.length)),metric('VERIFIED CALLS',numeric(steps.filter(s=>s.status==='succeeded').length)),metric('API MODEL CALLS',numeric(calls)),metric('EST. API COST',cost===null?'—':`$${cost.toFixed(3)}`));
    $('metrics').title=`${scripted} scripted runs are excluded from API usage. Spending is estimated from recorded usage. Cache-hit rate and repair metrics are unavailable.`;
  }
  function updateSelect(select, items, value, allLabel) {
    const signature=JSON.stringify(items);
    if(select.dataset.items!==signature) {clear(select).append(new Option(allLabel,''));for(const [id,label] of items) select.add(new Option(label,id));select.dataset.items=signature;}
    select.value=value;
  }
  function renderRuns() {
    const branches=[...new Set([...state.payload.branches.map(b=>b.name),...state.payload.runs.map(r=>r.branch)])].sort();
    updateSelect($('branch-filter'),branches.map(b=>[b,b]),state.branch,'All branches');
    updateSelect($('run-filter'),state.payload.runs.filter(r=>!state.branch||r.branch===state.branch).map(r=>[r.id,`${r.branch} · ${r.id.slice(-6)}`]),state.runFilter,'All runs');
    const runs=visibleRuns();$('run-count').textContent=runs.length;const container=clear($('run-list'));
    if(!runs.length) container.append(el('p','empty-list','No recorded runs match this view. Desktop previews remain available.'));
    for(const r of runs) {
      const item=button('','run-item'+(r.id===state.selectedRun?' active':''),()=>selectRun(r.id));
      item.dataset.runId=r.id;item.style.setProperty('--run-color',runColor(r.id));item.setAttribute('aria-pressed',String(r.id===state.selectedRun));
      const top=el('div','run-item-top');append(top,el('i','run-dot'),el('span','run-name',r.branch));
      const badge=r.synthetic?pill('Synthetic','synthetic'):r.usage_source==='scripted'?pill('Scripted','scripted'):r.usage_source==='api'?pill('API'):pill('Unknown');
      append(item,top,el('div','run-task',r.task_id),append(el('div','run-meta'),el('span','',`${r.step_count} calls`),badge));
      item.title=`${r.id}\n${r.status} · ${r.stop_reason||'No completion recorded'}\n${r.gap_count} observation gaps`;
      container.append(item);
    }
  }
  function setTimeline() {
    const previous=state.timeline[state.position]?.id;
    state.timeline=visibleSteps().filter(s=>!state.selectedRun||s.run_id===state.selectedRun);
    const retained=state.timeline.findIndex(s=>s.id===previous);
    state.position=retained>=0?retained:Math.min(state.position,Math.max(0,state.timeline.length-1));
    $('scrubber').max=Math.max(0,state.timeline.length-1);$('scrubber').disabled=!state.timeline.length;
    $('play').disabled=$('previous').disabled=$('next').disabled=!state.timeline.length;
    $('timeline-caption').textContent=state.selectedRun?runById(state.selectedRun)?.branch:'All recorded tool calls';
    const strip=clear($('timeline-steps'));
    for(const s of state.timeline) {
      const b=button(String(s.index),`timeline-step ${s.status}${s.gap_before||s.gap_after?' gap':''}`,()=>{pause();selectStep(s.id);});
      b.dataset.occurrenceId=s.id;b.title=`${s.branch} · tool call ${s.index} · ${s.tool} · ${statusName(s.status)}${s.gap_before||s.gap_after?' · Missing observation':''}`;
      b.setAttribute('aria-label',b.title);strip.append(b);
    }
    updatePosition();
  }
  function updatePosition() {
    $('scrubber').value=state.position;$('position').textContent=state.timeline.length?`${state.position+1} / ${state.timeline.length}`:'0 / 0';
    for(const b of $('timeline-steps').children) b.classList.toggle('active',b.dataset.occurrenceId===state.selected?.id);
  }
  function pause() {state.playing=false;clearTimeout(playbackTimer);$('play').textContent='▶';$('play').setAttribute('aria-label','Play recorded evidence');wake(450);}
  function playTick() {
    if(!state.playing||!state.timeline.length) return;
    selectStep(state.timeline[state.position].id,{playback:true});
    const link=linkCache.get(state.timeline[state.position].id);
    if(graph&&link&&!reduced) {graph.emitParticle(link);wake(1200);}
    if(state.position>=state.timeline.length-1) {playbackTimer=setTimeout(pause,900);return;}
    playbackTimer=setTimeout(()=>{state.position++;playTick();},1100);
  }
  function startPlayback() {if(!state.timeline.length)return;if(state.position>=state.timeline.length-1)state.position=0;state.playing=true;$('play').textContent='Ⅱ';$('play').setAttribute('aria-label','Pause recorded evidence');playTick();}
  function detailsGrid(items) {const dl=el('dl','detail-grid');for(const [key,value] of items)append(dl,el('dt','',key),el('dd','',value??'Unavailable'));return dl;}
  function showInspect() {state.tab='inspect';$('inspect-content').hidden=false;$('events-content').hidden=true;$('inspect-tab').setAttribute('aria-selected','true');$('events-tab').setAttribute('aria-selected','false');}
  function resetInspector() {
    inspectorRequest++;state.selected=null;
    const box=clear($('inspect-content'));
    append(box,el('div','eyebrow','PATH INSPECTOR'),el('div','inspector-symbol','⋈'),el('h2','','Follow a path.'),el('p','inspector-intro','Select a run to highlight its ordered execution. Choose a state or connection to inspect what was recorded.'),el('div','info-note','Paths share normalized UI states. Each connection keeps its own run and tool-call identity.'));
    append(box,detailsGrid([['Graph nodes','Normalized UI states'],['Connections','Recorded tool calls'],['Playback','Visual evidence only'],['Recovery','Branch from individual actions (saved state)']]));

  }
  function renderRunInspector(r) {
    const box=clear($('inspect-content'));append(box,el('div','eyebrow','RECORDED RUN'),el('h2','',r.branch),el('p','muted small',r.id),append(el('div','status-line'),pill(r.status),pill(r.synthetic?'Synthetic':r.usage_source,r.synthetic?'synthetic':r.usage_source)));
    append(box,detailsGrid([['Task',r.task_id],['Tool calls',r.step_count],['Gaps',r.gap_count],['Result',r.stop_reason||'Not recorded'],['Checker',r.checker_pass===null?'Not recorded':r.checker_pass?'Passed':'Not passed'],['Usage source',r.usage_source],['Model calls',numeric(r.model_calls)],['Est. cost',r.estimated_cost_usd===null?'Unavailable':`$${r.estimated_cost_usd.toFixed(4)}`]]));
    const branch=state.payload.branches.find(b=>b.name===r.branch);
    append(box,el('h3','','Branch ancestry'),el('p','muted small',branch?.parent_checkpoint?`${branch.parent_branch||'Unknown parent'} → ${branch.name} via ${branch.parent_checkpoint}`:'No parent checkpoint recorded.'));
    box.append(el('div','info-note','Ancestry comes from branch/checkpoint metadata. Shared UI states do not imply a branch relationship.'));
    append(box,el('h3','','Ordered tool calls'));
    for(const s of state.timeline) {const b=button(`${s.index}. ${s.tool} · ${statusName(s.status)}`,'occurrence-button',()=>selectStep(s.id));if(s.gap_before||s.gap_after)b.append(el('span','','Observation gap'));box.append(b);}
    showCheckpoints(box,r.branch);
  }
  function showCheckpoints(box,branch,ids=null) {
    const items=state.payload.checkpoints.filter(c=>ids?ids.includes(c.id):c.branch===branch);
    box.append(el('h3','','Checkpoints'));
    if(!items.length) box.append(el('p','muted small','No checkpoint metadata recorded for this selection.'));
    for(const c of items) {
      const row=el('div','checkpoint-row');append(row,el('div','mono',c.id),el('div','',c.label||c.status||'Filesystem checkpoint'),el('div','muted small',c.fidelity?.overall||'Restore fidelity not recorded'));
      if(c.commit_ms!==undefined)row.append(el('div','muted small',`${numeric(c.commit_ms)} ms commit`));box.append(row);
    }

    box.append(el('p','info-note','Checkpoint metadata is inspectable. Use an individual action’s Branch button to restore saved filesystem state. Timeline playback shows recorded screenshots.'));
  }
  function selectRun(id) {
    pause();state.position=0;state.selectedRun=id;state.selected={type:'run',id};showInspect();renderRuns();renderGraph();setTimeline();renderEvents();
    const run=runById(id);if(run)renderRunInspector(run);
  }
  async function selectNode(id) {
    pause();state.selected={type:'node',id};showInspect();refreshStyles();
    const request=++inspectorRequest;
    try {
      const response=await fetch(`/api/nodes/${encodeURIComponent(id)}`);if(!response.ok)throw new Error('State evidence unavailable');const n=await response.json();if(request!==inspectorRequest||state.selected?.id!==id)return;
      const box=clear($('inspect-content'));
      append(box,el('div','eyebrow','SCREEN DETAILS'),el('h2','',n.title||'Recorded screen'),el('p','inspector-intro',`This screen belongs to ${n.app || 'an unknown app'}. Below is what happened around it.`));
      append(box,el('p','muted small',`${n.occurrences.length} related steps across ${n.run_ids.length} recording${n.run_ids.length===1?'':'s'}. Latest first.`));
      const visits=n.occurrences.map(sid=>state.payload.graph.occurrences.find(step=>step.id===sid)).filter(Boolean).sort((a,b)=>String(b.ts||'').localeCompare(String(a.ts||''))||b.index-a.index);
      const list=el('div','screen-visits');box.append(list);
      let loaded=0;
      const more=button('Show earlier steps','button secondary',loadVisits);
      async function loadVisits() {
        more.disabled=true;
        const batch=visits.slice(loaded,loaded+4);loaded+=batch.length;
        const slots=batch.map(step=>{const slot=el('article','visit-card');slot.append(el('p','muted small',`Loading step ${step.index}…`));list.append(slot);return slot;});
        await Promise.all(batch.map(async(step,i)=>{
          try {
            const result=await fetch(`/api/runs/${encodeURIComponent(step.run_id)}/steps/${step.index}`);
            if(!result.ok)throw new Error('Details unavailable for this step.');
            const detail=await result.json();if(request!==inspectorRequest)return;
            clear(slots[i]);renderStepStory(slots[i],detail,n.id);
          } catch(error) {if(request===inspectorRequest)slots[i].textContent=error.message;}
        }));
        more.disabled=false;more.hidden=loaded>=visits.length;
      }
      box.append(more);
      const technical=el('details','details-section');technical.append(el('summary','','Screen technical details'));
      technical.append(detailsGrid([['Screen ID',n.id],['Page',n.url_pattern]]));
      technical.append(el('p','muted small','Similar screens are grouped together. A saved image belongs to its recorded step, not every visit to this screen.'));
      box.append(technical);await loadVisits();
    } catch(error) {if(request===inspectorRequest)clear($('inspect-content')).append(el('p','info-note',error.message));}
  }
  function evidenceCards(box,items) {
    for(const proof of items) {
      const card=el('div','evidence-card');card.append(el('strong','',proof.label));
      if(proof.available) {
        const link=el('a','',proof.mime.startsWith('image/')?'Open recorded screenshot':'Open recorded evidence');link.href=proof.url;link.target='_blank';link.rel='noopener';card.append(link);
        card.append(el('span','',`${proof.complete===false?'Incomplete · ':''}${proof.integrity==='legacy_reference'?'Legacy reference; no original hash':'Hash verified'} · ${numeric(proof.size_bytes)} bytes`));
        if(proof.mime.startsWith('image/')) {const img=el('img');img.src=proof.url;img.alt=proof.label;img.loading='lazy';img.addEventListener('error',()=>{img.remove();card.append(el('span','','Evidence became unavailable.'));});card.append(img);}
      } else card.append(el('span','',proof.reason||'Not recorded'));
      box.append(card);
    }
  }
  function actionStory(box,a) {
    const card=el('section','action-story');
    append(card,el('h3','',`${a.operation} · ${a.outcome}`),el('p','muted small',`Recovery point: ${a.checkpoint_status}`));
    for(const side of ['pre','post']) {
      const section=el('div','action-boundary');section.append(el('h4','',side==='pre'?'Before action':'After action'));
      evidenceCards(section,(a[`${side}_evidence`]||[]).filter(p=>p.mime?.startsWith('image/')));
      const ck=state.payload.checkpoints.find(c=>c.id===a[`${side}_checkpoint_id`]);
      if(ck?.status==='committed') {
        const status=el('p','muted small');
        const restore=button(`Branch from ${side==='pre'?'before':'after'}`,'small-button',async()=>{
          restore.disabled=true;status.textContent='Restoring saved state into a new desktop…';
          const name=`replay-${Date.now().toString(36)}-${side}`;
          try {
            const response=await fetch(`/api/actions/${encodeURIComponent(a.id)}/recover`,{method:'POST',headers:{'content-type':'application/json','x-fork-dashboard':'1'},body:JSON.stringify({side,name})});
            const result=await response.json();if(!response.ok)throw new Error(result.detail||'Recovery failed');
            status.textContent=`${name}: ${result.verification.message} Active page ${result.verification.active_browser_url.matched?'verified':'did not match'}.`;
            const link=el('a','','Open restored desktop');link.href=result.viewer_url;link.target='_blank';link.rel='noopener';section.append(link);
          }catch(error){status.textContent=error.message;}finally{restore.disabled=false;}
        });
        append(section,restore,status);
      }
      card.append(section);
    }
    card.append(el('p','info-note','Branching restores saved filesystem state. Unsaved app memory is unsupported. The current desktop is preserved.'));
    box.append(card);
  }
  function renderStepStory(box,s,nodeId=null) {
    const from=state.payload.graph.nodes.find(n=>n.id===s.from), to=state.payload.graph.nodes.find(n=>n.id===s.to);
    const relation=nodeId?(s.from===nodeId&&s.to===nodeId?'Stayed on this screen':s.to===nodeId?'Arrived at this screen':'Acted from this screen'):'Recorded step';
    append(box,el('div','eyebrow',relation),el('h3','story-title',`Step ${s.index} · ${s.branch}`));
    const outcome={succeeded:'The task check passed.',failed:'This step reported an error.',pending:'The call started. No result has been recorded yet.',unverified:'This step was recorded, but success has not been checked.'};
    box.append(el('p',`story-outcome ${s.status}`,outcome[s.status]||'Outcome not recorded.'));
    box.append(el('p','story-route',`${from?.title || 'Earlier screen not recorded'} → ${to?.title || 'Next screen not recorded'}`));
    const actions=s.actions||[];
    if(actions.length)box.append(el('p','inspector-intro',`Recorded actions: ${actions.map(a=>a.operation).join(', ')}`));
    else box.append(el('p','muted small',`Used ${s.tool==='exec_js'?'a browser script':s.tool==='exec_py'?'a desktop script':s.tool}. Individual actions were not separately recorded.`));
    for(const action of actions)actionStory(box,action);
    const proofs=actions.length?[]:[...(s.evidence||[])];
    const seen=new Set();const images=proofs.filter(p=>p.available&&p.mime?.startsWith('image/')&&!seen.has(p.url)&&seen.add(p.url));
    if(images.length) {
      box.append(el('h3','','Saved screenshots'));
      for(const proof of images) {
        const figure=el('figure','story-screenshot'),img=el('img');img.src=proof.url;img.alt=proof.label||'Recorded screenshot';img.loading='lazy';
        img.addEventListener('error',()=>{img.remove();figure.append(el('p','muted small','This screenshot is no longer available.'));});
        append(figure,img,el('figcaption','',proof.label||'Screenshot recorded during this step'));box.append(figure);
      }
    } else if(!actions.length)box.append(el('p','info-note','No screenshot was saved for this step.'));
    if(s.error)box.append(el('p','story-outcome failed',typeof s.error==='string'?s.error:(s.error.message||JSON.stringify(s.error))));
    const technical=el('details','details-section');technical.append(el('summary','','Code & recording details'));
    technical.append(el('pre','code',s.code||'No code recorded.'));
    technical.append(detailsGrid([['Recorded',clock(s.ts)],['Run ID',s.run_id],['Duration',s.exec_ms==null?'Unknown':`${numeric(s.exec_ms)} ms`]]));
    evidenceCards(technical,proofs.filter(p=>!p.mime?.startsWith('image/')));
    for(const a of actions){const detail=el('details','details-section');detail.append(el('summary','',`${a.operation} · ${a.outcome}`));detail.append(el('pre','code',JSON.stringify(a.arguments,null,2)));technical.append(detail);}
    showCheckpoints(technical,s.branch,actions.flatMap(a=>[a.pre_checkpoint_id,a.post_checkpoint_id]).filter(Boolean));
    box.append(technical);
  }
  async function selectStep(id,{playback=false}={}) {
    if(!playback) pause();
    state.selected={type:'step',id};showInspect();
    const occurrence=state.payload.graph.occurrences.find(s=>s.id===id);if(!occurrence)return;
    if(!state.timeline.some(s=>s.id===id)) {state.selectedRun=occurrence.run_id;renderRuns();setTimeline();renderGraph();}
    const position=state.timeline.findIndex(s=>s.id===id);if(position>=0)state.position=position;
    updatePosition();refreshStyles();
    const request=++inspectorRequest;
    try {
      const response=await fetch(`/api/runs/${encodeURIComponent(occurrence.run_id)}/steps/${occurrence.index}`);if(!response.ok)throw new Error('This recorded call is unavailable');const s=await response.json();if(request!==inspectorRequest||state.selected?.id!==id)return;
      const box=clear($('inspect-content'));
      renderStepStory(box,s);
    } catch(error) {if(request===inspectorRequest)clear($('inspect-content')).append(el('p','info-note',error.message));}
  }
  function renderEvents() {
    const events=visibleEvents();$('event-count').textContent=events.length||'';const box=clear($('events-content'));
    if(!events.length)box.append(el('p','empty-list','No recorded events in this view.'));
    for(const e of events.slice(-200).reverse()) {const row=el('div','event-row');append(row,append(el('div','event-meta'),el('span','',e.kind),el('time','',clock(e.ts))),el('p','',shorten(e.message,500)),el('p','muted small',e.branch||'Unknown branch'));box.append(row);}
    if(events.length>200)box.append(el('p','muted small',`Showing the latest 200 of ${events.length} events. Full history is available through /api/history.`));
  }
  function desktopCard(branch) {
    const card=el('article','desktop-card');card.dataset.branch=branch.name;
    const header=el('header');const title=el('strong','',branch.name), status=pill(branch.status);append(header,title,status);
    const frame=el('div','desktop-frame');const footer=el('footer'), form=el('form','steer-form'), input=el('input');input.placeholder='Steer the active agent…';input.setAttribute('aria-label',`Steer ${branch.name}`);input.maxLength=16384;
    const send=button('Send','button',()=>{});send.type='submit';append(form,input,send);const message=el('span','steer-status');append(footer,form,message);append(card,header,frame,footer);
    form.addEventListener('submit',async event=>{
      event.preventDefault();if(!input.value.trim())return;send.disabled=true;
      try {const response=await fetch(`/api/steer/${encodeURIComponent(branch.name)}`,{method:'POST',headers:{'Content-Type':'application/json','X-Fork-Dashboard':'1'},body:JSON.stringify({text:input.value})});const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Steering request failed');input.value='';message.textContent='Queued for the active agent';toast('Steering queued');}
      catch(error){message.textContent=error.message;toast(error.message);}finally{send.disabled=!branch.agent_active;}
    });
    return {card,status,frame,input,send,message,iframe:null,url:null,branch};
  }
  function renderDesktops() {
    const branches=state.payload.branches.filter(b=>b.status!=='removed');$('desktop-count').textContent=branches.length;
    const ids=new Set(branches.map(b=>b.name));
    for(const [name,item] of desktopCache)if(!ids.has(name)){item.card.remove();desktopCache.delete(name);}
    for(const branch of branches) {
      let item=desktopCache.get(branch.name);if(!item){item=desktopCard(branch);desktopCache.set(branch.name,item);$('grid').append(item.card);}
      Object.assign(item.branch,branch);item.status.textContent=branch.status;
      item.input.disabled=item.send.disabled=!branch.agent_active;
      if(document.activeElement!==item.input)item.message.textContent=branch.agent_active?'Existing steering channel · Agent active':'No active agent · Steering unavailable';
      if(state.grid && branch.desktop_url && ['healthy','running'].includes(branch.status)) {
        if(!item.iframe){item.iframe=el('iframe');item.iframe.title=`${branch.name} live desktop, view only`;item.iframe.loading='lazy';clear(item.frame).append(item.iframe);}
        if(item.url!==branch.desktop_url){item.url=branch.desktop_url;item.iframe.src=branch.desktop_url;}
      } else if(!item.iframe) item.frame.textContent=state.grid?`Desktop ${branch.status}`:'Preview loads when expanded';
    }
  }
  async function refresh() {
    if(pollBusy){refreshQueued=true;return;}pollBusy=true;
    try {
      const response=await fetch('/api/snapshot'+(state.revision?`?after=${encodeURIComponent(state.revision)}`:''));
      if(response.status===304){setConnected(true);return;}
      if(!response.ok)throw new Error('Recording service unavailable');
      const payload=await response.json();const first=!state.payload;
      const oldIds=new Set(state.payload?.graph.occurrences.map(s=>s.id)||[]);
      const newIds=first?[]:payload.graph.occurrences.filter(s=>!oldIds.has(s.id)).map(s=>s.id);
      state.payload=payload;state.revision=payload.revision;
      if(state.selectedRun&&!runById(state.selectedRun)){state.selectedRun=null;resetInspector();}
      $('workspace-label').textContent=payload.label;
      const messages=[...(payload.synthetic?['Synthetic validation fixture — not real executions']:[]),...payload.warnings];
      $('warning').hidden=!messages.length;$('warning').textContent=messages.join(' · ');
      renderRuns();renderMetrics();renderGraph(newIds);setTimeline();renderEvents();renderDesktops();setConnected(true);
      if(state.selected?.type==='run')renderRunInspector(runById(state.selected.id));
    } catch(error){setConnected(false);$('warning').hidden=false;$('warning').textContent='Recording service unavailable. Keeping the last received view; reconnecting automatically.';}
    finally{pollBusy=false;if(refreshQueued){refreshQueued=false;setTimeout(refresh,0);}}
  }
  function setConnected(online) {$('connection').classList.toggle('online',online);$('connection').replaceChildren(el('i'),document.createTextNode(online?'Live updates':'Reconnecting'));}
  function reconnect() {
    if(source)source.close();
    source=new EventSource('/api/events'+(state.revision?`?after=${encodeURIComponent(state.revision)}`:''));
    source.addEventListener('revision',()=>refresh());source.onerror=()=>setConnected(false);
  }
  function refilter(){pause();if(state.selectedRun&&!visibleRuns().some(r=>r.id===state.selectedRun)){state.selectedRun=null;resetInspector();}renderRuns();renderMetrics();renderGraph();setTimeline();renderEvents();}
  $('branch-filter').addEventListener('change',e=>{state.branch=e.target.value;state.runFilter='';refilter();});
  $('run-filter').addEventListener('change',e=>{state.runFilter=e.target.value;state.selectedRun=state.runFilter||null;resetInspector();refilter();if(state.selectedRun)selectRun(state.selectedRun);});
  $('clear-selection').addEventListener('click',()=>{pause();state.selectedRun=null;state.runFilter='';resetInspector();refilter();});
  $('inspect-tab').addEventListener('click',showInspect);
  $('events-tab').addEventListener('click',()=>{state.tab='events';$('inspect-content').hidden=true;$('events-content').hidden=false;$('inspect-tab').setAttribute('aria-selected','false');$('events-tab').setAttribute('aria-selected','true');renderEvents();});
  $('toggle-grid').addEventListener('click',()=>{state.grid=!state.grid;$('desktops').hidden=!state.grid;$('toggle-grid').setAttribute('aria-expanded',String(state.grid));renderDesktops();if(state.grid)$('desktops').scrollIntoView({behavior:reduced?'instant':'smooth',block:'start'});});
  $('play').addEventListener('click',()=>state.playing?pause():startPlayback());
  $('scrubber').addEventListener('input',e=>{pause();state.position=Number(e.target.value);if(state.timeline[state.position])selectStep(state.timeline[state.position].id);});
  $('previous').addEventListener('click',()=>{pause();state.position=Math.max(0,state.position-1);if(state.timeline[state.position])selectStep(state.timeline[state.position].id);});
  $('next').addEventListener('click',()=>{pause();state.position=Math.min(state.timeline.length-1,state.position+1);if(state.timeline[state.position])selectStep(state.timeline[state.position].id);});
  $('fit').addEventListener('click',()=>{fitCamera(reduced?0:240,true);});
  $('reset-camera').addEventListener('click',()=>{graph?.cameraPosition({x:0,y:0,z:450},{x:0,y:0,z:0},reduced?0:240);wake(650);});
  document.addEventListener('visibilitychange',()=>{if(document.hidden){pause();source?.close();clearInterval(pollTimer);graph?.pauseAnimation();}else{reconnect();refresh();pollTimer=setInterval(refresh,5000);wake(500);}});
  window.addEventListener('pagehide',()=>{source?.close();clearInterval(pollTimer);clearTimeout(playbackTimer);graph?.pauseAnimation();});
  // Read-only diagnostics for local integration validation; no execution controls.
  window.forkDashboard={get snapshot(){return state.payload;},get selection(){return state.selected;},get camera(){return graph?.cameraPosition();},get positions(){return graph?.graphData().nodes.map(n=>({id:n.id,x:n.x,y:n.y,z:n.z}));},get graphCounts(){return graph?{nodes:graph.graphData().nodes.length,links:graph.graphData().links.length}:null;}};
  initializeGraph();resetInspector();refresh().then(reconnect);pollTimer=setInterval(refresh,5000);
})();
