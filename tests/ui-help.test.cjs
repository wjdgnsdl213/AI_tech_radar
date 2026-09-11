const test = require('node:test');
const assert = require('node:assert/strict');
const help = require('../web/static/ui-help.js');

test('tooltip placement stays inside the usable viewport including scrollbar space', () => {
  assert.deepEqual(help.position({left:300,top:150,bottom:178}, {width:320,height:100}, {width:375,height:800}), {left:47,top:184});
  assert.deepEqual(help.position({left:10,top:750,bottom:778}, {width:320,height:100}, {width:375,height:800}), {left:10,top:644});
});

test('help content stays escaped in a keyboard-accessible button', () => {
  const html = help.render('자료 <보관> "기준"');
  assert.match(html, /<button\b/);
  assert.match(html, /type="button"/);
  assert.match(html, /data-help="자료 &lt;보관&gt; &quot;기준&quot;"/);
  assert.match(html, /aria-label="도움말"/);
  assert.doesNotMatch(html, /<보관>/);
});
