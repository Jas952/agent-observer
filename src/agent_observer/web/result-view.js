(() => {
  'use strict';
  const MAX_VERTICES = 750000;
  const element = (tag, cls, text) => { const el = document.createElement(tag); if (cls) el.className = cls; if (cls === 'result-response-preview') el.setAttribute('translate', 'no'); if (text != null) el.textContent = text; return el; };
  function safeUrl(value) { try { const url = new URL(value, location.origin); return url.origin === location.origin && url.pathname.startsWith('/api/results/') ? url.href : null; } catch { return null; } }
  function parseMesh(buffer, format) {
    const data = buffer instanceof ArrayBuffer ? new Uint8Array(buffer) : buffer;
    const positions = [];
    const vertex = (x, y, z) => { if (![x,y,z].every(Number.isFinite) || positions.length >= MAX_VERTICES * 3) throw new Error('Invalid or oversized mesh'); positions.push(x,y,z); };
    if (format === 'stl' && data.byteLength >= 84) {
      const view = new DataView(data.buffer, data.byteOffset, data.byteLength), count = view.getUint32(80, true);
      if (count > 0 && count <= MAX_VERTICES / 3 && 84 + count * 50 === data.byteLength) {
        for (let i=0;i<count;i++) for(let j=0;j<3;j++){const p=84+i*50+12+j*12;vertex(view.getFloat32(p,true),view.getFloat32(p+4,true),view.getFloat32(p+8,true));}
      }
    }
    if (!positions.length) {
      const text = new TextDecoder('utf-8',{fatal:true}).decode(data);
      if (format === 'stl') {
        const number='[-+]?(?:\\d+\\.?\\d*|\\.\\d+)(?:[eE][-+]?\\d+)?', regex=new RegExp('\\bvertex\\s+('+number+')\\s+('+number+')\\s+('+number+')','g');
        for (const match of text.matchAll(regex)) vertex(Number(match[1]),Number(match[2]),Number(match[3]));
      } else if (format === 'obj') {
        const vertices=[];
        for(const line of text.split(/\r?\n/)){
          const parts=line.trim().split(/\s+/);
          if(parts[0]==='v'){if(vertices.length>=MAX_VERTICES)throw new Error('Mesh exceeds preview limit');const p=parts.slice(1,4).map(Number);if(p.length!==3||!p.every(Number.isFinite))throw new Error('Invalid OBJ vertex');vertices.push(p);}
          if(parts[0]==='f'){
            if(parts.length<4||parts.length>258)throw new Error('Invalid OBJ face');
            const face=parts.slice(1).map(token=>{const n=Number(token.split('/')[0]);if(!Number.isInteger(n)||n===0)throw new Error('Invalid OBJ index');const p=vertices[n<0?vertices.length+n:n-1];if(!p)throw new Error('Missing OBJ vertex');return p;});
            for(let i=1;i<face.length-1;i++)for(const p of [face[0],face[i],face[i+1]])vertex(...p);
          }
        }
      } else throw new Error('Unsupported preview format');
    }
    if(!positions.length||positions.length%9)throw new Error('No complete triangles in mesh');
    const min=[Infinity,Infinity,Infinity],max=[-Infinity,-Infinity,-Infinity];
    for(let i=0;i<positions.length;i++) {const axis=i%3;min[axis]=Math.min(min[axis],positions[i]);max[axis]=Math.max(max[axis],positions[i]);}
    const center=min.map((value,i)=>(value+max[i])/2),radius=Math.hypot(...max.map((value,i)=>value-min[i]))/2;
    if(!Number.isFinite(radius)||radius<=0)throw new Error('Degenerate mesh');
    const packed=new Float32Array(positions.length*2);
    for(let i=0;i<positions.length;i+=9){
      const ux=positions[i+3]-positions[i],uy=positions[i+4]-positions[i+1],uz=positions[i+5]-positions[i+2],vx=positions[i+6]-positions[i],vy=positions[i+7]-positions[i+1],vz=positions[i+8]-positions[i+2];
      const normal=[uy*vz-uz*vy,uz*vx-ux*vz,ux*vy-uy*vx],length=Math.hypot(...normal)||1;
      for(let j=0;j<3;j++)for(let axis=0;axis<3;axis++){packed[(i/3+j)*6+axis]=(positions[i+j*3+axis]-center[axis])/radius;packed[(i/3+j)*6+3+axis]=normal[axis]/length;}
    }
    return {vertices:packed,count:positions.length/3,bounds:{min,max}};
  }
  class MeshPreview {
    constructor(host,mesh){
      this.host=host;this.canvas=element('canvas','result-mesh');this.canvas.tabIndex=0;this.canvas.setAttribute('aria-label','3D model preview. Drag to rotate, wheel to zoom.');host.append(this.canvas);
      this.yaw=.65;this.pitch=-.45;this.zoom=.85;this.mesh=mesh;this.drag=null;this.events=new AbortController();
      const gl=this.canvas.getContext('webgl',{alpha:true,antialias:true});if(!gl)throw new Error('WebGL is unavailable');this.gl=gl;
      const shader=(type,source)=>{const s=gl.createShader(type);gl.shaderSource(s,source);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS)){gl.deleteShader(s);throw new Error('Model shader is unavailable');}return s;};
      const vs=shader(gl.VERTEX_SHADER,'attribute vec3 p;attribute vec3 n;uniform vec2 angle;uniform vec2 extent;varying vec3 normal;vec3 rotate(vec3 v){float a=angle.x,b=angle.y;vec3 q=vec3(cos(a)*v.x+sin(a)*v.z,v.y,-sin(a)*v.x+cos(a)*v.z);return vec3(q.x,cos(b)*q.y-sin(b)*q.z,sin(b)*q.y+cos(b)*q.z);}void main(){vec3 q=rotate(p);normal=rotate(n);gl_Position=vec4(q.x*extent.x,q.y*extent.y,q.z*.2,1.0);}');
      const fs=shader(gl.FRAGMENT_SHADER,'precision mediump float;varying vec3 normal;void main(){float l=.3+.7*abs(dot(normalize(normal),normalize(vec3(.4,.7,1.))));gl_FragColor=vec4(vec3(.24,.65,.73)*l,1.0);}');
      const program=gl.createProgram();gl.attachShader(program,vs);gl.attachShader(program,fs);gl.linkProgram(program);gl.deleteShader(vs);gl.deleteShader(fs);if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw new Error('Model shader could not be linked');this.program=program;
      gl.useProgram(program);this.buffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,this.buffer);gl.bufferData(gl.ARRAY_BUFFER,mesh.vertices,gl.STATIC_DRAW);
      for(const [name,offset]of [['p',0],['n',12]]){const a=gl.getAttribLocation(program,name);gl.enableVertexAttribArray(a);gl.vertexAttribPointer(a,3,gl.FLOAT,false,24,offset);}
      this.angle=gl.getUniformLocation(program,'angle');this.extent=gl.getUniformLocation(program,'extent');gl.enable(gl.DEPTH_TEST);
      const on=(target,type,fn,options={})=>target.addEventListener(type,fn,{...options,signal:this.events.signal});
      const stop=()=>{const drag=this.drag;this.drag=null;if(drag&&this.canvas.hasPointerCapture(drag.id))this.canvas.releasePointerCapture(drag.id);};
      on(this.canvas,'pointerdown',event=>{if(event.button!==0)return;event.preventDefault();event.stopPropagation();this.canvas.focus({preventScroll:true});this.drag={id:event.pointerId,x:event.clientX,y:event.clientY,yaw:this.yaw,pitch:this.pitch};this.canvas.setPointerCapture(event.pointerId);});
      on(this.canvas,'pointermove',event=>{if(!this.drag||event.pointerId!==this.drag.id)return;this.yaw=this.drag.yaw+(event.clientX-this.drag.x)*.012;this.pitch=this.drag.pitch+(event.clientY-this.drag.y)*.012;this.draw();});
      for(const type of ['pointerup','pointercancel','lostpointercapture'])on(this.canvas,type,stop);on(window,'blur',stop);
      on(this.canvas,'wheel',event=>{event.preventDefault();event.stopPropagation();this.zoom=Math.max(.15,Math.min(5,this.zoom*Math.exp(-event.deltaY*.002)));this.draw();},{passive:false});
      on(this.canvas,'keydown',event=>{if(['ArrowLeft','ArrowRight'].includes(event.key))this.yaw+=event.key==='ArrowLeft'?-.1:.1;else if(['ArrowUp','ArrowDown'].includes(event.key))this.pitch+=event.key==='ArrowUp'?-.1:.1;else if(['+','=','-'].includes(event.key))this.zoom=Math.max(.15,Math.min(5,this.zoom*(event.key==='-'?1/1.15:1.15)));else if(event.key==='0'){this.yaw=.65;this.pitch=-.45;this.zoom=.85;}else return;event.preventDefault();event.stopPropagation();this.draw();});
      this.resize=new ResizeObserver(()=>this.draw());this.resize.observe(host);this.draw();
    }
    draw(){const gl=this.gl;if(!gl)return;const width=Math.max(1,Math.round(this.host.clientWidth*Math.min(2,devicePixelRatio||1))),height=Math.max(1,Math.round(this.host.clientHeight*Math.min(2,devicePixelRatio||1)));if(this.canvas.width!==width)this.canvas.width=width;if(this.canvas.height!==height)this.canvas.height=height;gl.viewport(0,0,width,height);gl.clearColor(0,0,0,0);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);gl.useProgram(this.program);gl.uniform2f(this.angle,this.yaw,this.pitch);gl.uniform2f(this.extent,this.zoom*Math.min(1,height/width),this.zoom*Math.min(1,width/height));gl.drawArrays(gl.TRIANGLES,0,this.mesh.count);}
    destroy(){this.events.abort();this.resize?.disconnect();if(this.gl){this.gl.deleteBuffer(this.buffer);this.gl.deleteProgram(this.program);this.gl.getExtension('WEBGL_lose_context')?.loseContext();this.gl=null;}this.drag=null;this.canvas.remove();}
  }
  class ObserverResultView {
    constructor({request,requestFile,onLayout}){this.request=request;this.requestFile=requestFile;this.onLayout=onLayout;this.generation=0;this.cardViewer=null;this.detailViewer=null;this.abort=null;this.detailAbort=null;this.signature='';this.previewUrls={};}
    get gestureActive(){return Boolean(this.cardViewer?.drag||this.detailViewer?.drag);}
    counters(file){const counts=element('span','result-counts');if(Number.isInteger(file.added))counts.append(element('span','result-added','+'+file.added));if(Number.isInteger(file.removed))counts.append(element('span','result-removed','−'+file.removed));if(!counts.children.length)counts.append(element('span','result-change',file.change==='output'?'Output':file.change||'File'));return counts;}
    link(file){
      const url=safeUrl(file.original_url)||safeUrl(file.artifact_url);if(!url)return null;
      const link=element('a','result-file-link',file.original_url?'Open file':file.change==='deleted'?'Open before':'Open captured');link.href=url;link.target='_blank';link.rel='noopener';
      link.onclick=async event=>{event.preventDefault();event.stopPropagation();const readable=['text','code','image'].includes(file.kind),target=readable?window.open('about:blank','_blank'):null;if(target)target.opener=null;
        try{const response=await this.requestFile(url);if(!response.ok)throw new Error(`File unavailable (HTTP ${response.status})`);const blob=URL.createObjectURL(await response.blob());if(target)target.location.href=blob;else{const download=element('a');download.href=blob;download.download=file.name||'result';download.click();}setTimeout(()=>URL.revokeObjectURL(blob),60000);}
        catch(error){target?.close();link.title=error.message;window.ObserverFeedback.show(link,error.message,{error:true});}
      };return link;
    }
    clearPreview(which){this[which+'Viewer']?.destroy();this[which+'Viewer']=null;if(this.previewUrls[which]){URL.revokeObjectURL(this.previewUrls[which]);delete this.previewUrls[which];}}
    render(card,summary,context){
      this.summary=summary||{text:context.text||'',files:[]};this.context=context;this.card=card;
      if(this.dialog?.open&&this.detailContext?.runId===context.runId&&this.reportHolder){
        const reportSignature=JSON.stringify(context.report);
        if(reportSignature!==this.reportSignature){const next=window.ObserverResultReport.render(context.report,this.full?.files||[]);this.reportHolder.replaceWith(next);this.reportHolder=next;this.reportSignature=reportSignature;this.detailContext.report=context.report;}
      }
      const signature=JSON.stringify([context.runId,context.status,context.mode,this.summary]);if(signature===this.signature)return;this.signature=signature;this.generation++;
      this.clearPreview('card');this.abort?.abort();this.abort=new AbortController();
      let surface=card.querySelector('.result-surface');if(!surface){surface=element('div','result-surface');card.insertBefore(surface,card.querySelector('.comment-widget'));}surface.replaceChildren();
      const files=this.summary.files||[],text=this.summary.text||context.text||'';
      if(!files.length){
        const preview=element('div','result-response-preview');if(text)window.ObserverMarkdown.render(preview,text);else{preview.setAttribute('translate','yes');preview.textContent='No result yet.'}surface.append(preview);
        const manifestKey=JSON.stringify([context.runId,this.summary.revision,text]);
        if(context.runId&&context.status&&context.status!=='idle'&&context.mode!=='demo'&&this.request&&this.requestedManifest!==manifestKey){
          this.requestedManifest=manifestKey;
          const fetchGen=this.generation;
          this.request(`/api/results/${encodeURIComponent(context.runId)}`).then(res=>{
            if(res.files?.length&&fetchGen===this.generation){
              this.render(card,res,context);
            }
          }).catch(()=>{});
        }
        if(this.dialog?.open&&this.detailContext?.runId===context.runId)void this.open(this.summary,this.context,this.selectedFileId);
        return;
      }
      const list=element('div','result-file-list');
      for(const file of files){const row=element('button','result-file-row');row.type='button';row.title=file.path;row.append(element('span','result-file-name',file.name||file.path),this.counters(file));row.onclick=event=>{event.stopPropagation();this.open(this.summary,this.context,file.id);};list.append(row);}
      surface.append(list);
      const featured=files.find(file=>file.kind==='model'&&file.change!=='deleted')||files.find(file=>file.kind==='image'&&file.change!=='deleted');
      if(featured){const name=element('div','result-preview-name',featured.name||featured.path),preview=element('div','result-preview');surface.append(name,preview);void this.preview(preview,featured,'card',context);}
      else if(files.length===1&&files[0].preview_text)surface.append(element('pre','result-code-preview',files[0].preview_text));
      else if(text){const preview=element('div','result-response-preview');window.ObserverMarkdown.render(preview,text);surface.append(preview);}
      this.onLayout?.();
      if(this.dialog?.open&&this.detailContext?.runId===context.runId){
        void this.open(this.summary,this.context,this.selectedFileId);
      }
    }
    async preview(host,file,which,context){
      const generation=which==='card'?this.generation:this.detailGeneration,preview=file.preview,signal=which==='card'?this.abort.signal:this.detailAbort.signal;
      if(!preview){host.append(element('span','result-preview-unavailable','No mesh preview recorded'));return;}
      try{
        const url=safeUrl(preview.url);
        if(['png','jpg','jpeg','webp'].includes(preview.format)){
          if(!url)throw new Error('Preview unavailable');const response=await this.requestFile(url,signal);if(!response.ok)throw new Error('Captured image is unavailable');const blob=await response.blob();if(signal.aborted||generation!==(which==='card'?this.generation:this.detailGeneration)||!host.isConnected)return;const imageUrl=URL.createObjectURL(blob);this.previewUrls[which]=imageUrl;const img=element('img','result-image');img.alt=file.name||file.path;img.onerror=()=>host.isConnected&&host.replaceChildren(element('span','result-preview-unavailable','Image preview is unavailable'));img.src=imageUrl;host.append(img);return;
        }
        let mesh;
        if(context.mode==='demo'&&Array.isArray(preview.triangles)){
          const vertices=new Float32Array(preview.triangles);if(vertices.length>MAX_VERTICES*3||vertices.length%9)throw new Error('Invalid demo mesh');
          const ascii='solid demo\n'+Array.from({length:vertices.length/9},(_,i)=>'facet normal 0 0 0\nouter loop\n'+[0,1,2].map(j=>'vertex '+Array.from(vertices.slice(i*9+j*3,i*9+j*3+3)).join(' ')).join('\n')+'\nendloop\nendfacet').join('\n')+'\nendsolid';mesh=parseMesh(new TextEncoder().encode(ascii),'stl');
        }else{
          if(!url)throw new Error('Preview unavailable');const response=await this.requestFile(url,signal);if(!response.ok)throw new Error('Captured mesh is unavailable');const bytes=await response.arrayBuffer();if(bytes.byteLength>32*1024*1024)throw new Error('Mesh exceeds preview limit');mesh=parseMesh(bytes,preview.format);
        }
        if(signal.aborted||generation!==(which==='card'?this.generation:this.detailGeneration)||!host.isConnected)return;
        const viewer=new MeshPreview(host,mesh);if(which==='card')this.cardViewer=viewer;else this.detailViewer=viewer;
      }catch(error){if(signal.aborted||generation!==(which==='card'?this.generation:this.detailGeneration)||!host.isConnected)return;host.replaceChildren(element('span','result-preview-unavailable',error.message));}
    }
    async open(summary,context,selectedId){
      const previousScroll=this.dialog?.open&&this.detailContext?.runId===(context||this.context).runId?this.body.scrollTop:0;
      this.detailSummary=summary||this.summary;this.detailContext={...(context||this.context)};
      this.detailAbort?.abort();this.detailAbort=new AbortController();this.clearPreview('detail');
      if(!this.dialog){
        const dialog=element('dialog','result-dialog');this.dialog=dialog;dialog.setAttribute('aria-labelledby','result-dialog-title');dialog.setAttribute('aria-modal','true');
        const header=element('header','result-dialog-header'),title=element('h2','','Result');title.id='result-dialog-title';title.tabIndex=-1;const close=element('button','result-close');close.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="m6 6 12 12M18 6 6 18"/></svg>';close.type='button';close.setAttribute('aria-label','Close result');close.onclick=()=>dialog.close();header.append(title,close);this.body=element('div','result-dialog-body');dialog.append(header,this.body);document.body.append(dialog);
        dialog.addEventListener('close',()=>{this.detailAbort?.abort();this.clearPreview('detail');window.ObserverFeedback.clear(dialog);this.opener?.isConnected&&this.opener.focus({preventScroll:true});});
      }
      if(!this.dialog.open){this.opener=document.activeElement;this.dialog.showModal();this.dialog.querySelector('h2').focus({preventScroll:true});}
      this.body.replaceChildren(element('div','result-loading','Loading result…'));
      const signal=this.detailAbort.signal,runId=this.detailContext.runId;
      let full=this.detailSummary||{text:this.detailContext.text,files:[]};
      if(this.detailContext.mode!=='demo'&&runId&&this.detailContext.status!=='idle'){try{full=await this.request('/api/results/'+encodeURIComponent(runId));}catch(error){if(signal.aborted)return;window.ObserverFeedback.show(this.dialog,error.message,{error:true});}}
      if(signal.aborted||!this.dialog.open)return;this.full=full;this.body.replaceChildren(this.summaryPanel(full));
      this.reportHolder=window.ObserverResultReport.render(this.detailContext.report,full.files||[]);this.reportSignature=JSON.stringify(this.detailContext.report);this.body.append(this.reportHolder);this.body.scrollTop=previousScroll;
      if(!(full.files||[]).length)return;
      const workspace=element('div','result-workspace'),list=element('nav','result-detail-files');list.setAttribute('aria-label','Result files');this.fileDetail=element('section','result-file-detail');workspace.append(list,this.fileDetail);this.body.append(workspace);
      for(const file of full.files){const button=element('button','result-file-row');button.type='button';button.dataset.fileId=file.id;button.append(element('span','result-file-name',file.name||file.path),this.counters(file));button.onclick=()=>this.selectFile(file);list.append(button);}
      this.selectFile(full.files.find(file=>file.id===selectedId)||full.files[0]);
      this.body.scrollTop=previousScroll;
    }
    summaryPanel(full){const panel=element('section','result-final-response');if(full.text){const text=element('div','result-full-text');window.ObserverMarkdown.render(text,full.text);panel.append(text);}return panel;}
    selectFile(file){
      this.selectedFileId=file?.id;
      this.clearPreview('detail');
      for(const button of this.dialog.querySelectorAll('[data-file-id]'))button.setAttribute('aria-current',String(button.dataset.fileId===file.id));
      const header=element('div','result-detail-heading'),path=element('span','result-detail-path',file.path);header.append(path,this.counters(file));const link=this.link(file);if(link)header.append(link);
      const controls=element('div','result-detail-controls'),content=element('div','result-detail-content');
      this.fileDetail.replaceChildren(header,controls,content);
      const show=mode=>{
        this.detailGeneration=(this.detailGeneration||0)+1;this.clearPreview('detail');content.replaceChildren();
        for(const button of controls.children)button.setAttribute('aria-pressed',String(button.dataset.mode===mode));
        if(mode==='preview')void this.preview(content,file,'detail',this.detailContext);
        else if(mode==='diff'){
          const pre=element('pre','result-diff');for(const [index,line] of (file.diff||'No net text changes.').split('\n').entries())pre.append(element('div',index>1&&line.startsWith('+')?'result-diff-added':index>1&&line.startsWith('-')?'result-diff-removed':'',line||' '));content.append(pre);
        }else content.append(element('pre','result-full-code',file[mode]??(mode==='before'?'Before not recorded.':'Text preview unavailable.')));
      };
      const modes=[];if(file.preview)modes.push(['preview','Preview']);if(file.diff!=null)modes.push(['diff','Changes']);if(['code','text'].includes(file.kind)){if(file.before!=null)modes.push(['before','Before']);if(file.after!=null)modes.push(['after','After']);}
      for(const[mode,label]of modes){const button=element('button','',label);button.type='button';button.dataset.mode=mode;button.onclick=()=>show(mode);controls.append(button);}
      if(modes.length)show(modes[0][0]);else content.append(element('div','result-preview-unavailable',file.notice||'Text changes not captured.'));
    }
    destroy(){this.generation++;this.abort?.abort();this.detailAbort?.abort();this.clearPreview('card');this.clearPreview('detail');this.dialog?.remove();this.dialog=null;}
  }
  ObserverResultView.parseMesh=parseMesh;
  window.ObserverResultView=ObserverResultView;
})();
