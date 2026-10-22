import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import Proof from './Proof'
import './styles.css'

createRoot(document.getElementById('root')!).render(<StrictMode>{location.pathname.startsWith('/proof') ? <Proof /> : <App />}</StrictMode>)
