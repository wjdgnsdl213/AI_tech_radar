(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports) module.exports=api;
  else {
    root.PreviewTheme=api;
    let saved=null;
    try{saved=root.localStorage.getItem('sab-preview-theme')}catch{}
    api.apply(api.resolve(saved,root.matchMedia('(prefers-color-scheme: dark)').matches),root.document.documentElement);
  }
})(typeof globalThis!=='undefined'?globalThis:this,function(){
  const resolve=(saved,systemDark)=>saved==='dark'||saved==='light'?saved:systemDark?'dark':'light';
  function apply(value,element,storage){
    const theme=resolve(value,false);
    element.classList.toggle('dark',theme==='dark');
    element.style.colorScheme=theme;
    try{storage?.setItem('sab-preview-theme',theme)}catch{}
    return theme;
  }
  return {resolve,apply};
});
