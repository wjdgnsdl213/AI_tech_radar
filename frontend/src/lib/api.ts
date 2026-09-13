import { useEffect, useState } from 'react'

export interface Article { id: number; title: string; summary?: string; insight?: string; url: string; source: string; published: string; axes?: string[]; related?:Article[]; checklist?:{label:string;detail:string}[] }
export interface Source { id: number; title: string; url: string }
export interface Review { period: string; label: string; kept: number; articles: Article[]; start?:string; through?:string; previous?:{kept:number;start:string;until_exclusive:string;same_elapsed:boolean}; axes?:{axis:string;n:number;share:number;previous_share:number|null}[]; series?:{date:string;count:number}[]; editorial?: { title: string; summary: string; as_of: string; sections: {title: string; body: string; source_ids: number[]}[]; sources: Source[]; tasks?:{title:string;fact:string;mean:string;ask:string;source_ids:number[]}[] } }
const cache = new Map<string, {at: number; value: unknown}>()
const pending = new Map<string, Promise<unknown>>()
export async function getJSON<T>(url: string): Promise<T> {
  const hit = cache.get(url)
  if (hit && Date.now() - hit.at < 30_000) return hit.value as T
  if (!pending.has(url)) {
    pending.set(url, fetch(url).then(async response => {
      if (!response.ok) throw new Error('데이터를 불러오지 못했습니다.')
      const value = await response.json()
      if (value.error) throw new Error('자료를 찾을 수 없습니다.')
      if (cache.size >= 80) cache.delete(cache.keys().next().value!)
      cache.set(url, {at: Date.now(), value})
      return value
    }).finally(() => pending.delete(url)))
  }
  return pending.get(url) as Promise<T>
}
export function useAPI<T>(url: string) {
  const [result, setResult] = useState<{url: string; data?: T; error?: string}>({url: ''})
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let active = true
    getJSON<T>(url).then(data => { if (active) setResult({url, data}) }, error => { if (active) setResult({url, error: String(error.message)}) })
    return () => { active = false }
  }, [url, attempt])
  return {data: result.url === url ? result.data : undefined, error: result.url === url ? result.error : undefined,
    retry: () => { setResult({url: ''}); setAttempt(n => n + 1) }}
}
