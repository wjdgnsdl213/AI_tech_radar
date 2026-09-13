import { useEffect, useRef, useState } from 'react'
import suitURL from '@sun-typeface/suit/fonts/variable/woff2/SUIT-Variable.woff2?url'

// Keep the proven graph gestures, navigation context and personal-data workflows.
// Isolated styling avoids letting legacy global selectors leak into React components.
export function LegacyPanel({route, title, theme, autoHeight = false, onRoute, onOpenItem}: {route:string; title:string; theme:string; autoHeight?:boolean; onRoute?(hash:string):void; onOpenItem(id:number):void}) {
  const ref = useRef<HTMLIFrameElement>(null)
  const initialTheme = useRef(theme)
  const [loaded, setLoaded] = useState(false)
  const [height, setHeight] = useState(620)
  const sync = () => ref.current?.contentWindow?.postMessage({type:'sab-preview-theme', theme, viewportHeight:window.innerHeight, fontURL:new URL(suitURL,location.href).href}, location.origin)
  useEffect(() => {
    sync()
    window.addEventListener('resize', sync)
    return () => window.removeEventListener('resize', sync)
  }, [theme, loaded])
  useEffect(() => {
    const receive = (event:MessageEvent) => {
      if(event.origin !== location.origin || event.source !== ref.current?.contentWindow) return
      if(autoHeight && event.data?.type === 'sab-preview-height' && Number.isSafeInteger(event.data.height) && event.data.height > 0) setHeight(event.data.height)
      if(event.data?.type === 'sab-preview-route' && typeof event.data.hash === 'string') onRoute?.(event.data.hash)
      if(event.data?.type === 'sab-preview-item' && Number.isSafeInteger(event.data.id) && event.data.id > 0) onOpenItem(event.data.id)
    }
    window.addEventListener('message', receive)
    return () => window.removeEventListener('message', receive)
  }, [onRoute, onOpenItem, autoHeight])
  return <iframe ref={ref} className="legacy-panel" style={autoHeight ? {height} : undefined} title={title} src={'/legacy-preview?previewEmbed=1&theme='+initialTheme.current+(autoHeight?'&autoHeight=1':'')+'#'+route} onLoad={()=>{setLoaded(true);sync()}}/>
}
