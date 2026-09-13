import path from 'node:path'
import {defineConfig} from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
export default defineConfig({
  plugins:[react(),tailwindcss()],
  resolve:{alias:{'@':path.resolve(import.meta.dirname,'src')}},
  base:'/preview/',
  server:{port:5173,strictPort:true,proxy:{'/search.csv':'http://127.0.0.1:8023','/legacy-preview':'http://127.0.0.1:8023','/api':'http://127.0.0.1:8023','/static':'http://127.0.0.1:8023','/preview/static':{target:'http://127.0.0.1:8023',rewrite:path=>path.replace('/preview','')},'/report':'http://127.0.0.1:8023'}},
  // OneDrive can lock old build assets; preserve them while publishing new hashed files.
  build:{outDir:'dist',emptyOutDir:false,rollupOptions:{output:{manualChunks(id){
    if(id.includes('node_modules')) return id.includes('react-markdown') || id.includes('remark-') || id.includes('micromark') || id.includes('mdast') || id.includes('hast') || id.includes('unified') ? 'markdown' : 'vendor'
  }}}},
})
