import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import Landing from './Landing'
import Proof from './Proof'
import './styles.css'

const path = location.pathname
const page = path.startsWith('/proof') ? <Proof /> : path.startsWith('/watch') || path.startsWith('/demo') ? <App /> : <Landing />
createRoot(document.getElementById('root')!).render(<StrictMode>{page}</StrictMode>)
