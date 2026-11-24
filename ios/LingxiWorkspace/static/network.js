(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  let form, footer, footerSignature, extraAddresses;
  let hasFooterData = false;
  let state=null,loading=false,saving=false,dirty=false,portEdited=false,selectedAddresses=new Set(),addressControls=[];
  const element=(tag,className,text)=>{const node=document.createElement(tag);node.className=className;if(text!==undefined)node.textContent=text;return node;};
  function link(url){const node=element('a','network-address',url);node.href=url;node.target='_blank';node.rel='noopener noreferrer';node.title=url;return node;}
  function family(label,urls,enabled){
    const block=element('div','network-family');block.append(element('span','network-family-label',label));
    if(!enabled)block.append(element('small','','未开启对外监听'));
    else if(!urls.length)block.append(element('small','','未检测到可用地址'));
    else for(const url of urls)block.append(link(url));
    return block;
  }
  function endpoint(label,url){
    const row=element('div','network-endpoint'),info=element('div','network-endpoint-info'),address=link(url),copy=element('button','network-copy','复制');
    address.textContent=url.replace(/^https?:\/\//,'').replace(/\/$/,'');
    copy.type='button';copy.ariaLabel='复制 '+label+' 访问地址';copy.title='复制完整访问地址';copy.ariaLive='polite';
    copy.addEventListener('click',async()=>{
      try{
        if(navigator.clipboard?.writeText)await navigator.clipboard.writeText(url);
        else{
          const field=element('textarea','');field.value=url;field.readOnly=true;
          field.style.position='fixed';field.style.opacity='0';document.body.append(field);field.select();
          const copied=document.execCommand('copy');field.remove();
          if(!copied)throw new Error('copy unavailable');
        }
        copy.textContent='已复制';
      }catch{copy.textContent='失败';copy.title='请右键地址选择“复制链接地址”';}
      setTimeout(()=>{copy.textContent='复制';},1800);
    });
    info.append(element('span','network-endpoint-type',label),address);
    row.append(element('span','workspace-service-dot'),info,copy);
    return row;
  }
  function updateControls(){
    const custom=$('network-custom').checked;
    for(const id of ['network-ipv4','network-ipv6','network-custom','network-save'])$(id).disabled=saving||!state.managed;
    $('network-port').disabled=saving||!state.port_editable;
    for(const {input,row} of addressControls){
      input.checked=custom?selectedAddresses.has(row.address):row.available&&$('network-'+row.family).checked;
      input.disabled=saving||!state.managed||!custom||!$('network-'+row.family).checked;
    }
    const missing=(state.unavailable||[]).filter(address=>selectedAddresses.has(address));
    const warning=state.warning||state.address_warning||(custom&&missing.length?'所选地址暂不可用，已关联的网卡恢复后会自动更新：'+missing.join('、'):'');
    $('network-address-warning').hidden=!warning;
    $('network-address-warning').textContent=warning;
  }
  function renderChoices(value){
    addressControls=[];
    const groups={ipv4:element('div','network-family'),ipv6:element('div','network-family')};
    groups.ipv4.append(element('span','network-family-label','IPv4'));
    groups.ipv6.append(element('span','network-family-label','IPv6'));
    for(const row of value.addresses){
      const label=element('label','network-address-choice'),input=element('input',''),text=element('span','');
      input.type='checkbox';input.ariaLabel='监听 '+row.address;input.value=row.address;
      input.addEventListener('change',()=>{dirty=true;if(input.checked)selectedAddresses.add(row.address);else selectedAddresses.delete(row.address);updateControls();});
      text.append(link(row.url),element('small',row.listening?'network-listening':'',(row.interface_name?row.interface_name+' · ':'')+(!row.available?'等待网卡恢复':row.listening?'当前监听':'当前未监听')));
      label.append(input,text);groups[row.family].append(label);addressControls.push({input,row});
    }
    for(const [key,group] of Object.entries(groups))if(!value.addresses.some(row=>row.family===key))group.append(element('small','','未检测到可用地址'));
    $('network-address-list').replaceChildren(family('本机 · 始终保留',value.local,true),groups.ipv4,groups.ipv6);
    updateControls();
  }
  function renderFooter(value) {
    if (!footer) return;
    const active = value?.active || value?.settings;
    const families = [['ipv4', 'IPv4'], ['ipv6', 'IPv6']].map(([key, label]) => {
      const urls = value?.listening[key] || [];
      const note = !value ? '正在读取地址…' : active[key] ? '暂无可用地址' : '未开启';
      return {label, urls, note: urls.length ? '' : note};
    });
    const signature = JSON.stringify(families);
    if (footerSignature === signature) return;
    footerSignature = signature;
    const settings = element('a', 'network-settings-link', '监听设置');
    settings.href = '/platforms#network-settings-form';
    settings.title = '管理网络监听';
    const list = element('div', 'network-endpoints'), remaining = [];
    for (const {label, urls, note} of families) {
      if (urls.length) {
        list.append(endpoint(label, urls[0]));
        remaining.push(...urls.slice(1).map(url => ({label, url})));
      } else {
        const row = element('div', 'network-endpoint network-endpoint-empty');
        const info = element('div', 'network-endpoint-info');
        info.append(element('span', 'network-endpoint-type', label), element('small', '', note));
        row.append(element('span', 'workspace-service-dot'), info);
        list.append(row);
      }
    }
    const wasOpen = extraAddresses?.open;
    extraAddresses = null;
    if (remaining.length) {
      extraAddresses = element('details', 'network-more');
      extraAddresses.open = Boolean(wasOpen);
      extraAddresses.append(element('summary', '', `其他地址 · ${remaining.length}`),
        ...remaining.map(({label, url}) => endpoint(label, url)));
      list.append(extraAddresses);
    }
    footer.replaceChildren(list, settings);
  }
  function render(value){
    state=value;const active=value.active||value.settings;
    renderFooter(value);
    if(!form)return;
    if(!dirty){$('network-ipv4').checked=value.settings.ipv4;$('network-ipv6').checked=value.settings.ipv6;$('network-custom').checked=value.settings.custom;selectedAddresses=new Set(value.settings.addresses);$('network-port').value=String(value.port);}
    $('network-description').textContent=!value.managed?'当前监听由运行环境控制':value.applying?'正在更新监听 · 无需重启':`当前端口 ${value.port} · IPv4 ${active.ipv4?'对外开启':'仅本机'} · IPv6 ${active.ipv6?'对外开启':'仅本机'} · 自动跟随地址`;
    renderChoices(value);
  }
  async function responseData(response){const value=await response.json();if(!response.ok)throw new Error(value.error||'网络设置读取失败');return value;}
  async function load(){
    if(loading||saving)return;loading=true;
    try{render(await responseData(await fetch('/api/network/status')));}
    catch(error){if(form){$('network-description').textContent=error.message;}}
    finally{loading=false;}
  }
  function bindSettings(){
    $('network-port').addEventListener('input',()=>{dirty=true;portEdited=true;});
    for(const id of ['network-ipv4','network-ipv6'])$(id).addEventListener('change',()=>{dirty=true;updateControls();});
    $('network-custom').addEventListener('change',()=>{
      dirty=true;
      if($('network-custom').checked&&!selectedAddresses.size&&!state.settings.custom){
        for(const row of state.addresses)if(row.available&&$('network-'+row.family).checked)selectedAddresses.add(row.address);
      }
      updateControls();
    });
    $('network-refresh').addEventListener('click',load);
    form.addEventListener('submit',async event=>{
      event.preventDefault();if(saving||!state?.managed)return;saving=true;updateControls();
      try{
        const payload={ipv4:$('network-ipv4').checked,ipv6:$('network-ipv6').checked,custom:$('network-custom').checked,addresses:[...selectedAddresses]};
        if(state.port_editable&&portEdited){
          const port=Number($('network-port').value);
          if(!Number.isInteger(port)||port<1||port>65535)throw new Error('端口请输入 1 至 65535 的整数。');
          if(port!==state.port||state.settings.port!==undefined&&port!==state.settings.port)payload.port=port;
        }
        const value=await responseData(await fetch('/api/network/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}));
        dirty=false;portEdited=false;render(value);$('network-feedback').textContent=value.message;$('network-feedback').classList.toggle('success',true);$('network-feedback').hidden=false;
        if(value.port_changed&&payload.port===value.port){
          const target=new URL(location.href);
          target.port=String(value.port);
          const current=state.addresses.find(row=>new URL(row.url).hostname===target.hostname);
          if(current&&!current.listening){
            const feedback=$('network-feedback');
            feedback.textContent='监听已重启。当前地址未启用，请使用下面的访问地址。';
            const urls=[...value.listening.ipv4,...value.listening.ipv6];
            feedback.append(...(urls.length?urls:value.local).map(url=>link(url)));
            return;
          }
          if(sessionStorage.getItem('desktop-client')==='1')target.searchParams.set('desktop','1');
          location.replace(target.href);
        }
      }catch(error){$('network-feedback').textContent=error.message;$('network-feedback').classList.toggle('success',false);$('network-feedback').hidden=false;}
      finally{saving=false;updateControls();}
    });
  }

  window.mountWorkspaceNetwork = (target, snapshot) => {
    footer = target;
    renderFooter(snapshot);
    hasFooterData = Boolean(snapshot);
  };
  function start() {
    form = $('network-settings-form');
    if (!footer) {
      footer = $('workspace-addresses');
      renderFooter(null);
    }
    if (form) bindSettings();
    window.addEventListener('focus', load);
    setInterval(() => { if (!document.hidden) load(); }, 5000);
    if (form || !hasFooterData) load();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true});
  else start();
})();
