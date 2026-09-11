const {test}=require('node:test');
const assert=require('node:assert/strict');
const {render}=require('../web/static/review-format.js');
test('bullets and emphasis are semantic without accepting raw HTML',()=>{
  const html=render('- **핵심**: 지원 확대\n- <script>alert(1)</script>');
  assert.match(html,/<ul class="brief-points">/);
  assert.match(html,/<strong>핵심<\/strong>/);
  assert.equal((html.match(/<li>/g)||[]).length,2);
  assert.ok(!html.includes('<script>'));
});
test('legacy prose and paragraph breaks stay readable',()=>{
  assert.equal(render('기존 문장\n\n다음 문장'),'<p>기존 문장</p><p>다음 문장</p>');
});
