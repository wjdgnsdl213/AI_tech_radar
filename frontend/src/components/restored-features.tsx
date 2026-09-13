import { useState } from 'react'
import Markdown from 'react-markdown'
import { ArrowUpRight, Check, Plus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { useAPI, type Article, type Review, type Source } from '@/lib/api'

export type Candidate = {key?:string; title:string; period?:string; fact:string; mean:string; ask:string; source_ids?:number[]; sources?:Source[]}
export const Labels:Record<string,string>={ai:'AI',bigdata:'빅데이터',smallbiz:'소상공인'}
export function Content({text}:{text?:string}) { return <div className="reading"><Markdown>{(text||'').replace(/^·\s*/gm,'- ')}</Markdown></div> }
export function ReviewMetrics({data}:{data:Review}) {
  const previous=data.previous
  const difference=previous?data.kept-previous.kept:null
  const delta=(n:number)=>(n>0?'+':'')+n.toLocaleString()
  return <details className="feature-box"><summary>{previous?.same_elapsed?'직전 동일 경과기간과 비교':'직전 기간과 비교'}</summary>
    <div className="metric-grid">
      <div><strong>{data.kept.toLocaleString()}</strong><span>현재 기간</span><time>{data.start} ~ {data.through}</time></div>
      <div><strong>{previous?.kept.toLocaleString()||'0'}</strong><span>비교 기간</span><time title="비교 종료 시각은 포함하지 않습니다.">{previous?.start} ~ {previous?.until_exclusive.replace('T',' ').slice(0,16)} 직전</time></div>
      <div><strong>{difference===null?'—':delta(difference)}</strong><span>기사 수 차이</span></div>
    </div>
    <div className="table-scroll"><table><thead><tr><th>주제</th><th>기사 수</th><th>현재 비중</th><th>이전 비중</th><th>변화</th></tr></thead><tbody>{data.axes?.map(a=><tr key={a.axis}><th>{Labels[a.axis]||a.axis}</th><td>{a.n.toLocaleString()}</td><td>{a.share}%</td><td>{a.previous_share===null?'—':a.previous_share+'%'}</td><td>{a.previous_share===null||!data.kept?'—':delta(Number((a.share-a.previous_share).toFixed(1)))+'%p'}</td></tr>)}</tbody></table></div>
    <details><summary>일별 기사 수</summary><div className="daily-counts">{data.series?.map(d=><div key={d.date}><time>{d.date}</time><strong>{d.count.toLocaleString()}</strong></div>)}</div></details>
  </details>
}
export function Recommendations({period,editorialTasks,sources,addTask}:{period:string;editorialTasks?:Candidate[];sources:Source[];addTask(t:Candidate):boolean}) {
  const result=useAPI<{tasks:Candidate[]}>('/api/task-candidates')
  const [added,setAdded]=useState<Set<string>>(new Set())
  const tasks=editorialTasks?.length?editorialTasks:(result.data?.tasks||[]).filter(t=>t.period===period)
  return <section className="article-section"><div className="section-bar"><h2>사업 추천·과제 후보</h2><a href="#saved/tasks">검토 보드 <ArrowUpRight size={16}/></a></div>{!tasks.length?<div className="empty">{result.error?'과제 후보를 불러오지 못했습니다.':result.data?'등록된 과제 후보가 없습니다.':'불러오는 중…'}{result.error&&<Button onClick={result.retry}>다시 불러오기</Button>}</div>:<div className="review-grid">{tasks.map(t=>{const key=t.key||period+':'+t.title;return <Card key={key}><CardContent><h3 className="feature-title">{t.title}</h3><Content text={[t.fact&&'- **관찰**: '+t.fact,t.mean&&'- **업무 관련성**: '+t.mean,t.ask&&'- **확인할 점**: '+t.ask].filter(Boolean).join('\n')}/><Button variant="outline" onClick={()=>{if(addTask({...t,key,period,sources:t.sources||sources.filter(s=>t.source_ids?.includes(s.id))}))setAdded(new Set([...added,key]))}}>{added.has(key)?<Check/>:<Plus/>}{added.has(key)?'보드에 추가됨':'검토 보드에 추가'}</Button><details className="sources"><summary>근거 기사</summary><div>{(t.sources||sources.filter(s=>t.source_ids?.includes(s.id))).map(s=><a key={s.id} href={/^https?:\/\//.test(s.url)?s.url:undefined} target="_blank" rel="noopener noreferrer"><span>{s.title}</span><ArrowUpRight size={16}/></a>)}</div></details></CardContent></Card>})}</div>}</section>
}
type Law = Article & {checklist?:{label:string;detail:string}[];dept?:string;kind?:string;revision?:string;effective?:string}
type LawResult={items:Law[];total:number;checklist?:{label:string;count:number;items:{id:number;title:string;detail:string}[]}[]}
export function Laws({open}:{open(id:number):void}) {
  const [draft,setDraft]=useState(''),[q,setQ]=useState(''),[days,setDays]=useState('0'),[page,setPage]=useState(1)
  const params=new URLSearchParams({q,days,limit:'30',offset:String((page-1)*30)})
  const {data,error,retry}=useAPI<LawResult>('/api/regulatory?'+params)
  return <><div className="page-heading"><h1>법령·규제</h1></div><form className="search-bar" onSubmit={e=>{e.preventDefault();setQ(draft.trim());setPage(1)}}><Input aria-label="법령 검색" placeholder="법령 검색" value={draft} onChange={e=>setDraft(e.target.value)}/><Button>검색</Button></form><div className="section-bar"><div className="toolbar">{[['0','전체'],['7','최근 7일'],['30','최근 30일'],['90','최근 90일'],['365','최근 1년']].map(([v,label])=><Button key={v} variant={days===v?'default':'outline'} aria-pressed={days===v} onClick={()=>{setDays(v);setPage(1)}}>{label}</Button>)}</div><span>{data?.total.toLocaleString()}건</span></div>
    {!data?<div className="empty" role={error?'alert':'status'}>{error||'불러오는 중…'}{error&&<Button onClick={retry}>다시 불러오기</Button>}</div>:<>
      {!!data.checklist?.length&&<details className="feature-box"><summary>업무 적용점 · 현재 페이지</summary>{data.checklist.map(g=><section key={g.label}><h3 className="feature-title">{g.label}</h3>{g.items.map(i=><div className="application-item" key={i.id}><button onClick={()=>open(i.id)}>{i.title} ↗</button><Content text={i.detail}/></div>)}</section>)}</details>}
      {!data.items.length?<div className="empty">검색 결과가 없습니다.</div>:<div className="law-list">{data.items.map(item=><Card key={item.id}><CardContent><button className="law-title" onClick={()=>open(item.id)}>{item.title}<ArrowUpRight size={19}/></button><div className="article-meta">{item.dept&&<span>{item.dept}</span>}{item.kind&&<span>{item.kind}</span>}{item.revision&&<span>{item.revision}</span>}<time>발령 {item.published}</time>{item.effective&&<span>시행 {item.effective.replace(/^(\d{4})(\d{2})(\d{2})$/,'$1-$2-$3')}</span>}</div><div className="law-summary"><h3>AI 요약</h3>{item.insight?<Content text={item.insight}/>:<p>등록된 AI 요약이 없습니다.</p>}</div>{!!item.checklist?.length&&<div className="law-applications"><h3>업무 적용점</h3>{item.checklist.map(c=><Content key={c.label+c.detail} text={'- **'+c.label+'**: '+c.detail}/>)}</div>}{item.summary&&<details className="sources"><summary>제개정 내용</summary><Content text={item.summary}/></details>}</CardContent></Card>)}</div>}
      <div className="pagination"><Button variant="outline" disabled={page===1} onClick={()=>setPage(n=>n-1)}>이전</Button><span>{page} / {Math.max(1,Math.ceil(data.total/30))}</span><Button variant="outline" disabled={page*30>=data.total} onClick={()=>setPage(n=>n+1)}>다음</Button></div>
    </>}
  </>
}
