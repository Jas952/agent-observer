(() => {
  'use strict';
  function mount(host,{value='',id='block-vault-path',chooseFolder,onChange,isCurrent=()=>true}){
    const label=document.createElement('label');label.htmlFor=id;label.textContent='Vault folder';
    const controls=document.createElement('div');controls.className='observer-vault-controls';
    const input=document.createElement('input');input.id=id;input.type='text';input.spellcheck=false;input.placeholder='/Users/you/Documents/MyVault';input.value=value;
    input.oninput=()=>{if(isCurrent())onChange(input.value)};
    const choose=document.createElement('button');choose.type='button';choose.textContent='Choose folder…';
    choose.onclick=async()=>{
      choose.disabled=true;choose.setAttribute('aria-busy','true');
      try{
        const result=await chooseFolder();
        if(!isCurrent()||!host.isConnected)return;
        if(result?.path){input.value=result.path;onChange(result.path);input.focus()}
      }catch(error){if(isCurrent()&&host.isConnected)window.ObserverFeedback.show(host,error.message,{error:true})}
      finally{choose.disabled=false;choose.setAttribute('aria-busy','false')}
    };
    controls.append(input,choose);host.append(label,controls);
    return {input,choose};
  }
  window.ObserverVaultSettings={mount};
})();
