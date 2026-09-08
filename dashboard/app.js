/* Local execution explorer. Playback changes selection only; there is no restore/execution API. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const palette = ['#b6a0e2', '#80bbb5', '#dea381', '#8eaddb', '#cb92b8', '#bdc787', '#c39cdf', '#7fb8d0'];
  const statusColors = {succeeded:'#87c9ad', failed:'#ef8f91', pending:'#d9b77c', unverified:'#8dadd5'};
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const query = new URLSearchParams(location.search);
  const desktopView = query.get('view') === 'desktops';
  const desktopNames = new Set((query.get('branches') || '').split(',').filter(name=>/^[a-z0-9-]{1,32}$/.test(name)));
  const state = {payload:null, branch:'', runFilter:'', selectedRun:null, selected:null, timeline:[], position:0, playing:false, grid:desktopView, tab:'inspect', revision:null};
  if(desktopView) {
    document.body.classList.add('desktop-view');
    document.title='Multiverse · Live desktops';
    $('desktops').hidden=false;
    $('toggle-grid').setAttribute('aria-expanded','true');
    document.querySelector('.brand-caption').textContent='Live desktops';
  }
  const nodeCache = new Map(), linkCache = new Map(), desktopCache = new Map(), labelCache = new Map();
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
      if(!label) { label=el('span','node-label'); $('node-labels').append(label); labelCache.set(n.id,label); }
      label.textContent=shorten(n.title || n.url_pattern || n.id,25);
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
        .nodeVal(n=>Math.min(4,1+n.occurrence_count*.1)).nodeLabel(n=>tooltip(`${n.title}\n${n.occurrence_count} recorded occurrences`))
        .linkLabel(l=>tooltip(`${runById(l.run_id)?.branch || l.run_id} · tool call ${l.index}\n${l.tool} · ${statusName(l.status)}`))
        .linkOpacity(.78).linkDirectionalArrowLength(3.3).linkDirectionalArrowRelPos(.77)
        .linkDirectionalArrowColor(l=>statusColors[l.status]).linkDirectionalParticles(0)
        .linkDirectionalParticleWidth(2.5).linkDirectionalParticleSpeed(.014).linkDirectionalParticleColor(l=>runColor(l.run_id))
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
      if(state.selected?.type==='node'&&state.selected.id===n.id) return '#f2e5ff';
      if(state.selectedRun && !n.run_ids.includes(state.selectedRun)) return '#333747';
      return n.run_ids.length===1 ? runColor(n.run_ids[0]) : '#d1c3e7';
    }).linkColor(l=>selectedPath(l.run_id)?runColor(l.run_id):'#292b37')
      .linkWidth(l=>state.selected?.id===l.id?2.8:!selectedPath(l.run_id)?.3:l.status==='failed'?1.6:l.status==='succeeded'?1:.55)
      .linkDirectionalArrowColor(l=>selectedPath(l.run_id)?statusColors[l.status]:'#373744');
    labels();wake(400);
  }
  function renderGraph(newIds=[]) {
    const steps=visibleSteps(), ids=new Set(steps.flatMap(s=>[s.from,s.to]).filter(Boolean));
    const rawNodes=state.payload.graph.nodes.filter(n=>ids.has(n.id));
    const rawLinks=steps.filter(s=>s.from&&s.to);
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
    append(box,detailsGrid([['Graph nodes','Normalized UI states'],['Connections','Recorded tool calls'],['Playback','Visual evidence only'],['Recovery','Unavailable']]));
    const disabled=button('Restore unavailable','button restore-button',()=>{});disabled.disabled=true;box.append(disabled);
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
    const disabled=button('Restore unavailable','button restore-button',()=>{});disabled.disabled=true;box.append(disabled);
    box.append(el('p','info-note','Checkpoint metadata is inspectable. A verified recovery API is not available; timeline playback never restores a desktop.'));
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
      const box=clear($('inspect-content'));append(box,el('div','eyebrow','NORMALIZED UI STATE'),el('h2','',n.title||'UI state'),el('p','mono muted',n.id),detailsGrid([['Application',n.app],['URL pattern',n.url_pattern],['Occurrences',n.occurrences.length],['Runs',n.run_ids.length]]));
      box.append(el('p','info-note','Normalization removes some values for UI matching. This node does not certify exact desktop recovery.'));
      box.append(el('h3','','Recorded occurrences'));
      for(const sid of n.occurrences) {const s=state.payload.graph.occurrences.find(s=>s.id===sid);if(!s)continue;const b=button(`${s.branch} · call ${s.index}`,'occurrence-button',()=>selectStep(s.id));append(b,el('span','',`${s.tool} · ${statusName(s.status)}`));box.append(b);}
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
      append(box,el('div','eyebrow','RECORDED TOOL CALL'),el('h2','',`${s.tool}`),append(el('div','status-line'),pill(statusName(s.status),s.status),pill(`Call ${s.index}`)),el('p','muted small',s.branch));
      append(box,detailsGrid([['Run',s.run_id],['Recorded',clock(s.ts)],['Execution',s.exec_ms===null?'Unavailable':`${numeric(s.exec_ms)} ms`],['Verification',s.verification==='task_checker'?'Whole-task checker':'Unverified'],['Source',runById(s.run_id)?.usage_source==='scripted'?'Scripted':s.execution_source],['Granularity','Tool call']]));
      if(s.gap_before||s.gap_after||s.continuity_gap)box.append(el('p','info-note',`${s.gap_before?'Before observation is missing. ':''}${s.gap_after?'After observation is missing. ':''}${s.continuity_gap?'A gap separates this call from the preceding recorded state. ':''}No state or connecting transition is inferred.`));
      if(s.error)box.append(el('pre','code',typeof s.error==='string'?s.error:JSON.stringify(s.error,null,2)));
      box.append(el('h3','','Recorded code'));box.append(el('pre','code',s.code||'No code was recorded.'));
      box.append(el('h3','','Evidence'));evidenceCards(box,s.evidence);
      const actions=s.actions||[];
      box.append(el('h3','',`Individual actions · ${actions.length}`));
      if(!actions.length)box.append(el('p','muted small','Individual action boundaries were not recorded. This call may contain several actions.'));
      for(const a of actions) {const section=el('details','details-section');section.append(el('summary','',`${a.sequence+1}. ${a.operation} · ${a.outcome}`));section.append(el('pre','code',JSON.stringify(a.arguments,null,2)));section.append(el('p','muted small',`Checkpoint ${a.checkpoint_status}`));evidenceCards(section,[...a.pre_evidence,...a.post_evidence]);box.append(section);}
      showCheckpoints(box,s.branch,actions.flatMap(a=>[a.pre_checkpoint_id,a.post_checkpoint_id]).filter(Boolean));
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
    const open=el('a','desktop-open','Open ↗');open.target='_blank';open.rel='noopener noreferrer';open.setAttribute('aria-label',`Open ${branch.name} desktop`);header.append(open);
    const frame=el('div','desktop-frame');const footer=el('footer'), form=el('form','steer-form'), input=el('input');input.placeholder='Steer the active agent…';input.setAttribute('aria-label',`Steer ${branch.name}`);input.maxLength=16384;
    const send=button('Send','button',()=>{});send.type='submit';append(form,input,send);const message=el('span','steer-status');append(footer,form,message);append(card,header,frame,footer);
    form.addEventListener('submit',async event=>{
      event.preventDefault();if(!input.value.trim())return;send.disabled=true;
      try {const response=await fetch(`/api/steer/${encodeURIComponent(branch.name)}`,{method:'POST',headers:{'Content-Type':'application/json','X-Fork-Dashboard':'1'},body:JSON.stringify({text:input.value})});const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Steering request failed');input.value='';message.textContent='Queued for the active agent';toast('Steering queued');}
      catch(error){message.textContent=error.message;toast(error.message);}finally{send.disabled=!branch.agent_active;}
    });
    return {card,status,open,frame,input,send,message,iframe:null,url:null,branch};
  }
  function renderDesktops() {
    const branches=state.payload.branches.filter(b=>b.status!=='removed'&&(!desktopView||!desktopNames.size||desktopNames.has(b.name)));$('desktop-count').textContent=branches.length;
    if(desktopView)branches.sort((a,b)=>a.name.localeCompare(b.name,undefined,{numeric:true}));
    $('grid').style.setProperty('--desktop-rows',String(Math.max(1,Math.ceil(branches.length/3))));
    const ids=new Set(branches.map(b=>b.name));
    for(const [name,item] of desktopCache)if(!ids.has(name)){item.card.remove();desktopCache.delete(name);}
    for(const branch of branches) {
      let item=desktopCache.get(branch.name);if(!item){item=desktopCard(branch);desktopCache.set(branch.name,item);$('grid').append(item.card);}
      Object.assign(item.branch,branch);item.status.textContent=branch.status;
      item.open.hidden=!branch.desktop_url;
      if(branch.desktop_url) {const url=new URL(branch.desktop_url);url.searchParams.delete('view_only');item.open.href=url.href;}
      item.input.disabled=item.send.disabled=!branch.agent_active;
      if(document.activeElement!==item.input)item.message.textContent=branch.agent_active?'Existing steering channel · Agent active':'No active agent · Steering unavailable';
      if(state.grid && branch.desktop_url && ['healthy','running'].includes(branch.status)) {
        if(!item.iframe){item.iframe=el('iframe');item.iframe.title=`${branch.name} live desktop, view only`;item.iframe.loading=desktopView?'eager':'lazy';clear(item.frame).append(item.iframe);}
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
  $('toggle-grid').addEventListener('click',()=>{if(desktopView){location.href='/';return;}state.grid=!state.grid;$('desktops').hidden=!state.grid;$('toggle-grid').setAttribute('aria-expanded',String(state.grid));renderDesktops();if(state.grid)$('desktops').scrollIntoView({behavior:reduced?'instant':'smooth',block:'start'});});
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
  if(!desktopView)initializeGraph();resetInspector();refresh().then(reconnect);pollTimer=setInterval(refresh,5000);
})();
