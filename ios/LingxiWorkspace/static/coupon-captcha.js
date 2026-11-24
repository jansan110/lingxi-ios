(() => {
  'use strict';
  const root=location.origin+'/coupon-verification/mgt/';
  const query=new URLSearchParams(location.search);
  const requestedAppId=query.get('app_id')||'';
  const appId=/^[A-Za-z0-9_-]{1,128}$/.test(requestedAppId)?requestedAppId:'e1f92650d20263d38dbe327e7237f516';
  const riskId=query.get('risk_id');
  const loginChallenge=query.get('context')==='login';
  const devicePath=query.get('device_path')==='m1'?'m1':'c1';
  const status=document.getElementById('status'),verify=document.getElementById('verify');
  let reloadTimer=null,finished=false;
  let captchaWidth=0;
  const loginWidth=()=>Math.max(200,Math.min(300,verify.clientWidth));
  function renew(message,delay){
    if(finished||reloadTimer!==null)return;
    verify.replaceChildren();status.textContent=message;
    reloadTimer=setTimeout(()=>location.reload(),delay);
  }
  if(loginChallenge){
    document.documentElement.classList.add('captcha-login');
    window.addEventListener('resize',()=>{
      if(captchaWidth&&verify.clientWidth&&loginWidth()<captchaWidth){
        renew('正在调整滑块尺寸…',150);
      }
    });
    const layout=new ResizeObserver(()=>{
      const height=Math.ceil(document.body.getBoundingClientRect().height);
      parent.postMessage({type:'login-captcha-size',height},location.origin);
    });
    layout.observe(document.body);
  }
  function failed(){renew('官方验证加载失败，正在自动重新获取…',3000);}
  const script=document.createElement('script');
  script.src=root+'decision-captcha/zw-captcha/index.js';script.onerror=failed;
  script.onload=()=>{
    status.textContent='请拖动官方组件中的滑块。';
    captchaWidth=loginChallenge?loginWidth():300;
    window._zw.Captcha(verify,{
      appId,customLanguage:{init_inform:'拖动一下'},
      apiServer:root+'decision-captcha',constIDServer:root+'decision-engine/udid/'+devicePath,
      constID_js:root+'decision-captcha/zw-captcha/libs/const-id.js',
      ua_js:root+'decision-captcha/zw-captcha/libs/greenseer.js?t='+Date.now(),
      path:root+'decision-captcha/zw-captcha/',style:'embed',isSaaS:false,cacheStorage:loginChallenge,
      showVoice:loginChallenge,m:riskId?{riskId}:'',width:captchaWidth,
      success(token){
        if(finished||reloadTimer!==null)return;
        finished=true;status.textContent=loginChallenge?'验证成功，正在继续登录…':riskId?'验证成功，正在继续获取付款链接…':'验证成功，正在自动继续…';
        parent.postMessage({type:loginChallenge?'login-captcha-success':'coupon-captcha-success',token},location.origin);
      },
      fail(){
        renew('验证未通过，正在自动获取新的滑块…',500);
      },failSafe:failed,
    }).on('loadFail',failed);
  };
  document.head.append(script);
})();
