/* Authored bullet lists and emphasis only: never interpret arbitrary HTML. */
(function(root,factory){
  const format=factory();
  if(typeof module==='object'&&module.exports)module.exports=format;
  else root.ReviewFormat=format;
})(globalThis,()=>{
  const escape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const inline=s=>escape(s).replace(/\*\*([^*\n]+)\*\*/g,'<strong>$1</strong>');
  function render(text){
    let out='',list=false;
    for(const raw of String(text??'').split(/\r?\n/)){
      const line=raw.trim();
      if(/^[-•] /.test(line)){
        if(!list){out+='<ul class="brief-points">';list=true;}
        out+='<li>'+inline(line.slice(2))+'</li>';
      }else{
        if(list){out+='</ul>';list=false;}
        if(line)out+='<p>'+inline(line)+'</p>';
      }
    }
    return out+(list?'</ul>':'');
  }
  return {render};
});
